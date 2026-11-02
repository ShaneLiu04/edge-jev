# [AR022] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR022 |
| AR 主题 | PSS：GPU 并行槽与文档亲和调度 |
| 关联 SR | 端侧多用户/服务器并发吞吐 |
| 日期 | 2026-11-02 |
| 状态 | Approved（用户授权自动化 harnessX） |

## 1. 背景与目标

当前最优路径按单请求优化，但边缘服务器会同时收到多用户/多问题请求。
llama-server 支持 `--parallel N` 与请求级 `id_slot`；真实探测确认可指定 slot。
在不改变单请求语义的前提下，通过并行槽提高 GPU 利用率，并提供按文档轮转
的确定性槽位调度，避免同一文档前缀在槽间无序漂移。

真实并发探测（shared-prefix 12 题，客户端并发 4，真实 1.5B/GPU）：

| 模式 | wall | P50 | req/s | 相对 parallel1 |
| --- | ---: | ---: | ---: | ---: |
| parallel=1 | 8152ms | 2702ms | 1.472 | 1.00x |
| parallel=2 auto | 8218ms | 2698ms | 1.460 | 0.99x |
| parallel=2 scheduled | 6693ms | 2217ms | 1.793 | **1.22x** |
| parallel=4 auto | 4386ms | 1445ms | 2.736 | **1.86x** |
| parallel=4 scheduled | 4539ms | 1527ms | 2.644 | 1.80x |

目标：提供可配置并行吞吐档与文档亲和档，单请求默认行为不变；吞吐收益真实、
质量不变，且与 GEXA/GQRA/PPKS 的互斥/组合边界清晰。

## 2. 需求范围

**In Scope：**
- `[cerebrum] parallel` + `slot_scheduler_enabled`；
- `SlotScheduler` per-document 轮转与 `ScheduledClient` 注入 `id_slot`；
- Kernel/app/matrix/CLI 配置接线；
- 真实并发脚本与 profile；
- 单请求/抽取/PPKS 兼容边界。

**Out of Scope：**
- 不改变 OpenAI 线协议；`id_slot` 走内部 extra_body；
- 不做跨文档语义缓存/批推理；
- 不支持 `slot_scheduler` 与 PPKS 同时启用（PPKS 需 parallel=1）；
- 不承诺任意硬件同等加速。

## 3. 功能需求

### FR22-1 并行槽配置

**描述：** `[cerebrum] parallel=N`，`slot_scheduler_enabled` 默认 false；
启用调度需 N>=2；非法配置 `JevProtocolError`。

**验收：**
- 默认 parallel=1、scheduler off，单请求行为不变；
- enabled + parallel<2 fail-fast；
- 快照与调度同时启用 fail-fast。

### FR22-2 文档亲和调度

**描述：** `SlotScheduler` 对最后 user 文档做稳定 hash，按文档独立计数轮转
`0..N-1`；不同文档互不干扰；无文档时按问题文本轮转。

**验收：**
- 同文档连续请求 slot 0,1,2,0…；
- 不同文档各自从 0 开始；
- 关闭时返回 None，不注入。

### FR22-3 客户端注入

**描述：** `ScheduledClient` 在 chat/chat_stream 的 extra_body 注入 `id_slot`，
保留调用方其余 extra_body；不改变 usage/内容语义。

**验收：**
- chat 两次同文档使用 slot 0/1；
- stream 同样注入；
- 未启用时透传原 extra_body。

### FR22-4 真实并发实验

**描述：** `scripts/bench_concurrency.py` 真实启动 parallel=1/2/4，客户端并发 4，
对同一共享文档 12 题测墙钟/吞吐/质量。

**验收：**
- parallel=4 相对 parallel1 吞吐 ≥1.5x，质量 1.0；
- parallel=2 scheduled 相对同配置 auto 有正收益；
- 结果落 `benchmarks/results/`。

### FR22-5 组合与回退

**描述：** 调度只包装大脑客户端；GEXA/GQRA 命中不经过大脑，不受影响；
PPKS 需 parallel=1，故与调度互斥；默认关闭即回退单槽。

**验收：**
- 默认 profile 单请求延迟/质量不变；
- GEXA/GQRA 抽取命中仍 tokens=0；
- 配置关闭即回退。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 吞吐 | parallel=4 并发 4 | ≥1.5x vs parallel1 |
| 质量 | 12 题 | 1.0 |
| 兼容 | 默认 | 单槽行为不变 |
| 可靠性 | 非法配置 | fail-fast，无静默降级 |

## 5. 约束与假设

- `--parallel N` 会按 N 分割 ctx，每 slot 可用 ctx 需覆盖压缩后 prompt；
- 实验使用 ctx=16384（4 slots 每槽 4096）；
- GPU 显存/带宽决定实际加速比，服务器需按显存调 N/ctx；
- `id_slot` 为 llama-server 扩展字段，客户端通过 extra_body 传递；
- 调度器为进程内状态，不跨进程持久化。

## 6. 术语

| 术语 | 定义 |
|------|------|
| slot | llama-server KV 槽位 |
| 文档亲和 | 同文档请求在一组槽位间确定性轮转 |
| PSS | Parallel Slot Scheduler |
