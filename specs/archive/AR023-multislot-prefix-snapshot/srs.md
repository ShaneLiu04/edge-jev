# [AR023] 需求设计说明书：多槽持久化前缀快照（MSPP）

| 字段 | 内容 |
|------|------|
| AR 编号 | AR023 |
| AR 主题 | multislot-prefix-snapshot |
| 关联 SR | 端侧快慢双系统：组合性能最优、真实可复现 |
| 日期 | 2026-11-05 |
| 状态 | Draft |
| 前置 | AR019 PPKS 单槽快照、AR022 PSS 并行槽 |

## 1. 背景与目标

### 1.1 问题

AR019 证明持久化 KV 快照能把冷启动首请求从 6.13s 降到 0.88s，但实现固定 `slot 0 + parallel=1`；
AR022 证明 `parallel=4` 能把并发吞吐提升 1.86x，但快照与并行槽被代码**互斥 fail-fast**。
这意味着真实部署只能在“冷启动快”与“并发高”之间二选一，组合收益被人为切断。

同时，vLLM PagedAttention prefix caching、SGLang RadixAttention、llama.cpp 原生
`/slots/{id}?action=save|restore` 与 DistServe 的 prefill/decode 分离共同说明：
**前缀复用应当与并行服务共存，并按 slot 生命周期管理**，而不是全局单槽。

### 1.2 目标

实现 **MSPP（Multi-Slot Persistent Prefix Snapshot）**：

- 同一文档快照可恢复到请求实际使用的 `id_slot`；
- 每 `(document, slot)` 独立记录恢复状态，避免“一个槽恢复后其它槽误以为已恢复”；
- 并发时只在目标槽空闲时恢复/保存，避免破坏其它在途请求；
- 默认关闭；单槽行为与 AR019 完全一致；
- 真实冷重启 + 并发 4 场景下，相对 `parallel=4 auto` **首轮墙钟/P50 有正收益**，
  quality 不掉、稳态不回归；否则回退到 AR022 最优。

## 2. 需求范围

**In Scope：**
- `kernel/cerebrum/snapshot.py`：按请求 `id_slot` 恢复/保存，`(key, slot)` 状态、在途槽计数、LRU；
- `kernel/kernel.py`：移除 snapshot 与 parallel/scheduler 的互斥；启用调度时自动 per-request slot；
- `server/app.py`、`bench/matrix.py`：保持 SnapshotClient 内层、ScheduledClient 外层的组合顺序；
- 新增真实并发冷重启脚本与 profile/实验配置；
- 单元测试、真实集成测试、结果文档。

**Out of Scope：**
- 小脑多槽（Laya 无 slot 概念）；
- 0.5B 小模型多槽并行（仍单槽，GTB 不受影响）；
- 跨模型/跨机器共享快照；
- 修改 llama.cpp 或自行实现 KV 迁移；
- 语义缓存、生成质量策略变更。

## 3. 功能需求

### FR23-1 按请求槽恢复/保存

**描述：** SnapshotClient 从 `extra_body["id_slot"]` 读取实际槽位；无该字段时使用配置
`kv_snapshot_slot`（默认 0）。

**验收：**
- Given `parallel=4 + scheduler`，When 同一文档并发 4 请求，Then 4 个槽各自恢复同一快照文件；
- Given 单槽模式，When 请求命中快照，Then 行为与 AR019 一致。

### FR23-2 每槽恢复状态

**描述：** 恢复状态按 `(document_key, slot_id)` 记录；同一文档在槽 0 恢复后，
槽 1 首次请求仍必须尝试恢复。

**验收：** 真实 `/slots` 或响应 `cache_n` 证明两个槽都获得前缀 KV。

### FR23-3 并发安全

**描述：** 每槽维护在途计数；仅当该槽无其它在途请求时才 restore/save；
恢复失败不得删除可能仍有效的快照（多槽模式），只记录并回退正常 prefill。

**验收：**
- 并发同文档不出现槽内 KV 被错误覆盖导致的答案错误；
- 快照异常无 5xx，请求正常完成。

### FR23-4 配置与兼容

**描述：** 默认关闭；移除“快照必须 parallel=1”与“快照不能与 scheduler 同用”的硬限制；
保留非法目录、越界参数 fail-fast。

**验收：** 旧 `ppks.toml`（parallel=1）零行为变化；新配置可启用 4 槽快照。

### FR23-5 真实组合实验

**描述：** 提供 `scripts/bench_multislot_snapshot.py`，真实引擎/真实进程对比：
`parallel1`、`parallel4 auto`、`parallel4 scheduler`、`parallel4 scheduler + MSPP`
（冷启动第一轮 + 稳态第二轮），记录 wall/P50/prompt_n/cache_n/quality/restore。

**验收：** 结果落 `benchmarks/results/ar023-*`，选择器按 `quality − λtokens − λlatency`
判定 adopt/rollback；无正收益则回退 AR022。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | 冷启动首轮并发 | 相对 parallel4 auto 有正收益（目标 ≥10%） |
| 质量 | 金标 | 与 baseline 一致，0 误答 |
| 可靠性 | 快照异常 | 回退正常 prefill，不阻断请求 |
| 兼容 | 单槽 | AR019 行为与指标不回归 |
| 资源 | 磁盘 | 继续使用 `max_entries` LRU |

## 5. 约束与假设

- 仅使用真实 llama-server / Laya / Qwen 资产，禁止 mock 后端；
- 快照文件是“前缀 KV”，与 slot 无关，可被多个槽读取；
- 恢复/保存必须发生在请求使用该槽之前/之后，服务端同一槽串行；
- 本机 GPU 645MHz，绝对延迟仅供相对比较；
- 7B/14B 只提供配置，不在本机外推。

## 6. 风险与回退

| 风险 | 处理 |
|------|------|
| 恢复时槽正忙导致 KV 污染 | 在途计数；忙则跳过 restore |
| 多槽并发写同一文件 | `inflight/saved` 去重，同文档只保存一次 |
| 快照格式不兼容 | 失败回退正常 prefill，不删除文件（多槽） |
| 组合无正收益 | 选择器 rollback，默认 profile 不变 |
