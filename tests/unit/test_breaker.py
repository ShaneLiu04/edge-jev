"""T010 滑动窗口熔断器（design.md §4.2 流程图 B，srs FR6；UT 纯状态机）。

纯逻辑组件：对小脑后端的熔断状态机，时间经 clock 注入（依赖注入，非 mock 后端）。
"""

import time

import pytest

from jev.kernel.cascade.breaker import Breaker, BreakerState


class ControllableClock:
    """可控单调时钟：测试内推进 now 即可穿越冷却/窗口。"""

    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> ControllableClock:
    return ControllableClock()


@pytest.fixture
def breaker(clock: ControllableClock) -> Breaker:
    return Breaker(clock=clock)


def test_initial_state_closed_and_allows(breaker: Breaker) -> None:
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True


def test_two_failures_still_closed(breaker: Breaker) -> None:
    breaker.record(False)
    breaker.record(False)
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True


def test_three_failures_in_window_opens(breaker: Breaker) -> None:
    for _ in range(3):
        breaker.record(False)
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False


def test_transition_to_open_under_100ms(clock: ControllableClock) -> None:
    br = Breaker(clock=clock)
    br.record(False)
    br.record(False)
    start = time.perf_counter()
    br.record(False)  # 第三次失败触发 OPEN，切换动作须 <100ms
    elapsed = time.perf_counter() - start
    assert br.state is BreakerState.OPEN
    assert elapsed < 0.1


def test_open_blocks_until_cooldown_elapsed(breaker: Breaker, clock: ControllableClock) -> None:
    for _ in range(3):
        breaker.record(False)
    assert breaker.allow() is False  # 冷却 30s 未到
    clock.advance(30.0)
    assert breaker.allow() is True  # 冷却到期放行探测
    assert breaker.state is BreakerState.HALF_OPEN


def test_half_open_probe_success_closes_and_resets_window(
    breaker: Breaker, clock: ControllableClock
) -> None:
    for _ in range(3):
        breaker.record(False)
    clock.advance(30.0)
    assert breaker.allow() is True
    breaker.record(True)  # 探测成功
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True
    breaker.record(False)  # 窗口已清零，2 次失败不触发
    breaker.record(False)
    assert breaker.state is BreakerState.CLOSED


def test_half_open_probe_failure_doubles_cooldown(
    breaker: Breaker, clock: ControllableClock
) -> None:
    for _ in range(3):
        breaker.record(False)
    clock.advance(30.0)
    assert breaker.allow() is True  # HALF_OPEN 探测
    breaker.record(False)  # 探测失败 → 冷却翻倍 60s
    assert breaker.state is BreakerState.OPEN
    clock.advance(30.0)
    assert breaker.allow() is False  # 新冷却 60s 未到
    clock.advance(30.0)  # 累计 60s
    assert breaker.allow() is True
    assert breaker.state is BreakerState.HALF_OPEN


def test_cooldown_capped_at_120s(breaker: Breaker, clock: ControllableClock) -> None:
    for _ in range(3):
        breaker.record(False)  # OPEN，冷却 30s
    clock.advance(30.0)
    assert breaker.allow() is True  # HALF_OPEN
    breaker.record(False)  # 探测失败 #1 → 冷却 60s
    clock.advance(60.0)
    assert breaker.allow() is True  # HALF_OPEN
    breaker.record(False)  # 探测失败 #2 → 冷却 120s
    clock.advance(120.0)
    assert breaker.allow() is True  # HALF_OPEN
    breaker.record(False)  # 探测失败 #3 → 冷却仍 120（上限）
    clock.advance(119.0)
    assert breaker.allow() is False
    clock.advance(1.0)  # 累计 120s
    assert breaker.allow() is True


def test_window_slides_old_failures_expire(breaker: Breaker, clock: ControllableClock) -> None:
    breaker.record(False)  # t=0 失败 1 次
    clock.advance(61.0)  # 该失败滑出 60s 窗口
    breaker.record(False)
    breaker.record(False)
    assert breaker.state is BreakerState.CLOSED  # 窗口内仅 2 次失败
    assert breaker.allow() is True


def test_success_neither_triggers_nor_clears_window(
    breaker: Breaker, clock: ControllableClock
) -> None:
    breaker.record(False)
    breaker.record(False)
    breaker.record(True)  # 成功不清窗、不触发
    breaker.record(False)  # 窗口内共 3 失败
    assert breaker.state is BreakerState.OPEN


def test_clock_error_fails_open() -> None:
    def broken_clock() -> float:
        raise RuntimeError("clock unavailable")

    br = Breaker(clock=broken_clock)
    assert br.allow() is True  # 熔断器自身异常 → 安全侧放行
    assert br.state is BreakerState.CLOSED


def test_record_ignored_while_open(breaker: Breaker, clock: ControllableClock) -> None:
    for _ in range(3):
        breaker.record(False)
    breaker.record(True)  # OPEN 下忽略，不抛
    breaker.record(False)
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False


def test_breaker_state_wire_values() -> None:
    assert BreakerState.CLOSED.value == "closed"
    assert BreakerState.OPEN.value == "open"
    assert BreakerState.HALF_OPEN.value == "half_open"
