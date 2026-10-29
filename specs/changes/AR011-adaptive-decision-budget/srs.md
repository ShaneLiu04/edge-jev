# [AR011] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR011 |
| AR 主题 | Adaptive Decision Budget（ADB）：Laya 决策计算预算与联合最优 |
| 关联 SR | 端侧双系统在质量不降前提下继续降低真实端到端延迟 |
| 日期 | 2026-10-29 |
| 状态 | Approved（自动化 harnessX，用户已授权无需逐步确认） |

## 1. 背景与目标

AR010 修复 POST 盲区并引入 LADS 后，shared-prefix 12 题的端到端延迟仍为
B1 54.0s / FAST 34.2s / CACHE_FAST 24.6s。实测分解显示：

- LLM prefill（首问）约 12.9s，缓存后约 1.6s；
- LLM decode 约 1s；
- **Laya PRE 决策约 22s/请求**，成为最大瓶颈。

Laya PRE 的 10 个决策问题中，`intent` 占 8 个（2 个探针 × 4 个选项置换），
`needs_reasoning`/`safety` 各 2 个。置换用于抑制选项位置偏差，但真实数据上
多余置换是否改变 **applied 决策** 尚未验证。

本 AR 目标：

1. 引入**可配置决策计算预算**，允许限制每个决策点的置换数（probe/permutation）。
2. 用真实 Laya 在多样本上验证“预算裁剪后的 applied 决策与完整预算一致”。
3. 提供**可选的置信度升级**：仅当消费方需要置信度且 margin 低时，才对少数点用完整预算。
4. 把 ADB 与 AR007（前缀 KV）、AR009（CASC）、AR010（LADS）组合，测量联合收益；
   若无正收益或质量/路由不一致则回退。
5. 记录并排除无效方向：GPU Laya、槽位绑定、状态窗口压缩。

## 2. 需求范围

**In Scope**
- `RuntimeConfig.decision_permutations`（0=不限制）与 `decision_escalate_margin`。
- `configs/jev.toml` 配置项、Kernel 装配、chat 评估路径接入。
- 决策点预算裁剪与可选升级逻辑。
- 真实 Laya 决策一致性回归（assets 测试）。
- 矩阵实验：B1 / ADB / ADB_CACHE / ADB_CACHE_FAST；真实路由实验。
- 文档、复刻说明、Gitee 同步。

**Out of Scope**
- 修改 Laya 模型权重或重新训练。
- GPU Laya（实测 CUDA 30.6s > CPU 25.7s，放弃）。
- 槽位绑定/Radix 调度（llama.cpp 已原生多前缀缓存，无增益）。
- 缩小决策状态窗口（head+tail 一致率仅 84%，且有 safety 翻转，拒绝）。
- 7B/14B 实机运行（仅完善配置与复刻说明）。

## 3. 功能需求

| ID | 需求 | 验收 |
|----|------|------|
| FR11-1 | `decision_permutations=0` 时行为与 AR010 完全一致 | 默认配置回归通过 |
| FR11-2 | `>0` 时每个决策点的 `permutations` 裁剪为 `min(原值, 预算)` | 纯逻辑单测覆盖 |
| FR11-3 | 裁剪同时作用于 PRE/POST/MID，shadow 与 active 一致 | 单测 + 集成 |
| FR11-4 | `decision_escalate_margin>0` 时，低 margin 且被裁剪的 active 点用完整预算重算并合并 | 单测 |
| FR11-5 | 真实 Laya：裁剪预算与完整预算的 applied 决策一致率 ≥95%（目标 100%） | assets 测试记录 |
| FR11-6 | 端到端实验：质量不降、路由不降、延迟配对 CI 下界 >0 才 adopt | matrix 真实运行 |
| FR11-7 | 新能力默认关闭；通过实验 overlay / profile 显式启用 | 配置审查 |
| FR11-8 | 观测：`X-Jev-Decisions`、`X-Jev-Decision-Mode`、`decision_permutations` 快照 | 头/台账 |

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | 长 state PRE 决策 | 预算 1 时 ≤ 完整预算的 60% |
| 质量 | applied 决策一致率 | ≥95%，目标 100% |
| 可靠性 | 升级路径 | 失败或无收益自动回退 |
| 测试 | 真实后端 | 无 mock；Laya/llama-server 均为真实资产 |

## 5. 约束与假设

**约束**
- 只改决策计算预算，不改 Laya 权重与线协议。
- 所有实验必须使用真实 Laya ONNX 与真实 llama-server。
- 历史结果口径：AR010 已修复 POST 重试污染。

**假设**
- 置换主要抑制位置偏差；在真实业务 prompt 上，1 个置换已足够稳定。
- 若消费方需要 margin（`margin_tau>0`、verifier 开启），可通过升级恢复完整预算。

## 6. 术语

| 术语 | 定义 |
|------|------|
| 探针 probe | 同一决策点的异构提问视角 |
| 置换 permutation | choice 点选项顺序的随机重排 |
| applied 决策 | 经过 tau/fallback 后实际生效的标签 |
| ADB | 决策计算预算：限制探针/置换以降低 Laya 计算 |
