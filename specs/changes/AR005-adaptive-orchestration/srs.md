# [AR005] 需求设计说明书：风险可控的自适应编排（Risk-Aware Adaptive Orchestration）

| 字段 | 内容 |
|------|------|
| AR 编号 | AR005 |
| AR 主题 | adaptive-orchestration |
| 关联 SR | 端侧快慢双系统（Jev）：架构级创新 + 组合最优 + 可复现实验 |
| 日期 | 2026-10-26 |
| 状态 | Draft |

## 1. 背景与目标

AR002 建立了能力门控路由（真实 40 题命中 80%），AR003 建立了不确定性三带、大脑自证验证器、前缀复用、稀疏 prefill、语义缓存与长程记忆。但当前仍是**一组独立启发式**：
1. 各能力阈值相互独立，无法证明「降级不伤质量」的统计边界；
2. 记忆压缩、草稿注入、prefill 选块各自改写 prompt，存在预算冲突；
3. 组合方式只能手工开关，缺少「哪种组合最优」的实测依据与自动回退；
4. 全新克隆者缺少服务器大模型（7B/14B）与 GPU 复现实验的一键路径。

本 AR 的核心创新（相对 AutoMix/FrugalGPT 的路由、GPTCache 的相似度缓存、StreamingLLM 的窗口压缩）：
- **风险可控降级（Conformal Risk Control）**：用真实标注样本做 split-conformal 校准，对「降级质量损失」给出有限样本上界，替代拍脑袋阈值；缓存准入同样用 conformal p-value 控制假命中率。
- **联合上下文预算分配（JCBA）**：把记忆摘要/草稿/前缀/prefill 选块统一为带收益与 token 成本的候选单元，用确定性背包一次性分配，消除组件冲突。
- **代价感知策略选择 + 自动回退**：在真实 gold 集上评测多方案（B0/B1/B2/组合），用留一交叉验证选择「质量–成本」最优 profile；若无方案显著优于当前最优，自动回退到上一最优组合并保持默认配置。
- **可复现实验矩阵**：统一 `jev matrix` 入口，产出原始 JSON、Markdown 报告、环境指纹与服务器配置；7B/14B 在服务器执行，本机不伪造结果。

目标结果（全部真实后端，零 mock）：在真实 1.5B + Laya 上，AR005 组合相对 B1（AR002）与 B2（AR003）在质量不降（≥ -1pt）前提下降低综合代价（tokens/延迟），并能在质量下降时自动回退。

## 2. 需求范围

**In Scope：**
- `kernel/runtime/conformal.py`：回归/分类 conformal 校准、缓存假命中控制、风险门。
- `kernel/runtime/allocator.py`：联合上下文预算分配（确定性 DP + 首末块/安全约束）。
- `kernel/runtime/portfolio.py`：候选方案评测、代价感知策略选择、留一验证、自动回退。
- `bench/matrix.py` + `bench/selector.py` + CLI `jev matrix`：真实实验矩阵、原始 JSON、报告、profile 导出。
- chat/kernel/config 接线：按 profile 启用组合；风险门与 JCBA 进入真实请求路径；响应头只增不改（`X-Jev-Risk`、`X-Jev-Plan`）。
- 数据集：`configs/quality-set.yaml`（gold 质量集，≥30 题）、`configs/cache-set.yaml`（原句/复述/负例各 ≥10）。
- 复现基建：`README.md` 更新、`REPRODUCTION.md`、`docs/EXPERIMENTS.md`、`configs/experiments/*.toml`（含 7B/14B 服务器档）。
- 整理既有 AR：AR003 的 T013–T016 迁移到 AR005；AR003 仅保留 T007/T009/T012，由 AR005 矩阵产出其真实指标。

**Out of Scope：**
- 在线探索式 bandit（生产默认关闭，避免质量抖动）；GPU LoRA 训练（AR004）。
- 新协议端点、权重入仓、浏览器前端。

## 3. 功能需求

### FR5-1 Conformal 风险可控降级

**描述：** 以真实标注样本校准「便宜路径相对 full 的质量损失」风险，给出有限样本上界，替代固定 τ。

**触发条件：** `[runtime].risk_control=true` 且存在校准文件/样本。

**期望行为：**
- 对每个质量损失样本 `loss_i ∈ [0,1]`，按 split-conformal 取 `q̂ = Quantile_{⌈(n+1)(1-α)⌉/n}(loss)`（n 为校准样本数）。
- 请求级风险分 `s`（来自 U/验证器/自证）低于 `q̂` 时允许降级；否则 full。
- 校准样本不足（n < 20）或文件缺失 → 自动禁用并保持既有 AR003 行为（失败安全）。

**异常处理：** 校准文件损坏/字段非法 → `JevProtocolError`（启动期 fail-fast）；运行期风险门异常 → full（不降级）。

**验收标准：**
- Given 30 条真实标注样本、α=0.10，When 校准，Then `q̂` 单调且经验风险（留出集）≤ α + 1/√n。
- Given 校准缺失，When 请求，Then 行为与 AR003 三带完全一致（逐例回归）。
- Given `safety=unsafe`，When 任意 s，Then full 且不调用风险门。

### FR5-2 Conformal 缓存准入

**描述：** 语义缓存不再使用固定 similarity 硬阈值，而用负例分数分布控制假命中率。

**触发条件：** `[cache].conformal_admission=true` 且 embedding 可用。

**期望行为：**
- 用真实负例（不同实体/不同意图）与复述样本计算相似度；阈值 `τ_cache` 取负例分数的 `1-α` 分位，且要求正例通过率 ≥ 目标召回。
- 命中需同时满足：`score ≥ τ_cache`、与次近邻间隔 `≥ margin`、TTL 未过期。
- 返回 `CacheHit.model` 标注 `admission="conformal"` 与阈值，写入台账。

**异常处理：** embedding 不可用 → 仅精确层（沿用 AR003）；校准失败 → 回退固定阈值并记录。

**验收标准：**
- Given 10 负例 + 10 复述、α=0.10（真实 embedding），When 校准并检索，Then 负例误命中 ≤1/10 且复述命中 ≥6/10。
- Given 未校准，When 检索，Then 走固定 similarity（行为不变）。

### FR5-3 联合上下文预算分配（JCBA）

**描述：** 统一分配记忆摘要、草稿前缀、前缀复用与 prefill 选块的 token 预算。

**触发条件：** `[runtime].allocator_enabled=true` 且 prompt token ≥ `min_prompt_tokens`。

**期望行为：**
- 候选单元 = `{mandatory_system, recent_turns, memory_summary, prefill_blocks(按显著性), draft_prefix}`，各带 `benefit` 与 `tokens`。
- 在预算 `B = ceil(prompt_tokens × budget_ratio)` 下做 0/1 背包（确定性，收益相同时按输入顺序），必选单元预算不足 → 不压缩（`skipped=mandatory_over_budget`）。
- 输出分配结果与 `saved_tokens`；prompt 组装顺序固定：记忆 → 草稿 → prefill 选块（沿用 AR003 顺序契约）。
- 与 AR003 独立策略对比：同预算下保留收益不低于独立实现。

**异常处理：** 预算非法 → `ValueError`；分配后仍超预算 → 回退原 prompt 并记录 `skipped`。

**验收标准：**
- Given 固定输入与预算，When 连续分配两次，Then 结果逐字节一致。
- Given needles 集（40 条真实长文问答），When JCBA 压缩，Then `usage.prompt_tokens` 相对未压缩下降 ≥30%，且答案保留率下降 ≤2pt。
- Given 短 prompt（<512 tokens），When 分配，Then 不压缩（`skipped=short`）。

### FR5-4 方案矩阵与最优组合选择

**描述：** 在真实 gold 集上评测多个组合，选择质量–代价最优 profile；无提升则回退。

**触发条件：** 运行 `jev matrix --config configs/experiments/<name>.toml`。

**期望行为：**
- 方案至少包含：B0（全 full）、B1（AR002）、B2（AR003）、AR005（风险门+JCBA+conformal 缓存+验证器）。
- 每题记录：route、quality（gold 判定 0/1）、prompt/completion tokens、延迟、cache/verified 头、U。
- 指标：质量均值、命中率、成本、P50/P95 延迟、成对 bootstrap CI；输出原始 JSON + Markdown。
- 选择器：效用 `utility = quality_mean - λ × normalized_cost`（λ 可配）；仅当候选在留一 CV 中相对当前最优的效用下界 > 0 才采纳；否则 `decision=rollback` 并输出上一最优 profile。
- profile 写入 `configs/runtime-profile.toml`（可被 `jev.toml` include），默认不自动覆盖仓库文件，除非 `--write-profile`。

**异常处理：** 真实后端不可用 → 明确失败并退出码非 0，不生成伪结果；样本不足（<20）→ 拒绝选择并 rollback。

**验收标准：**
- Given 真实 30+ 题矩阵，When 选择，Then 生成 JSON/MD，且 `decision ∈ {adopt, rollback}` 与阈值规则一致。
- Given 候选质量低于基线或成本更高且无质量增益，When 选择，Then 必须 `rollback`，并输出上一最优组合。
- Given 后端 503，When 运行矩阵，Then 进程非 0 退出，不写 `adopt`。

### FR5-5 可复现实验与服务器大模型档

**描述：** 全新克隆按文档一键复现；7B/14B 在 GPU 服务器执行，配置与脚本完整。

**触发条件：** 新环境执行 README/REPRODUCTION。

**期望行为：**
- `uv sync` → `jev fetch --all` → `jev doctor` → `pytest tests/unit` → `jev matrix --config ... --limit 8` 全部可执行。
- 服务器档：`configs/experiments/server-7b.toml`/`server-14b.toml`，含模型路径、后端参数（`--parallel`、`--ctx-size`、`--n-gpu-layers`）、种子与指纹；缺少资产时给出明确指令，不伪造。
- 报告包含环境指纹（OS/CPU/GPU/Python/llama commit/资产 sha256 前缀）与运行命令。

**验收标准：**
- Given 全新克隆+真实资产，When 按 REPRODUCTION 执行，Then 单元测试全绿且矩阵子集产出 JSON。
- Given 无 14B 资产，When 执行 14B 档，Then 明确 `deferred-server`/资产缺失错误，不产生虚假指标。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 质量 | 相对 B1/B2 | 质量均值下降 ≤1pt；风险门违规率 ≤ α |
| 成本 | completion tokens | AR005 综合 ≤ B2；目标 ↓≥10% |
| 延迟 | P50 | ≤ B1 × 1.2；风险门/分配器额外 P50 ≤ 5ms |
| 可靠性 | 失败安全 | 校准/分配/选择任一失败 → 回退既有行为，不 500 |
| 覆盖 | 新增模块 | 语句 ≥85%；ruff 零告警 |
| 可复现 | 确定性 | 同输入/同 seed 结果一致；报告含指纹 |

## 5. 约束与假设

**约束：**
- 零 mock：全部指标来自真实 Laya ONNX + 真实 llama-server/embedding HTTP；纯算法单测允许确定性输入。
- 线协议只增不改；依赖方向 `server → kernel → 子模块`。
- 生产默认关闭新能力；仅显式 profile 启用。
- 7B/14B 本机不执行，配置与命令必须完整。

**假设：**
- gold 集答案可用字符串/数值匹配客观判定；负例与复述覆盖真实语义边界。
- 服务器具备 CUDA 与足够显存；模型资产不入仓。

## 6. 术语

| 术语 | 定义 |
|------|------|
| Conformal Risk Control | 用校准集分位数给出有限样本风险上界的无分布假设方法 |
| JCBA | Joint Context Budget Allocation，联合上下文预算分配 |
| Profile | 一组可复现的运行时配置（组合与阈值） |
| Rollback | 选择器判定无增益时回退到上一最优组合 |
| B0/B1/B2 | 全 full / AR002 能力门控 / AR003 运行时全开 |
