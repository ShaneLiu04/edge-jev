"""/v1/systemone 端点（TypeSafe 线协议严格兼容，design.md §4.3.1）。

薄壳：校验/错误映射在此，推理全部委托真实小脑后端；线格式经 wire.py 单源
编解码。错误口径：400 非法结构（字段级定位）、422 refuse 模式超长 state、
503 未就绪/后端不可用（含 Retry-After）。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jev.errors import (
    JevAssetError,
    JevOverflowError,
    JevProtocolError,
    JevUnavailableError,
)
from jev.kernel.cerebellum.base import CerebellumBackend
from jev.kernel.cerebellum.wire import decode_request, encode_response

logger = logging.getLogger(__name__)

_RETRY_AFTER_S = "1"


def _error(status: int, message: str, *, retry_after: str | None = None) -> JSONResponse:
    headers = {"Retry-After": retry_after} if retry_after else None
    return JSONResponse(
        status_code=status, content={"error": {"message": message}}, headers=headers
    )


def create_app(backend: CerebellumBackend | None, model_name: str | None = None) -> FastAPI:
    """构建挂载 /v1/systemone 的 FastAPI 应用；backend=None 表示服务未就绪。"""

    app = FastAPI(title="Jev server", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/systemone")
    async def systemone(request: Request) -> JSONResponse:
        if backend is None:
            return _error(503, "服务未就绪：小脑后端未配置", retry_after=_RETRY_AFTER_S)
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _error(400, "请求体不是合法 JSON")

        try:
            _requested_model, state, questions = decode_request(payload)
            result = backend.system_one(state, questions)
            body = encode_response(result, model_name or backend.name())
        except JevOverflowError as exc:
            # 先于 JevProtocolError 捕获（子类）：refuse 超长专用 422 口径
            return _error(422, str(exc))
        except JevProtocolError as exc:
            return _error(400, str(exc))
        except (JevAssetError, JevUnavailableError) as exc:
            logger.warning("小脑后端不可用: %s", exc)
            return _error(503, str(exc), retry_after=_RETRY_AFTER_S)
        return JSONResponse(status_code=200, content=body)

    return app
