# [AR019] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR019 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-31 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T1901 | 快照配置/校验 + gitignore | - | passing | 默认关闭；parallel=1 校验 |
| T1902 | Manager `--slot-save-path` + Client save/restore API | T1901 | passing | 真实 llama-server |
| T1903 | SnapshotClient 文档键/异步保存/LRU/损坏兜底 | T1902 | passing | 真实 HTTP |
| T1904 | Kernel/serve/matrix 接线与观测头 | T1903 | passing | `X-Jev-KV-Snapshot` |
| T1905 | `scripts/bench_ppks.py` 冷重启真实实验 | T1904 | passing | 6.13s→0.88s |
| T1906 | 全量测试/真实集成/文档/推送/浅克隆 | T1905 | passing | 741 unit + 1 real |

## 进度记录

- T1901~T1904：新增 `snapshot.py`、slot API、内核/服务/矩阵接线；
- T1905：真实冷重启 baseline 6131.7ms → PPKS 875.2ms（−85.7%），quality 一致；
- T1906：`uv run pytest tests/unit` 741 passed，ruff 零告警；真实集成 1 passed。

## 阶段门控记录

（自动化 harnessX：req/design 门控按用户授权跳过，仅记录本行）
