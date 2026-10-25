# [AR003] 需求设计说明书：运行时智能（验证器路由 / KV 复用 / 稀疏 prefill / 语义记忆）

| 字段 | 内容 |
|------|------|
| AR 编号 | AR003 |
| AR 主题 | runtime-intelligence |
| 关联 SR | 端侧快慢双系统（Jev）核心创新：判定质量 + 智能路由 + 成本 |
| 日期 | 2026-10-25 |
| 状态 | Draft |
| 前置 AR | AR001（归档）、AR002（部分 passing，剩余任务移交本 AR） |

## 1. 背景与目标

AR002 已把「多探针判定 + 能力门控路由」从 0% 命中率提升到真实资产 80%（40 题，仅小脑判定），但存在三个本质缺陷：

1. **降级不可验证**：7/20 复杂题被误降级到 light，缺乏「验证后升级」机制，精度只能靠事后 POST 复核；
2. **重复计算**：多轮对话与相似请求每次全量重算 prompt/KV，TTFT 高；无前缀复用；
3. **无记忆**：无跨请求语义缓存与长程记忆，成本和延迟无法进一步下降。

本 AR 的目标是**在不引入任何 mock 的前提下**，把级联从「静态规则路由」升级为「可验证、可复用、可记忆」的运行时：

- **V1 验证器驱动路由**（AutoMix/SwiftSage 思想）：不确定性分带，中带用真实大脑验证，预算感知升级；
- **V2 前缀/KV 复用**：前缀指纹 + llama-server 原生 `cache_prompt`/`--slot-prompt-similarity` 槽复用，真实 TTFT 下降；
- **V3 稀疏 prefill 协同**：确定性块显著性规划（词法 + 决策信号 + 位置先验），预算化 prompt 压缩；输出与 EdgePrefill（C++ 稀疏 prefill）协同的服务器实验配置；
- **V4 语义缓存 + 长程记忆**：真实 embedding 服务（独立 llama-server `--embeddings`）+ 精确/语义 LRU + 结构化长程摘要。

**成功标准**：真实资产上，命中率相对 AR002 能力门控基线（B1，同集实测 80%）下降不超过 5pt（即 **≥75%**，且绝对 ≥60%）、精度保持 ≥B2-2pt、TTFT 降 ≥30%、brain token 比 B0 降 ≥40% 且比 B1 再降 ≥15%、语义缓存总命中 ≥40% 且同义改写命中 ≥20%、学习与规则双路 CI 下限 >0；全部数字来自真实模型与真实服务，报告中附环境指纹与命令。

## 2. 需求范围

**In Scope：**
- `kernel/cascade/`：不确定性量化、验证策略、三带路由、预算共享
- `kernel/cerebrum/`：embedding 客户端、验证客户端、槽/前缀复用请求参数
- `kernel/cache/`（新）：语义缓存与长程记忆
- `kernel/prefill/`（新）：块分割、显著性、预算选择
- `server/chat.py`：集成点、响应头、台账字段
- `configs/`：新配置项（默认关闭，保持 AR002 行为）
- `bench/`：TTFT/缓存/验证指标与门控
- `scripts/` + `docs/`：EdgePrefill 协同与服务器实验配置（不要求本机执行 C++ 侧）

**Out of Scope：**
- 训练新小脑权重本体（归 AR004；本 AR 仅使用真实推理与真实特征）
- 修改 `/v1/systemone` 与 `/v1/chat/completions` 的既有字段语义（只增不改）
- MNN/车机移植、多模态、中文小脑重训
- 在本机编译/运行 EdgePrefill C++（Windows 环境；交付配置与脚本，服务器执行）

## 3. 功能需求

### 3.1 FR1 验证器驱动级联路由

#### FR1-1 不确定性量化
**描述：** 把每个决策点的置信度、margin、跨探针置换一致性合成为请求级不确定性 `U∈[0,1]`。

**触发条件：** PRE 批评估完成后。
**期望行为：** 采用**归一化凸组合**（权重和恒为 1，`U∈[0,1]`）：

```
U = w_c·(1 - conf) + w_m·(1 - margin) + w_p·disagreement + w_x·(1 - self_agree)
w_c=0.40, w_m=0.25, w_p=0.20, w_x=0.15   # Σw = 1；除 self_agree 外，缺省项按剩余权重归一再分配
```

- `conf`：请求内所有 ACTIVE choice/score 点置信度的几何平均；`margin`：对应 margin 均值（0–1）；
- `disagreement`：跨探针置换的标签分布平均 JS 散度（0–1）；单探针/置换=1 → 0；
- `self_agree`：小脑自证一致率（见 FR1-2）。**口径统一：无自证结果时置 `self_agree=1`（不升高 U）且保留其权重，不做权重重分配。**

**异常处理：** 探针缺失/单探针 → `disagreement=0` 且记录 `probe_agreement=None`；不阻断路由。
**验收标准：**
- Given 同一决策批，When 重复计算，Then `U` 逐位一致（确定性），且 `0 ≤ U ≤ 1`。
- Given 其余不变仅置信度下降，When 重算，Then `U` 单调不减。
- Given 全最优决策（conf=1, margin=1, disagreement=0, self_agree=1），Then `U=0`；全最差，Then `U=1`。
- Given 自证结果缺失（`self_verify=None`），When 计算 U，Then 使用 `self_agree=1` 且结果与「自证全一致」逐位相同（一致性单测）。

#### FR1-2 小脑自证信号 + 三带策略
**描述：** 先由小脑自身信号做「自证」（不增加任何后端调用），再按不确定性划分三带。

**自证信号（默认开启计算、默认不改变路由，`self_verify_mode=observe`）：**
- 置换一致性：同一探针 N 个选项顺序下，融合后 top-1 标签一致的比例（复用 AR002 置换产物，不重复前向）；
- 交叉一致性：同一语义点在多探针（如 intent 的 handling/binary）上的 applied 值一致比例；
- 输出 `self_agree∈[0,1]`，供 FR1-1 的 U 与验证触发使用；`self_verify_mode=gate` 时才允许阻断（与 AR002 默认行为不同，须显式开启）。

**三带策略：**
- confident（U ≤ τ_low）：沿用 AR002 能力门控路由（direct/light 直出）；
- borderline（τ_low < U < τ_high）：先走 cheap 路径生成草稿，再由大脑验证器裁决（FR1-3）；
- uncertain（U ≥ τ_high）：直接 full（不浪费验证成本）。
- 默认 `τ_low=0.35, τ_high=0.65`，可配；默认 `verifier_enabled=false`（保持 AR002 行为）。

**异常处理：** 自证数据缺失 → `self_agree=1`（不额外升高 U），并记录 `self_verify=None`。
**验收标准：**
- Given 三带阈值配置，When U 落在各带，Then 路由与验证调用次数符合预期（0/1/0）。
- Given `safety=unsafe`，When 任意 U，Then 必为 full 且不调用验证器。
- Given `self_verify_mode=observe`（默认），When 自证一致率低，Then 路由与 AR002 完全一致（回归）。
- Given 同一请求，When 置换顺序改变，Then `self_agree` 变化 ≤ 0.1（顺序鲁棒性）。

#### FR1-3 大脑验证器（真实调用）
**描述：** 对 borderline 的草稿做结构化验证，使用真实 llama-server 调用（不是自评分数造假）。

**输入：** 原始 user 请求、草稿答案、PRE 结构化结论（applied 值）。
**期望行为：** 发单一验证提示（要求输出 `ACCEPT`/`REJECT` + 一句理由，严格解析）；解析失败 → 视为 REJECT（安全侧）；token 计入台账 `verify_tokens`。
**异常处理：** 超时/协议错误 → 视为 REJECT 并升级 full，不重试。
**验收标准：**
- Given 真实大脑，When 验证提示返回 ACCEPT，Then 交付草稿且 `X-Jev-Route` 保持 cheap 路径；REJECT → full。
- Given 返回非结构化文本，Then 判 REJECT（不猜测）。

#### FR1-4 预算感知验证与升级
**描述：** 验证调用与 full 生成共享请求预算；预算不足时优先保证生成。

**期望行为：** `验证预估 tokens + full 预估 > budget` → 跳过验证直接 full（若 full 也超预算 → light 截断）；`X-Jev-Budget`/台账记录 `verify_skipped_reason`。
**验收标准：**
- Given 紧预算，When borderline，Then 不调用验证器且路由符合预算策略。
- Given 宽预算，When borderline，Then 调用一次验证器（恰好一次）。

### 3.2 FR2 前缀 / KV 复用

#### FR2-1 前缀指纹与 cache_prompt
**描述：** 对「system 前缀 + 历史消息」计算稳定指纹，请求中启用 `cache_prompt`，并统计服务端缓存命中。

**期望行为：** 指纹 = sha256(规范化 system + 历史 roles/contents)；相同指纹的连续请求只计一次前缀计算；台账记 `prefix_fingerprint`、`prefix_reused`、`prompt_tokens`、`ttft_ms`。**复用证据优先取 llama-server `/slots` 的 `n_past`/cache 指标或服务端日志；仅在不可得时退化为 TTFT+prompt_tokens 代理，并在台账标注 `evidence=proxy`。**
**异常处理：** 服务端不支持 `cache_prompt` → 请求忽略该字段，功能降级为「仅观测」。
**验收标准：**
- Given 相同前缀请求重复 ≥5 次，When 取 P50，Then 第 2–5 次 TTFT P50 比冷启动 P50 降 ≥30%（真实计时；证据非 proxy 或已标注）。
- Given 不同前缀，Then `prefix_reused=false`。

#### FR2-2 会话槽复用
**描述：** 同一会话（由 `X-Jev-Session` 或消息前缀指纹标识）尽量复用 llama-server 槽。

**期望行为：** 启动参数开启 `--parallel N` 与 `--slot-prompt-similarity`；客户端稳定发送相同前缀；统计槽命中与 TTFT。
**验收标准：**
- Given 5 轮同会话真实对话，When 第 2–5 轮，Then P50 TTFT 相比冷启动降 ≥30%，且 POST 复核通过率不降。

#### FR2-3 复用失效与安全
**描述：** 会话切换、前缀改写、缓存过期时不得串话。

**期望行为：** 指纹不匹配则强制新前缀；不得把 A 会话内容当作 B 会话上下文；台账可追溯。
**验收标准：**
- Given A/B 两会话交替请求，Then 各自答案不含对方内容（真实模型输出断言关键词不出现）。

### 3.3 FR3 稀疏 prefill 协同

#### FR3-1 块分割与显著性
**描述：** 把长 prompt 切成块（按段落/句子/长度上限），用确定性特征打分：词法重叠（与当前问题）、决策信号（PRE applied 值相关块）、位置先验（头尾保留）。

**期望行为：** 输出每块分数与选择结果；同输入同配置逐位一致。
**验收标准：**
- Given 固定输入与 seed，When 运行两次，Then 选择集合完全一致。
- Given 预算比例 0.5 且块数 ≥4，Then 选中块 token 数 ≤ 50%±5% 且首尾块必选。
- Given 块数 <4，Then 不压缩（`prefill_skipped=few_blocks`），避免首尾必选与预算冲突。

#### FR3-2 预算化压缩接入
**描述：** full/light 路径在启用时以压缩后的 prompt 调用大脑，原始完整 prompt 仅用于 POST 复核。

**基线定义：** 「压缩前」= 同一请求未启用 prefill 压缩时实际发送给大脑的 messages（即 AR002 行为，含其草稿注入若开启）。

**期望行为：** 单轮长 prompt（≥512 prompt tokens）压缩后 `prompt_tokens` 降 ≥15%；多轮（≥5 轮）降 ≥30%；短 prompt（<512 tokens）不压缩并记 `prefill_skipped=short`；压缩决策写入台账（selected/total blocks、压缩前后 tokens）。
**验收标准：**
- Given 多轮长上下文（≥5 轮、≥1024 tokens），When 启用压缩，Then 真实 `usage.prompt_tokens` 相比同请求「压缩前」降 ≥30%。
- Given 单轮长 prompt（512–2048 tokens），Then 降 ≥15%。
- Given 单轮短 prompt（<512 tokens），Then 不压缩（`prefill_skipped=short`）且输出与未启用逐字一致（确定性）。

#### FR3-3 质量保持
**描述：** 压缩不得显著损伤答案质量。

**期望行为：** 在真实「关键信息问答」小集（自建 needles，真实模型）上，压缩后**失败条数不得增加**（成对口径；比“通过率不降超 2pt”更严，按此验收）。
**验收标准：**
- Given needles 集（≥40 条真实问题，含单跳/多跳/否定/数字四类），When 压缩 vs 不压缩（成对），Then `压缩后失败条数 ≤ 未压缩失败条数`（不新增失败），并报告两项通过率的二项分布 95% CI。

#### FR3-4 EdgePrefill 协同配置
**描述：** 提供与 EdgePrefill（C++ 稀疏 prefill）联动的服务器实验配置与脚本，明确职责边界：Python 规划层负责块选择/预算（本 AR T008/T009），C++ 执行层负责稀疏 attention/FFN（EdgePrefill AR001–AR004）。

**触发条件：** 在具备 Linux/WSL2 + llama.cpp 构建环境的服务器上执行。
**期望行为：** 交付 `configs/experiments/prefill-*.yaml`、`scripts/run_prefill_experiment.sh`、`docs/REPRODUCTION.md` 片段；本机以「命令拼装 + 配置 schema 校验」验证，服务器按文档产出同口径 TTFT/PPL。
**异常处理：** 服务器环境缺失 → 脚本以明确错误码退出，不静默跳过。
**验收标准：**
- Given 本机，When 运行配置校验与命令拼装测试，Then 生成命令包含正确模型路径/预算参数，且 schema 校验通过。
- Given 服务器（延后验证，AR003 交付时标注 `deferred-server`），When 按文档执行，Then 产出 TTFT 与 PPL 报告并写入 `benchmarks/results/`。

### 3.4 FR4 语义缓存与长程记忆

#### FR4-1 真实 embedding 服务
**描述：** 独立 llama-server `--embeddings` 实例（同一 GGUF，pooling=mean）提供真实向量；不可用则缓存禁用。

**期望行为：** 启动探测 `/v1/embeddings`；失败 → 缓存功能 `disabled` 且日志告警；**禁止**用哈希/随机向量冒充语义。
**验收标准：**
- Given 未配置 embedding 服务，When 启动，Then 缓存状态为 disabled 且不影响主路径。
- Given 配置正确，When 调用，Then 返回维度一致的真实向量。

#### FR4-2 精确 + 语义缓存
**描述：** 两级缓存：精确哈希（规范化消息）+ 语义近似（余弦 ≥ 阈值），LRU + TTL；仅缓存 direct/light 答案。

**期望行为：** 命中 → 直接返回缓存答案（附 `X-Jev-Cache: hit-exact|hit-semantic`），台账记相似度；未命中 → 正常流程后按策略写入。语义层验收前提：FR4-1 的 embedding 服务状态为 `ready`（禁用态只验精确层，语义门记 `not_applicable`）。
**验收标准：**
- Given 重复/复述集（≥20 条：10 条逐字重复 + 10 条同义改写），Then 总命中率 ≥40%，其中逐字重复命中率 100%、同义改写命中率 ≥20%（≥2/10）。
- Given 负例集（≥10 条：同实体不同意图/否定翻转/数字变更），Then 误命中率 ≤10%。
- Given full 路由，Then 不写入缓存。

#### FR4-3 长程记忆
**描述：** 超期轮次压缩为结构化摘要（保留决策结论与用户约束），最近 `keep_turns` 轮原文；摘要可被检索。

**期望行为：** 多轮对话中 token 随轮数近似线性而非平方增长；台账记 `memory_summary_tokens`。
**基线与测量：** 固定 10 轮脚本对话（每轮 user+assistant，单轮内容 80–120 字），记录每轮真实 `prompt_tokens`；「单轮增量」= 第 2 轮 prompt_tokens − 第 1 轮。
**基线定义：** 「记忆关闭」= 同脚本、同内容、关闭长程记忆（仅 keep_turns 原文）时的逐轮 `prompt_tokens`。

**验收标准：**
- Given 10 轮真实对话，When 启用长程记忆，Then 第 5–10 轮每轮 prompt_tokens 增量 ≤ 单轮增量 × 1.2（无平方增长），且第 10 轮 prompt_tokens 比「记忆关闭」基线低 ≥15%。
- Given 摘要后提问第 2 轮的事实，Then 答案包含该事实关键词（真实模型输出断言）。

## 4. 非功能需求

**基线口径（唯一，避免歧义）：**
- B0「全 full 直连大脑」：所有请求都走 full（当前 AR002 代码关闭能力门控时的 full 比例）；
- B1「AR002 能力门控」：同资产、同 40 题双语集、仅小脑判定（实测命中 80%，作为回归基线）；
- B2「AR002 完整级联」：含真实大脑生成与 POST 复核（精度对照）。

| 类型 | 指标 | 要求 | 基线 |
|------|------|------|------|
| 质量 | 命中率（direct+light） | 绝对门 ≥60%，且 ≥ B1 − 5pt（即 ≥75%） | B1=80%（同集同资产） |
| 质量 | 精度保持 | ≥ B2 − 2pt（成对样本，真实 POST 复核） | B2 |
| 延迟 | TTFT（前缀/槽复用） | 第 2–5 轮 P50 比冷启动降 ≥30% | 冷启动同请求 |
| 延迟 | 直答 P50 | ≤ 同集大脑 P50 × 1/3 | B0 |
| 成本 | brain token（验证+生成合计） | 比 B0 降 ≥40%；且比 B1 再降 ≥15% | B0=全部请求 full 的总 token；B1=同集能力门控路由下实际发生的大脑生成 token（full 与 light 段，不含 POST 验证，验证 token 单列） |
| 延迟 | PRE 判定增量 | P50 ≤150ms 且 ≤ B1 同集 P50 ×1.2（两者同时满足） | B1：T013 在冻结提交 `e19a074` 上用同集采集（字段 `pre_p50_ms`，命令写入报告） |
| 可靠性 | 验证器/缓存故障 | 一律安全侧降级（full / disabled），请求成功率不降 | AR002 |
| 可观测 | 台账新增字段 | 见下方 schema，齐全率 100% | — |
| 覆盖 | 单测 | 新增模块语句覆盖 ≥85%（kernel 目标不变） | — |
| 复现 | 确定性 | 固定 seed 下压缩/缓存键/不确定性逐位一致 | — |

**台账字段 schema（NFR 可观测判定基准，只增不改）：**

```json
{
  "uncertainty": {"u": 0.0, "band": "confident", "self_agree": 1.0, "probe_agreement": 1.0},
  "self_verify": {"consistency": 1.0, "cross_point": 1.0, "mode": "observe"},
  "verifier": {"called": false, "verdict": null, "tokens": 0, "reason": null, "skipped_reason": null, "unavailable": false},
  "prefix": {"fingerprint": "sha256:...", "reused": true, "evidence": "slots|proxy"},
  "prefill": {"selected": 0, "total": 0, "before_tokens": 0, "after_tokens": 0, "skipped": null},
  "cache": {"layer": "none|exact|semantic", "similarity": null},
  "memory": {"summary_tokens": 0}
}
```



## 5. 约束与假设

**与 AR002 已交付能力的差异 / 增量（避免重复与行为漂移）：**

| 能力 | AR002 已交付 | AR003 增量 | 默认行为 |
|------|-------------|-----------|---------|
| 预算 | T004 请求级预算 + `X-Jev-Budget` | 验证调用与 full 共享预算（FR1-4） | 预算默认关闭；开启后验证计入同一预算 |
| 草稿/压缩 | T005 system 前缀 + 截断式历史压缩 | prefill 规划器（显著性选择，而非逐条截断）+ 真实 token 计量 | 均默认关闭 |
| 置换 | T001 多探针/置换融合 | 自证信号（置换一致性/交叉一致性） | 自证默认 observe，不改路由 |
| 路由 | T002 能力门控（capability_gate） | 三带 + 验证器（默认关闭） | 关闭时与 AR002 逐例一致（回归用例保障） |
| 台账 | 决策/路由/答案 | 验证/缓存/前缀/prefill/记忆字段 | 只增不改，旧字段语义不变 |

**延后范围与承接（AR004）：** 飞轮闭环（采集→难例/对比集→numpy 真训练→门控发布）、多基线报告升级、7B/14B 大模型实验矩阵、README/REPRODUCTION/CI 门、`jev distill --flywheel` CLI。AR004 需求骨架已建（`specs/changes/AR004-learning-reproduction/srs.md`）。

**约束：**
- **零 mock**：真实 laya ONNX、真实 llama-server、真实 HTTP/子进程；测试用真实微型契约 ONNX 或真实本地服务；严禁伪造后端响应与计时。
- 依赖方向：`server/sdk/cli/mcp_server → kernel → 子模块`；薄壳不直连后端。
- 线协议只增不改；默认行为保持 AR002（除显式开启的验证/缓存/压缩功能）。
- 不新增重型依赖（numpy 已在；embedding/验证均走 HTTP）。
- 模型资产不入仓；服务器实验配置与脚本入仓（脱敏）。

**假设：**
- llama-server build 11829 支持 `cache_prompt`、`--slot-prompt-similarity`、`--embeddings`（已在 help 中确认存在；请求字段在 T006 用真实调用验证）。
- 本机 RTX 5000 16GB 可跑 1.5B Q4（已验证）；7B/14B 实验归 AR004。
- 企业代理限制不影响本地回环 HTTP。

**参考（SOTA 依据）：**
- AutoMix: *Automix: A cascade framework for cost-effective LLM inference*（arXiv:2310.12963）
- FrugalGPT: *How to Use Large Language Models While Reducing Cost and Improving Performance*（arXiv:2305.05176）
- RouteLLM: *Learning to Route LLMs with Preference Data*（arXiv:2406.18665）
- SwiftSage（NeurIPS 2023）：快慢双系统 Agent 协同
- EdgePrefill（本机外部项目）：llama.cpp 稀疏 prefill（块选择/FFN skip/attention top-k），本 AR FR3-4 协同对象
- 现有调研：`docs/research/github-survey.md`（DuoMind/OpenJev/AnyJev/von 等）

## 6. 术语说明

| 术语 | 定义 |
|------|------|
| 不确定性 U | 置信度/margin/探针一致性合成的 [0,1] 标量，越高越不确定 |
| 三带 | confident / borderline / uncertain 路由分区 |
| 验证器 | 用真实大脑对草稿做 ACCEPT/REJECT 裁决的组件 |
| 前缀指纹 | system+历史的稳定哈希，用于 KV 复用与缓存键 |
| 槽复用 | llama-server 的 slot/KV cache 复用机制 |
| 稀疏 prefill | 只把高显著块送入 KV 的 prefill 加速（Python 规划 + C++ 执行） |
| 语义缓存 | 基于真实 embedding 余弦相似度的答案缓存 |
| 长程记忆 | 超期轮次的结构化摘要与检索 |
