# 实验设计与指标（AR005）

## 1. 目标

回答三个问题，全部用真实后端：

1. 相对 AR002（B1）与 AR003（B2），AR005 能否在**质量不降**的前提下降低代价？
2. Conformal 风险门/缓存准入是否把「假降级/假命中」控制在 α 以内？
3. JCBA 是否能在固定预算下保留更多信息（needles 保留率）？

## 2. 数据集

| 文件 | 规模 | 用途 |
| --- | --- | --- |
| `configs/quality-set.yaml` | 30 题（数学/逻辑/事实/指令，zh+en） | 质量与成本主表；gold 客观判分 |
| `configs/cache-set.yaml` | 20 原句 + 20 复述 + 20 负例 | Conformal 缓存准入校准与误命中 |
| `configs/bench-set.yaml` | 40 题（AR002 双语） | 路由命中率回归 |
| needles（运行时生成） | 40 长文问答 | JCBA/prefill token 与保留率 |

> 质量集判定：`contains`（不区分大小写）、`regex`、`numeric`（容差）。不调用模型判分。

## 3. 方案与变量

| 变量 | 取值 |
| --- | --- |
| arm | B0 / B1 / B2 / AR005 |
| λ（成本权重） | 0.05 / 0.15 / 0.30（敏感性，可选） |
| seed | 20261026 |
| 后端 | 本机 1.5B；服务器 7B/14B |
| 采样 | 全部题（30），不做挑选 |

**生成提示前缀**：矩阵对每题统一加 `prompt_prefix = "写一段回答，并说明理由："`（实验配置可改）。
原因：真实 Laya 会把裸问句判为 `no_generation` 并走 `direct`（结构化模板），而 direct 不产出散文答案；
加此前缀使工作负载为**生成式问答**，从而公平比较 light/full/运行时组合。该前缀会原样进入模型输入，
并记录在实验配置中以保证可复现。

## 4. 指标

- **质量**：gold 通过率；
- **成本**：`completion_tokens` 均值、`prompt_tokens` 均值；
- **延迟**：P50/P95 墙钟；
- **路由**：direct/light/full 分布、命中率；
- **缓存**：`hit-exact/hit-semantic` 次数；负例误命中率；
- **风险**：`X-Jev-Risk=deny` 次数；校准违规率；
- **选择**：效用、配对 bootstrap 95% CI、LOO 策略效用。

## 5. 门（建议）

| 门 | 阈值 |
| --- | --- |
| 质量不降 | AR005 ≥ B2 − 1pt |
| 成本 | AR005 completion tokens ≤ B2；目标 ↓≥10% |
| AR006 长上下文 | AEB ≥ B1 − 2pt；prompt tokens ↓≥50% |
| 风险违规 | ≤ α（默认 0.10）+ 有限样本松弛 |
| 缓存误命中 | ≤1/20 负例（α=0.10） |
| 决策稳定性 | 同 seed 两次运行 `decision` 一致 |

## 5.1 AR006 组合实验

`configs/experiments/combos.toml`（needles 12 题）与 `quality-combos.toml`（短问答 12 题）：

| 组合 | 定义 |
| --- | --- |
| B1 | 路由基线（无运行时压缩） |
| P60 | 固定 0.6 预算 |
| AEB | 真实 embedding 证据 + 自适应预算（0.22–0.60） |
| AEB_ALLOC | AEB + JCBA 统一预算 |

运行：`uv run jev matrix --config configs/experiments/combos.toml`

服务器：`server-7b-combos.toml` / `server-14b-combos.toml`（external 大脑+embedding），
结果分别写入 `benchmarks/results/ar006-server-7b-combos-*/`，不得把小模型结论外推到大模型。

判定：选择器仍按配对 bootstrap + LOO；若 AEB 质量下降 >2pt 或无正下界 → rollback；
若通过，`docs/RESULTS.md` 记录实际运行数字与延迟代价（embedding 选块开销）。

## 5.2 AR007 前缀 KV 复用

- 数据：`jev.bench.prefixset.build_shared_prefix`（同文档、不同编号问题，前缀逐字一致）；
- 配置：`configs/experiments/prefix-cache.toml`（B1/CACHE/AEB/AEB_CACHE）；
- 关键指标：`X-Jev-Prefix-Cache` 的 `cache_n`、`X-Jev-Prefill-Ms`、端到端延迟；
- 选择效用：`quality - λ_tokens·tokens - λ_latency·latency`（默认 λ_latency=0，AR007 配置 0.5）；
- 服务器：`server-7b-prefix.toml` / `server-14b-prefix.toml`（external，30 题）。

判定：CACHE 的质量不得下降，且同前缀请求 prefill 显著下降（本机 ≥10× 目标）；
若 AEB_CACHE 的命中率低且延迟高，则不得把 AEB 与缓存盲目叠加。

## 5.3 AR008 异构投机解码

- 数据：`jev.bench.generation.build_long_generation`（中文）/`build_long_generation_en`（英文），
  短 prompt + 长输出，隔离解码阶段；
- 配置：`spec-short.toml`、`spec-short-en.toml`、`spec-tuning-cpu.toml`
  （draft n_max 2/4/8）、`spec-confirm-cpu.toml`、`spec-decode.toml`；
- 关键指标：`X-Jev-Spec: accepted/draft`、`X-Jev-Decode-Tps`、
  `mean_decode_tps`、`spec_accept_rate`、`outputs_identical`；
- 对照：CPU-only、CUDA target（本机 GPU 限频 645 MHz，需单独标注）。

判定：质量不得下降，且 decode tps 的配对 bootstrap CI 下界 >0 才 adopt；
本机真实结果为 **rollback B1**（CPU draft 开销抵消接受率收益），默认关闭。

## 5.4 AR009 CASC：稳定压缩 + 缓存 + 异构放置

- 数据：`shared_prefix`（同文档 12 问，可验证前缀复用）与 `needles`（单文档单问，验证质量）；
- 配置：`casc-cpu.toml`（CPU embedding）、`casc-final-gpu.toml`（CPU 解码 + GPU embedding，
  ratio 0.4）、`casc-order.toml`（反转 arm 顺序）、`casc-tune.toml`（ratio 0.4/0.5/0.6）、
  `casc-needles6.toml`；
- 关键指标：`X-Jev-Stable-Compression`、`X-Jev-Stable-Cache`、`X-Jev-Stable-Ms`、
  `X-Jev-Prefix-Cache`、prompt tokens、prefill、端到端延迟；
- 设备：`gpu_layers=0`（brain CPU）+ `embedding_gpu_layers=99`（embedding GPU）；
- 判定：质量 ≥ baseline −2pt，tokens ≤0.75×CACHE，cache hit ≥11/12；
- POST 修复后真实结果：shared-prefix B1 52.9s / CACHE 42.2s / CASC **38.1s**，
  tokens 2662→**1079（−59.5%）**，quality 1.0，输出一致；needles B1 47.6s / CASC 50.0s，
  tokens 1847→762（−58.7%），quality 1.0；两场景均 `adopt CASC`；
- 7B/14B：`server-7b-casc.toml` / `server-14b-casc.toml`（external，本机不运行）。

## 5.5 AR010 LADS：决策调度 + POST 窗口修复

- 缺陷：Laya 超长 state 原保留头部（`max_len=512`），assistant 答案被截掉，
  POST 恒定 `needs_retry`，B1/CACHE 每次重试一次；历史长 prompt 表被放大。
- 修复：`laya_sequence.build_sequence` 与 `laya_onnx.system_one` 均改为保尾部；
  新增退化重复守卫（短周期全串重复 → 重试一次）。
- 配置：`lads.toml`（B1 / FAST / CACHE / CACHE_FAST），`profiles/latency-fast.toml`；
- 关键指标：`X-Jev-Decision-Mode`、`X-Jev-Decisions`、`X-Jev-Prefix-Cache`、端到端延迟、
  `outputs_identical`；
- 真实结果（shared-prefix 12 题）：B1 54.0s / FAST 34.2s（−36.7%）/ CACHE 42.0s /
  **CACHE_FAST 24.6s（−54.4%）**；四臂 quality 1.0、输出完全一致；选择器 adopt CACHE_FAST；
- 回归：`tests/integration/test_laya_post_window_real.py`（长 state 下 POST 可区分正确/空答）。

## 6. 选择与回退

选择器流程（`src/jev/bench/selector.py`）：

1. 逐题效用 `u = q − λ·(tokens/max_tokens)`；
2. 以当前最优静态 arm（默认 B2）为基线；
3. 对每个候选 arm 与 LOO band→arm 策略计算配对差值；
4. 1,000 次 bootstrap 取 2.5% 分位；
5. 仅当 `CI_low > 0` 且质量 ≥ 基线 −1pt 才 `adopt`，否则 `rollback`。

**回退语义**：`rollback` 时 profile 保持上一最优（默认 B2），不写 `configs/runtime-profile.toml`。

## 7. 产物

```text
benchmarks/results/<run>/
├── results.json     # schema jev.matrix/1（逐题原始 + 指标 + 选择 + 指纹）
├── report.md        # 人类可读
├── profile.toml     # 仅供采纳/回灌
├── risk.json        # Conformal 降级校准（n≥20 时）
└── admission.json   # Conformal 缓存准入（n_neg≥20 时）
```

## 8. 已知边界

- 本机 1.5B 无法代表 7B/14B 的质量临界点；服务器结果需单独补录。
- 质量集是**小样本**（30 题），bootstrap CI 仅作工程参考，不作统计论文结论。
- CPU 模式可跑通但长生成慢；本机历史结果实为 CPU-only，GPU 复测被限频（645 MHz）。
  正常 GPU 主机需先补齐 CUDA 运行库并核对 `llama-server --list-devices`。
- 负例/复述集为人工构造，覆盖真实语义边界但不穷尽领域。

## 5.6 AR011 ADB：决策计算预算与联合最优

- 缺陷/瓶颈：Laya PRE 约 22s/请求；`intent` 的 2 探针 ×4 置换占 8/10 个 qid。
- 修复：`apply_permutation_budget` + `RuntimeConfig.decision_permutations`
  （0=不限制，1=每点 1 置换）+ 可选 `decision_escalate_margin` 升级。
- 真实一致性：8 条多样题 + 6 条 shared，applied 决策 100% 一致；PRE 22.1s→11.3s。
- 配置：
  - `adb.toml`（隔离，force_full=true，4 arms）；
  - `adb-route.toml`（真实路由，force_full=false，3 arms）；
  - `adb-casc.toml`（+AR009，4 arms）；
  - `server-7b-adb-casc.toml` / `server-14b-adb-casc.toml`（external，30 题，服务器复刻）。
- 真实结果：
  - 隔离：B1 45.6s → ADB 31.5s → ADB_CACHE 29.9s → **ADB_CACHE_FAST 13.1s**；
  - 真实路由：B1 51.1s → **13.4s**，12/12 路由一致；
  - 联合：B1 49.2s → **ADB_CASC_FAST 14.1s（P50 12.1s）**，tokens 2662→1079；
  - 全部 quality 1.0、输出一致；选择器 adopt ADB_CASC_FAST（+0.4054）。
- 负结果：GPU Laya 30.6s > CPU 25.7s；槽位绑定无增益；head+tail 状态窗口一致率 84%。
- 复刻：`uv run jev matrix --config configs/experiments/adb.toml`，
  或使用 `configs/profiles/latency-best.toml`。


## 5.7 AR012 CADP????????????

- ?????safety=unsafe?full?intent=decision?direct?needs_reasoning=reasoning?full?
  ?????light????? needs_generation/complexity?
- ??????prompt draft/memory/prefill/allocator?verifier?margin?risk ???? ?
  ???? PRE?????????? ? ?? PRE?
- ???`cadp.toml`?4 arms?force_full=false??`cadp-casc.toml`????4 arms??
  ????`server-7b-cadp-casc.toml` / `server-14b-cadp-casc.toml`?30 ??external??
- ?????B1 51.6s ? CADP_CACHE_FAST **10.0s** ? CADP_CASC_FAST **10.6s?P50 8.8s?**?
  quality 1.0????????? adopt CADP_CASC_FAST?+0.444??
- ??????6 ??? prompt ?????????? 100% ???
- ???`uv run jev matrix --config configs/experiments/cadp-casc.toml`?

## 5.8 AR013 RDMP：最小 wave1 + 保守确认

- 动机：CADP 后 wave1 仍占 6 qid（intent 2 探针、safety/needs_reasoning 各 2 置换）。
- 真实探测：最小 wave1（3 qid）在 16 条多样 prompt 上仅 2 次翻转，均为 LIGHT→FULL；
  保守确认保证不出现激进翻转。
- 实现：`decision_staged_minimal`（默认 false）；最小结果非 FULL 时用完整 wave1 复算。
- 隔离矩阵：B1 36.8s → CADP 21.2s → **RDMP 19.7s**（P50 19.5s），quality 1.0。
- 联合矩阵：CADP_CACHE_FAST 6.85s → RDMP_CACHE_FAST **5.05s**（P50 4.33s，−26%）；
  RDMP_CASC_FAST P50 **4.33s**、tokens **1079**、quality 1.0，选择器 adopt。
- 复刻：`uv run jev matrix --config configs/experiments/rdmp-casc.toml`。

## 5.9 AR014 LCPT-RFG：长上下文精度分层 + reasoning-first

- 量化：`uv run jev prepare-laya-int8` 生成 ~427MB int8 Laya（QInt8/per-channel）；
- 门控：`decision_long_context_tokens=4000`；超长 state 先算 `needs_reasoning`（int8 单行），
  applied≠no_reasoning 直接 FULL；否则回退 fp32 RDMP；
- 真实 16 题：与 RDMP 路由 16/16 一致、0 激进翻转；shared 6 题 Laya 决策 2.1×；
- 隔离：B1 44.36s → RDMP 23.00s → **LCPT 19.46s（P50，−15.4%）**；
- 联合：RDMP_CACHE_FAST 6.63s → **LCPT_CACHE_FAST 2.53s**；
  **LCPT_CASC_FAST P50 2.42s、tokens 1079、quality 1.0**（选择器 adopt）；
- needles 回归：LCPT 37.88s vs RDMP 38.25s，quality 1.0，无回退；
- 服务器：`server-{7b,14b}-lcpt-casc.toml`（完整 overlay，external 8388/8389）；
- 复刻：`uv run jev matrix --config configs/experiments/lcpt-casc.toml`。

## 5.10 AR015 AGW：自适应门控窗口

- `decision_long_context_gate_tokens`（默认 384）：int8 门控只读短窗口，
  fp32 RDMP 兜底保持完整 512；
- 真实 shared 窗口扫描：384 命中 12/12，门控 1.51s→1.05s；
- 隔离矩阵无收益（uncached prefill 主导）；联合矩阵 AGW_CASC_FAST
  mean 3.765s vs LCPT_CASC_FAST 3.836s（−1.9%），P50 1.969s vs 1.984s，utility 采纳；
- 诚实结论：门控层收益较小，不宣称大幅提升；当前综合最优仍为
  `AGW_CASC_FAST`（P50 1.97s、tokens 1079、quality 1.0）；
- 复刻：`uv run jev matrix --config configs/experiments/agw-casc.toml`。

## 5.11 AR016 GSP：GPU 服务放置

- `[cerebrum] gpu_layers` 接入生产 llama-server；默认 0，GPU profile 为 99；
- 同配置真实对照：CPU mean 3.462s/P50 2.032s → GPU **3.054s/1.753s**，
  prefill 726ms→357ms，decode 不降；
- 当前综合最优更新为 **AGW_CASC_FAST + gpu_layers=99**；
- 无 GPU 回退 `latency-best.toml`；
- 复刻：`uv run jev matrix --config configs/experiments/ar016-gpu-offload.toml`
  （对照 `ar016-gpu-cpu.toml`）。

## 5.12 AR017 CGW：级联门控窗口

- `decision_long_context_gate_windows` 默认 `[128, 384]`，空列表保持单窗口；
- `windowed()` 共享 int8 session，短窗命中即停；真实 128→384 链命中 18/18，
  mean 361ms、P50 200ms；
- 隔离矩阵：CGW mean 16.338s/P50 16.296s vs AGW 16.540s/16.538s（prefill 主导）；
- 联合矩阵：**CGW_CASC_FAST P50 1.001s** vs AGW_CASC_FAST 1.658s（−39.6%），
  mean 2.063s vs 2.970s，tokens 1079、quality 1.0；
- 注意：GPU 争用会使末段 arm decode 掉到 ~2.8 tps，需清理环境后重跑；
- 复刻：`uv run jev matrix --config configs/experiments/cgw.toml`，
  联合用 `cgw-casc.toml`。

## 5.13 AR018 GTB：证据接地双脑级联

- 小模型 Qwen2.5-0.5B 先答；仅在答案位于问题锚点所在文档段落时采用，否则升级 1.5B；
- 隔离：GTB quality 1.0，P50 10.363s vs CGW 13.080s（7/12 小模型）；
- 联合：`GTB_CASC_FAST` P50 **0.721s** vs CGW 1.493s，mean 1.009s vs 1.452s，
  quality 1.0；选择器 adopt；
- 保守复跑（外部 GPU 争用）：P50 0.684s vs 1.035s（−33.9%），mean +3.1%，
  按 mean 回退 CGW；GTB 定位为 P50/交互优化档；
- needles force-full：12/12 小模型接地、quality 1.0，P50 14.596s vs 15.489s；
- 复刻：`uv run jev matrix --config configs/experiments/gtb.toml`、
  `gtb-casc.toml`；稳态统计使用 `drop_first=true`（同时作用于选择器）。
