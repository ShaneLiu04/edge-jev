from __future__ import annotations

from pathlib import Path

from jev.kernel.cerebrum.scheduling import (
    ScheduledClient,
    SlotScheduler,
    SlotSchedulerConfig,
)
from jev.kernel.cerebrum.snapshot import SnapshotClient, SnapshotConfig
from jev.server.app import _wrap_with_scheduler


class _Inner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def base_url(self) -> str:
        return "http://inner"

    def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def chat_stream(self, messages, **kwargs):
        yield {"choices": [{"delta": {"content": "ok"}}]}

    def close(self) -> None:
        pass


def test_scheduler_wraps_snapshot_client(tmp_path: Path) -> None:
    inner = _Inner()
    snap = SnapshotClient(
        inner,
        SnapshotConfig(enabled=True, directory=tmp_path, min_chars=200),
        "ns",
    )
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2))
    wrapped = _wrap_with_scheduler(snap, scheduler)
    assert isinstance(wrapped, ScheduledClient)
    wrapped.chat(
        [{"role": "user", "content": "d" * 1500 + "\n问题：q"}],
        max_tokens=8,
        temperature=0.0,
    )
    assert inner.calls[0]["extra_body"] == {"id_slot": 0}


def test_disabled_scheduler_keeps_client() -> None:
    inner = _Inner()
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=False, slots=1))
    assert _wrap_with_scheduler(inner, scheduler) is inner
