# [AR007] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR007 |
| AR 主题 | 前缀 KV 复用 + 会话亲和上下文（含 AEB 冲突分析） |
| 关联 SR | 核心创新：端侧/服务器快慢双系统的真实低延迟与可复现 |
| 日期 | 2026-10-27 |
| 状态 | Approved（用户授权全自动推进） |

## 1. 背景与目标

AR006 的 AEB 在单次长文请求上把 prompt tokens 降低 51%，但每个请求都重新 prefill，
且 embedding 选块带来约 10s 额外延迟。真实对话/多轮/同文档多问场景中，**前缀高度重复**，
服务器侧 KV/prefix cache 可以复用已计算的上下文。

本机真实 smoke（1.5B，1383 token 前缀）：
- 首次：`prompt_n=1383, prompt_ms=3280`
- 第二次同前缀不同问题：`cache_n=1365, prompt_n=17, prompt_ms=174` → **prefill 约 18.8× 加速**

但 AEB 会按问题选择不同块，**破坏前缀稳定性**，与 KV 复用直接冲突。本 AR 要解决：

1. 把「前缀缓存」做成一等运行时能力（可观测、可回退、默认关闭）；
2. 用**会话亲和**策略在“稳定前缀+缓存”和“AEB 压缩”之间做确定性选择；
3. 用真实 shared-prefix 数据集量化收益与冲突，选出综合最优组合；无增益则回退；
4. 给出 7B/14B 服务器复现配置（显存/并发/缓存权衡）。

对标与取舍：

| 来源 | 核心思想 | 本项目落地 |
|------|---------|-----------|
| SGLang RadixAttention | 前缀树 KV 复用 | 复用 llama-server 的 prefix cache，Jev 只做**请求级前缀稳定性策略**与可观测 |
| vLLM PagedAttention / continuous batching | 分页 KV、连续批处理 | 不重造推理引擎；服务器侧用 `--parallel` + 缓存，见文档 |
| LLMLingua / AEB（本项目 AR006） | 压缩上下文降 token | 单次长文仍用 AEB；会话场景优先稳定前缀 |
| OpenAI prompt caching | 相同前缀自动复用、按缓存 token 计费 | 采用 `cache_prompt` 与 `usage.prompt_tokens_details.cached_tokens` 语义 |
| 企业网关 sticky session | 会话亲和 | 采用 `user` 字段作为 session key（OpenAI 兼容，无需改协议） |

## 2. 需求范围

**In Scope：**
- `kernel/runtime/config.py` + `kernel.py`：`prefix_cache` / `session_affinity` 配置。
- `server/chat.py`：`user` → session；会话模式下返回**稳定原文 prompt**（跳过 AEB/记忆/草稿）；
  捕获 `timings.cache_n`，输出 `X-Jev-Prefix-Cache` 头与台账。
- `kernel/cerebrum/supervisor.py` / `server/app.py`：客户端 `cache_prompt` 与配置一致。
- `bench/prefixset.py`：shared-prefix 数据集（同文档、多问题）。
- `bench/matrix.py`：`prompt_cache` 配置、session user 注入、缓存命中指标。
- 真实实验与服务器配置、文档、结果。

**Out of Scope：**
- 修改推理引擎/自建 Radix 树（llama-server 已有）；
- 多槽并行下的跨槽 KV 迁移（服务器文档说明限制）；
- 大模型实际运行（仅提供可复现配置，未跑不得宣称结果）。

## 3. 功能需求

### 3.1 前缀缓存开关（FR7-1）

**描述：** 打开后请求携带 `cache_prompt=true`，复用同前缀 KV。
**触发：** `[runtime] prefix_cache=true`。
**期望行为：**
- 客户端显式发送 `cache_prompt`（关闭时显式 `false`，避免服务器默认值造成基线污染）；
- 响应头 `X-Jev-Prefix-Cache: hit:<cache_n>` / `miss` / `off`；
- `last_runtime.prefix_cache = {active, cached_tokens}`；
- 首次请求 miss 属正常，不报错。
**异常：** 服务器不支持/忽略时 `cache_n=0`，按 miss 记录，不影响正确性。

### 3.2 会话亲和稳定前缀（FR7-2）

**描述：** 带 `user`（session）的长请求优先稳定前缀，避免 AEB 选块破坏缓存。
**触发：** `prefix_cache=true` 且 `session_affinity=true` 且请求含非空 `user`。
**期望行为：**
- 跳过 AEB/记忆/草稿/预算分配，直接发送原始 messages；
- `last_runtime.prefill = {skipped:"prefix_cache", selector:"prefix-cache"}`；
- 同一 session 内除新增问题外前缀逐字一致；
- 无 `user` 或 `session_affinity=false` 时维持 AR006 的 AEB 行为。
**验收：**
- Given 同一 session 两次请求（同文档、不同问题），When 第二次执行，Then `cache_n>0` 且延迟显著下降；
- Given 无 session，When 长文请求，Then 仍走 AEB 且结果与 AR006 一致。

### 3.3 AEB 与缓存冲突可量化（FR7-3）

**描述：** 组合实验必须暴露“压缩换缓存命中”的真实权衡。
**臂：**
- B1：无压缩、无缓存；
- CACHE：完整前缀 + 缓存；
- AEB：证据自适应压缩、无缓存；
- AEB_CACHE：两者同时开（不同问题选块不同 → 预期低命中）。
**验收：** 报告给出各臂 quality、prompt tokens、latency、cache_n；明确最优与冲突结论。

### 3.4 真实评测与门（FR7-4）

- shared-prefix 12 题，真实 Laya + llama-server（可选真实 embedding）；
- 门：CACHE 第二次起 `cache_n ≥ 50%` 前缀 token；P50 延迟相对 B1 ↓≥2×；
- AEB 单次仍保持 AR006 的 ≥50% token 降幅（不回归）；
- 未达标不得宣称收益，按回退记录。

### 3.5 服务器复现（FR7-5）

- 7B/14B external 配置增加 `[runtime] prefix_cache=true` 与 `session_affinity=true`；
- 文档写清 `--parallel`、`--slot-prompt-similarity`、显存与缓存收益/限制；
- 明确多槽可能降低跨请求命中，需要固定槽或单槽复现。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 兼容 | 默认行为 | `prefix_cache=false` 时输出与 AR006 一致 |
| 正确性 | 缓存不改变答案 | 同请求缓存前后答案逐字一致（temperature=0） |
| 可观测 | 命中 | 头/台账可见 `cache_n` |
| 可靠性 | 假死 | 沿用 BrainSupervisor；缓存不绕过健康检查 |

## 5. 约束与假设

- 不新增模型资产（复用现有 1.5B/服务器模型）；
- `cache_prompt` 仅影响服务器内部 KV，不改变 OpenAI 响应结构；
- 多轮真实前端需传稳定 `user`；未传则退回 AEB（安全默认）。

## 6. 术语

| 术语 | 定义 |
|------|------|
| 前缀 KV 复用 | 相同 prompt 前缀命中服务器已计算的 KV，只 prefill 新增后缀 |
| session | 由 OpenAI `user` 字段标识的稳定会话键 |
| 稳定前缀 | 同一 session 内除新增问题外逐字一致的 prompt |
| AEB_CACHE | AEB 与 prefix cache 同时开启的冲突对照臂 |
