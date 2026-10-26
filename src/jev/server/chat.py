"""/v1/chat/completions 端点（OpenAI 兼容 + SSE 流式，design.md §4.3.1）。

M2（T013）：级联引擎（engine 注入）激活三路径全链路——路径① 小脑直答
模板合成（不经大脑）；路径② 分段生成（段预算 LIGHT_SEGMENT_TOKENS）+
MID 批早停；路径③ 单次生成。POST 复核：needs_retry 一次上限（附反馈
重试），重试后仍不过 → 降级交付（jev_degraded=true）。台账（ledger
注入）逐请求记录 decision/route/answer 三类条目（FR8）。

engine 未注入时保持 M1 直通行为（决策仅写头，大脑单次调用原样透传）。
流式（stream=true）需级联引擎：direct 模板切片；full 逐 chunk 直传；
light 每 LIGHT_MID_CHUNKS 个 chunk 评估一次 MID，STOP 即断流；流式
POST 只记录不重试（重试需整流重发，M2 口径）。

EX-04（非流式）：路径②/③ 生成总时长上限 generation_timeout_s——段间/
调用前检查时限，超时以已生成内容截断、POST 复核后降级交付
（jev_degraded=true）；流式超时断流属后续迭代口径。

错误口径（OpenAI 形状 {error:{message,type,param,code}}）：400
messages/参数非法（param 字段级定位）；429 并发超限（仅 chat 端点）；
503 大脑不可用（含 Retry-After，过载统一口径）。
"""

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from jev.errors import JevProtocolError, JevTimeoutError, JevUnavailableError
from jev.kernel.cache.semantic import CacheHit, SemanticCache
from jev.kernel.cascade.budget import BudgetContext
from jev.kernel.cascade.draft import DraftConfig, apply_draft, build_draft_prefix
from jev.kernel.cascade.engine import (
    CascadeEngine,
    MidAction,
    PostVerdict,
    Route,
    RouteDecision,
)
from jev.kernel.cascade.prefix import prefix_fingerprint
from jev.kernel.cerebellum.base import (
    Answer,
    CerebellumBackend,
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
)
from jev.kernel.cerebrum.embeddings import EmbeddingService
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.calibration import Calibration
from jev.kernel.decisions.evaluate import StageEvaluation, evaluate_stage
from jev.kernel.decisions.framework import (
    Decision,
    DecisionPoint,
    DecisionRegistry,
    Stage,
)
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter
from jev.kernel.prefill.evidence import EvidenceSelector
from jev.kernel.runtime.bands import apply_self_verify_gate, classify_band
from jev.kernel.runtime.config import RuntimeConfig
from jev.kernel.runtime.conformal import AdmissionPolicy, RiskController
from jev.kernel.runtime.prompt import build_prompt
from jev.kernel.runtime.uncertainty import estimate_uncertainty
from jev.kernel.runtime.verifier import BrainVerifier, VerifyResult

logger = logging.getLogger(__name__)

_RETRY_AFTER_S = "1"
_ROLES = {"system", "user", "assistant", "tool"}
LIGHT_SEGMENT_TOKENS = 128  # 路径② 分段生成预算（[cascade] light_max_tokens 均分口径）
LIGHT_MID_CHUNKS = 8  # 路径② 流式每 N 个 chunk 评估一次 MID 批
_DIRECT_CHUNK_CHARS = 24  # 路径① 流式模板切片粒度
_ROUTES = {"direct", "light", "full"}
RETRY_FEEDBACK = "上一次回答未通过复核（不完整或偏题），请重新给出更完整、更切题的回答。"


def _error(
    status: int,
    message: str,
    *,
    type_: str,
    param: str | None = None,
    code: str | None = None,
    include_param: bool = True,
    retry_after: str | None = None,
) -> JSONResponse:
    error: dict = {"message": message, "type": type_}
    if include_param:
        error["param"] = param
    error["code"] = code
    headers = {"Retry-After": retry_after} if retry_after else None
    return JSONResponse(
        status_code=status, content={"error": error}, headers=headers
    )


class _InvalidRequest(Exception):
    """字段级校验失败（param 定位），由 handler 统一映射 400。"""

    def __init__(self, param: str, message: str) -> None:
        super().__init__(message)
        self.param = param
        self.message = message


def _validate_chat_request(
    payload: object,
) -> tuple[list[dict], float, int, list[str] | None, bool]:
    """OpenAI 规范校验；返回 (messages, temperature, max_tokens, stop, stream)。"""
    if not isinstance(payload, dict):
        raise _InvalidRequest("body", "请求体须为 JSON 对象")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _InvalidRequest("messages", "messages 必选、须为非空数组")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise _InvalidRequest(f"messages[{index}]", "消息元素须为对象")
        role = message.get("role")
        if role not in _ROLES:
            raise _InvalidRequest(
                f"messages[{index}].role",
                f"role 非法: {role!r}（合法: system/user/assistant/tool）",
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise _InvalidRequest(
                f"messages[{index}].content", "content 必选且须为字符串"
            )

    temperature = payload.get("temperature", 0.7)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise _InvalidRequest("temperature", "temperature 须为数值")

    max_tokens = payload.get("max_tokens", 512)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise _InvalidRequest("max_tokens", "max_tokens 须为整数")

    stop = payload.get("stop")
    if stop is not None:
        valid_stop = isinstance(stop, str) or (
            isinstance(stop, list) and all(isinstance(item, str) for item in stop)
        )
        if not valid_stop:
            raise _InvalidRequest("stop", "stop 须为字符串或字符串数组")

    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise _InvalidRequest("stream", "stream 须为布尔值")

    return (
        messages,
        float(temperature),
        max_tokens,
        stop if isinstance(stop, list) else stop,
        stream,
    )


def _decision_from_answer(point: DecisionPoint, answer: Answer) -> Decision:
    if isinstance(answer, ChoiceAnswer):
        value, confidence = answer.choice, answer.confidence
    elif isinstance(answer, ScoreAnswer):
        # 语义值取众数等级（线协议 score 为期望等级浮点）
        value, confidence = answer.mode_level, answer.confidence
    elif isinstance(answer, NoulAnswer):
        value, confidence = answer.noul, answer.confidence
    else:
        raise JevProtocolError(f"{point.id}: 未知答案类型: {type(answer).__name__}")
    return Decision(
        point_id=point.id, stage=point.stage, mode=point.mode,
        value=value, confidence=confidence,
    )


def evaluate_decisions(
    cerebellum: CerebellumBackend | None,
    points: list[DecisionPoint],
    state: str,
    *,
    seed: int = 0,
    calibration: Calibration | None = None,
) -> tuple[list[Decision], dict[str, Answer], float]:
    """兼容 shim（AR002）：委托 kernel.decisions.evaluate.evaluate_stage。

    保留既有签名（bench/测试调用点不变）；多探针/置换/校准由 kernel 侧实现。
    返回 (决策列表, 原始答案[qid→Answer], 批量前向耗时 ms)。
    """
    result = evaluate_stage(
        cerebellum, points, state, seed=seed, calibration=calibration
    )
    return result.decisions, result.answers, result.latency_ms


def render_direct_answer(decisions: list[Decision], registry: DecisionRegistry) -> str:
    """路径① 模板合成：PRE 决策 applied 值 → 结构化答案文本（UT-07 口径）。

    小脑不生成散文（design §4.1 决策 3）：直答即决策结果的结构化渲染。
    """
    lines = ["【Jev 小脑直答·结构化决策结果】"]
    for decision in decisions:
        try:
            point = registry.get(decision.point_id)
        except JevProtocolError:
            continue
        lines.append(f"- {decision.point_id}: {decision.applied_value(point)}")
    lines.append("（路径① 决策直答，未经大脑生成）")
    return "\n".join(lines)


def _content_of(response: dict[str, Any]) -> str:
    return response["choices"][0]["message"]["content"]


def _usage_of(response: dict[str, Any]) -> dict[str, int]:
    usage = response.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
    for key in total:
        total[key] += usage.get(key, 0)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_chunk(chat_id: str, created: int, piece: str) -> str:
    return _sse(
        {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "local",
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
    )


def _sse_finish(chat_id: str, created: int) -> str:
    return _sse(
        {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "local",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )


def _sse_error(message: str) -> str:
    return _sse(
        {"error": {"message": message, "type": "server_error", "code": "brain_unavailable"}}
    )


def _split_text(text: str, size: int = _DIRECT_CHUNK_CHARS) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class _CascadeContext:
    """单请求级联编排上下文：组件引用 + 决策计数 + 台账记录 + 生成时限。"""

    def __init__(
        self,
        cerebellum: CerebellumBackend | None,
        brain: LlamaServerClient | None,
        registry: DecisionRegistry | None,
        engine: CascadeEngine,
        route_policy: Callable[[list[Decision]], str] | None,
        ledger: LedgerWriter | None,
        request_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
        generation_timeout_s: float | None = None,
        calibration: Calibration | None = None,
        max_brain_tokens: int | None = None,
        max_latency_ms: float | None = None,
        draft_config: DraftConfig | None = None,
        runtime_config: RuntimeConfig | None = None,
        cache: SemanticCache | None = None,
        verifier: BrainVerifier | None = None,
        embeddings: EmbeddingService | None = None,
        risk_controller: RiskController | None = None,
        admission_policy: AdmissionPolicy | None = None,
        profile_name: str = "",
        evidence: EvidenceSelector | None = None,
    ) -> None:
        self.cerebellum = cerebellum
        self.brain = brain
        self.registry = registry
        self.engine = engine
        self.route_policy = route_policy
        self.ledger = ledger
        self.request_id = request_id
        self.chat_id = f"chatcmpl-jev-{request_id}"
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stop = stop
        self.calibration = calibration
        self.max_brain_tokens = max_brain_tokens
        self.max_latency_ms = max_latency_ms
        self.draft_config = draft_config if draft_config is not None else DraftConfig()
        self.runtime_config = runtime_config
        self.cache = cache
        self.verifier = verifier
        self.embeddings = embeddings
        self.risk_controller = risk_controller or RiskController.disabled()
        self.admission_policy = admission_policy
        self.profile_name = profile_name
        self.evidence = evidence
        self.last_fused: dict = {}
        self.last_runtime: dict = {
            "uncertainty": {"u": None, "band": "off", "self_agree": 1.0,
                            "probe_agreement": None, "disagreement": None},
            "self_verify": {"consistency": 1.0, "cross_point": 1.0, "mode": "observe"},
            "verifier": {"called": False, "verdict": None, "tokens": 0, "reason": None,
                         "skipped_reason": None, "unavailable": False},
            "prefix": {"fingerprint": None, "reused": False, "evidence": None,
                       "ttft_ms": 0.0, "prompt_tokens": 0, "prompt_n": 0},
            "prefill": {"selected": 0, "total": 0, "before_tokens": 0,
                        "after_tokens": 0, "skipped": None,
                        "selector": "salience", "budget_ratio": 0.0,
                        "entropy": 0.0, "top_gap": 0.0, "fallback": None},
            "cache": {"layer": "none", "similarity": None},
            "memory": {"summary_tokens": 0},
            "risk": {"mode": "off", "score": None, "threshold": None, "decision": "off"},
            "plan": profile_name,
            "admission": {"mode": "similarity", "threshold": None},
        }
        self.prompt_build = None
        self.budget_capped = False
        self.decision_count = 0
        # EX-04：路径②/③ 生成总时长上限（None 表示不限时）
        self.deadline = (
            time.monotonic() + float(generation_timeout_s)
            if generation_timeout_s is not None and generation_timeout_s > 0
            else None
        )

    def timed_out(self) -> bool:
        """生成时限是否已到（无时限恒 False）。"""
        return self.deadline is not None and time.monotonic() >= self.deadline

    def remaining_s(self) -> float | None:
        """距级联 deadline 的剩余秒数（AR002 FR2-4：作为 HTTP 超时覆盖值）。"""
        if self.deadline is None:
            return None
        return max(0.001, self.deadline - time.monotonic())

    # ------------------------------------------------------------------
    # 决策评估与台账
    # ------------------------------------------------------------------

    def evaluate(self, stage: Stage, state: str) -> list[Decision]:
        """该 stage active+shadow 点合并单次批量评估；shadow 只记台账不进控制流。

        返回全量决策（active+shadow）——引擎侧按 Mode.ACTIVE 过滤，天然不受
        shadow 干扰；decision_count 仅计 active（X-Jev-Decisions 控制流口径）。
        """
        if self.registry is None:
            return []
        active_points = self.registry.active(stage)
        shadow_points = self.registry.shadow(stage)
        points = active_points + shadow_points
        if not points:
            return []
        decisions, answers, latency_ms = evaluate_decisions(
            self.cerebellum, points, state, calibration=self.calibration
        )
        self.decision_count += len(active_points)
        for decision, point in zip(decisions, points, strict=True):
            self._log_decision(decision, point, answers.get(decision.point_id), latency_ms)
        return decisions

    def state_json(self, assistant_text: str | None = None) -> str:
        """决策 state：messages JSON（可选追加 assistant 生成文本）。"""
        messages = (
            self.messages
            if assistant_text is None
            else self.messages + [{"role": "assistant", "content": assistant_text}]
        )
        return json.dumps(messages, ensure_ascii=False, separators=(",", ":"))


    # ------------------------------------------------------------------
    # AR003 运行时（默认关闭）
    # ------------------------------------------------------------------

    @property
    def runtime_enabled(self) -> bool:
        return bool(self.runtime_config is not None and self.runtime_config.enabled)

    def query_text(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""

    def applied_map(self, decisions: list[Decision]) -> dict:
        result: dict = {}
        if self.registry is None:
            return {decision.point_id: decision.value for decision in decisions}
        for decision in decisions:
            try:
                point = self.registry.get(decision.point_id)
            except JevProtocolError:
                result[decision.point_id] = decision.value
                continue
            result[decision.point_id] = decision.applied_value(point)
        return result

    def cache_lookup(self) -> CacheHit | None:
        if not self.runtime_enabled or self.cache is None:
            return None
        fingerprint = prefix_fingerprint(self.messages)
        self.last_runtime["prefix"]["fingerprint"] = fingerprint
        hit = self.cache.lookup(
            fingerprint,
            self.query_text(),
            embeddings=self.embeddings,
            admission=self.admission_policy,
        )
        if hit is not None:
            self.last_runtime["cache"] = {
                "layer": hit.layer,
                "similarity": hit.similarity,
            }
            self.last_runtime["admission"] = {
                "mode": hit.admission,
                "threshold": hit.threshold,
            }
        return hit

    def cache_store(self, answer: str, route: str) -> None:
        if not self.runtime_enabled or self.cache is None or not answer:
            return
        fingerprint = self.last_runtime["prefix"].get("fingerprint")
        if fingerprint is None:
            fingerprint = prefix_fingerprint(self.messages)
            self.last_runtime["prefix"]["fingerprint"] = fingerprint
        self.cache.store(
            fingerprint,
            self.query_text(),
            answer,
            route,
            embeddings=self.embeddings,
        )

    def evaluate_detailed(self, stage: Stage, state: str) -> StageEvaluation:
        """与 evaluate 同口径，但保留 fused 供不确定性/自证使用。"""
        if self.registry is None:
            return StageEvaluation([], {}, 0.0, {})
        active_points = self.registry.active(stage)
        shadow_points = self.registry.shadow(stage)
        points = active_points + shadow_points
        if not points:
            return StageEvaluation([], {}, 0.0, {})
        from jev.kernel.decisions.evaluate import evaluate_stage

        result = evaluate_stage(
            self.cerebellum, points, state, calibration=self.calibration
        )
        self.decision_count += len(active_points)
        for decision, point in zip(result.decisions, points, strict=True):
            self._log_decision(
                decision, point, result.answers.get(decision.point_id), result.latency_ms
            )
        if stage is Stage.PRE:
            self.last_fused = result.fused
        return result

    def route_decision(self, pre: list[Decision]) -> RouteDecision:

        if self.route_policy is not None:
            return RouteDecision(
                route=Route(self.route_policy(pre)), band="off", needs_verify=False
            )
        if not self.runtime_enabled or self.runtime_config is None:
            budget = self.budget()
            legacy_route = self.engine.route_chat(
                pre, margin_tau=self.margin_tau, budget=budget
            )
            self.budget_capped = (
                budget is not None
                and budget.max_brain_tokens is not None
                and not budget.allows_full()
                and legacy_route is not Route.FULL
            )
            return RouteDecision(route=legacy_route, band="off", needs_verify=False)
        config = self.runtime_config
        uncertainty = estimate_uncertainty(pre, self.last_fused)
        self.last_runtime["uncertainty"] = {
            "u": round(uncertainty.u, 6),
            "band": classify_band(uncertainty.u, config.bands),
            "self_agree": round(uncertainty.self_agree, 6),
            "probe_agreement": uncertainty.probe_agreement,
            "disagreement": uncertainty.disagreement,
        }
        decision = self.engine.route_chat_v2(
            pre,
            uncertainty=uncertainty,
            policy=config.bands,
            margin_tau=self.margin_tau,
            budget=self.budget(),
        )
        band = apply_self_verify_gate(
            decision.band, uncertainty.self_agree, config.bands
        )
        if band != decision.band:
            decision = RouteDecision(
                route=Route.FULL if band == "uncertain" else decision.route,
                band=band,
                needs_verify=False if band == "uncertain" else decision.needs_verify,
                skipped_reason=decision.skipped_reason,
            )
        self.last_runtime["uncertainty"]["band"] = band
        budget = self.budget()
        self.budget_capped = (
            budget is not None
            and budget.max_brain_tokens is not None
            and not budget.allows_full()
            and decision.route is not Route.FULL
        )
        decision = self._apply_risk_gate(decision, uncertainty.u)
        return decision

    def _apply_risk_gate(self, decision: RouteDecision, u: float) -> RouteDecision:
        controller = self.risk_controller
        self.last_runtime["risk"] = {
            "mode": "conformal" if controller.enabled else "off",
            "score": round(float(u), 6) if isinstance(u, (int, float)) else None,
            "threshold": controller.threshold,
            "decision": "off" if not controller.enabled else "allow",
        }
        if not controller.enabled or decision.route is Route.FULL:
            return decision
        if controller.allow_downgrade(u):
            return decision
        self.last_runtime["risk"]["decision"] = "deny"
        return RouteDecision(
            route=Route.FULL,
            band="uncertain",
            needs_verify=False,
            skipped_reason="risk",
        )

    def prepare_prompt(self, pre: list[Decision], *, full_route: bool = True) -> list[dict]:
        if not self.runtime_enabled or self.runtime_config is None:
            return _draft_messages(self, pre) if full_route else self.messages
        config = self.runtime_config
        prompt_config = config.prompt
        if not full_route:
            import dataclasses as _dc

            prompt_config = _dc.replace(prompt_config, draft_enabled=False)
        applied = self.applied_map(pre)
        build = build_prompt(
            self.messages,
            pre,
            prompt_config,
            query=self.query_text(),
            registry=self.registry,
            applied=applied,
            prefill_config=config.prefill,
            evidence=self.evidence,
        )
        self.prompt_build = build
        self.last_runtime["memory"] = {"summary_tokens": build.memory_summary_tokens}
        if build.prefill is not None:
            self.last_runtime["prefill"] = {
                "selected": build.prefill.selected,
                "total": build.prefill.total,
                "before_tokens": build.prefill.before_tokens,
                "after_tokens": build.prefill.after_tokens,
                "skipped": build.prefill.skipped,
                "selector": build.prefill.selector,
                "budget_ratio": build.prefill.budget_ratio,
                "entropy": build.prefill.entropy,
                "top_gap": build.prefill.top_gap,
                "fallback": build.prefill.fallback,
            }
        if build.allocation is not None:
            self.last_runtime["prefill"] = {
                "selected": len(build.allocation.selected),
                "total": len(build.allocation.selected)
                + len(build.allocation.omitted),
                "before_tokens": build.allocation.before_tokens,
                "after_tokens": build.allocation.after_tokens,
                "skipped": build.allocation.skipped,
                "selector": build.allocation.selector,
                "budget_ratio": build.allocation.budget_ratio,
                "entropy": build.allocation.entropy,
                "top_gap": build.allocation.top_gap,
                "fallback": build.allocation.fallback,
            }
        return build.messages

    def verify_draft(
        self, pre: list[Decision], draft: str
    ) -> VerifyResult | None:
        if self.verifier is None or not self.runtime_enabled:
            return None
        result = self.verifier.verify(
            self.query_text(),
            draft,
            self.applied_map(pre),
            timeout_s=self.remaining_s(),
        )
        self.last_runtime["verifier"] = {
            "called": True,
            "verdict": result.verdict,
            "tokens": result.tokens,
            "reason": result.reason,
            "skipped_reason": None,
            "unavailable": result.unavailable,
        }
        return result

    @property
    def margin_tau(self) -> float:
        return self.calibration.margin_tau if self.calibration is not None else 0.0

    def budget(self) -> BudgetContext | None:
        """请求级预算：state token 以 4 字符≈1 token 粗估（保守且确定性）。"""
        if self.max_brain_tokens is None and self.max_latency_ms is None:
            return None
        return BudgetContext(
            max_brain_tokens=self.max_brain_tokens,
            max_latency_ms=self.max_latency_ms,
            state_tokens=max(1, len(self.state_json()) // 4),
            completion_tokens=self.max_tokens,
        )

    def effective_max_tokens(self) -> int:
        """预算约束下的完成 token 上限（至少 1，保证有进展）。"""
        budget = self.budget()
        if budget is None or budget.max_brain_tokens is None:
            return self.max_tokens
        return max(1, min(self.max_tokens, budget.effective_max_tokens(self.max_tokens) or 1))

    def route_of(self, pre: list[Decision]) -> str:
        if self.route_policy is not None:
            return self.route_policy(pre)
        budget = self.budget()
        route = self.engine.route_chat(
            pre, margin_tau=self.margin_tau, budget=budget
        ).wire
        self.budget_capped = (
            budget is not None
            and budget.max_brain_tokens is not None
            and not budget.allows_full()
            and route != Route.FULL.wire
        )
        return route

    def log_route(self, route: str, pre: list[Decision]) -> None:
        if self.ledger is None:
            return
        posterior = (
            self.engine.posterior(Route(route), pre)
            if route in _ROUTES
            else {"route": route}
        )
        self.ledger.log(
            LedgerEntry(
                request_id=self.request_id,
                kind="route",
                ts=time.time(),
                route=route,
                state=self.state_json(),
                posterior=posterior,
            )
        )

    def log_answer(
        self, content: str, post_verdict: str | None, tokens: int
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.log(
            LedgerEntry(
                request_id=self.request_id,
                kind="answer",
                ts=time.time(),
                route=None,
                content=content,
                post_verdict=post_verdict,
                tokens=tokens,
            )
        )

    def _log_decision(
        self,
        decision: Decision,
        point: DecisionPoint,
        answer: Answer | None,
        latency_ms: float,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.log(
            LedgerEntry(
                request_id=self.request_id,
                kind="decision",
                ts=time.time(),
                point_id=decision.point_id,
                stage=decision.stage.value,
                mode=decision.mode.value,
                question=point.question,
                question_type=point.question_type,
                value=decision.value,
                confidence=decision.confidence,
                probabilities=answer.probabilities if answer is not None else None,
                applied=decision.applied_value(point),
                from_fallback=decision.from_fallback,
                latency_ms=round(latency_ms, 3),
            )
        )


def create_chat_router(
    cerebellum: CerebellumBackend | None,
    brain: LlamaServerClient | None,
    registry: DecisionRegistry | None,
    *,
    max_concurrency: int = 8,
    route_policy: Callable[[list[Decision]], str] | None = None,
    engine: CascadeEngine | None = None,
    ledger: LedgerWriter | None = None,
    generation_timeout_s: float | None = None,
    calibration: Calibration | None = None,
    max_brain_tokens: int | None = None,
    max_latency_ms: float | None = None,
    draft_config: DraftConfig | None = None,
    runtime_config: RuntimeConfig | None = None,
    cache: SemanticCache | None = None,
    embeddings=None,
    risk_controller: RiskController | None = None,
    admission_policy: AdmissionPolicy | None = None,
    profile_name: str = "",
    evidence: EvidenceSelector | None = None,
) -> APIRouter:
    """构建 /v1/chat/completions 路由。

    cerebellum/brain 为 None 表示该组件未配置（决策走安全侧回退 / 503）。
    engine 注入激活级联全链路（三路径/MID/POST/流式/台账）；未注入保持
    M1 直通行为（route_policy 仅写响应头）。route_policy 在 engine 模式
    下覆盖路由判定（Kernel/SDK 编程扩展点）。generation_timeout_s 为
    路径②/③ 生成总时长上限（EX-04：超时截断并降级交付 jev_degraded）。
    """
    router = APIRouter()
    limiter = threading.BoundedSemaphore(max_concurrency) if max_concurrency > 0 else None

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _error(400, "请求体不是合法 JSON", type_="invalid_request_error")

        try:
            messages, temperature, max_tokens, stop, stream = _validate_chat_request(payload)
        except _InvalidRequest as exc:
            return _error(
                400, exc.message, type_="invalid_request_error", param=exc.param
            )
        if stream and engine is None:
            return _error(
                400,
                "流式生成需级联引擎装配（engine 未注入）",
                type_="invalid_request_error",
                param="stream",
            )

        if limiter is not None and not limiter.acquire(blocking=False):
            return _error(
                429,
                "并发请求超限，请稍后重试",
                type_="rate_limit_error",
                code="concurrency_limit",
                include_param=False,
            )
        try:
            if engine is None:
                return _handle_chat_passthrough(
                    cerebellum, brain, registry, route_policy,
                    messages, temperature, max_tokens, stop,
                    calibration=calibration,
                )
            context = _CascadeContext(
                cerebellum, brain, registry, engine, route_policy, ledger,
                uuid.uuid4().hex, messages, temperature, max_tokens, stop,
                generation_timeout_s=generation_timeout_s,
                calibration=calibration,
                max_brain_tokens=max_brain_tokens,
                max_latency_ms=max_latency_ms,
                draft_config=draft_config,
                runtime_config=runtime_config,
                cache=cache,
                verifier=(
                    BrainVerifier(brain, max_tokens=runtime_config.verifier_max_tokens)
                    if runtime_config is not None
                    and runtime_config.enabled
                    and runtime_config.bands.verifier_enabled
                    and brain is not None
                    else None
                ),
                embeddings=embeddings,
                risk_controller=risk_controller,
                admission_policy=admission_policy,
                profile_name=profile_name,
                evidence=evidence,
            )
            if stream:
                return _stream_response(context)
            return _handle_chat_cascade(context)
        finally:
            if limiter is not None:
                limiter.release()

    return router


# ---------------------------------------------------------------------------
# M1 直通（engine 未注入）
# ---------------------------------------------------------------------------


def _handle_chat_passthrough(
    cerebellum: CerebellumBackend | None,
    brain: LlamaServerClient | None,
    registry: DecisionRegistry | None,
    route_policy: Callable[[list[Decision]], str] | None,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    stop: list[str] | None,
    calibration: Calibration | None = None,
) -> JSONResponse:
    active_pre = registry.active(Stage.PRE) if registry is not None else []
    state = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    decisions, _answers, _latency = evaluate_decisions(
        cerebellum, active_pre, state, calibration=calibration
    )
    route = route_policy(decisions) if route_policy is not None else "full"

    if brain is None:
        return _error(
            503, "大脑后端未配置", type_="server_error", code="brain_unavailable",
            include_param=False, retry_after=_RETRY_AFTER_S,
        )
    try:
        brain_response = brain.chat(
            messages, max_tokens=max_tokens, temperature=temperature, stop=stop
        )
    except (JevUnavailableError, JevProtocolError) as exc:
        logger.warning("大脑不可用: %s", exc)
        return _error(
            503, f"大脑服务不可用: {exc}", type_="server_error", code="brain_unavailable",
            include_param=False, retry_after=_RETRY_AFTER_S,
        )

    return JSONResponse(
        status_code=200,
        content=brain_response,
        headers={"X-Jev-Route": route, "X-Jev-Decisions": str(len(decisions))},
    )


# ---------------------------------------------------------------------------
# 级联全链路（非流式）
# ---------------------------------------------------------------------------


def _handle_chat_cascade(context: _CascadeContext) -> JSONResponse:
    # AR003：语义缓存先于判定（命中直接返回，tokens=0）
    hit = context.cache_lookup()
    if hit is not None:
        empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        context.log_answer(hit.answer, None, 0)
        response = _json_response(
            context, hit.answer, empty_usage, degraded=False, route=hit.route
        )
        response.headers["X-Jev-Route"] = hit.route
        return response

    evaluated = context.evaluate_detailed(Stage.PRE, context.state_json())
    pre = evaluated.decisions
    decision = context.route_decision(pre)
    route = decision.route.wire
    if decision.skipped_reason:
        context.last_runtime["verifier"]["skipped_reason"] = decision.skipped_reason
        context.last_runtime["verifier"]["called"] = False
    context.log_route(route, pre)

    if route == "direct":
        content = render_direct_answer(pre, context.registry)
        context.log_answer(content, None, 0)
        context.cache_store(content, route)
        empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return _json_response(
            context, content, empty_usage, degraded=False, route=route
        )

    light_messages = context.prepare_prompt(pre, full_route=False)
    full_messages = (
        context.prepare_prompt(pre, full_route=True)
        if route == "full" or decision.needs_verify
        else None
    )

    if context.brain is None:
        return _error(
            503, "大脑后端未配置", type_="server_error", code="brain_unavailable",
            include_param=False, retry_after=_RETRY_AFTER_S,
        )
    timed_out = False
    try:
        if decision.needs_verify and context.verifier is not None:
            draft_content, draft_usage, draft_timeout = _generate_light(
                context, messages=light_messages
            )
            if draft_timeout:
                content, usage, timed_out = draft_content, draft_usage, True
            else:
                verified = context.verify_draft(pre, draft_content)
                if verified is not None and verified.verdict == "accept":
                    content, usage = draft_content, draft_usage
                else:
                    route = "full"
                    content, usage = _generate_full(
                        context, messages=full_messages or light_messages
                    )
        elif route == "light":
            content, usage, timed_out = _generate_light(
                context, messages=light_messages
            )
        else:
            content, usage = _generate_full(
                context, messages=full_messages or light_messages
            )
        verdict, degraded, content, usage = _post_review(
            context, content, usage, allow_retry=not timed_out
        )
        if timed_out:
            # EX-04：生成超时截断——POST correct_format 复核后降级交付
            degraded = True
    except (JevUnavailableError, JevProtocolError) as exc:
        if context.timed_out() or isinstance(exc, JevTimeoutError):
            # EX-04：大脑调用超时——截断（空/部分内容）复核后降级交付
            logger.warning(
                "大脑生成超时（request_id=%s，route=%s），截断降级交付: %s",
                context.request_id, route, exc,
            )
            content = ""
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            verdict, degraded, content, usage = _post_review(
                context, content, usage, allow_retry=False
            )
            degraded = True
            timed_out = True
        else:
            logger.warning("大脑不可用: %s", exc)
            return _error(
                503, f"大脑服务不可用: {exc}", type_="server_error", code="brain_unavailable",
                include_param=False, retry_after=_RETRY_AFTER_S,
            )

    if timed_out:
        logger.warning(
            "路径%s 生成超时截断，降级交付（request_id=%s）", route, context.request_id
        )
    if not degraded and verdict.value == "accept":
        context.cache_store(content, route)
    context.log_answer(content, verdict.value, usage["completion_tokens"])
    return _json_response(context, content, usage, degraded=degraded, route=route)


def _draft_messages(context: _CascadeContext, pre: list[Decision]) -> list[dict]:
    """路径③ messages 组装：默认逐字沿用；启用草稿注入时加 system 前缀并压缩历史。"""
    config = context.draft_config
    if config is None or not config.enabled:
        return context.messages
    prefix = build_draft_prefix(pre, context.registry)
    return apply_draft(context.messages, prefix, keep_turns=config.keep_turns)


def _generate_full(
    context: _CascadeContext,
    *,
    pre: list[Decision] | None = None,
    messages: list[dict] | None = None,
) -> tuple[str, dict[str, int]]:
    response = context.brain.chat(
        messages if messages is not None else _draft_messages(context, pre or []),
        max_tokens=context.effective_max_tokens(),
        temperature=context.temperature,
        stop=context.stop,
        timeout_s=context.remaining_s(),
        extra_body={"cache_prompt": True},
    )
    return _content_of(response), _usage_of(response)


def _generate_light(
    context: _CascadeContext,
    *,
    messages: list[dict] | None = None,
) -> tuple[str, dict[str, int], bool]:
    """路径②：按 LIGHT_SEGMENT_TOKENS 分段生成，段间 MID 批早停。

    EX-04：段间检查生成时限，超时以已生成部分截断返回（timed_out=True）。
    """
    segments: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    base_messages = context.messages if messages is None else messages
    remaining = context.effective_max_tokens()
    timed_out = False
    while remaining > 0:
        if context.timed_out():
            timed_out = True
            break
        prefix = "".join(segments)
        segment_messages = (
            base_messages + [{"role": "assistant", "content": prefix}]
            if prefix
            else base_messages
        )
        try:
            response = context.brain.chat(
                segment_messages,
                max_tokens=min(LIGHT_SEGMENT_TOKENS, remaining),
                temperature=context.temperature,
                stop=context.stop,
                timeout_s=context.remaining_s(),
            )
        except (JevUnavailableError, JevProtocolError) as exc:
            if context.timed_out() or isinstance(exc, JevTimeoutError):
                # 超时段调用失败——保留已生成部分截断交付
                logger.warning(
                    "路径② 段生成超时，截断交付（request_id=%s）: %s",
                    context.request_id, exc,
                )
                timed_out = True
                break
            raise
        segment_usage = _usage_of(response)
        _accumulate(usage, segment_usage)
        text = _content_of(response)
        if not text:
            break  # 空段无进展，防死循环
        segments.append(text)
        remaining -= segment_usage["completion_tokens"] or min(
            LIGHT_SEGMENT_TOKENS, remaining
        )
        mid = context.evaluate(Stage.MID, context.state_json("".join(segments)))
        if context.engine.handle_mid(mid) is MidAction.STOP:
            logger.info(
                "路径② MID 早停（request_id=%s，第 %d 段后）",
                context.request_id, len(segments),
            )
            break
    return "".join(segments), usage, timed_out


def _post_review(
    context: _CascadeContext,
    content: str,
    usage: dict[str, int],
    *,
    allow_retry: bool = True,
) -> tuple[PostVerdict, bool, str, dict[str, int]]:
    """POST 复核：needs_retry 触发一次反馈重试；重试后仍不过 → 降级交付。

    allow_retry=False（EX-04 超时截断）跳过重试直接降级；时限已到同样不再
    发起重试调用（截断语义优先）。
    """
    post = context.evaluate(Stage.POST, context.state_json(content))
    verdict = context.engine.handle_post(content, post, retried=False)
    if verdict is not PostVerdict.RETRY:
        return verdict, False, content, usage
    if not allow_retry or context.timed_out():
        logger.warning(
            "POST 复核需重试但生成时限已到，降级交付（request_id=%s）",
            context.request_id,
        )
        return PostVerdict.DOWNGRADE, True, content, usage

    logger.info("POST 复核触发一次重试（request_id=%s）", context.request_id)
    retry_messages = context.messages + [{"role": "user", "content": RETRY_FEEDBACK}]
    response = context.brain.chat(
        retry_messages,
        max_tokens=context.effective_max_tokens(),
        temperature=context.temperature,
        stop=context.stop,
        timeout_s=context.remaining_s(),
    )
    _accumulate(usage, _usage_of(response))
    retried_content = _content_of(response)

    second_post = context.evaluate(Stage.POST, context.state_json(retried_content))
    second_verdict = context.engine.handle_post(retried_content, second_post, retried=True)
    degraded = second_verdict is PostVerdict.DOWNGRADE
    if degraded:
        logger.warning(
            "POST 重试后仍未通过复核，降级交付（request_id=%s）", context.request_id
        )
    return second_verdict, degraded, retried_content, usage


def _route_headers(context: _CascadeContext, route: str) -> dict[str, str]:
    """路由响应头；margin/预算仅在启用时出现（AR002 接口契约）。"""
    headers = {
        "X-Jev-Route": route,
        "X-Jev-Decisions": str(context.decision_count),
    }
    if context.route_policy is None and context.margin_tau > 0:
        headers["X-Jev-Margin"] = f"{context.margin_tau:.3f}"
    if context.max_brain_tokens is not None or context.max_latency_ms is not None:
        headers["X-Jev-Budget"] = "capped" if context.budget_capped else "ok"
    if context.runtime_enabled:
        runtime = context.last_runtime
        u = runtime["uncertainty"]["u"]
        if u is not None:
            headers["X-Jev-U"] = f"{float(u):.3f}"
        verifier = runtime["verifier"]
        if verifier["called"] or verifier["skipped_reason"]:
            headers["X-Jev-Verified"] = (
                "skipped" if verifier["skipped_reason"] else str(verifier["verdict"])
            )
        else:
            headers["X-Jev-Verified"] = "off"
        headers["X-Jev-Cache"] = (
            f"hit-{runtime['cache']['layer']}"
            if runtime["cache"]["layer"] in {"exact", "semantic"}
            else "miss"
        )
        risk = runtime.get("risk") or {}
        if risk.get("mode") == "conformal":
            headers["X-Jev-Risk"] = str(risk.get("decision", "allow"))
        if context.profile_name:
            headers["X-Jev-Plan"] = context.profile_name
        prefill = runtime["prefill"]
        if prefill["skipped"]:
            headers["X-Jev-Prefill"] = f"skipped:{prefill['skipped']}"
        elif prefill["total"]:
            headers["X-Jev-Prefill"] = f"{prefill['selected']}/{prefill['total']}"
        else:
            headers["X-Jev-Prefill"] = "off"
        mode = prefill.get("selector") or "salience"
        ratio = prefill.get("budget_ratio") or 0.0
        if prefill["selected"]:
            headers["X-Jev-Prefill-Mode"] = f"{mode};r={ratio:.2f}"
    return headers


def _json_response(
    context: _CascadeContext,
    content: str,
    usage: dict[str, int],
    *,
    degraded: bool,
    route: str,
) -> JSONResponse:
    body: dict[str, Any] = {
        "id": context.chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "local",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": dict(usage),
    }
    if degraded:
        body["jev_degraded"] = True
    return JSONResponse(
        status_code=200,
        content=body,
        headers=_route_headers(context, route),
    )


# ---------------------------------------------------------------------------
# 级联全链路（SSE 流式）
# ---------------------------------------------------------------------------


def _stream_response(context: _CascadeContext) -> StreamingResponse | JSONResponse:
    pre = context.evaluate(Stage.PRE, context.state_json())
    route = context.route_of(pre)
    context.log_route(route, pre)
    if route != "direct" and context.brain is None:
        return _error(
            503, "大脑后端未配置", type_="server_error", code="brain_unavailable",
            include_param=False, retry_after=_RETRY_AFTER_S,
        )
    # 头部在流开始前固化，故流式决策数=PRE 批（MID/POST 见台账）
    headers = _route_headers(context, route)
    return StreamingResponse(
        _stream_generate(context, route, pre),
        media_type="text/event-stream",
        headers=headers,
    )


def _stream_generate(
    context: _CascadeContext, route: str, pre: list[Decision]
) -> Iterator[str]:
    created = int(time.time())

    if route == "direct":
        content = render_direct_answer(pre, context.registry)
        for piece in _split_text(content):
            yield _sse_chunk(context.chat_id, created, piece)
        context.log_answer(content, None, 0)
        yield _sse_finish(context.chat_id, created)
        yield "data: [DONE]\n\n"
        return

    collected: list[str] = []
    chunk_count = 0
    try:
        for chunk in context.brain.chat_stream(
            context.messages,
            max_tokens=context.effective_max_tokens(),
            temperature=context.temperature,
            stop=context.stop,
            timeout_s=context.remaining_s(),
        ):
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            delta = (
                choices[0].get("delta")
                if choices and isinstance(choices[0], dict)
                else None
            )
            piece = delta.get("content") if isinstance(delta, dict) else None
            if not piece:
                continue
            collected.append(piece)
            chunk_count += 1
            yield _sse_chunk(context.chat_id, created, piece)
            if route == "light" and chunk_count % LIGHT_MID_CHUNKS == 0:
                mid = context.evaluate(Stage.MID, context.state_json("".join(collected)))
                if context.engine.handle_mid(mid) is MidAction.STOP:
                    logger.info(
                        "路径② 流式 MID 早停（request_id=%s，第 %d chunk 后）",
                        context.request_id, chunk_count,
                    )
                    break
    except (JevUnavailableError, JevProtocolError) as exc:
        logger.warning("流式生成中断: %s", exc)
        yield _sse_error(f"大脑服务不可用: {exc}")
        yield "data: [DONE]\n\n"
        return

    content = "".join(collected)
    # 流式 POST 只记录不重试（重试需整流重发，M2 口径）；按已重试口径裁决
    post = context.evaluate(Stage.POST, context.state_json(content))
    verdict = context.engine.handle_post(content, post, retried=True)
    context.log_answer(content, verdict.value, 0)
    yield _sse_finish(context.chat_id, created)
    yield "data: [DONE]\n\n"
