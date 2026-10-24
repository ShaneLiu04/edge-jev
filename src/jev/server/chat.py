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

from jev.errors import JevError, JevProtocolError, JevUnavailableError
from jev.kernel.cascade.engine import CascadeEngine, MidAction, PostVerdict, Route
from jev.kernel.cerebellum.base import (
    Answer,
    CerebellumBackend,
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
)
from jev.kernel.cerebrum.llama_server import LlamaServerClient
from jev.kernel.decisions.fallback import fallback_point
from jev.kernel.decisions.framework import (
    Decision,
    DecisionPoint,
    DecisionRegistry,
    Stage,
    to_question,
)
from jev.kernel.ledger.writer import LedgerEntry, LedgerWriter

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
) -> tuple[list[Decision], dict[str, Answer], float]:
    """同 stage active 点合并为单次小脑批量前向；小脑缺失/异常逐点安全侧回退。

    返回 (决策列表, 逐点原始答案, 批量前向耗时 ms)。bench（T021）与级联
    共用同一评估口径（公共契约）。
    """
    if not points:
        return [], {}, 0.0
    if cerebellum is None:
        return [fallback_point(point, "小脑后端未配置") for point in points], {}, 0.0

    questions = {point.id: to_question(point) for point in points}
    started = time.perf_counter()
    try:
        response = cerebellum.system_one(state, questions)
    except JevError as exc:
        logger.warning(
            "小脑评估失败，%d 个 %s 点安全侧回退: %s",
            len(points), points[0].stage.value, exc,
        )
        return (
            [fallback_point(point, f"小脑不可用: {exc}") for point in points],
            {},
            (time.perf_counter() - started) * 1000,
        )
    latency_ms = (time.perf_counter() - started) * 1000

    decisions: list[Decision] = []
    answers: dict[str, Answer] = {}
    for point in points:
        answer = response.answers.get(point.id)
        if answer is None:
            decisions.append(fallback_point(point, "小脑响应缺失该点答案"))
            continue
        answers[point.id] = answer
        decisions.append(_decision_from_answer(point, answer))
    return decisions, answers, latency_ms


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
        decisions, answers, latency_ms = evaluate_decisions(self.cerebellum, points, state)
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

    def route_of(self, pre: list[Decision]) -> str:
        if self.route_policy is not None:
            return self.route_policy(pre)
        return self.engine.route_chat(pre).wire

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
                )
            context = _CascadeContext(
                cerebellum, brain, registry, engine, route_policy, ledger,
                uuid.uuid4().hex, messages, temperature, max_tokens, stop,
                generation_timeout_s=generation_timeout_s,
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
) -> JSONResponse:
    active_pre = registry.active(Stage.PRE) if registry is not None else []
    state = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    decisions, _answers, _latency = evaluate_decisions(cerebellum, active_pre, state)
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
    pre = context.evaluate(Stage.PRE, context.state_json())
    route = context.route_of(pre)
    context.log_route(route, pre)

    if route == "direct":
        content = render_direct_answer(pre, context.registry)
        context.log_answer(content, None, 0)
        empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return _json_response(
            context, content, empty_usage, degraded=False, route=route
        )

    if context.brain is None:
        return _error(
            503, "大脑后端未配置", type_="server_error", code="brain_unavailable",
            include_param=False, retry_after=_RETRY_AFTER_S,
        )
    timed_out = False
    try:
        if route == "light":
            content, usage, timed_out = _generate_light(context)
        else:
            content, usage = _generate_full(context)
        verdict, degraded, content, usage = _post_review(
            context, content, usage, allow_retry=not timed_out
        )
        if timed_out:
            # EX-04：生成超时截断——POST correct_format 复核后降级交付
            degraded = True
    except (JevUnavailableError, JevProtocolError) as exc:
        if context.timed_out():
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
    context.log_answer(content, verdict.value, usage["completion_tokens"])
    return _json_response(context, content, usage, degraded=degraded, route=route)


def _generate_full(context: _CascadeContext) -> tuple[str, dict[str, int]]:
    response = context.brain.chat(
        context.messages,
        max_tokens=context.max_tokens,
        temperature=context.temperature,
        stop=context.stop,
    )
    return _content_of(response), _usage_of(response)


def _generate_light(
    context: _CascadeContext,
) -> tuple[str, dict[str, int], bool]:
    """路径②：按 LIGHT_SEGMENT_TOKENS 分段生成，段间 MID 批早停。

    EX-04：段间检查生成时限，超时以已生成部分截断返回（timed_out=True）。
    """
    segments: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    remaining = context.max_tokens
    timed_out = False
    while remaining > 0:
        if context.timed_out():
            timed_out = True
            break
        prefix = "".join(segments)
        segment_messages = (
            context.messages + [{"role": "assistant", "content": prefix}]
            if prefix
            else context.messages
        )
        try:
            response = context.brain.chat(
                segment_messages,
                max_tokens=min(LIGHT_SEGMENT_TOKENS, remaining),
                temperature=context.temperature,
                stop=context.stop,
            )
        except (JevUnavailableError, JevProtocolError) as exc:
            if context.timed_out():
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
        max_tokens=context.max_tokens,
        temperature=context.temperature,
        stop=context.stop,
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
        headers={
            "X-Jev-Route": route,
            "X-Jev-Decisions": str(context.decision_count),
        },
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
    headers = {
        "X-Jev-Route": route,
        # 头部在流开始前固化，故流式决策数=PRE 批（MID/POST 见台账）
        "X-Jev-Decisions": str(context.decision_count),
    }
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
            max_tokens=context.max_tokens,
            temperature=context.temperature,
            stop=context.stop,
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
