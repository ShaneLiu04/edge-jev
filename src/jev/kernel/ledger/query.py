"""台账检索与导出（design.md §4.4 ledger/query.py，srs FR8）。

按 request_id 聚合一次请求的完整决策链（decision/route/answer 按写入序）；
JSONL 导出按 request_id 过滤。目录内全部 *.jsonl 参与检索（多日/分段兼容）。
"""

import json
from pathlib import Path

from jev.kernel.ledger.writer import LedgerEntry


class LedgerQuery:
    """只读检索器：扫描目录内 JSONL 台账文件。"""

    def __init__(self, *, dir: Path | str) -> None:
        self._dir = Path(dir)

    def query(self, request_id: str) -> list[LedgerEntry]:
        """按 request_id 聚合（文件序 = 写入序）。"""
        return [e for e in self.iter_entries() if e.request_id == request_id]

    def iter_entries(self):
        """遍历目录内全部 *.jsonl 台账条目。"""
        for path in sorted(self._dir.glob("*.jsonl")):
            if not path.is_file():  # 磁盘异常占位（同名目录）等非文件跳过
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        yield LedgerEntry.from_dict(payload)

    def export_jsonl(self, request_id: str, out_path: Path | str) -> int:
        """导出指定 request_id 的全部条目为 JSONL；返回条数。"""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out.open("w", encoding="utf-8") as fh:
            for entry in self.query(request_id):
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
                count += 1
        return count
