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
| AR006 | AEB 证据自适应 + 自愈 | 长文 token ↓51% | 选块延迟 +10s | 关 |
| AR007 | 前缀 KV 复用 + 会话亲和 | prefill 27.7×、端到端 ↓23.5%（CPU） | 需稳定前缀/`user` | 关 |
| AR008 | 异构投机解码（0.5B draft） | 理论降 target 前向次数 | 本机实测 tps 反降，rollback | 关 |
| AR009 | CASC 稳定压缩 + 缓存 + 异构放置 | token ↓29.6%（shared）/↓58.7%（needles），质量 1.0 | 首问 embedding；延迟不优于 CACHE | 关 |

## 2. 负载 → 最优组合

| 负载 | 推荐组合 | 证据 |
| --- | --- | --- |
| 短问答、低延迟 | **B1**（能力门控，无额外运行时） | quality-combos：四组合一致，选择器 rollback B1 |
| 一次性长文 / 内存受限 | **AEB**（AR006：证据+自适应预算，必要时 +JCBA） | needles：2457→1204 tokens（−51%），质量不变 |
| 多轮/同文档多问/会话 | **CACHE**（AR007：稳定前缀 + KV 复用，`user` 会话键） | shared-prefix：prefill 8848→319ms（27.7×），端到端 −23.5% |
| 成本/上下文受限的共享文档 | **CASC**（AR009：文档级稳定压缩 + 压缩 LRU + KV） | shared-prefix：5354→3771 tok（−29.6%），12/12 命中，quality 1.0 |
| 单文档长文、token 成本优先 | **CASC**（无跨问缓存也保留证据） | needles：1847→762 tok（−58.7%），quality 1.0 |
| 重复/近义请求 | **语义缓存**（AR003/AR005 准入） | repeat 10×2：completion token ↓36%，延迟 ↓25% |
| 高风险降级 | **Conformal 风险门**（AR005） | 无可行阈值时自动 disabled，失败安全 |
| 长输出解码 | **B1（不启用投机）** | 中文 B1 8.51 vs SPEC 9.07（接受率 0.187）；英文 B1 5.36 vs SPEC 4.78；选择器 rollback |

## 3. 冲突与禁止叠加

| 组合 | 结果 | 结论 |
| --- | --- | --- |
| AEB + 前缀缓存 | AEB_CACHE 命中 **0/4**，延迟 141.6s | **不要盲目叠加**；会话用 CACHE，单次长文用 AEB |
| AEB + CACHE（CASC 之前） | 查询相关选块使前缀不稳定 | **CASC 用 query-independent 压缩修复**；12/12 命中 |
| CACHE + CASC 同时追求低延迟 | CASC 首问 embedding 开销，延迟不优于 CACHE | 默认延迟选 CACHE；λ_cost ≥0.5 选 CASC |
| 长 prompt + verifier/memory（本环境） | 偶发 llama-server 假死 | 长上下文实验显式关闭；生产用 Supervisor 自愈 |
| token-only 选择 | 误选省 token 但慢的 AEB_CACHE | 必须启用 `lambda_latency`（AR007） |
| 投机解码 + 本机 CPU draft | S2 接受率 0.624 仍只有 5.83 tps（B1 8.10） | draft/验证开销 > 收益；默认关闭 |
| GPU target（本机） | GPU 限频 645 MHz，tg 5.06 t/s < CPU 9.47 t/s | 历史结果按 CPU-only 标注；正常 GPU 需复测 |

## 4. 配置模板

- `configs/profiles/aeb.toml`：一次性长文；
- `configs/profiles/prefix-cache.toml`：会话/共享前缀（延迟优先）；
- `configs/profiles/casc.toml`：共享文档的成本/上下文优先；
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

## 5. 选择规则

1. 先固定基线 B1；
2. 只对与负载匹配的能力做实验（不要全量叠加）；
3. 用配对 bootstrap + LOO 评估；
4. 效用 = `quality − λ_tokens·tokens − λ_latency·latency`（延迟敏感场景 λ_latency=0.5）；
5. 无正下界 / 质量降 >1pt → `rollback`，不更新 profile；
6. 结果落 `benchmarks/results/<run>/` 与 `docs/RESULTS.md`，不得手改。
