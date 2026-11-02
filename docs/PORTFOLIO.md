# AR 组合与最优配置（Portfolio）

> 原则：**不追求把所有能力叠加**。不同负载的最优组合不同；无正收益即回退（选择器 adopt/rollback）。
> 所有数字来自 `benchmarks/results/` 的真实后端运行，详见 `docs/RESULTS.md`。

## 1. AR 能力地图

| AR | 能力 | 主要收益 | 主要代价/风险 | 默认 |
| --- | --- | --- | --- | --- |
| AR001 | 双系统骨架 | 快慢分流 | — | 开 |
| AR002 | 能力门控/验证器/草稿 | 质量与路由 | 额外小脑/大脑调用 | 门控开 |
| AR003 | 不确定性/缓存/记忆/prefill | 复用、成本 | 语义缓存需 embedding | 关 |
| AR005 | Conformal/JCBA/选择回退 | 风险可控、预算统一 | 需校准数据 | 关 |
| AR006 | AEB 证据自适应 + 自愈 | 长文 token ↓45.6%（POST 修复后） | 选块延迟 +10s | 关 |
| AR007 | 前缀 KV 复用 + 会话亲和 | prefill 8.8×、端到端 ↓16.3%（GPU） | 需稳定前缀/`user` | 关 |
| AR008 | 异构投机解码（0.5B draft） | 理论降 target 前向次数 | 本机实测 tps 反降，rollback | 关 |
| AR009 | CASC 稳定压缩 + 缓存 + 异构放置 | token ↓59.5%（shared）/↓58.7%（needles），质量 1.0 | 首问 embedding | 关 |
| AR010 | LADS 决策调度 + POST 窗口修复 | 端到端 ↓54.4%（CACHE_FAST） | fast 模式禁用 shadow/POST 复核 | 关 |
| AR011 | ADB 决策计算预算 | 决策 ↓30%，联合端到端 ↓71.3% | 置换 1 的一致性需按负载验证 | 关 |
| AR012 | CADP 消费者感知分阶段决策 | 再降 26%，联合端到端 ↓80.6% | 依赖路由短路规则一致性 | 关 |
| AR013 | RDMP 最小 wave1 探针 + 保守确认 | 相对 CADP P50 −28%，联合 P50 4.3s | 保守翻转（LIGHT→FULL）允许 | 关 |
| AR014 | LCPT-RFG 长上下文 int8 + reasoning 门控 | 联合 P50 2.42s，相对 RDMP −62% | 需生成本地 int8 资产；仅超长上下文 | 关 |
| AR015 | AGW 自适应门控窗口 | 联合 mean −1.9%，P50 1.97s | 收益较小；隔离无收益 | 关 |
| AR016 | GSP GPU 服务放置 | GPU offload mean −11.8%，P50 1.75s | 无 GPU/显存不足需回退 CPU | 关 |
| AR017 | CGW 级联门控窗口 | 联合 P50 1.00s（相对 AGW −39.6%） | 需 int8 资产；窗口为空退化为单窗 | 关 |
| AR018 | GTB 证据接地双脑级联 | 交互 P50 0.68–0.72s；quality 1.0 | mean 与 CGW 基本持平，独立文档回退 CGW | 关 |
| AR019 | PPKS 持久化前缀 KV 快照 | 重启后首请求 −85.7%（6.13s→0.88s） | 需磁盘 73MB/文档；仅 parallel=1 | 关 |
| AR020 | GEXA 确定性抽取快答 | 抽取命中零 LLM；shared P50 −84%，needles −99.97% | 仅覆盖有唯一答案的抽取型问题 | 关 |
| AR021 | GQRA 文档数值推理 | 多记录和/差/最大最小零 LLM；P50 3.8ms，quality 1.0 | 仅覆盖可确定性数值运算的问题 | 关 |
| AR022 | PSS 并行槽吞吐 | parallel4 并发吞吐 1.86x；quality 1.0 | 需更大 ctx/显存；与 PPKS 互斥 | 关 |

## 2. 负载 → 最优组合

| 负载 | 推荐组合 | 证据 |
| --- | --- | --- |
| 短问答、低延迟 | **B1**（能力门控，无额外运行时） | quality-combos：四组合一致，选择器 rollback B1 |
| 一次性长文 / 内存受限 | **AEB**（AR006：证据+自适应预算，必要时 +JCBA） | needles：2921→1590 tokens（−45.6%），质量不变 |
| 多轮/同文档多问/会话 | **CACHE**（AR007：稳定前缀 + KV 复用，`user` 会话键） | shared-prefix：prefill 8202→927ms（8.8×），端到端 −16.3% |
| 成本/上下文受限的共享文档 | **CASC**（AR009：文档级稳定压缩 + 压缩 LRU + KV） | shared-prefix：2662→1079 tok（−59.5%），11/12 命中，quality 1.0 |
| 单文档长文、token 成本优先 | **CASC**（无跨问缓存也保留证据） | needles：1847→762 tok（−58.7%），quality 1.0 |
| 极低延迟（可放弃 Laya 复核） | **CACHE_FAST / FAST**（AR010：跳过 shadow+POST，叠加前缀 KV） | shared-prefix：54.0s→24.6s（−54.4%），输出一致 |
| 端侧综合最优（token+mean） | **CGW_CASC_FAST + GPU offload**（AR017+AR016+AR015+AR014+AR013+AR012+AR011+AR010+AR009+AR007） | shared-prefix：P50 1.00s，mean 2.06s，tokens 1079，quality 1.0 |
| 交互 P50 最优（重复/共享文档） | **GTB_CASC_FAST + GPU offload**（AR018+AR017+...+AR007） | shared-prefix：P50 0.68–0.72s，quality 1.0，9/12 走 0.5B、3 次安全升级 |
| 纯延迟最优（不压缩上下文） | **GTB_CACHE + GPU offload**（AR018+AR017+...+AR007） | shared-prefix：P50 0.87–1.37s；CASC 更省 token，通常优先 GTB_CASC |
| 滚动重启/复现实验（冷启动首请求） | **任一稳态组合 + PPKS**（AR019） | shared-prefix 冷启动 6.13s→0.88s（−85.7%），quality 一致 |
| 文档抽取问答（编号/校验码/日期等） | **GEXA + GTB_CASC_FAST + GPU**（AR020+AR018+...） | shared P50 175ms、needles 3.7ms，tokens 0，quality 1.0 |
| 文档多记录数值推理（和/差/最大/最小） | **GQRA + GEXA + CGW/GTB + GPU**（AR021+AR020+...） | numeric_reasoning P50 3.8ms vs CGW 1.31s，quality 1.0 |
| 多用户并发吞吐（服务器/GPU） | **parallel=4 auto**（AR022）或 **parallel=2 + 文档亲和** | 并发 4：1.86x 吞吐；亲和 1.22x vs auto 0.99x |
| 无 GPU / 显存不足 | **AGW_CASC_FAST（CPU）** | shared-prefix：P50 1.97s，mean 3.77s，quality 1.0；CGW 未在 CPU 联合矩阵实测 |
| 重复/近义请求 | **语义缓存**（AR003/AR005 准入） | repeat 10×2：completion token ↓36%，延迟 ↓25% |
| 高风险降级 | **Conformal 风险门**（AR005） | 无可行阈值时自动 disabled，失败安全 |
| 长输出解码 | **B1（不启用投机）** | 中文 B1 8.51 vs SPEC 9.07（接受率 0.187）；英文 B1 5.36 vs SPEC 4.78；选择器 rollback |

## 3. 冲突与禁止叠加

| 组合 | 结果 | 结论 |
| --- | --- | --- |
| AEB + 前缀缓存 | AEB_CACHE 命中 **0/4**，延迟 141.6s | **不要盲目叠加**；会话用 CACHE，单次长文用 AEB |
| AEB + CACHE（CASC 之前） | 查询相关选块使前缀不稳定 | **CASC 用 query-independent 压缩修复**；11/12 命中 |
| CACHE + CASC 同时追求低延迟 | POST 修复后 CASC 首次低于 CACHE（38.1s vs 42.2s） | shared-prefix 选 CASC；needles 延迟略差但 token 大降 |
| 长 state + POST 复核 | 旧 Laya 窗口保头部 → 答案被截掉 → 恒定重试 | 已改保尾部；退化重复另加确定性重试 |
| 决策计算过度 | PRE 10 个 qid 中 intent 占 8 个置换 | 预算 1：applied 100% 一致，PRE 22.1s→11.3s |
| GPU Laya / 槽位绑定 / 状态压缩 | 分别负收益、无增益、safety 翻转 | 放弃；保留 CPU Laya + 全量 512 状态 |
| 决策点全量计算 | 路由存在安全短路，无需每次算满 5 点 | CADP 分阶段：3 点可短路，联合再降 26% |
| 最小 wave1 直接使用 | 16 题中 2 次 LIGHT→FULL，无法排除未知数据激进翻转 | RDMP 非 FULL 时完整确认；保守翻转保留 |
| int8 全上下文决策 | 短问 direct→full、needle light→full（保守但拖慢） | 仅 `>4000 tokens` 用 int8 reasoning 门控，其余 fp32 |
| 多探针打包进同一行 | 跨问题注意力改变 needs_reasoning，gen full→light | 放弃；保留每问题独立行 |
| 长 prompt + verifier/memory（本环境） | 偶发 llama-server 假死 | 长上下文实验显式关闭；生产用 Supervisor 自愈 |
| token-only 选择 | 误选省 token 但慢的 AEB_CACHE | 必须启用 `lambda_latency`（AR007） |
| 投机解码 + 本机 CPU draft | S2 接受率 0.624 仍只有 5.83 tps（B1 8.10） | draft/验证开销 > 收益；默认关闭 |
| GPU target（长输出历史结论） | 早期长输出 GPU decode 5.06 t/s < CPU 9.47 t/s | 长输出保持 CPU；**短输出+长 prefill 用 GPU offload（AR016）** |
| 抽取快答 + 详细生成 | 8 条“详细说明”题全部 `non-extractive` miss | GEXA 只接唯一答案抽取题；生成/解释请求不接管 |

## 4. 配置模板

- `configs/profiles/aeb.toml`：一次性长文；
- `configs/profiles/prefix-cache.toml`：会话/共享前缀（延迟优先）；
- `configs/profiles/casc.toml`：共享文档的成本/上下文优先；
- `configs/profiles/latency-fast.toml` / `latency-best.toml`：CPU 最优档（含长上下文门控）；
- `configs/profiles/latency-best-gpu.toml`：GPU offload 最优档（AR016 + AR017 级联窗口）；
- `configs/profiles/short-qa.toml`：短问答基线。

在 `configs/jev.toml`：

```toml
[portfolio]
profile = "configs/profiles/aeb.toml"   # 或 prefix-cache / short-qa
```

矩阵实验配置：

| 场景 | 配置 |
| --- | --- |
| 小模型组合 | `configs/experiments/combos.toml` |
| 前缀缓存 | `configs/experiments/prefix-cache.toml` |
| GPU 复测 | `prefix-cache-gpu.toml` / `combos-gpu.toml` |
| 投机解码 | `spec-short.toml` / `spec-short-en.toml` / `spec-tuning-cpu.toml` |
| 稳定压缩 + 缓存 | `casc-cpu.toml` / `casc-final-gpu.toml` / `casc-tune.toml` / `casc-needles6.toml` |
| 服务器 7B/14B | `server-7b-combos.toml` / `server-7b-prefix.toml` / `server-7b-spec.toml` / `server-7b-casc.toml`（14B 同名） |
| AR013 最小 wave1 | `rdmp.toml` / `rdmp-casc.toml` |
| AR014 长上下文精度分层 | `lcpt.toml` / `lcpt-casc.toml` / `lcpt-needles.toml` |
| AR015 自适应门控窗口 | `agw.toml` / `agw-casc.toml` |
| AR016 GPU 放置 | `ar016-gpu-cpu.toml` / `ar016-gpu-offload.toml`；`latency-best-gpu.toml` |
| AR017 级联门控窗口 | `cgw.toml` / `cgw-casc.toml` |
| AR018 双脑级联 | `gtb.toml` / `gtb-casc.toml` / `gtb-needles*.toml`；`latency-best-gpu.toml` |
| AR019 持久化 KV 快照 | `ppks.toml`；冷重启对照 `scripts/bench_ppks.py` |
| AR020 抽取快答 | `extractive.toml` / `extractive-needles.toml` / `extractive-quality.toml`；`latency-best-gpu-extractive.toml` |
| AR020 服务器 7B/14B | `server-{7b,14b}-extractive-casc.toml` |
| AR021 数值推理 | `quantitative.toml`；`latency-best-gpu-grounded.toml` |
| AR021 服务器 7B/14B | `server-{7b,14b}-grounded-casc.toml` |
| AR022 并发吞吐 | `scripts/bench_concurrency.py`；`latency-best-gpu-{throughput,affinity}.toml` |
| 服务器 7B/14B（完整 overlay） | `server-{7b,14b}-lcpt-casc.toml` / `server-{7b,14b}-agw-casc.toml` |

## 5. 选择规则

1. 先固定基线 B1；
2. 只对与负载匹配的能力做实验（不要全量叠加）；
3. 用配对 bootstrap + LOO 评估；
4. 效用 = `quality − λ_tokens·tokens − λ_latency·latency`（延迟敏感场景 λ_latency=0.5）；
5. 无正下界 / 质量降 >1pt → `rollback`，不更新 profile；
6. 结果落 `benchmarks/results/<run>/` 与 `docs/RESULTS.md`，不得手改。

## 6. AR001–AR022 最终综合最优叠加方案

### 6.1 结论：一个公共前栈，两个 miss 档

不存在“所有负载上全域支配”的单一配置。最终方案固定公共前栈，在**非命中路径**
按目标二选一，其余能力按部署模式叠加：

```text
GQRA + GEXA（零大脑 LLM 快路径，位于 Laya 路由后）
  └─ miss → CASC + prefix KV + session affinity + LADS/CADP/RDMP/LCPT/CGW
             └─ 0.5B GTB（交互 P50 档） 或 1.5B CGW（平均/独立文档档）
                └─ GPU offload
部署叠加：PPKS（冷启动，parallel=1） / parallel=4（多用户吞吐）
```

- **F1 交互默认（推荐）**：`GQRA+GEXA+GTB_CASC_FAST+GPU`
  → `configs/profiles/latency-best-gpu-grounded.toml`；
- **F2 平均/独立文档**：`GQRA+GEXA+CGW_CASC_FAST+GPU`
  → 同档设置 `tier_enabled=false`；mean 更稳，独立长文档回退 CGW；
- **F3 多用户吞吐**：`GTB_CASC_FAST + parallel=4`（ctx 16384，scheduler off）
  → `latency-best-gpu-throughput.toml`；文档亲和改 `parallel=2+scheduler`；
- **F4 冷启动**：`GTB/CGW_CASC_FAST + PPKS`（parallel=1，`--slots --slot-save-path`）
  → `ppks.toml` / `scripts/bench_ppks.py`；与 F3 互斥；
- **F5 无 GPU**：`CGW_CASC_FAST`（CPU 基础档）
  → `latency-best.toml`；CGW 未在 CPU 联合矩阵单独实测；
- **F6 一次性长文**：`AEB`（可选叠加 GEXA/GQRA；独立 profile，不与前缀缓存叠加）。

> F3/F4/F5/F6 的 profile 默认未开启 GEXA/GQRA；需要零大脑快答时额外开启
> `[extractive] enabled=true, quantitative_enabled=true`。

选择规则：交互助手/短输出优先 F1；批处理/独立长文档/mean 优先 F2；并发服务用 F3；
滚动重启或实验冷启动加 F4。**F1 与 F2 是 Pareto 档，不是互相替代的“谁绝对更好”。**

### 6.2 最终组合的分层实测

| 层 | 能力 | 最终栈指标 | 对照 |
| --- | --- | --- | --- |
| 零大脑 LLM 命中 | GEXA 单事实抽取 | shared P50 **175ms**、needles **3.7ms**、tokens **0**、quality 1.0 | CGW shared 1.101s / needles 13.089s |
| 零大脑 LLM 命中 | GQRA 多记录数值 | numeric P50 **3.8ms**、tokens **0**、quality **1.0** | CGW P50 1.310s、quality 0.545 |
| miss + 压缩 | CASC + prefix KV | shared tokens 2662→**1079（−59.5%）**、cache 11/12 | B1 cache 0/12 |
| miss + 决策 | LADS fast + ADB/CADP/RDMP + LCPT + CGW | shared P50 **1.00s**（CGW）；门控 w128 | B1 36.6–52.6s |
| miss + 双脑 | GTB 0.5B 接地验证 | shared P50 **0.72s**，9 small / 3 escalate，0 错误接受 | CGW P50 1.00s |
| 放置 | GPU offload | mean −11.8%、P50 −13.7% | CPU decode |
| 冷启动 | PPKS | 6.13s→**0.88s（−85.7%）** | 冷重启无快照 |
| 并发 | parallel=4 auto | 吞吐 **1.86x**、quality 1.0 | parallel=1 |
| 质量 | 全栈 | 各数据集 **1.0**（numeric B1 0.364→1.0） | — |

> 组合口径：GEXA/GQRA 命中不调用任何大脑 LLM；未命中才进入 CASC/双脑级联。
> F1 的 shared miss 指标来自 GTB_CASC_FAST，F2 来自 CGW_CASC_FAST。

### 6.3 全部 AR 在最终方案中的去留

| AR | 能力 | 最终角色 | 依据/边界 |
| --- | --- | --- | --- |
| AR001 | 双系统骨架 | **保留** | 快慢分流基础 |
| AR002 | 能力门控/验证器/草稿 | **保留门控**；verifier/draft 关 | 高品质路由；草稿未稳定增益 |
| AR003 | 语义缓存/记忆/prefill | **可选**：`[cache] enabled=true` 仅重复负载 | 记忆/验证器长 prompt 本机假死；默认关 |
| AR004 | 学习与复现基建 | **流程工具**，不进请求路径 | 飞轮/矩阵/复现 |
| AR005 | Conformal/JCBA | **可选风险门**；JCBA 被 CASC 取代 | 无可行阈值时失败安全；长文预算由 AEB/CASC 覆盖 |
| AR006 | AEB 证据预算 | **专项**：一次性长文 F6 | 与前缀缓存冲突（命中 0/4） |
| AR007 | 前缀 KV/会话亲和 | **保留** | prefill 8.8×，端到端 −16.3% |
| AR008 | 异构投机解码 | **拒绝** | 本机 tps 反降，默认关 |
| AR009 | CASC 稳定压缩 | **保留** | tokens −59.5%，质量 1.0 |
| AR010 | LADS fast | **保留** | 跳过 shadow/POST，−54.4% |
| AR011 | ADB perm=1 | **保留** | PRE 2×，路由 100% 一致 |
| AR012 | CADP 分阶段 | **保留** | 联合再降 26% |
| AR013 | RDMP 最小 wave1 | **保留** | P50 −28%，保守翻转 |
| AR014 | LCPT 长文 int8 | **保留** | 长文决策 2.1×，路由 16/16 一致 |
| AR015 | AGW w384 | **保留为 CGW 输入** | 单窗收益小，CGW 在其基础上 −39.6% |
| AR016 | GPU 放置 | **保留**（有 GPU） | P50 −13.7%；无 GPU 回退 CPU |
| AR017 | CGW 级联窗口 | **保留**（F2 miss 档） | w128，P50 −39.6% |
| AR018 | GTB 双脑接地 | **保留**（F1 交互档） | P50 −51.7%，0 错误接受；独立文档回退 CGW |
| AR019 | PPKS 快照 | **F4 冷启动专项** | 与 parallel>1 互斥，磁盘 73MB/文档 |
| AR020 | GEXA 抽取 | **保留前栈** | 命中零大脑 LLM，0 误答 |
| AR021 | GQRA 数值推理 | **保留前栈** | numeric quality 0.364→1.0 |
| AR022 | PSS 并行槽 | **F3 并发专项** | 1.86x；与 PPKS 互斥 |

### 6.4 明确禁止/不默认叠加

1. **GEXA/GQRA 与生成型请求**：只接唯一答案/可确定性运算，其余必须 miss 回退；
2. **AEB + 前缀缓存/CASC**：查询相关选块破坏前缀稳定性；
3. **PPKS + parallel>1 或 slot_scheduler**：快照固定 slot 0，代码 fail-fast；
4. **GTB 用于一次性独立长文档**：needles mean +8.3%，回退 CGW；
5. **投机解码、GPU Laya、全量决策点、head+tail 状态、多探针同行**：已实测负收益/安全翻转；
6. **fast 模式 + 依赖 Laya POST 的安全复核**：fast 明确跳过 shadow/POST，需严格复核时用
   `decision_post=true`（抽取命中也走 POST，P50 739ms）。

### 6.5 诚实边界

- 7B/14B 仅有配置与复现步骤，**未本机实测，禁止外推指标**；
- AR022 的 1.86x 是**裸大脑并发**结果；`parallel=4 + GTB + GEXA/GQRA + CASC`
  的完整应用级并发尚未单独矩阵验证；
- F1/F2 的 shared/numeric 指标来自不同矩阵轮次，绝对值受本机 645MHz GPU
  与外部争用影响；相对结论和 quality 一致；
- 任何新增叠加仍按 §5 选择器复验，无正下界即回退。
