"""小脑熔断器状态机（design.md §4.2 流程图 B，srs FR6）。

CLOSED（60s 滑动窗口失败≥3）→ OPEN（冷却 30s，规则 fallback 兜底）→
HALF_OPEN（到期放行探测）→ 成功闭合 / 失败重回 OPEN（冷却翻倍上限 120s）。
切换为内存标志翻转（<100ms）。熔断器自身异常一律放行真实后端（安全侧）。
"""

import logging
import time
from collections.abc import Callable
from enum import Enum

logger = logging.getLogger(__name__)


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Breaker:
    """滑动窗口熔断器（时间经 clock 注入，默认单调时钟）。"""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        window_s: float = 60.0,
        cooldown_s: float = 30.0,
        cooldown_max_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._window_s = window_s
        self._cooldown_base = cooldown_s
        self._cooldown_max = cooldown_max_s
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._failures: list[float] = []
        self._deadline = 0.0
        self._cooldown = cooldown_s

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow(self) -> bool:
        """是否放行真实后端；熔断器自身异常安全侧放行。"""
        try:
            return self._allow()
        except Exception:
            logger.exception("熔断器内部异常，安全侧放行真实后端")
            return True

    def record(self, ok: bool) -> None:
        """记录一次真实后端调用结果；熔断器自身异常忽略本次记录。"""
        try:
            self._record(ok)
        except Exception:
            logger.exception("熔断器内部异常，忽略本次 record")

    # ------------------------------------------------------------------
    # 内部状态机
    # ------------------------------------------------------------------

    def _allow(self) -> bool:
        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.HALF_OPEN:
            return True
        # OPEN：冷却到期转半开、放行探测请求
        if self._clock() >= self._deadline:
            self._state = BreakerState.HALF_OPEN
            return True
        return False

    def _record(self, ok: bool) -> None:
        if self._state is BreakerState.OPEN:
            # 熔断期间后端不被调用（走规则 fallback），迟到记录忽略
            return
        if self._state is BreakerState.HALF_OPEN:
            if ok:
                # 探测成功：闭合、清窗、冷却基准复位
                self._state = BreakerState.CLOSED
                self._failures.clear()
                self._cooldown = self._cooldown_base
            else:
                self._trip(doubled=True)
            return
        # CLOSED：滑动窗口失败计数
        if ok:
            return
        now = self._clock()
        self._failures.append(now)
        self._failures = [ts for ts in self._failures if now - ts < self._window_s]
        if len(self._failures) >= self._threshold:
            self._trip(doubled=False)

    def _trip(self, *, doubled: bool) -> None:
        if doubled:
            self._cooldown = min(self._cooldown * 2, self._cooldown_max)
        else:
            self._cooldown = self._cooldown_base
        self._state = BreakerState.OPEN
        self._deadline = self._clock() + self._cooldown
        self._failures.clear()
        logger.warning("熔断开启（冷却 %.0fs）", self._cooldown)
