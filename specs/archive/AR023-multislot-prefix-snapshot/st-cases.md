# [AR023] 系统测试用例

| 字段 | 内容 |
|------|------|
| AR | AR023 MSPP 多槽持久化前缀 |
| 日期 | 2026-11-05 |
| 环境 | Windows 10 / RTX 5000 16GB / Xeon Gold 6234 |

## 用例

| ID | 场景 | 步骤 | 期望 | 结果 |
|----|------|------|------|------|
| ST2301 | 单槽兼容 | parallel=1 + snapshot，重启后同文档请求 | 恢复 1 次，行为与 AR019 一致 | PASS（1.62x 冷启动） |
| ST2302 | 多槽恢复 | parallel=2 + scheduler，重启后两次请求 | 两个槽各 restore 一次 | PASS（真实集成） |
| ST2303 | 四槽并发冷启动 | parallel=4 + scheduler + MSPP，12 题并发 4 | restore=4，quality 12/12 | PASS |
| ST2304 | 冷启动性能 | 对比 parallel4 auto 与 MSPP | MSPP 冷启动显著更快 | PASS（20.96s→3.12s，6.7x） |
| ST2305 | 稳态非回归 | 第二轮稳态 P50 | 与 auto/scheduler 同轮噪声内 | PASS（0.90–1.05s） |
| ST2306 | 忙槽安全 | 不同文档同槽并发，首请求在途 | 第二个请求跳过 restore，不污染 KV | PASS（单测） |
| ST2307 | 保存/恢复互斥 | 异步 save 期间新请求 | 等待 save 完成，不读半写快照 | PASS（LRU 重存单测） |
| ST2308 | 快照异常 | restore 失败 / 文件缺失 | 回退正常 prefill，无 5xx | PASS |
| ST2309 | 配置兼容 | kernel/matrix 同时启用 snapshot+scheduler | 不 fail-fast，进入多槽模式 | PASS |
| ST2310 | serve 装配 | SnapshotClient + scheduler | `ScheduledClient(SnapshotClient)` 顺序正确 | PASS |

## 证据

- 单测：`uv run pytest tests/unit -q` → **789 passed**；
- 真实：`tests/integration/test_multislot_snapshot_real.py` → 1 passed（两槽 restore）；
- 真实 GPU：`benchmarks/results/ar023-mspp-20261106-013505`、`...-013110`；
- 复现：`uv run python scripts/bench_multislot_snapshot.py`；
- Lint：`uv run ruff check .` → 零告警。
