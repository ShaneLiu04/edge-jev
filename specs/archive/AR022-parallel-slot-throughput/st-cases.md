# [AR022] 系统测试用例

| 字段 | 内容 |
|------|------|
| AR | AR022 PSS 并行槽吞吐 |
| 日期 | 2026-11-02 |
| 环境 | Windows 10 / RTX 5000 16GB / Xeon Gold 6234 |

## 用例

| ID | 场景 | 步骤 | 期望 | 结果 |
|----|------|------|------|------|
| ST2201 | 默认关闭不变 | 默认配置启动 serve，提交流式/非流式请求 | 无 `id_slot`，响应与 AR021 一致 | PASS |
| ST2202 | 文档轮转 | parallel=2 + scheduler，连续 4 次同文档 | id_slot 0,1,0,1；不同文档独立计数 | PASS |
| ST2203 | 真实双槽 | parallel=2 真实服务，两次请求后查 `/slots` | 两个槽均写入 prompt | PASS |
| ST2204 | 并发吞吐 | `bench_concurrency.py`，并发 4 | parallel4 auto 1.86x；quality 1.0 | PASS |
| ST2205 | 文档亲和 | parallel=2 scheduled vs auto | scheduled 1.22x > auto 0.99x | PASS |
| ST2206 | 配置非法 | parallel=1 + scheduler / 非法 parallel / slots<2 | 抛 `JevProtocolError`/`ValueError`，不启动 | PASS |
| ST2207 | 快照互斥 | 快照 + parallel>1 或 + scheduler | 启动前 fail-fast | PASS |
| ST2208 | 提取组合 | GEXA/GQRA 命中 + scheduler 启用 | 命中在调用大脑前，usage.total_tokens=0 | PASS |

## 证据

- 单测：`uv run pytest tests/unit/test_slot_scheduler.py -q` → 13 passed；
- 真实：`tests/integration/test_slot_scheduler_real.py` → 1 passed；
- 并发：`benchmarks/results/ar022-concurrency-20261102-142142/results.json`；
- 全量：`uv run pytest tests/unit` → 780 passed；
- Lint：`uv run ruff check .` → 零告警。
