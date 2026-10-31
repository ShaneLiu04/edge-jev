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

## AR011 ADB：Laya 决策计算预算 + 联合最优

### 1. 瓶颈与取舍

- AR010 修复后，shared-prefix 12 题的 Laya PRE 仍约 **22s/请求**（10 个决策问题，
  `intent` 占 8 个置换）。
- 真实验证：置换 4→1 后，6 条 shared + 8 条多样题（中英/needles/生成）的
  **applied 决策 100% 一致**，PRE 22.1s→11.3s（2.0×）。
- 单探针：一致率 95%，有 2 处翻转 → 不采用。
- 负结果：
  - GPU Laya（ORT CUDA 1.19.2 + CUDA12/cuDNN9）：可运行，但限频 645 MHz +
    部分节点回退 CPU，PRE **30.6s > CPU 25.7s**。
  - 槽位绑定/`id_slot`：llama.cpp 已原生多前缀缓存，A/B/C 交替仍命中，无增益。
  - 状态窗口 head+tail：一致率 84%，出现 safety 翻转，拒绝。

### 2. 隔离实验（`ar011-adb-20261029-192424`，force_full，12 题）

| arm | quality | mean latency | P50 | prefill | cache 命中 | utility |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 45.6s | 48.0s | 8997 ms | 0/12 | 0.350 |
| ADB | 1.000 | 31.5s | 30.2s | 8527 ms | 0/12 | 0.505 |
| ADB_CACHE | 1.000 | 29.9s | 29.6s | 1217 ms | 11/12 | 0.522 |
| **ADB_CACHE_FAST** | **1.000** | **13.1s（−71.3%）** | 12.6s | 1259 ms | 11/12 | **0.707** |

四臂 `outputs_identical=1.0`；选择器 adopt ADB_CACHE_FAST（效用下界 +0.3135）。

### 3. 真实路由实验（`ar011-adb-route-20261029-194947`，force_full=false）

| arm | quality | mean latency | routes | 路由差异 |
| --- | --- | --- | --- | --- |
| B1 | 1.000 | 51.1s | full 12 | — |
| ADB | 1.000 | 38.0s | full 12 | 0 |
| **ADB_CACHE_FAST** | **1.000** | **13.4s（−73.8%）** | full 12 | 0 |

预算裁剪未改变任何路由与输出。

### 4. 联合最优（`ar011-adb-casc-20261029-201140`，12 题）

| arm | quality | mean latency | P50 | prompt tok | cache 命中 | utility |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 49.2s | 49.6s | 2662 | 0/12 | 0.350 |
| ADB_CACHE | 1.000 | 29.9s | 29.4s | 2662 | 11/12 | 0.546 |
| ADB_CASC | 1.000 | 30.3s | 29.0s | **1079** | 11/12 | 0.631 |
| **ADB_CASC_FAST** | **1.000** | **14.1s（P50 12.1s）** | 12.1s | **1079** | 11/12 | **0.796** |

选择器 adopt **ADB_CASC_FAST**（+0.4054）：token 与延迟综合最优；
若只看均值延迟，ADB_CACHE_FAST（13.1s）略优，可作为纯延迟档。
两档均保留于 `profiles/latency-best.toml` 与实验配置。

### 5. 组合结论

- **ADB 是独立增益**：在 AR007/AR009/AR010 之上再降 30%（仅决策）到 47%（联合）。
- 推荐组合（端侧、本地 CPU，GPU 仅 embedding）：
  `CASC + 前缀 KV + LADS fast + decision_permutations=1`。
- 回退规则：若质量或路由不一致，选择器回退到 AR010 最优组合（CACHE_FAST）。
- 大模型服务器（7B/14B）已提供 `server-{7b,14b}-adb-casc.toml`，
  需在目标服务器实测后再宣称收益。


## AR012 CADP?????? + ???????

### 1. ???????

AR011 ? PRE ?? 11s?perm1???????????????safety=safe?intent?decision??

- `needs_reasoning=reasoning` ? ?? full?light/direct ??? no_reasoning??
- ???? ? ?? light?
- `needs_reasoning=no_reasoning` ??? `needs_generation/complexity` ?? direct/light/full?

?? wave1 = safety/intent/needs_reasoning???????? wave2?

### 2. ???????

`tests/integration/test_cadp_route_real.py`??? Laya?6 ??? prompt??
???????? PRE ?? **100% ??**?189.8s??

### 3. ?????`ar012-cadp-20261029-230014`?force_full=false?12 ??

| arm | quality | mean latency | P50 | ???? | staged | utility |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 52.6s | 52.6s | 9 | ? | 0.350 |
| ADB | 1.000 | 42.1s | 42.0s | 9 | ? | 0.450 |
| CADP | 1.000 | 30.9s | 31.0s | **7** | on | 0.556 |
| **CADP_FAST** | **1.000** | **21.7s??58.7%?** | 21.8s | **3** | on | **0.644** |

12/12 ?? full??????????? adopt CADP_FAST?

### 4. ?????`ar012-cadp-casc-20261029-233045`?12 ??

| arm | quality | mean latency | P50 | prompt tok | cache | utility |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 51.6s | 52.1s | 2662 | 0/12 | 0.350 |
| ADB_CACHE_FAST?AR011? | 1.000 | 13.5s | 12.7s | 2662 | 11/12 | 0.719 |
| **CADP_CACHE_FAST** | **1.000** | **10.0s??80.6%?** | 8.9s | 2662 | 11/12 | 0.753 |
| **CADP_CASC_FAST** | **1.000** | 10.6s | **8.8s** | **1079** | 11/12 | **0.837?adopt?** |

- ?????????CADP ?? AR011 ?? **26%**?13.5s?10.0s??
- ???? CADP_CACHE_FAST?token+????? CADP_CASC_FAST?
- ?????`profiles/latency-fast.toml` / `latency-best.toml` ?????

### 5. ??????????

`CASC + ?? KV + LADS fast + ???? 1 + ?????`?
shared-prefix 12 ? **51.6s ? 10.0?10.6s?? 5??**?quality 1.0??????
tokens 2662?1079?CASC ????????????????????? AR011 ???

## AR013 RDMP：最小路由探针 + 保守确认

### 1. 问题

AR012 CADP 后 wave1 仍为 6 个 qid（safety 2 置换 + intent 2 探针 + needs_reasoning 2 置换），
是 Laya 主要成本。

### 2. 真实探测

16 条多样 prompt（质量/needles/生成/shared）：wave1 用“1 探针 + 1 置换”（3 qid）：
路由翻转 2/16，**全部为 LIGHT→FULL（保守，不降质）**；无激进翻转。

### 3. 策略

最小 wave1 判 FULL → 直接采用；否则用完整 wave1 复算确认，再走 wave2。
`decision_staged_minimal` 默认关闭；确认路径消除激进翻转。

### 4. 真实回归

`tests/integration/test_rdmp_route_real.py`：8 条真实 Laya prompt，
激进翻转 0、保守翻转 ≤2、确认路径至少 1 次（160.7s）。

### 5. 隔离矩阵 `ar013-rdmp-20261030-100203`（12 题）

| arm | quality | mean latency | P50 | 决策数 | utility |
| --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 36.8s | 36.7s | 9 | 0.350 |
| CADP | 1.000 | 21.2s | 20.9s | 7 | 0.563 |
| **RDMP** | **1.000** | **19.7s** | **19.5s** | 7 | **0.582（adopt）** |

RDMP 相对 CADP 再降约 6.7%，质量/输出一致。

### 6. 联合矩阵 `ar013-rdmp-casc-20261030-101917`（12 题）

| arm | quality | mean latency | P50 | prompt tok | cache | utility |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 37.0s | 37.5s | 2662 | 0/12 | 0.350 |
| CADP_CACHE_FAST | 1.000 | 6.85s | 6.02s | 2662 | 11/12 | 0.758 |
| RDMP_CACHE_FAST | 1.000 | 5.05s | 4.33s | 2662 | 11/12 | 0.782 |
| **RDMP_CASC_FAST** | **1.000** | 5.59s | **4.33s** | **1079** | 11/12 | **0.864（adopt）** |

- 相对 CADP_CACHE_FAST：P50 6.02s→4.33s（**−28%**），mean 6.85s→5.05s（−26%）。
- token+延迟综合最优仍为 CASC 版本：tokens 2662→1079（−59.5%），P50 4.33s。
- `latency-fast` / `latency-best` 已启用 `decision_staged_minimal=true`。
- 负结果：缩短 wave1 状态窗口（384/320/256）导致 needle 路由翻转，暂不采用。

## AR014 LCPT-RFG：长上下文精度分层 + reasoning-first 门控

### 1. 问题与探测

AR013 RDMP_CASC_FAST 已到 P50 4.33s，剩余成本主要是 Laya wave1 的 3 个 512-token 决策行。

真实探测（16 条多样 prompt）：

- 动态 int8 Laya：1.5× 加速，翻转仅保守方向；
- 长上下文（>4000 tokens）先算 `needs_reasoning`：applied≠no_reasoning 直接 FULL；
  与 fp32 RDMP 路由 **16/16 一致、0 激进翻转**；
- shared 6 题 Laya 决策 5.45s→2.56s（**2.1×**）；needles（~2810 tokens）保持 fp32。

### 2. 实现

- `jev prepare-laya-int8`：合并外部数据 + ORT 动态 int8（QInt8/per-channel），
  产物 `models/laya/laya_int8.onnx`（~427MB，gitignored）。
- `decision_long_context_tokens=4000`：仅超长 state 启用 int8 reasoning 门控；
  未命中严格回退 AR013 RDMP。
- 观测：`X-Jev-Decision-Long: int8-reason-full`。
- 真实回归：`test_lcpt_real.py` 6 条 shared 长文，路由与 RDMP 一致（49.3s）。

### 3. 隔离矩阵 `ar014-lcpt-20261030-133457`（12 题）

| arm | quality | P50 | 决策数 | long 头 |
| --- | --- | --- | --- | --- |
| B1 | 1.000 | 44.36s | 9 | — |
| RDMP | 1.000 | 23.00s | 7 | — |
| **LCPT** | **1.000** | **19.46s（−15.4%）** | **5** | int8-reason-full |

### 4. 联合矩阵 `ar014-lcpt-casc-20261030-135323`（12 题）

| arm | quality | P50 | prompt tok | cache |
| --- | --- | --- | --- | --- |
| B1 | 1.000 | 44.43s | 2662 | 0/12 |
| RDMP_CACHE_FAST | 1.000 | 6.63s | 2662 | 11/12 |
| RDMP_CASC_FAST | 1.000 | 6.43s | 1079 | 11/12 |
| LCPT_CACHE_FAST | 1.000 | 2.53s | 2662 | 11/12 |
| **LCPT_CASC_FAST** | **1.000** | **2.42s** | **1079** | 11/12 |

- 相对 RDMP_CACHE_FAST：P50 **6.63s→2.53s（−62%）**；
- 相对 B1：**44.43s→2.42s（−94.6%）**，quality 1.0、输出一致、tokens 1079；
- 选择器 adopt `LCPT_CASC_FAST`。

### 5. needles 回归 `ar014-lcpt-needles-20261030-140824`（6 题）

| arm | quality | P50 |
| --- | --- | --- |
| B1 | 0.833（旧基线） | 49.76s |
| RDMP | 1.000 | 38.25s |
| **LCPT** | **1.000** | **37.88s** |

needle state ~2810 < 4000，完全走 fp32 RDMP，无回退、无质量下降。

## AR015 AGW：自适应门控窗口

### 1. 探测

int8 门控在 shared-prefix 12 题上的窗口扫描：

| 窗口 | 命中 reasoning | 门控均值 |
| --- | --- | --- |
| 192 | 9/12 | 456ms |
| 256 | 9/12 | 639ms |
| 320 | 8/12 | 867ms |
| **384** | **12/12** | **1046ms** |
| 448 | 10/12 | 1273ms |
| 512 | 10/12 | 1513ms |

384 命中率最高且比 512 快约 30%；未命中仍回退 fp32 RDMP。

### 2. 实现

- `decision_long_context_gate_tokens`（默认 384，0=完整窗口）；
- 仅 int8 门控后端使用短窗口，fp32 兜底不受影响；
- `X-Jev-Decision-Long: int8-reason-full:w384`。

### 3. 隔离矩阵 `ar015-agw-20261030-152144`（12 题）

| arm | quality | P50 |
| --- | --- | --- |
| B1 | 1.000 | 47.94s |
| RDMP | 1.000 | 24.75s |
| LCPT512 | 1.000 | 20.00s |
| AGW384 | 1.000 | 20.20s |

隔离场景被 uncached prefill 主导，门控收益被掩盖，**无正收益**。

### 4. 联合矩阵 `ar015-agw-casc-20261030-154608`（12 题）

| arm | quality | mean | P50 | prompt tok |
| --- | --- | --- | --- | --- |
| LCPT_CACHE_FAST | 1.000 | 3.326s | 2.214s | 2662 |
| LCPT_CASC_FAST | 1.000 | 3.836s | 1.984s | 1079 |
| AGW_CACHE_FAST | 1.000 | 3.260s | 2.168s | 2662 |
| **AGW_CASC_FAST** | **1.000** | **3.765s** | **1.969s** | **1079** |

- 相对 LCPT_CASC_FAST：mean −1.9%、P50 −0.8%、utility 0.9024→0.9031，选择器 adopt；
- 收益幅度小，属门控层优化；若后续更大改动无增益，应回退到本组合。

## AR016 GSP：GPU 服务放置

### 1. 背景

当前最优 AGW_CASC_FAST 是“短输出 + 长 prefill”，与此前“长输出 GPU 更慢”的结论不同。
真实同配置对照（shared-prefix 12 题，embedding GPU 均开启）：

| 放置 | quality | mean | P50 | prefill | decode tps |
| --- | --- | --- | --- | --- | --- |
| CPU decode（gpu_layers=0） | 1.000 | 3.462s | 2.032s | 726ms | 7.83 |
| **GPU offload（gpu_layers=99）** | **1.000** | **3.054s** | **1.753s** | **357ms** | 7.78 |

B1 也从 48.67s→45.44s（prefill 8.97s→5.75s）。GPU 放置 mean −11.8%、P50 −13.7%，
输出一致、质量 1.0。

### 2. 实现

- `[cerebrum] gpu_layers`（默认 0）真正传入 `llama-server --n-gpu-layers`；
- 新增 `configs/profiles/latency-best-gpu.toml`（GPU 部署档）；
- `ar016-gpu-cpu.toml` / `ar016-gpu-offload.toml` 为可复现同题对照；
- 服务器 7B/14B 使用已有 external brain 启动参数 `--n-gpu-layers`。

### 3. 结论

- **GPU 放置纳入当前综合最优**：AGW_CASC_FAST + gpu_layers=99；
- 无 GPU/显存不足保持 `gpu_layers=0`，行为与 AR015 相同；
- 本机 GPU 被限频，仍获得正收益；高速服务器应重新实测后更新结果。

## AR017 CGW：级联门控窗口

### 1. 背景

AGW 单窗口 384 在 shared 12 题上命中 12/12，但每请求都要跑完整 384 token
状态的 int8 前向。多数请求在更短前缀即可判定 FULL，级联“短窗优先、命中即停”
可在不改变路由结论的前提下缩短门控。

### 2. 实现

- `LayaOnnxBackend.windowed(max_state_tokens)` 从共享 ONNX session 生成浅拷贝，
  不重复加载 int8 模型；
- `[runtime] decision_long_context_gate_windows`（默认 `(128, 384)`，空列表=单窗口）；
- chat 门控按升序逐级判定，命中 FULL 即停，全部未中回退 fp32 RDMP；
- `X-Jev-Decision-Long` 记录实际链，如 `int8-reason-full:w128` / `w128+w384`。

### 3. 真实探测

`tests/integration/test_lcpt_real.py` 的级联用例：128→384 链命中 18/18，
mean 361ms、P50 200ms、平均 1.22 级；共享同一 int8 session。

### 4. 隔离矩阵 `ar017-cgw-20261030-182328`（12 题，GPU offload）

| arm | quality | mean | P50 | tokens | 实际门控链 |
| --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 36.587s | 38.285s | 2662 | — |
| AGW | 1.000 | 16.540s | 16.538s | 2662 | w384 |
| **CGW** | **1.000** | **16.338s** | **16.296s** | **2662** | **w128** |

隔离场景 uncached prefill 占主导，CGW 仅小幅下降（P50 −1.5%），但门控链已按预期
在首级 128 命中。

### 5. 联合矩阵 `ar017-cgw-casc-20261030-222210`（12 题，GPU offload）

| arm | quality | mean | P50 | tokens | 实际门控链 |
| --- | --- | --- | --- | --- | --- |
| B1 | 1.000 | 52.338s | 52.644s | 2662 | — |
| AGW_CACHE_FAST | 1.000 | 2.286s | 1.849s | 2662 | w384 |
| AGW_CASC_FAST | 1.000 | 2.970s | 1.658s | 1079 | w384 |
| CGW_CACHE_FAST | 1.000 | 1.531s | 1.044s | 2662 | w128 |
| **CGW_CASC_FAST** | **1.000** | **2.063s** | **1.001s** | **1079** | **w128** |

- 相对 AGW_CASC_FAST：P50 1.658s→**1.001s（−39.6%）**，mean −30.5%，
  tokens/quality/输出一致性均不变；选择器 adopt CGW_CASC_FAST；
- CACHE 档同样从 1.849s→1.044s（−43.5%），说明收益来自门控窗口而非压缩路径；
- 首次运行曾受外部 WSL/Hyper-V GPU 争用污染（decode 2.8 tps），清理后复现稳定。

### 6. 结论

- **级联门控纳入当前最优**：CGW_CASC_FAST + GPU offload，P50 **1.001s**；
- 历史配置显式写入 `decision_long_context_gate_windows = []` 保持单窗口行为；
  当前 profile/服务器配置使用 `[128, 384]`；
- 无 int8 资产或窗口配置为空时自动退化为 AR015/AR013 路径。

### 7. 验证

- 本机全量：`721 passed`，`ruff` 零告警；
- Gitee 浅克隆：`718 passed`，另 3 个依赖 `models/` 真实资产的用例
  （doctor/serve）在无资产环境按预期失败；AR017 相关 57 个纯逻辑用例全通过、lint 干净。

## AR018 GTB：证据接地的双脑级联

### 1. 背景与真实探测

AR017 后 shared-prefix 稳态约 1.0s，其中解码约 0.6s。真实对照：

| 模型 | shared 12 题质量 | shared 稳态 P50 | needles 12 题质量 | 解码 tps |
| --- | --- | --- | --- | --- |
| Qwen2.5-1.5B q4 | 1.000 | 1001ms | 1.000 | 9.5 |
| Qwen2.5-0.5B q4 | 0.750（联合压缩） | 497ms | 1.000 | 21.5 |

0.5B 的错误答案多为其它针的编号；用“问题锚点所在段落”的证据窗口验证后，
真实 shared 12 题接受 7~9 个正确答案、**0 个错误答案被接受**，失败自动升级 1.5B。

### 2. 实现

- `[cerebrum] tier_enabled/small_model/small_gpu_layers/tier_ground_window/
  tier_max_answer_chars/tier_prewarm`，默认关闭；
- `TieredBrain`：full 路由先小模型，`grounded_verify` 通过即用；否则大模型；
  小模型超时/不可达直接回退大模型；
- 接地验证：问题锚点（标识符/长数字/引号/英文词/中文片段）+ 文档段落内窗口，
  纯确定性、无模型/网络；
- 同一文档第二次出现时预热大模型 1 token prefix KV（避免独立文档额外开销）；
- 响应头 `X-Jev-Tier` / `X-Jev-Tier-Reason`；light/streaming/POST 路径不变。

### 3. 隔离矩阵 `ar018-gtb-20261031-102551`

| arm | quality | mean | P50 | 实际分派 |
| --- | --- | --- | --- | --- |
| B1 | 1.000 | 43.743s | 43.861s | — |
| AGW | 1.000 | 16.496s | 16.528s | — |
| CGW | 1.000 | 13.127s | 13.080s | — |
| **GTB** | **1.000** | **12.924s** | **10.363s** | 7 small / 5 escalate |

隔离下 uncached prefill 占主导；GTB P50 −20.8%，0 错误接受。

### 4. 联合矩阵 `ar018-gtb-casc-20261031-115815`

| arm | quality | mean | P50 | tokens | 分派 |
| --- | --- | --- | --- | --- | --- |
| AGW_CASC_FAST | 1.000 | 2.042s | 2.006s | 1079 | — |
| CGW_CASC_FAST | 1.000 | 1.452s | 1.493s | 1079 | — |
| GTB_CACHE_FAST | 1.000 | 1.410s | 0.872s | 2662 | 7 small / 5 escalate |
| **GTB_CASC_FAST** | **1.000** | **1.009s** | **0.721s** | **1079** | **9 small / 3 escalate** |

- 选择器 adopt `GTB_CASC_FAST`，utility 0.9281 vs CGW 0.9233；
- P50 −51.7%、mean −30.5%（该轮外部 GPU 争用使 CGW 也偏慢，但同轮相对比较有效）；
- 后续 `ar018-gtb-casc-20261031-144049` 在持续外部 GPU 争用下复现为
  **P50 0.684s vs CGW 1.035s（−33.9%）**，mean 1.062s vs 1.030s（+3.1%），
  选择器按 mean 回退 CGW。**结论：GTB 是交互 P50 优化档，mean 与 CGW 基本持平；
  对 P50 敏感场景启用 GTB，对 mean/混合负载保守用 CGW。**

### 5. needles 回归 `ar018-gtb-needles-full-20261031-124016`

- force_full 12 题：GTB 12/12 小模型接地、quality 1.0；
- P50 14.596s vs CGW 15.489s（−5.8%），mean 19.693s vs 18.184s（+8.3%，含外部波动）；
- 选择器按 mean rollback CGW；独立文档场景推荐 CGW，GTB 仅对重复/共享文档启用。

### 6. 回退与配置

- 默认 `tier_enabled=false`；`latency-best-gpu.toml` 显式开启 GTB；
- 无正收益/混合负载可回退 `latency-best.toml`（CGW，mean 更稳）；
- `gtb.toml` / `gtb-casc.toml` / `gtb-needles*.toml` 为可复现实验；
- 使用 `drop_first=true` 排除每个 arm 的冷启动预热请求，并同步作用于选择器。

### 7. 验证

- 本机全量：`731 passed`，`ruff` 零告警；
- 真实双脑集成：`tests/integration/test_tiered_brain_real.py` 1 passed（双 llama-server 真实进程）；
- Gitee 浅克隆：AR018 相关 67 个纯逻辑用例通过、lint 干净；
- 无资产环境不运行 `assets+slow` 用例，符合既有测试纪律。
