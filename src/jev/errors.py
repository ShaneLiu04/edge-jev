"""Jev 类型化错误族（design.md §4.3.3 异常契约）。

薄壳与内部接口统一抛本族异常；具体异常不吞错、错误信息可定位但不含敏感信息。
"""


class JevError(Exception):
    """Jev 错误基类。"""


class JevAssetError(JevError):
    """资产缺失/manifest 非法/下载失败/校验失败。"""


class JevProtocolError(JevError):
    """请求/响应不符合线协议（如非法 questions 结构）。"""


class JevOverflowError(JevProtocolError):
    """输入超长且截断策略为 refuse（端点映射 HTTP 422）。"""


class JevUnavailableError(JevError):
    """后端不可达/超时（连接拒绝、5xx、超时）。"""


class JevTimeoutError(JevUnavailableError):
    """大脑调用超时（AR002 FR2-4：按异常类型而非时钟判定截断降级）。"""


class BrainStartupError(JevError):
    """llama-server 启动失败（携带诊断信息）。"""
