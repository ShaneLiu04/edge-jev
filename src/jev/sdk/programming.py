"""决策点编程注册（design.md §4.3.3，FR10：与 TOML 声明等价）。

SDK 嵌入进程内 Kernel 时的运行态扩展入口——注册后立即参与级联（三态
语义与配置文件注册完全一致）。
"""

from jev.kernel.decisions.framework import DecisionPoint
from jev.kernel.kernel import Kernel


def register_point(kernel: Kernel, point: DecisionPoint) -> None:
    """向进程内 Kernel 注册自定义决策点（FR10 编程 API）。"""
    kernel.register_point(point)


__all__ = ["register_point"]
