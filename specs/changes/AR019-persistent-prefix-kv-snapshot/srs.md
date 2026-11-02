# [AR019] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR019 |
| AR 主题 | PPKS：持久化前缀 KV 快照 |
| 关联 SR | 服务重启/冷启动后的长文档复现效率 |
| 日期 | 2026-10-31 |
| 状态 | Approved（自动 harnessX，用户已授权无需逐步确认） |

## 1. 背景与目标

当前最优组合（CGW/GTB+CASC）在**稳态**下已复用 prefix KV，但 llama-server 重启后
KV 丢失，首个长文档请求仍需完整 prefill。shared-prefix 真实冷启动首请求约
24–52s（取决于 GPU 争用），而稳态约 0.7–1.0s。

llama-server 支持 `--slot-save-path` 与 `/slots/{id}?action=save|restore`。
真实探测（1.5B、8192 ctx、GPU offload）：

- 首次请求 6326ms，`prompt_n=2684`；
- `save` 200，`n_saved=2688`，文件 77MB，`save_ms=183`；
- 重启进程后 `restore` 200，`n_restored=2688`，`restore_ms=107`；
- 同文档新问题：**764ms**，`prompt_n=1`、`cache_n=2683`、内容正确。

目标：把“重启后首请求”从完整 prefill 降为快照恢复，保留质量与稳态性能，
为高速算力服务器复现实验/滚动重启提供真实可用的持久化能力。

## 2. 需求范围

**In Scope：**
- `[cerebrum]` 快照配置（默认关闭、opt-in）；
- 真实 `--slot-save-path` 生命周期与 `/slots` save/restore 客户端；
- 以“压缩后文档 + 模型/上下文指纹”为键的持久化快照；
- 异步保存、LRU 条数限制、损坏自动忽略、正常请求兜底；
- 真实冷重启实验脚本与 README/REPRODUCTION 复刻说明。

**Out of Scope：**
- 不改变 OpenAI/线协议既有字段；不改变压缩/PRE/POST 语义；
- 不持久化对话隐私到仓库（`data/` gitignored，默认关闭）；
- 不保证不同模型/量化/ctx 之间快照可迁移（键包含指纹）；
- 不改变稳态 P50/mean；该 AR 的目标指标是**冷启动首请求**。

## 3. 功能需求

### FR19-1 快照配置与目录

**描述：** `[cerebrum] kv_snapshot_dir`（默认空）、`kv_snapshot_max_entries`、
`kv_snapshot_min_chars`、`kv_snapshot_slot`。

**验收：**
- Given 空目录, When 启动, Then 不传 `--slot-save-path`，行为与 AR018 一致；
- Given 合法目录, When 启动大/小模型, Then 均传入独立目录并真实可用；
- 非法 max_entries/slot fail-fast。

### FR19-2 真实 save/restore 客户端

**描述：** `LlamaServerClient.slot_save/restore/slots` 调用真实 HTTP API。
**验收：** save 返回 `n_saved>0`；restore 后同文档请求 `prompt_n` 显著下降。

### FR19-3 文档级快照键与异步保存

**描述：** `SnapshotClient` 从实际发送的压缩 prompt 提取文档，按
`model_fingerprint + document_sha256` 生成文件名；首次请求后异步保存，
后续新问题恢复同一文档前缀。

**验收：**
- Given 文档 A 首次请求, When 完成, Then 后台生成 `.bin` 且不阻塞响应；
- Given 新进程 + 同文档问题, When 请求, Then 先 restore，`prompt_n` 下降；
- 不同文档/模型/ctx 不串用。

### FR19-4 安全兜底与可观测

**描述：** restore 失败/文件损坏/服务不支持时，删除坏文件并继续正常请求；
响应头 `X-Jev-KV-Snapshot: restore|save|miss|error`。
**验收：** 任何快照异常都不产生 5xx；关闭开关时无快照头。

### FR19-5 冷重启真实实验

**描述：** 新增 `scripts/bench_ppks.py`，真实启动/停止 llama-server 对照
baseline 冷启动 vs 快照恢复；输出 JSON/Markdown。
**验收：** shared-prefix 同文档新问题冷启动首请求延迟显著下降，内容质量一致。

### FR19-6 回退

**描述：** 无正收益或磁盘/API 不可用时关闭 `kv_snapshot_dir` 即可回退，
稳态 CGW/GTB 不受影响。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 冷启动 | 同文档首请求 | 相对 baseline 下降 ≥50% |
| 稳态 | P50/mean | 关闭/开启无可测退化（restore 仅首次） |
| 磁盘 | 单快照 | ~77MB/文档，默认最多 4 条 |
| 可靠性 | 快照异常 | 100% 回退正常 prefill，无 5xx |
| 兼容 | 默认 | 关闭时与 AR018 字节级一致 |

## 5. 约束与假设

- 仅支持 `parallel=1`（slot 0）；多 slot 显式不启用；
- 快照目录在 `data/`，gitignored；不提交用户文档内容；
- 快照文件不可跨模型/量化/ctx/压缩配置复用；
- 保存是异步的，进程立刻退出可能丢失快照（可接受，下次重建）。

## 6. 术语

| 术语 | 定义 |
|------|------|
| PPKS | Persistent Prefix KV Snapshot |
| slot | llama-server 的 KV 槽位，parallel=1 时 slot 0 |
| 文档键 | 压缩后文档文本的 sha256 |
| 模型指纹 | 模型路径+ctx+KV 类型等配置的 sha256 前缀 |
