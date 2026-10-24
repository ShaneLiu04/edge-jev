"""kev-0.8B 小脑后端：经 HTTP 连接本地 kev.serve 子进程（/v1/systemone 兼容）。

与 Laya ONNX 同 CerebellumBackend 契约；不可用时由上层（级联引擎/熔断器）
回退默认 Laya 后端。真实 kev 推理验证属 assets 集成测试。
"""

import logging

import httpx

from jev.errors import JevProtocolError, JevUnavailableError
from jev.kernel.cerebellum.base import (
    CerebellumBackend,
    Question,
    SystemOneResponse,
)
from jev.kernel.cerebellum.wire import decode_response, encode_request

logger = logging.getLogger(__name__)


class KevHttpBackend(CerebellumBackend):
    def __init__(
        self,
        serve_url: str,
        model: str = "kev-0.8b",
        timeout_s: float = 5.0,
    ) -> None:
        self.serve_url = serve_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout_s)

    def name(self) -> str:
        return "kev"

    def warmup(self) -> None:
        # kev.serve 无 /health 路由（TypeSafe 面只有 /v1/systemone 与
        # /v1/models，ST-005 真实对端发现）；以 /v1/models 作轻量就绪探测
        try:
            response = self._client.get(f"{self.serve_url}/v1/models")
        except httpx.HTTPError as exc:
            raise JevUnavailableError(f"kev 后端不可达（{self.serve_url}）: {exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise JevUnavailableError(
                f"kev 后端健康检查失败（{self.serve_url}）: HTTP {response.status_code}"
            )

    def system_one(self, state: str, questions: dict[str, Question]) -> SystemOneResponse:
        payload = encode_request(self._model, state, questions)
        try:
            response = self._client.post(f"{self.serve_url}/v1/systemone", json=payload)
        except httpx.HTTPError as exc:
            raise JevUnavailableError(f"kev 后端不可达（{self.serve_url}）: {exc}") from exc

        if response.status_code >= 500:
            raise JevUnavailableError(
                f"kev 后端错误（{self.serve_url}）: HTTP {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise JevProtocolError(
                f"kev 后端拒绝请求（{self.serve_url}）: HTTP {response.status_code}"
            )
        return decode_response(response.json())

    def close(self) -> None:
        self._client.close()
