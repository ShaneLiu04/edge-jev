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
- CPU 模式可跑通但长生成慢；实验默认 GPU。
- 负例/复述集为人工构造，覆盖真实语义边界但不穷尽领域。
