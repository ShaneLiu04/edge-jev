# [AR009] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR009 |
| AR 主题 | Cache-Aware Stable Compression（CASC）+ CPU/GPU 异构放置 |
| 关联 SR | 核心创新：端侧快慢双系统的真实低延迟、可复现与组合最优 |
| 日期 | 2026-10-27 |
| 状态 | Approved（用户授权全自动推进） |

## 1. 背景与目标

AR006 的 AEB 用“问题相关证据”压缩长文，prompt tokens 降低 51%，但每个问题选块不同，
**破坏前缀稳定性**；AR007 的稳定前缀 + KV 复用把 prefill 降到 319 ms，但 **不减少 prompt tokens**。
实测 `AEB_CACHE` 命中 0/4，证明二者简单叠加失败。

SOTA 启示：

| 来源 | 核心思想 | 本项目落地 |
|------|---------|-----------|
| SGLang RadixAttention | 前缀树 KV 复用 | 复用 llama.cpp prefix cache，Jev 负责前缀稳定策略 |
| vLLM PagedAttention | KV 分页/复用 | 不重造引擎；用 `cache_n` 真实观测 |
| LLMLingua / LongLLMLingua | 压缩长上下文 | 从“查询相关压缩”升级为“文档级稳定压缩” |
| RouteLLM / FrugalGPT | 成本感知路由 | 选择器同时计 token 与延迟，按负载选组合 |
| 端侧异构 | CPU/GPU 各司其职 | CPU 解码快 → brain 留 CPU；GPU prefill 快 → embedding 放 GPU |

本 AR 的目标：**在不降低质量的前提下，同时获得压缩收益与缓存收益**，并用真实实验证明：

1. **CASC**：对文档做**查询无关**的确定性压缩，同一文档所有问题得到逐字一致的前缀；
2. **压缩缓存**：以文档哈希为键缓存压缩结果，避免每个问题重复 embedding/选块；
3. **前缀复用**：压缩前缀 + 问题尾部分离，llama prefix cache 命中压缩前缀；
4. **异构放置**：brain 与 embedding 设备独立配置，用 GPU 承担 prefill 密集的 embedding；
5. **组合最优**：在 B1/CACHE/AEB/CASC/CASC_GPU 中真实测量，按负载选择，无收益回退。

## 2. 需求范围

**In Scope：**
- `kernel/prefill/stable.py`：文档/问题切分、文档级稳定压缩、LRU 压缩缓存、纯函数指标。
- `RuntimeConfig` / `configs/jev.toml`：`stable_compression`、`stable_compression_ratio`、
  `stable_compression_block_tokens`、`stable_compression_min_tokens`。
- `server/chat.py`：稳定前缀路径内接入 CASC；响应头/台账记录压缩收益与缓存命中。
- `Kernel`：构造 `StableCompressor` 并注入 chat。
- `[embedding] gpu_layers` + matrix `embedding_gpu_layers`：brain/embedding 设备独立。
- 数据集/实验：shared-prefix（同文档多问）与 needles（单文档单问），CPU/GPU 对照。
- 服务器 7B/14B CASC 配置与复刻文档。
- 单测 + 真实集成测试（真实 LLaMA/embedding，禁止 mock）。

**Out of Scope：**
- 训练压缩模型/摘要模型；
- 修改 llama.cpp/kv cache 内部实现；
- 修改 OpenAI/TypeSafe 线协议结构（只增响应头与运行时台账）；
- 未实际运行的 7B/14B 结论外推。

## 3. 功能需求

### 3.1 文档级稳定压缩（FR9-1）

**描述：** 对“长文档 + 末尾问题”输入，仅压缩文档部分，问题原文保留。

**触发：** `runtime.stable_compression=true` 且 `prefix_cache=true` 且请求含长文档
（文档 token ≥ `stable_compression_min_tokens`，默认 512）。

**期望行为：**
- 用末段（问题）切分文档与查询；切不出可靠问题时不压缩，回退稳定原文；
- 文档按固定块大小切块，基于**文档全局稀有度 + 覆盖度 MMR**选块，
  **不得使用当前问题**（保证同一文档所有问题选块一致）；
- 选块按原文顺序重组，比例由 `stable_compression_ratio`（默认 0.6）控制；
- 首/末块必选；确定性 tie-break（索引升序）；
- 问题尾部逐字保留，压缩文档与问题之间用固定分隔符连接。

**异常处理：**
- embedding 不可用 → 回退稳定原文（`fallback=embedding_unavailable`），不伪造向量；
- 压缩后 token 不低于原文 → 回退原文（`fallback=no_reduction`）；
- 文档过短/块数不足 → 跳过压缩（`skipped=short/few_blocks`）。

**验收：**
- Given 同一文档的 12 个不同问题，When 依次请求，Then 压缩前缀逐字一致；
- Given 同一文档重复请求，When 第二次请求，Then 压缩缓存命中且不重复调用 embedding；
- Given 任意问题，Then 答案质量不低于 CACHE/B1（Δquality ≤ 2pt）。

### 3.2 压缩结果缓存（FR9-2）

**描述：** 以 `sha256(文档 + 压缩配置)` 为键缓存压缩文本与统计，避免重复计算。

**期望行为：**
- 进程内 LRU（默认 64 条），线程安全；
- 命中时 `stable_compression.cache=hit`，未命中 `miss` 并记录耗时；
- 不缓存问题、不跨文档复用；
- 提供 `clear()` 供测试隔离；缓存大小可配置（0 表示禁用缓存）。

### 3.3 压缩前缀 KV 复用（FR9-3）

**描述：** CASC 输出的压缩文档作为稳定前缀，问题作为尾部，复用 llama prefix cache。

**期望行为：**
- 客户端 `cache_prompt=true`；
- 第二次起 `cache_n` 应覆盖压缩文档主体（≥ 压缩文档 token 的 50%）；
- 响应头新增：
  - `X-Jev-Stable-Compression: hit|miss|off;r=<ratio>;t=<compressed_tokens>`
  - `X-Jev-Stable-Cache: hit|miss|off`
- `last_runtime.stable_compression = {active, cache, ratio, before_tokens, after_tokens, ms}`。

### 3.4 异构设备放置（FR9-4）

**描述：** brain 与 embedding 分别配置 GPU 层数，允许“CPU 解码 + GPU embedding”。

**触发：** `[cerebrum] gpu_layers` / `[embedding] gpu_layers`（默认 0）。

**期望行为：**
- embedding 子进程启动参数包含 `--n-gpu-layers <embedding.gpu_layers>`；
- 矩阵 `embedding_gpu_layers` 独立于 `gpu_layers`；
- 实验必须记录真实 GPU 显存/利用率，证明 GPU 实际参与；
- 设备未核实（`--list-devices` 无 CUDA0）时不得标记为 GPU 实验。

### 3.5 组合实验与回退（FR9-5）

**臂定义：**

| arm | 定义 |
|-----|------|
| B1 | 无压缩、无缓存 |
| CACHE | AR007 稳定前缀 + KV |
| AEB | AR006 查询相关证据压缩（无缓存） |
| CASC | 文档级稳定压缩 + KV + 压缩缓存 |
| CASC_GPU | CASC + embedding 放 GPU（brain CPU） |

**验收：**
- shared-prefix 12 题真实运行，报告 quality/prompt tokens/prefill/latency/cache hit；
- CASC 质量 ≥ B1/CACHE −2pt，prompt tokens ≤ 0.75×CACHE，cache hit ≥ 11/12；
- CASC_GPU 相对 CASC_CPU 延迟下降（目标 ≥10%），且 GPU 显存/利用率有实测证据；
- needles 12 题验证单文档单问质量不回归；
- 选择器仍用配对 bootstrap + LOO；CI 下界 ≤0 或质量降 >2pt → `rollback`。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 兼容 | 默认关闭 | `stable_compression=false` 时与 AR007/AR008 行为一致 |
| 正确性 | 确定性 | 同文档同配置压缩结果逐字一致 |
| 可靠性 | embedding 失败 | 回退原文，不中断请求 |
| 可观测 | 头/台账 | ratio/before/after/cache/ms 可见 |
| 性能 | 缓存命中 | 同文档第二问不重复 embedding |
| 依赖 | 方向 | kernel 不 import server；薄壳不绕过 Kernel |
| 真实 | 测试 | 禁止 mock；真实模型/进程；缺资产显式失败 |

## 5. 约束与假设

- 本机 GPU（Quadro RTX 5000）被限频 645 MHz：decode 慢于 CPU，但 prefill/embedding 快；
  因此 CASC_GPU 仅把 embedding 放 GPU，brain 保持 CPU；
- 压缩质量依赖 embedding 模型；embedding 不可用时安全回退；
- 共享文档场景才有缓存收益；一次性文档仍由 AEB 或 B1 竞争；
- 质量集为小样本，bootstrap CI 仅作工程决策依据。

## 6. 术语

| 术语 | 定义 |
|------|------|
| CASC | Cache-Aware Stable Compression，查询无关文档压缩 + 前缀缓存 |
| 稳定压缩 | 同一文档在不同问题下产生逐字一致的压缩文本 |
| 稀有度 | 块向量与文档质心的距离，用于保留独特事实 |
| 覆盖度 MMR | 在稀有度基础上抑制重复块，提升信息覆盖 |
| 异构放置 | brain 与 embedding 使用不同设备 |
| 压缩缓存 | 以文档哈希为键的进程内 LRU |
