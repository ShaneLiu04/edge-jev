"""决策台账异步落盘（design.md §4.4 ledger/writer.py，srs FR8）。

主路径仅 queue.put_nowait（<1ms 口径）；写线程单消费者追加 JSONL。
磁盘失败丢弃该条并累计告警计数（连续 warn_after_failures 条输出 ERROR，
不阻塞主路径——设计 §4.2 异常流程 5）。
"""

import json
import logging
import queue
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class LedgerEntry:
    """台账条目：decision（决策点）/ route（路由去向）/ answer（最终交付）。

    三类共享 request_id 聚合键；字段按 kind 取用，序列化时省略 None。
    """

    __slots__ = (
        "request_id", "kind", "ts",
        "point_id", "stage", "mode", "question", "question_type",
        "value", "confidence", "probabilities", "applied", "from_fallback",
        "breaker_state", "latency_ms", "backend_fallback",
        "route", "state", "posterior",
        "content", "post_verdict", "tokens",
        "runtime",
    )

    def __init__(
        self,
        *,
        request_id: str,
        kind: str,
        ts: float,
        point_id: str | None = None,
        stage: str | None = None,
        mode: str | None = None,
        question: str | None = None,
        question_type: str | None = None,
        value: str | float | None = None,
        confidence: float | None = None,
        probabilities: dict | None = None,
        applied: str | float | None = None,
        from_fallback: bool = False,
        breaker_state: str | None = None,
        latency_ms: float | None = None,
        backend_fallback: bool | None = None,
        route: str | None = None,
        state: str | None = None,
        posterior: dict | None = None,
        content: str | None = None,
        post_verdict: str | None = None,
        tokens: int | None = None,
        runtime: dict | None = None,
    ) -> None:
        self.request_id = request_id
        self.kind = kind
        self.ts = ts
        self.point_id = point_id
        self.stage = stage
        self.mode = mode
        self.question = question
        self.question_type = question_type
        self.value = value
        self.confidence = confidence
        self.probabilities = probabilities
        self.applied = applied
        self.from_fallback = from_fallback
        self.breaker_state = breaker_state
        self.latency_ms = latency_ms
        self.backend_fallback = backend_fallback
        self.route = route
        self.state = state
        self.posterior = posterior
        self.content = content
        self.post_verdict = post_verdict
        self.tokens = tokens
        self.runtime = runtime

    def to_dict(self) -> dict:
        """JSONL 行载荷：None 字段省略（行保持紧凑）。"""
        return {k: getattr(self, k) for k in self.__slots__ if getattr(self, k) is not None}

    @classmethod
    def from_dict(cls, payload: dict) -> "LedgerEntry":
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in payload.items() if k in known})


class LedgerWriter:
    """异步台账写入器：构造即起写线程；close() 排空队列并收线程。"""

    def __init__(
        self,
        *,
        dir: Path | str,
        filename: str = "jev-ledger.jsonl",
        queue_maxsize: int = 10000,
        warn_after_failures: int = 100,
    ) -> None:
        self._dir = Path(dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / filename
        self._queue: queue.Queue[LedgerEntry | None] = queue.Queue(maxsize=queue_maxsize)
        self._warn_after = int(warn_after_failures)
        self._dropped_lock = threading.Lock()
        self.written = 0
        self.dropped = 0
        self._closed = False
        self._thread = threading.Thread(target=self._write_loop, name="jev-ledger", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # 主路径（<1ms 口径）
    # ------------------------------------------------------------------

    def log(self, entry: LedgerEntry) -> None:
        """非阻塞投递；队列满时丢弃并计数（主路径永不阻塞/抛错）。"""
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            self._count_drop()
            logger.warning("台账队列已满，丢弃 entry（request_id=%s）", entry.request_id)

    def flush(self, timeout_s: float = 10.0) -> None:
        """等待队列排空（写线程消化完已投递条目；timeout_s 兼容参数）。"""
        self._queue.join()

    def close(self) -> None:
        """排空队列并停止写线程；幂等。"""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)  # 哨兵：写线程消化完存量后退出
        self._thread.join(timeout=10.0)

    def __enter__(self) -> "LedgerWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 写线程
    # ------------------------------------------------------------------

    def _write_loop(self) -> None:
        consecutive_failures = 0
        warned = False
        while True:
            entry = self._queue.get()
            try:
                if entry is None:
                    return
                try:
                    with self._path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
                        fh.flush()
                except OSError as exc:
                    self._count_drop()
                    consecutive_failures += 1
                    if consecutive_failures >= self._warn_after and not warned:
                        logger.error(
                            "台账连续 %d 条写入失败（最近错误: %s），"
                            "条目被丢弃不阻塞主路径；请检查磁盘/目录权限: %s",
                            consecutive_failures, exc, self._path,
                        )
                        warned = True
                else:
                    with self._dropped_lock:
                        self.written += 1
                    consecutive_failures = 0
                    warned = False
            finally:
                self._queue.task_done()

    def _count_drop(self) -> None:
        with self._dropped_lock:
            self.dropped += 1
