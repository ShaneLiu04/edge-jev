"""JevClient——SDK HTTP 客户端（httpx 同步+异步，design.md §4.3.3，FR10）。

三型便捷方法（decide/judge/rate）与 system_one 走 /v1/systemone（TypeSafe
线协议，codec 复用 wire.py）；chat 走 /v1/chat/completions（OpenAI 兼容）。
异常契约：连接失败/超时 → JevUnavailableError；非 2xx/响应不合约 →
JevProtocolError（不吞异常，向上透传）。
"""

import httpx

from jev.errors import JevProtocolError, JevUnavailableError
from jev.kernel.cerebellum.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
)
from jev.kernel.cerebellum.wire import decode_response, encode_request
from jev.sdk.results import ChatResult, ChoiceResult, NoulResult, ScoreResult


class JevClient:
    """jev serve 端点的进程外客户端（同一实例可同步/异步混用）。"""

    def __init__(self, base_url: str, *, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._sync = httpx.Client(timeout=timeout_s)
        self._async = httpx.AsyncClient(timeout=timeout_s)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._sync.close()

    async def aclose(self) -> None:
        await self._async.aclose()

    def __enter__(self) -> "JevClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    async def __aenter__(self) -> "JevClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # 三型便捷方法（单问封装，返回类型化结果对象）
    # ------------------------------------------------------------------

    def decide(
        self,
        state: str,
        choices: dict[str, str | None],
        instructions: str,
    ) -> ChoiceResult:
        return _choice_result(self.system_one(
            state, {"q": ChoiceQuestion(choices=choices, instructions=instructions)}
        ))

    def judge(self, state: str, instructions: str, criteria: dict | None = None) -> NoulResult:
        return _noul_result(self.system_one(
            state, {"q": NoulQuestion(instructions=instructions, criteria=criteria)}
        ))

    def rate(self, state: str, criteria: list[str], instructions: str) -> ScoreResult:
        return _score_result(self.system_one(
            state, {"q": ScoreQuestion(criteria=criteria, instructions=instructions)}
        ))

    async def adecide(
        self, state: str, choices: dict[str, str | None], instructions: str
    ) -> ChoiceResult:
        return _choice_result(await self.asystem_one(
            state, {"q": ChoiceQuestion(choices=choices, instructions=instructions)}
        ))

    async def ajudge(
        self, state: str, instructions: str, criteria: dict | None = None
    ) -> NoulResult:
        return _noul_result(await self.asystem_one(
            state, {"q": NoulQuestion(instructions=instructions, criteria=criteria)}
        ))

    async def arate(
        self, state: str, criteria: list[str], instructions: str
    ) -> ScoreResult:
        return _score_result(await self.asystem_one(
            state, {"q": ScoreQuestion(criteria=criteria, instructions=instructions)}
        ))

    # ------------------------------------------------------------------
    # system_one / chat
    # ------------------------------------------------------------------

    def system_one(self, state: str, questions: dict[str, Question]) -> SystemOneResponse:
        payload = encode_request("jev", state, questions)
        data, _ = self._request("/v1/systemone", payload)
        return decode_response(data)

    async def asystem_one(
        self, state: str, questions: dict[str, Question]
    ) -> SystemOneResponse:
        payload = encode_request("jev", state, questions)
        data, _ = await self._arequest("/v1/systemone", payload)
        return decode_response(data)

    def chat(self, messages: list[dict], **kwargs) -> ChatResult:
        payload: dict = {"messages": messages}
        payload.update(kwargs)
        data, headers = self._request("/v1/chat/completions", payload)
        return _chat_result(headers, data)

    async def achat(self, messages: list[dict], **kwargs) -> ChatResult:
        payload: dict = {"messages": messages}
        payload.update(kwargs)
        data, headers = await self._arequest("/v1/chat/completions", payload)
        return _chat_result(headers, data)

    # ------------------------------------------------------------------
    # 内部：真实 HTTP（同步/异步）+ 统一错误映射
    # ------------------------------------------------------------------

    def _request(self, path: str, payload: dict) -> tuple[dict, dict]:
        """返回 (响应 JSON, 响应头)——chat 级联元信息在头部。"""
        try:
            response = self._sync.post(f"{self._base_url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise JevUnavailableError(
                f"Jev 服务不可达（{self._base_url}{path}）: {exc}"
            ) from exc
        return _check(response), dict(response.headers)

    async def _arequest(self, path: str, payload: dict) -> tuple[dict, dict]:
        """返回 (响应 JSON, 响应头)——chat 级联元信息在头部。"""
        try:
            response = await self._async.post(f"{self._base_url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise JevUnavailableError(
                f"Jev 服务不可达（{self._base_url}{path}）: {exc}"
            ) from exc
        return _check(response), dict(response.headers)


def _check(response: httpx.Response) -> dict:
    """2xx → JSON dict；503 → JevUnavailableError；其余 → JevProtocolError。"""
    if not 200 <= response.status_code < 300:
        message = response.text[:200]
        try:
            body = response.json()
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict) and error.get("message"):
                    message = str(error["message"])
        except ValueError:
            pass
        if response.status_code == 503:
            raise JevUnavailableError(f"Jev 服务不可用: {message}")
        raise JevProtocolError(
            f"Jev 服务错误 HTTP {response.status_code}: {message}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise JevProtocolError(
            f"Jev 响应不是合法 JSON: {response.text[:200]}"
        ) from exc
    if not isinstance(data, dict):
        raise JevProtocolError(f"Jev 响应须为 JSON 对象: {str(data)[:200]}")
    return data


def _choice_result(response: SystemOneResponse) -> ChoiceResult:
    answer = response.answers["q"]
    if not isinstance(answer, ChoiceAnswer):
        raise JevProtocolError(f"decide 期望 choice 答案，实际: {type(answer).__name__}")
    return ChoiceResult(
        choice=answer.choice,
        confidence=answer.confidence,
        probabilities=answer.probabilities,
    )


def _noul_result(response: SystemOneResponse) -> NoulResult:
    answer = response.answers["q"]
    if not isinstance(answer, NoulAnswer):
        raise JevProtocolError(f"judge 期望 noul 答案，实际: {type(answer).__name__}")
    return NoulResult(noul=answer.noul, confidence=answer.confidence)


def _score_result(response: SystemOneResponse) -> ScoreResult:
    answer = response.answers["q"]
    if not isinstance(answer, ScoreAnswer):
        raise JevProtocolError(f"rate 期望 score 答案，实际: {type(answer).__name__}")
    return ScoreResult(
        score=answer.score,
        confidence=answer.confidence,
        legend=answer.legend,
        mode_level=answer.mode_level,
        probabilities=answer.probabilities,
    )


def _chat_result(headers: dict, data: dict) -> ChatResult:
    try:
        message = data["choices"][0]["message"]
        content = message["content"]
        finish_reason = data["choices"][0]["finish_reason"]
        usage = data.get("usage") or {}
    except (KeyError, TypeError, IndexError) as exc:
        raise JevProtocolError(
            f"chat 响应缺少必需字段（choices[0].message.content 等）: {str(data)[:200]}"
        ) from exc
    route = headers.get("x-jev-route") or headers.get("X-Jev-Route", "")
    decisions_raw = headers.get("x-jev-decisions") or headers.get("X-Jev-Decisions", "0")
    return ChatResult(
        id=str(data.get("id", "")),
        content=content,
        finish_reason=str(finish_reason),
        route=route,
        decisions=int(decisions_raw),
        degraded=bool(data.get("jev_degraded", False)),
        usage={k: int(v) for k, v in usage.items() if isinstance(v, int)},
    )
