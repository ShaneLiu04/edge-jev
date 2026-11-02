from __future__ import annotations

import pytest

from jev.kernel.cerebrum.scheduling import (
    ScheduledClient,
    SlotScheduler,
    SlotSchedulerConfig,
)


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
        self.calls.append(kwargs)
        yield {"choices": [{"delta": {"content": "ok"}}]}

    def close(self) -> None:
        pass


def _messages(document: str, question: str = "q") -> list[dict[str, str]]:
    return [{"role": "user", "content": f"{document}\n问题：{question}"}]


def test_disabled_scheduler_returns_none() -> None:
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=False, slots=4))
    assert not scheduler.enabled
    assert scheduler.slot_for(_messages("doc")) is None


def test_document_round_robin() -> None:
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=3))
    assert [scheduler.slot_for(_messages("doc-a")) for _ in range(4)] == [0, 1, 2, 0]
    assert [scheduler.slot_for(_messages("doc-b")) for _ in range(3)] == [0, 1, 2]


def test_question_change_keeps_document_lane() -> None:
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2))
    assert scheduler.slot_for(_messages("same-doc", "q1")) == 0
    assert scheduler.slot_for(_messages("same-doc", "q2")) == 1


def test_scheduled_client_injects_id_slot() -> None:
    inner = _Inner()
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2))
    client = ScheduledClient(inner, scheduler)
    client.chat(_messages("doc-a"), extra_body={"cache_prompt": True})
    client.chat(_messages("doc-a"), extra_body={"cache_prompt": True})
    assert inner.calls[0]["extra_body"] == {"cache_prompt": True, "id_slot": 0}
    assert inner.calls[1]["extra_body"] == {"cache_prompt": True, "id_slot": 1}


def test_scheduled_client_stream_injects_id_slot() -> None:
    inner = _Inner()
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2))
    client = ScheduledClient(inner, scheduler)
    list(client.chat_stream(_messages("doc-a")))
    assert inner.calls[0]["extra_body"] == {"id_slot": 0}


def test_kernel_builder_off_and_parallel_guard() -> None:
    from jev.errors import JevProtocolError
    from jev.kernel.kernel import _build_slot_scheduler

    assert not _build_slot_scheduler({}).enabled
    with pytest.raises(JevProtocolError, match="parallel"):
        _build_slot_scheduler(
            {"cerebrum": {"slot_scheduler_enabled": True, "parallel": 1}}
        )
    scheduler = _build_slot_scheduler(
        {"cerebrum": {"slot_scheduler_enabled": True, "parallel": 2}}
    )
    assert scheduler.enabled


def test_disabled_client_passthrough_keeps_extra_body() -> None:
    inner = _Inner()
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=False, slots=4))
    client = ScheduledClient(inner, scheduler)
    body = {"cache_prompt": True}
    client.chat(_messages("doc-a"), extra_body=body)
    assert inner.calls[0]["extra_body"] is body


def test_scheduled_client_does_not_mutate_caller_extra_body() -> None:
    inner = _Inner()
    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=2))
    client = ScheduledClient(inner, scheduler)
    body = {"cache_prompt": True}
    client.chat(_messages("doc-a"), extra_body=body)
    assert body == {"cache_prompt": True}


def test_counter_eviction_resets_old_documents() -> None:
    scheduler = SlotScheduler(
        SlotSchedulerConfig(enabled=True, slots=2, max_documents=1)
    )
    assert scheduler.slot_for(_messages("doc-a")) == 0
    assert scheduler.slot_for(_messages("doc-b")) == 0
    assert scheduler.slot_for(_messages("doc-a")) == 0


def test_counter_eviction_is_lru() -> None:
    scheduler = SlotScheduler(
        SlotSchedulerConfig(enabled=True, slots=2, max_documents=2)
    )
    assert scheduler.slot_for(_messages("doc-a")) == 0
    assert scheduler.slot_for(_messages("doc-b")) == 0
    assert scheduler.slot_for(_messages("doc-a")) == 1
    assert scheduler.slot_for(_messages("doc-c")) == 0
    assert scheduler.slot_for(_messages("doc-b")) == 0


def test_concurrent_requests_round_robin_without_duplicates() -> None:
    import threading

    scheduler = SlotScheduler(SlotSchedulerConfig(enabled=True, slots=4))
    slots: list[int] = []
    lock = threading.Lock()

    def run() -> None:
        slot = scheduler.slot_for(_messages("doc-a"))
        with lock:
            slots.append(slot)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(slots) == [0, 0, 1, 1, 2, 2, 3, 3]


def test_matrix_rejects_snapshot_with_parallel_or_scheduler(tmp_path) -> None:
    from jev.bench.matrix import MatrixConfig, _validate_snapshot_scheduler
    from jev.errors import JevProtocolError
    from jev.kernel.cerebrum.snapshot import SnapshotConfig

    snapshot = SnapshotConfig(enabled=True, directory=tmp_path)
    base = {
        "name": "t",
        "seed": 1,
        "lambda_cost": 0.0,
        "limit": 0,
        "arms": (),
        "brain": "embedded",
        "brain_base_url": "",
        "embedding": "off",
        "embedding_base_url": "",
        "gpu_layers": 0,
        "ctx_size": 4096,
        "max_tokens": 32,
        "timeout_s": 10.0,
        "prompt_prefix": "",
        "repeat": 1,
        "dataset": "shared",
        "overlays": {},
    }
    _validate_snapshot_scheduler(MatrixConfig(**base), snapshot)
    with pytest.raises(JevProtocolError, match="parallel"):
        _validate_snapshot_scheduler(MatrixConfig(**base, parallel=2), snapshot)
    with pytest.raises(JevProtocolError, match="slot_scheduler"):
        _validate_snapshot_scheduler(
            MatrixConfig(**base, parallel=1, slot_scheduler_enabled=True), snapshot
        )


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        SlotSchedulerConfig(enabled=True, slots=0)
    with pytest.raises(ValueError):
        SlotSchedulerConfig(enabled=True, slots=1)
    with pytest.raises(ValueError):
        SlotSchedulerConfig(enabled=True, slots=2, max_documents=0)

