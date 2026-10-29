# AR005 真实实验结果（本机 1.5B / Laya / 真 llama-server）

> 所有数字来自 `jev matrix` 真实后端运行；原始数据在 `benchmarks/results/<run>/results.json`。
> 运行环境：Windows 11，Intel Xeon Gold 6234（8C/16T），Quadro RTX 5000 16GB，
> Qwen2.5-1.5B-Instruct-Q4_K_M。**重要更正：AR005–AR007 主表实际为 CPU-only**——
> 当时 CUDA 运行库缺失，`--n-gpu-layers 99` 未生效；GPU 复测见文末「环境更正」。
>
> **2026-10-29 POST 复核更正**：Laya 批处理状态窗口原保留头部（`max_len=512`），
> 长输入下 assistant 答案被截掉，POST `needs_retry` 恒定 `retry`，B1/CACHE 每次白跑
> 第二次生成。现已改为保留尾部，并加退化重复守卫。凡 2026-10-29 之前的长 prompt 表
> （needles / shared-prefix）的绝对延迟与 prompt token 均被放大；以带「POST 修复后」
> 标记的复测表为准。

## 实验 1：唯一问题（20 题 ×4 方案）

命令：`jev matrix --config configs/experiments/ar005-local-20.toml`

| arm | quality | completion tok | prompt tok | mean latency | 路由 |
| --- | --- | --- | --- | --- | --- |
| B0（全 full） | **0.70** | 24.9 | 57.6 | 11.7s | full 20 |
| B1（AR002） | 0.60 | 29.2 | 107.8 | 16.4s | light 18 / full 2 |
| B2（AR003） | 0.50 | 26.0 | 86.8 | 17.6s | light 19 / full 1 |
| AR005 | 0.50 | 26.0 | 86.8 | 17.0s | light 19 / full 1 |

选择器：`adopt B0`（效用下界 +0.080），即**自动回退/选择当前最优组合**。
AR005 与 B2 相同：本次运行中 Conformal 风险门找到的可行阈值不足（降级损失高），
缓存准入校准因 embedding 服务在该时段不可用而未启用，系统按设计**失败安全回退**。

## 实验 2：重复/复述（10 题 ×2 轮 ×4 方案）

命令：`jev matrix --config configs/experiments/ar005-repeat-10x2.toml`

| arm | quality | completion tok | prompt tok | mean latency | cache hits |
| --- | --- | --- | --- | --- | --- |
| B0 | **0.60** | 20.6 | 57.8 | 12.2s | 0 |
| B1 | 0.40 | 26.4 | 133.4 | 16.2s | 0 |
| B2 | 0.35 | 13.2 | 66.7 | 9.3s | **10** |
| AR005 | 0.35 | 13.2 | 66.7 | 9.1s | **10** |

结论：语义/精确缓存真实生效——第二轮命中后 completion tokens 较 B0 下降 **36%**，
延迟下降 **25%**；但 light 路径质量低于 full。选择器：`rollback B2`（无正效用下界）。

## 实验 3：长上下文 needles（12 题 ×3 方案，真实 prefill/JCBA）

命令：`jev matrix --config configs/experiments/needles.toml`

| arm | quality | prompt tokens | completion tok | mean latency |
| --- | --- | --- | --- | --- |
| B1（无压缩） | 0.917 | 2457.3 | 5.9 | 38.8s |
| B2（AR003 prefill） | 0.917 | **1493.2** | 5.9 | 33.2s |
| AR005（JCBA） | 0.917 | **1493.1** | 5.9 | 32.9s |

结论：在长上下文真实负载上，prefill/JCBA 将 prompt tokens 降低 **39.2%**，质量保持 **0.917 不变**，
延迟下降约 14%，达到 FR5-3 的 ≥30% 与 ≤2pt 门。AR005 与 B2 持平（同预算下同解），选择器 `rollback B2`。

> 环境说明：本机长 prompt 下 B2 同时开启 verifier/memory 会触发 llama-server 假死；
> 本实验显式关闭二者以隔离 prefill/JCBA（配置见 `configs/experiments/needles.toml`），
> 不代表这些能力无效，只表示该环境下未测。

## 实验 4：AR006 AEB 组合（needles 12 题 ×4 组合，真实 embedding）

命令：`jev matrix --config configs/experiments/combos.toml`

| arm | 说明 | quality | prompt tok | mean latency |
| --- | --- | --- | --- | --- |
| B1 | 仅级联（无压缩） | 0.917 | 2457.3 | 33.6s |
| P60 | 固定 0.6 预算 | 0.917 | 1493.2 | 30.1s |
| **AEB** | 证据 + 自适应预算 | **0.917** | **1204.2** | 45.2s |
| AEB_ALLOC | AEB + JCBA 统一预算 | **0.917** | **1204.2** | 45.8s |

- 相对 B1：prompt tokens **↓51.0%**，质量完全不变（12/12），达到 FR6-5 门（≥50%，Δq≤2pt）。
- 相对固定 0.6：再降 **19.3%** 词元；选择器 `adopt AEB`（效用下界 > 0）。
- 代价：embedding 选块增加约 10–12s（本机 CPU 争用下）；纯生成阶段并未变慢。
- AEB_ALLOC 与 AEB 同解，说明 JCBA 组合没有破坏收益（组合一致性成立）。

### 实验 5：短问答回归（quality 12 题 ×4 组合）

| arm | quality | prompt tok | 路由 |
| --- | --- | --- | --- |
| B1 | 0.333 | 141 | light 12 |
| P60 | 0.333 | 141 | light 12 |
| AEB | 0.333 | 141 | light 12 |
| AEB_ALLOC | 0.333 | 141 | light 12 |

短 prompt 不触发压缩，四组合完全一致；选择器 `rollback B1`，符合“无增益即回退”。

## 实验 6：AR007 前缀 KV 复用（shared-prefix 12 题，真实后端）

> 下表为 **POST 修复前** 的 CPU 结果，仅保留作历史；正确口径见文末
> 「AR007 GPU 复测（POST 修复后）」：prefill 8202→927ms、端到端 48.4→40.5s。

命令：`jev matrix --config configs/experiments/prefix-cache.toml`

| arm | quality | prompt tok | mean prefill | mean latency | cache 命中 |
| --- | --- | --- | --- | --- | --- |
| B1（无缓存） | 1.000 | 5354 | **8848 ms** | **70.6s** | 0/12 |
| **CACHE（稳定前缀+KV）** | **1.000** | 5354 | **319 ms** | **54.0s** | **12/12** |

- 前缀 KV 复用使 prefill 大幅加速，端到端延迟明显下降；旧绝对值的 prompt token 含 POST 重试累加。
- 质量完全一致；选择器在加入延迟惩罚（`lambda_latency=0.5`）后 `adopt CACHE`。
- 注：`usage.prompt_tokens` 仍按逻辑 token 计数，缓存收益体现在 `prefill_ms` 与 `cache_n`。

### 实验 7：AEB 与缓存的冲突（同一 shared-prefix 负载）

| arm | quality | prompt tok | mean latency | cache 命中 |
| --- | --- | --- | --- | --- |
| AEB（12 题） | 1.000 | 3998（−25%） | 153.4s | 0/12 |
| AEB_CACHE（4 题） | 1.000 | 3998 | 141.6s | **0/4** |

- 每个问题选块不同 → 前缀不稳定 → KV 命中为 0，压缩收益无法叠加缓存。
- 仅按 token 选择会误选 AEB_CACHE（token 更少）；加入延迟惩罚后正确选择 CACHE。
- 结论：**会话/同文档场景用 CACHE；一次性长文用 AEB**；二者不要盲目叠加。

## 关键发现（诚实记录）

1. **缓存价值已验证**：重复请求命中后 token/延迟显著下降，且不改变答案。
2. **风险门按预期失败安全**：当降级会带来不可接受损失时，CRC 找不到可行阈值 → 禁用，不退化为乱降级。
3. **当前实验的偏差**：为控制 CPU/GPU 时间，`max_tokens=32`，light 路径被截断，
   质量低于 full；这**不利于** light/缓存组合。生产默认 light 为 128 tokens，需在正式实验中放开。
4. **AR005 增量在长上下文上验证了预算收益**（prompt↓39%，质量不变），但在短问答上未超过 B2/B0；
   选择器正确 adopt B0 或 rollback B2，体现“无增益即回退”。
5. **环境风险**：本机长跑与外部负载下 llama-server 偶发假死；矩阵已改为**每 arm 重启大脑**
   并写 `progress.json` 检查点，避免整轮丢失；AR006 另提供 `BrainSupervisor`（探针→重启→重试一次）
   与 embedding 子进程自愈，真实集成测试覆盖。
6. **AR006 AEB**（POST 修复后）：prompt 2921→1590（−45.6%），质量 0.833 三臂一致；
   但 embedding 选块带来额外延迟。生产建议：长文档/内存受限场景用 AEB，
   低延迟短问答保持 B1（选择器按目标函数选择）。
7. **AR007 前缀复用**（POST 修复后 GPU）：prefill 8202→927ms（8.8×），端到端 48.4→40.5s；
   质量不变；但 AEB 的按问题选块会破坏前缀，AEB_CACHE 命中为 0。
8. **选择器必须计入延迟**：token-only 效用会误选慢但省 token 的 AEB_CACHE；
   新增 `lambda_latency` 后正确选择，这是组合冲突的可执行解法。
9. **POST 复核曾系统性失效**：长 state 保头部导致答案不可见，恒重试使长 prompt 表被放大；
   已修复为保尾部，并以修复后复测表为准。

## 下一步（正式复现）

- 用 `max_tokens=128`（或按 light 设计）重跑 30 题，避免截断偏差；
- 在**正常未限频 GPU** 上运行（本机 GPU 仅 645 MHz，见下节），使 `risk.json` / `admission.json` 生成；
- 增加长上下文 needles 40 题，验证 JCBA 的 prompt token ↓≥30% 且保留率下降 ≤2pt；
- 服务器 7B/14B 按 `REPRODUCTION.md` 执行，结果单独补录。

## 环境更正与 GPU 复测（2026-10-27）

### 1. 历史结果实为 CPU-only

- `llama-server --list-devices` 最初返回 `(none)`：`ggml-cuda.dll` 依赖的
  `cudart64_12.dll` / `cublas64_12.dll` / `cublasLt64_12.dll` 缺失。
- 因此上文 AR005–AR007 的旧数字（含旧 27.7× prefill）均为 **CPU-only**，
  不是 GPU 结果；代码与配置未变，仅运行设备需更正。
- 补齐 CUDA 12.4 redistributable 后设备可见，但 GPU 被硬限制在 **645 MHz / ~32 W**
  （`nvidia-smi -lgc` 权限拒绝）；`llama-bench`：`tg64` GPU **5.06 t/s** vs CPU **9.47 t/s**。
  本机 GPU 反而更慢，故 CPU 仍是默认。

### 2. AR007 GPU 复测（`ar007-prefix-cache-gpu-20261029-130230`，POST 修复后）

| arm | quality | prefill | mean latency | cache 命中 |
| --- | --- | --- | --- | --- |
| B1 | 1.000 | 8202 ms | 48.4s | 0/12 |
| CACHE | 1.000 | **927 ms** | **40.5s** | 11/12 |

prefill **8.8×**，端到端 ↓16.3%；缓存收益在 GPU 上仍成立，但绝对延迟受限于 GPU 低频。
旧表（68.9s → 58.5s / 25.8×）包含 POST 无条件重试，已废弃。

### 3. AR006 GPU 复测（`ar006-needles-combos-gpu-20261029-145415`，POST 修复后）

| arm | quality | prompt tok | mean latency |
| --- | --- | --- | --- |
| B1 | 0.833 | 2921.3 | 58.9s |
| P60 | 0.833 | 1896.7 | **57.0s** |
| AEB | 0.833 | **1589.7** | 61.9s |

- 三臂两两输出一致（`outputs_identical=1.0`），质量同为 11/12；失败题固定为
  `needle-04`（数字错误）与 `needle-08`（退化重复经守卫重试后仍截断）。
- 旧表 0.917 依赖“每次无条件重试”的偶然修复，且绝对延迟翻倍；新表为单次生成。
- AEB 仍将 prompt 从 2921 降至 1590（−45.6%）；选择器 `adopt AEB`（效用下界 +0.048）。
- 注意：本配置未 `force_full`，部分 needles 走 `light` + MID 多调用，是延迟主因。

### 4. AR008 投机解码（详细见 `specs/changes/AR008-heterogeneous-speculative/`）

- 中文 12 题（n8/t8）：B1 8.51 tps / 21.0s；SPEC 9.07 tps / 21.9s，接受率 0.187 → rollback。
- 英文 12 题（GPU target）：B1 5.36 tps / 56.2s；SPEC 4.78 tps / 61.0s，接受率 0.39 → rollback。
- CPU 对照（4 题，draft 长度扫描）：B1 8.10 tps；S2 5.83（接受率 0.624）；
  S4 5.03（0.470）→ 均慢于 B1，`rollback`。
- 结论：0.5B CPU draft 在本机**不带来稳定解码收益**，默认 `spec.enabled=false`；
  代码/配置/指标保留，供正常 GPU 主机复现。

> 未达标即未达标：上述数字不支持「AR008 已超越 B1」；回退机制按设计工作。

## AR009 CASC：稳定压缩 + 前缀缓存 + 异构放置

### 1. shared-prefix 12 题（CPU 解码 + GPU embedding，ratio 0.4，POST 修复后）

| arm | quality | prompt tok | prefill | mean latency | cache 命中 |
| --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 2662 | 12693 ms | 52.9s | 0/12 |
| CACHE | 1.000 | 2662 | **1585 ms** | 42.2s | 11/12 |
| CASC | **1.000** | **1079（−59.5%）** | **796 ms** | **38.1s** | **11/12** |

- 三臂输出完全一致（`outputs_identical=1.0`），quality 1.0。
- 旧表的 5354 / 3771 是 POST 重试把两次调用累加所致；真实 prompt 为 2662 / 1079。
- CASC 压缩后 token 更少且 LLM prefill 更短，端到端首次低于 CACHE（−28.0% vs B1）。
- 反转 arm 顺序复测结论不变；CASC 的 `decode_tps` 抖动属本机 CPU 调度噪声。

### 2. needles 6 题（单文档单问，无跨问缓存，POST 修复后）

| arm | quality | prompt tok | prefill | mean latency | cache |
| --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 1847 | 8912 ms | **47.6s** | 0 |
| CASC | **1.000** | **762（−58.7%）** | **3747 ms** | 50.0s | 0 |

查询无关压缩仍保留目标 needle（quality 1.0）；代价是 embedding 首问开销，
且压缩改写导致 2/6 题输出文本不同（答案仍正确）。选择器按 token 效用 `adopt CASC`。

### 3. 组合权重扫描（POST 修复后同一份 12 题，min_samples=12）

| λ_cost | λ_latency | 选择 | 结论 |
| --- | --- | --- | --- |
| 0.00 | 0.00 | rollback B1 | 质量全 1.0，无差异 |
| 0.00 | 0.50 | **CASC** | 只看延迟也选 CASC |
| 0.15 | 0.50 | **CASC** | 默认效用 |
| 0.30 | 0.50 | **CASC** | 仍选 CASC |
| 0.50 | 0.50 | **CASC** | 成本权重上升 |
| 0.80 | 0.00 | **CASC** | 纯 token 成本最优 |

**核心结论：**
1. CASC 解决了 AR006/AR007 的“压缩破坏前缀”冲突：query-independent 压缩 + 压缩 LRU，11/12 命中且 quality 1.0。
2. POST 修复后 CASC 在 shared-prefix 上同时赢 token 与延迟（38.1s vs CACHE 42.2s / B1 52.9s）；
   旧表“CACHE 最低延迟”是被重试污染后的错误结论。
3. GPU 放置只放 prefill 密集的 embedding（实测 4.20s→3.09s，−26.5%）；brain 保持 CPU 解码，
   避免本机 645 MHz GPU 的 decode 劣势。
4. 本机所有结论按真实后端记录；7B/14B 仅提供配置，未运行不外推。

## AR010 LADS：Laya 决策调度（POST 盲区修复后）

### 1. 缺陷与修复

- 旧 `laya_onnx.system_one` 与 `laya_sequence.build_sequence` 对超长 state **保留头部**，
  `max_len=512`；shared-prefix 输入约 2.6k tokens，assistant 答案位于尾部被整体截掉。
- 结果：POST 对任何答案（正确/空/英文）给出**完全相同**判定，`needs_retry` 恒为 `retry`，
  每次请求都无条件生成第二次；`usage.prompt_tokens` 因此约为单次的 2 倍。
- 修复：批处理与序列构造均改为**保留尾部**；新增退化重复检测（短周期全串重复）触发一次重试。
- 真实回归：`tests/integration/test_laya_post_window_real.py` 证明长 state 下正确长答与空答
  的 POST 决策可区分；`tests/unit/test_chat_cascade.py::test_degenerate_repetition_triggers_one_retry`
  覆盖退化重试。

### 2. LADS 12 题（`ar010-lads-20261029-120142`）

| arm | 决策模式 | quality | prompt tok | mean latency | p50 | cache 命中 |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | 完整 PRE+shadow+POST | 1.000 | 2662 | 54.0s | 54.4s | 0/12 |
| FAST | 跳过 shadow+POST | 1.000 | 2662 | **34.2s（−36.7%）** | 35.1s | 0/12 |
| CACHE | 完整决策 + 前缀 KV | 1.000 | 2662 | 42.0s | 41.8s | 11/12 |
| CACHE_FAST | 跳过 shadow+POST + 前缀 KV | **1.000** | 2662 | **24.6s（−54.4%）** | 24.3s | 11/12 |

- 四臂 `outputs_identical=1.0`：跳过 shadow/POST 不改变输出。
- 选择器 `adopt CACHE_FAST`，效用下界 **+0.2539**。
- 收益来源：`X-Jev-Decision-Mode: fast` 跳过 Laya shadow 与 POST 复核（约 11s/请求），
  叠加前缀 KV（prefill 12.9s→1.6s）。
- 约束：`/v1/systemone` 仍返回完整 active+shadow；fast 模式仅作用于 chat。
- 诚实边界：POST 修复后 Laya 对“短答/空答”仍偏向 `off_topic`，因此 fast 模式以
  “输出一致性 + 外部质量金标”验证，而不是依赖 Laya 自评。
