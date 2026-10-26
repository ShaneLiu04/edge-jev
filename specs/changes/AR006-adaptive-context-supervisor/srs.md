# [AR006] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR006 |
| AR 主题 | 证据驱动自适应上下文与自愈大脑编排（组合最优） |
| 关联 SR | 核心创新：端侧快慢双系统的高质量、可复现增益 |
| 日期 | 2026-10-26 |
| 状态 | Approved（用户授权全自动推进） |

## 1. 背景与目标

AR005 已证明「长上下文 prefill/JCBA」能在真实后端上把 prompt tokens 降低约 39%（2457→1493）
而质量不变。但现有选择器仍是**固定预算 + 词法显著性**，存在三个可量化短板：

1. 固定 `budget_ratio=0.6` 对「答案集中在一段」的查询浪费预算，对「证据分散」的查询又过于激进；
2. 词法重叠对同义改写/跨语言/编号指代不鲁棒，长文档中真正证据可能被漏选；
3. 真实 llama-server 在长 prompt + verifier/memory 组合下会假死，缺少进程级自愈，实验被迫关闭能力。

本 AR 的目标是给出**比 baseline 显著更强、且诚实可复现**的组合：
以 embedding 证据选择 + 自适应预算（AEB）替代固定阈值，以大脑监督器消除假死，
并以「任意能力组合」矩阵找出综合最优组合，无增益即回退。

对标与取舍（SOTA 思想 → 本项目落地）：

| 来源 | 核心思想 | 本项目取舍 |
|------|---------|-----------|
| SGLang RadixAttention / vLLM PagedAttention | 前缀/KV 复用、连续批处理 | 复用留在 llama-server 层；Jev 只做请求级规划，避免重复造轮子 |
| LLMLingua / LLMLingua-2 | 面向任务压缩、保留关键证据 | 不做 token 级删除（破坏可读性/可审计性），改为**块级预算 + 证据锚点** |
| RAG MMR（Carbonell & Goldstein） | 相关性 + 多样性联合选择 | 采纳：块级 MMR，防止相邻冗余块挤占预算 |
| RouteLLM / FrugalGPT | 成本-质量联合优化、级联 | 已有 AR002/AR005；本 AR 只做**上下文侧**的成本-质量 |
| GPTCache | 语义缓存 | 已有 AR003/AR005；本 AR 不改缓存语义 |
| EAGLE/Medusa 投机解码 | 小模型起草+大模型验证 | 本机无同词表 draft 资产，**明确不做**（列入 AR007 候选） |
| Kubernetes liveness/readiness | 健康探针 + 重启 | 采纳：大脑监督器（探针→重启→重试一次） |

## 2. 需求范围

**In Scope：**
- `kernel/prefill/evidence.py`：真实 embedding 证据打分（余弦 + 词法 + 位置 + 决策信号融合），失败降级。
- `kernel/prefill/planner.py`：自适应预算比例（分数熵 + top-gap）、MMR 去冗余、证据锚点必选。
- `kernel/runtime/prompt.py` + `allocator.py`：AEB 接入 JCBA，台账记录实际预算与选择器。
- `kernel/cerebrum/supervisor.py`：健康探针、崩溃/假死重启、单次重试、统计。
- `bench/matrix.py`：支持配置中任意 arm 名（组合），组合选择与回退。
- 真实实验：needles + quality 组合矩阵；结果、报告、profile 写入仓库。
- 复现文档：服务器 7B/14B 大模型实验步骤与配置说明。

**Out of Scope：**
- 投机解码/多 draft 资产（需新增模型，AR007 评估）。
- LoRA/飞轮训练（AR004）。
- 修改 TypeSafe 线协议与既有响应字段语义（只加可选头/台账键）。

## 3. 功能需求

### 3.1 证据感知选择（FR6-1）

**描述：** 长文档按块选择时，用真实 embedding 余弦相似度替代纯词法排序。
**触发：** `[prefill].evidence=true` 且 embedding 服务可用且 prompt 超阈值。
**期望行为：**
- 批量取 query 与各块向量（真实 HTTP），余弦后 min-max 归一；
- 融合分数 = 0.55·emb + 0.25·lexical + 0.10·position + 0.10·decision（可配）；
- MMR：`utility = score − λ·max_sim(selected)`，λ 默认 0.25；
- 必选集合 = 首块 + 末块 + **证据锚点**（最高融合分块）；
- 超过预算时按确定性顺序填充。
**异常：** embedding 不可用/超时 → 整体降级为现有 salience，并返回 `fallback` 原因；**绝不伪造向量**。
**验收：**
- Given 真实 embedding 服务，When 对 needles 文档选择，Then 返回证据分与锚点且计划 token 下降；
- Given embedding 不可用，When 选择，Then 结果与 salience 完全一致且记录 fallback。

### 3.2 自适应预算（FR6-2）

**描述：** 每个请求按分数分布决定压缩比例，而非固定 0.6。
**触发：** `[prefill].adaptive=true`。
**期望行为：**
- 归一分数熵 `H`（/log n）与 top-gap `g=(s1−s2)/(s1+ε)`；
- `ratio = clamp(min + (max−min)·(0.5H + 0.5(1−g)), min, max)`；
- 默认 `min=0.35, max=0.75`；记录 ratio/entropy/gap 到计划与响应台账；
- 若可用块不足/必选超预算，保持原输入（现有安全回退）。
**异常：** 无证据分（降级）时 ratio 回退为配置固定值。
**验收：**
- 单峰分布 → ratio 接近 min；均匀分布 → 接近 max；单调性单测；
- 真实 needles 上自适应 ratio 的平均 prompt token 低于固定 0.6 且质量不降。

### 3.3 大脑自愈监督（FR6-3）

**描述：** 检测 llama-server 不健康/请求超时后重启并重试一次。
**触发：** 每次调用前健康探针（可配间隔）或调用抛 `JevTimeoutError/JevUnavailableError`。
**期望行为：**
- `health()` 失败 → `stop()` + `start()`（真实子进程），记一次 recovery；
- 原请求重试一次；仍失败则按原错误上抛；
- 统计 `restarts/retries/recoveries/last_recovery_ms`；不伪造成功。
**验收：**
- Given 真实 server 被 `kill`，When 发起 chat，Then 监督器重启并成功返回，统计 +1；
- Given 重启失败，Then 抛 `BrainStartupError`（或原错误），不返回假内容。

### 3.4 组合最优与回退（FR6-4）

**描述：** 实验矩阵支持在配置中声明任意 arm（组合名）+ 覆盖片段，跑完按统一效用选择最优组合。
**触发：** `aligns = ["B1","B2","C_prefill","C_aeb","C_aeb_cache","C_all"]`。
**期望行为：**
- 未知 arm 若在 `overlays` 中有定义则允许；未定义的未知 arm 报错；
- 每 arm 独立重启大脑；每完成一个 arm 写 `progress.json`；
- 选择器仍用配对 bootstrap + LOO；无正下界/质量降 >1pt → rollback 到当前最优基线；
- 产出 `results.json/report.md/profile.toml`，最优组合写入 `docs/RESULTS.md`。
**验收：** 组合实验真实跑通，报告明确 chosen/rollback 与逐组合指标。

### 3.5 真实评测与复现（FR6-5）

- 真实 Laya + 真实 llama-server + 真实 embedding，全链路无 mock；
- needles：AEB 组合相对 B1 prompt tokens ↓ ≥50%，quality Δ ≤2pt；
- 若未达标，不得宣称增益，按回退流程记录；
- 服务器 7B/14B 配置与命令写入 `REPRODUCTION.md`，包含显存/时长/风险说明。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 确定性 | 选择结果 | 同输入同配置逐字节一致（embedding 服务同为确定性输入） |
| 性能 | 选择开销 | 计划阶段额外 ≤300ms（真实 embedding 批量，文档 ≤30 块） |
| 可靠性 | 自愈 | 单进程假死可恢复；恢复失败显式报错 |
| 兼容 | 默认行为 | 新能力默认关闭，旧配置输出不变 |
| 可观测 | 台账 | 记录 selector/ratio/entropy/gap/restarts |

## 5. 约束与假设

**约束：**
- 禁止 mock 后端；纯逻辑单测可注入确定性纯函数，但真实路径必须走真实 HTTP/进程；
- 模型资产不入仓；缺失即 `JevAssetError`；
- 线协议只增不改；新头/台账键可选。

**假设：**
- 本机 embedding 可用同一 1.5B gguf 的 `--embeddings` 实例（真实、质量一般但可用）；
- 窄带条件下长 prompt 可能触发服务假死，由 FR6-3 兜底并如实记录。

## 6. 术语

| 术语 | 定义 |
|------|------|
| AEB | Adaptive Evidence Budgeting，证据驱动自适应上下文预算 |
| 锚点块 | 融合分最高的块，压缩时强制保留 |
| MMR | Maximal Marginal Relevance，相关性−冗余度贪心选择 |
| 组合 arm | 由若干能力开关组成的实验方案（如 prefill+evidence+adaptive） |
