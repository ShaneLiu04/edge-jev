# [AR002] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR002 |
| AR 主题 | cascade-quality-innovation（判定质量工程 + 智能路由 + 蒸馏飞轮 + 评测体系 + 复现工程） |
| 关联 SR | 承接 AR001 ST 遗留（ST-014/ST-023 命中率门）+ 用户战略指令（深度架构创新、Gitee 交付、高算力可复现） |
| 日期 | 2026-10-24 |
| 状态 | Draft |

## 1. 背景与目标

### 1.1 来自 AR001 ST 的硬事实

AR001 完成双系统运行时骨架并通过 31/33 ST 用例，但**级联命中率门 FAIL**（ST-014/ST-023）：

- 真实 laya 资产下 20/20 题全部路由 full，小脑直答命中率 **0%**（门 ≥70%）；
- 探针证据：complexity（score 型）恒输出 medium 且置信度 0.3–0.6 < τ=0.60（不应用 → 安全侧 full）；intent 从不输出 decision（direct 不可达）；英文题同样失败（en-light-05 complexity=low 但 conf 0.56 < τ）；
- formulation 实验：choice 型模板对 simple/complex 有区分力（light 3/4 高置信），但 full-01 被误判 simple 0.89 —— 单探针存在**位置偏差与过度自信**。

结论：**瓶颈不在运行时（路由机制、台账聚合、安全侧回退均正确），而在「单探针、单顺序、固定阈值」的判定方式。**

### 1.2 对标最先进项目的核心思想（docs/research/github-survey.md）

| 来源 | 核心思想 | 对本 AR 的直接价值 |
|---|---|---|
| OpenJev | 候选隔离评分、**100% 顺序不变**；"explore first, crystallize later" | A2 顺序不变性（批内置换平均）；C 飞轮深化 |
| AnyJev | 任意 LLM 免训练读出；**Top-2 概率差 < τ 升级为慢思考**，CoT 占比硬封顶 0.2 | B1 margin 升级信号 + 预算封顶 |
| von | **0.80 阈值保留 25% 样本、92.4% 精度**；Noul 无 criteria 是最弱路径 | A3 阈值扫描校准；A1 探针必须带 criteria |
| DuoMind | 18 决策点 PRE/MID/POST；熔断降级 | AR001 已继承，本 AR 深化路由 |
| hermes-jev-skills | shadow mode（决定并记录不切换）；"小脑做选择，不做创作" | B3 shadow 学习式路由；全文口径 |
| RouteLLM / FrugalGPT | 学习型路由、级联预算 | B2 代价感知预算；B3 学习式 shadow 路由 |
| system1-agents | 同分数下最高 6× 快、25× 便宜 | D1 多基线的量化口径 |
| kev / laya | prefill-only 类型化决策（Choice/Noul/Score）；温度校准内置 | A1/A2 批内多问的技术基础（单前向多问） |

### 1.3 目标

在**真实后端、零 mock** 的前提下，把级联命中率从 0% 提升到可用水平（目标 ≥60%，95% CI 显著优于基线），同时以「多基线 + 显著性」证明双系统相对直连大脑的系统级收益，并交付高算力服务器可复现的完整实验体系。

## 2. 需求范围

**In Scope：**
- 小脑判定质量：多探针并行、顺序不变性（置换平均）、路由专用校准（温度/阈值/margin）
- 级联智能路由：Top-2 margin 升级、请求级代价预算、PRE 结论草稿注入（真实压缩）、慢响应超时可靠性
- 学习式路由（shadow）：基于真实台账特征训练轻量判别器，shadow 记录与规则路由的对比
- 蒸馏飞轮深化：难例主动采样、对比式校准样本、`jev distill --flywheel` 一键闭环
- 双系统评测：多基线对比、成对 bootstrap 显著性、环境指纹、结构化报告
- 复现工程：README 重写、docs/REPRODUCTION.md、实验矩阵配置、Bash/PowerShell 一键脚本、推送方法论与工具入库
- 大模型实验：kev 0.8B/4B/9B 的蒸馏/LoRA 训练与评测配置（本机 CPU 小规模验证 + 高算力 GPU 服务器复现路径）

**Out of Scope：**
- MNN/车机适配、中文小脑重训练（换底座）、端云变体（云大脑）、多模态小脑
- 自研推理引擎（继续复用 ONNX Runtime / llama.cpp / kev）
- 修改 `/v1/systemone` 与 `/v1/chat/completions` 的既有线协议（只增不改；新增能力通过配置与内部模块实现）

## 3. 功能需求

### 3.1 FR1 小脑判定质量工程

#### FR1-1 多探针并行决策

**描述：** 单个决策点可声明 K≥1 种探针问法（choice 直问 / 正反 Noul（带 criteria）/ 成对比较），同一 state 下以一次 systemone 请求（单次编码、多问）并行评估，按校准权重融合为最终决策与置信度。

**触发条件：** 决策点配置 `probes = [...]`（TOML）；未配置时保持单探针既有行为（向后兼容）。

**期望行为：** 探针集合一次性批量提交（不增加后端往返次数）；融合值写入台账（`probes` 字段记录各探针原始分布），路由使用融合置信度。

**异常处理：** 任一探针失败/缺失 → 该点降级为可用探针子集；全部失败 → 按该点 fallback_action（默认安全侧）。

**验收标准：**
- Given 配置 K=3 探针的 intent 点, When 一次 chat 请求, Then 后端仅收到 1 次 systemone 请求且台账记录 3 个探针分布
- Given 同一 state 的扰动集, When 单探针 vs 多探针, Then 多探针的决策翻转率下降 ≥30%

#### FR1-2 顺序不变性（置换平均）

**描述：** choice 型探针支持 `permutations = N`（默认 4）：选项顺序以固定种子置换 N 次，作为 N 个同构问题在**一次前向**内评估，概率按选项名归并平均。

**触发条件：** 探针或决策点配置 permutations ≥2。

**期望行为：** 输出顺序无关的归并概率；bench 报告 `order_flip_rate`（同一题不同置换下 argmax 不一致比例）。

**异常处理：** N 超上限（>8）→ 配置校验拒绝（JevAssetError/配置错误）。

**验收标准：**
- Given 基准集 choice 型题, When permutations=4, Then `order_flip_rate` 相比单顺序下降 ≥50%
- Given 固定种子, When 重复执行, Then 结果逐位一致（可复现）

#### FR1-3 路由专用校准

**描述：** 在 distill 校准基础上增加**路由口径**：对每个决策点拟合温度（复用现有 calibrate），并在基准集上扫描路由阈值 τ 与 margin，选择满足「精度保持 ≥ 直连大脑基线 - 2pt」前提下命中率最大的配置；产物版本化落盘并由 `[cerebellum.laya_onnx] calibration_path` 加载。

**触发条件：** `jev distill --calibrate --routing`（或 flywheel 内部调用）。

**期望行为：** 产出校准文件（温度 + 逐点 τ + margin），加载后 bench 使用新阈值；报告记录扫描曲线与选中点。

**异常处理：** 无足够样本（<min_samples）→ 显式报错，不静默降级；校准文件非法 → 拒载（沿用 AR001 行为）。

**验收标准：**
- Given 基准集与直连大脑基线, When 校准阈值扫描, Then 选中配置的命中率 > 固定 τ=0.60 基线且精度保持 ≥ -2pt
- Given 校准产物, When 服务加载, Then 台账记录实际生效的温度/阈值版本

### 3.2 FR2 级联智能路由

#### FR2-1 Top-2 margin 升级信号

**描述：** 路由决策除绝对置信度外引入 margin = p1 - p2；`margin < τ_margin` 时升级（对齐 AnyJev adaptive）。可配置启用/禁用，默认启用（τ_margin 由 FR1-3 校准产出）。

**验收标准：**
- Given 置信度高但 margin 小的对抗样本, When 路由, Then 升级 full（不被过度自信误导）
- Given 关闭 margin（配置兼容）, When 请求, Then 行为与 AR001 一致（回归不变）

#### FR2-2 请求级代价预算

**描述：** 引入请求级预算 `max_brain_tokens` 与 `max_latency_ms`（配置/请求头可选覆盖）：PRE 估计预期成本，超预算时收敛到 light/direct 或触发截断；台账记录预算决策（`budget` 字段）。对齐 FrugalGPT 的级联预算思想，但保持确定性可解释。

**异常处理：** 预算解析失败 → 使用默认预算并记 warning（不阻断请求）。

**验收标准：**
- Given 小预算配置, When 复杂请求, Then 不升级 full（或 full 被截断）且请求仍 200
- Given 默认预算, When 请求, Then 行为与基线一致（回归不变）

#### FR2-3 PRE 结论草稿注入（真实压缩）

**描述：** 将小脑 PRE 结构化结论（intent/complexity/needs_reasoning/ambiguity 等 + 可选计划提示）压缩为一段 system 前缀注入大脑，替代 AR001 的 messages 原样透传（MVP 裁剪项补全）。

**验收标准：**
- Given full 路由请求, When 注入开启, Then 发给大脑的 prompt token 数相比原样透传下降 ≥15%
- Given 同一题集, When 注入开/关, Then 精度保持 ≥ -2pt（bench 成对比较）

#### FR2-4 大脑慢响应可靠性

**描述：** 修复 AR001 ST 观察到的竞态：大脑 HTTP 超时预算与级联 generation deadline 统一口径（HTTP timeout < deadline 且留 buffer），超时分类明确（可重试/不可重试），并补充真实慢响应路径测试。

**验收标准：**
- Given 真实 HTTP 对端延迟低于 deadline 但高于当前 HTTP 超时, When chat, Then 请求成功（不再误 503）
- Given 大脑超过 deadline, When chat, Then 降级交付（截断+POST 复核）而非裸 503

#### FR2-5 学习式路由（shadow）

**描述：** 从真实台账抽取特征（各决策点融合值/置信度/margin、state 长度、问题类型）与标签（该请求 full 路由后 POST 复核是否通过 / 直答是否足够），训练轻量线性判别器（numpy 自实现逻辑回归，无新增依赖），以 **shadow 模式**记录其路由建议与规则路由的差异与假阳性/假阴性，不切换控制流。

**验收标准：**
- Given 足量台账（≥200 请求）, When 训练 shadow 路由器, Then 输出 shadow 报告（准确率/AUC/与规则一致性）
- Given shadow 开启, When 请求, Then 控制流不变（台账记录 mode=shadow）

### 3.3 FR3 蒸馏飞轮深化

#### FR3-1 难例主动采样

**描述：** 数据集构建支持优先采样：低置信（<τ）、needs_retry、fallback、预算截断样本按权重优先纳入，并输出采样统计报告（对齐 OpenJev explore-first）。

**验收标准：**
- Given 含难例的台账, When build_dataset(priority=True), Then 难例占比显著高于均匀采样（报告可断言）
- Given 关闭优先采样, When 构建, Then 与 AR001 行为一致

#### FR3-2 对比式校准样本

**描述：** 将 POST 复核通过的正样本与 needs_retry/复核失败负样本配对，产出偏好对数据集（JSONL schema 版本化），供校准/训练使用（本轮先打通数据与报告，训练损失侧复用 kev.train 现有能力）。

**验收标准：**
- Given 含正负样本的台账, When 构建对比集, Then 产出合法配对（同一决策点、同 state）且报告配对数量与分布

#### FR3-3 一键飞轮闭环

**描述：** `jev distill --flywheel`：采集→数据集（含难例）→校准（含路由口径）→bench 复测（多基线）→门判定→accepted 发布 / rejected 保持旧参数；退出码反映门结果；产物全部版本化。

**验收标准：**
- Given 足量轨迹, When flywheel, Then 一条命令完成闭环并产出报告；门通过则发布、否则拒绝且服务参数不变
- Given 空台账, When flywheel, Then 显式失败（非零退出码 + 诊断）

### 3.4 FR4 双系统评测体系

#### FR4-1 多基线对比

**描述：** bench 支持 `baselines = [brain_direct, cerebellum_only, cascade]`：直连大脑（无级联）、纯小脑（全直答，不经大脑）、级联；统一计算命中率、精度保持（同一把尺子：POST 复核）、P50/P95、token 成本、顺序不变性、ECE。

**验收标准：**
- Given 全量基准集, When bench --baselines all, Then 报告含三条基线的全部指标与相对收益（延迟比/成本比/精度差）

#### FR4-2 显著性与可复现

**描述：** 成对 bootstrap（≥2000 重采样，固定种子）给出命中率/精度的 95% CI；报告写入环境指纹（Python/torch/onnxruntime 版本、GPU、模型 sha256、配置 hash）。

**验收标准：**
- Given 固定种子与配置, When 重复 bench, Then 指标与 CI 逐位一致（除时间戳外）
- Given 级联 vs 直连大脑, When 报告, Then 含命中率与精度的 CI 及差值 CI

#### FR4-3 结构化报告

**描述：** 报告输出 JSON + Markdown（含对比汇总表、逐题明细、门判定、失败样本清单），路径版本化。

### 3.5 FR5 复现工程与交付

#### FR5-1 交付文档

**描述：** 重写 README（定位/架构/创新点/快速开始/实验矩阵/目录结构/指标口径）；新增 `docs/REPRODUCTION.md`（本机 Windows 路径 + 高算力 Linux/GPU 服务器路径：Python/uv、资产获取（含内网镜像与代理）、CUDA 版 torch、kev 环境、4B/9B 训练配置与显存/耗时预估、常见问题）。

**验收标准：**
- Given 全新克隆, When 按 REPRODUCTION.md 执行, Then 能完成 doctor + 单元测试 + 无 GPU 的 bench 子集；服务器章节给出可执行命令与资源预估

#### FR5-2 实验矩阵配置与脚本

**描述：** `configs/experiments/*.yaml` 定义实验矩阵（模型规模 0.8B/4B/9B、baseline 组合、探针/置换/预算开关、种子）；`scripts/run_experiment.(sh|ps1)` 一键执行并归档结果。

**验收标准：**
- Given 实验配置, When 脚本执行, Then 产出统一命名的结果目录（报告 + 日志 + 环境指纹）

#### FR5-3 推送方法论与工具

**描述：** 将企业代理环境下 Gitee 推送方法论（脱敏，无凭据）与最小包工具入库（`docs/GITEE_SYNC_METHODOLOGY.md`、`tools/push_min_pack.py`），并在 AGENTS.md 增设推送约束条目。

#### FR5-4 质量门脚本

**描述：** `scripts/ci.ps1` / `scripts/ci.sh` 升级为一键质量门（ruff + 单元测试 + 覆盖率门 + 可选 assets 标记）。

**验收标准：**
- Given 干净环境, When CI 脚本, Then 全绿（ruff 零告警、unit 全过、kernel 覆盖率 ≥85%）

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 质量 | 级联命中率（真实 laya + 双语基准集） | ≥60%，且 95% CI 下限高于 AR001 基线（0%）；分阶段门：A 完成后 ≥40% |
| 质量 | 精度保持（vs 直连大脑基线） | ≥ -2pt（成对可复核样本） |
| 稳定性 | 顺序不变性 | `order_flip_rate` 相比单顺序下降 ≥50% |
| 性能 | 决策开销（多探针+置换） | PRE 阶段 P50 增量 ≤150ms（laya CPU EP） |
| 性能 | 直答/轻路径延迟 | P50 ≤ 大脑路径 P50 × 1/3（有直答样本时） |
| 成本 | 大脑 token | 级联平均 brain tokens 相比直连大脑下降 ≥40%；full 路径 prompt 压缩 ≥15% |
| 可靠性 | 无 mock | 全部后端为真实资产；慢响应/超时/熔断路径有真实对端测试 |
| 可靠性 | 回归 | 既有 396 单元测试保持全绿；kernel 语句覆盖 ≥85% |
| 可复现 | 一键复现 | 全新克隆 → uv sync → fetch → doctor → bench 子集 全链路可跑通，固定种子结果一致 |

## 5. 约束与假设

**约束：**
- C1 全量真实后端，禁止任何 mock/桩模型（真实 HTTP 对端、真实子进程、真实 ONNX/GGUF 属真实实现，不属 mock）
- C2 依赖方向不变：薄壳 → kernel → 子模块
- C3 `/v1/systemone` 与 `/v1/chat/completions` 线协议只增不改（新增字段/头向后兼容）
- C4 模型资产不入仓；新增依赖需评估必要性（优先 numpy/标准库自实现）
- C5 企业代理约束：单次 git push ≤~60KB 包（方法论 L1-L4），图片/大文件需减重或分片
- C6 本机无 CUDA 版 torch（内网镜像仅 Linux 轮）：4B/9B 训练只交付配置+脚本+CPU 小规模验证

**假设：**
- laya 资产对「带 criteria 的 choice 探针」有可用的区分度（AR001 ST 已实测 3/4 高置信）
- 批内多问（单次编码）能力可承载 N 个置换探针而不显著增加延迟
- kev-0.8B 可在 CPU 完成小规模 LoRA 验证；4B/9B 由高算力服务器承接

## 6. 术语说明

| 术语 | 定义 |
|------|------|
| 探针（probe） | 针对同一决策点的一种具体问法（choice/noul/成对比较），带独立选项与 criteria |
| 融合（fusion） | 多探针分布按校准权重合并为单一分布与置信度 |
| margin | 选项分布中 top-1 与 top-2 的概率差，作为升级信号 |
| 顺序不变性 | 选项顺序置换后决策结果不变的性质；度量指标 `order_flip_rate` |
| 代价预算 | 每请求允许的大脑 token/延迟上限，用于约束升级 |
| 飞轮（flywheel） | 台账→数据集→校准→复测→发布/拒绝 的自进化闭环 |
| 基线（baseline） | 直连大脑（无级联）/ 纯小脑（全直答）/ 级联，三者为对照 |
