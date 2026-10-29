# [AR012] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR012 |
| AR 主题 | Consumer-Aware Decision Planner（CADP）：消费者感知 + 路由分阶段决策 |
| 关联 SR | 端侧双系统在质量/路由不降前提下继续降低真实端到端延迟 |
| 日期 | 2026-10-29 |
| 状态 | Approved（自动化 harnessX，用户已授权无需逐步确认） |

## 1. 背景与目标

AR011 后联合最优 shared-prefix 12 题为 13.1–14.1s，但分解显示 **Laya PRE 仍约 11.3s**
（置换预算已降到 1）。PRE 每次都计算 5 个 active 点、合计 10 个 qid，而：

- 当 `route_policy` 强制路由（矩阵/固定 full）且 prompt/verifier/预算等消费者均关闭时，
  PRE 结果**没有任何消费者**；
- 自动路由存在安全短路：
  `safety=unsafe → full`、`intent=decision → direct`、
  `needs_reasoning=reasoning → full`（因为 light 需要 no_reasoning）。

本 AR 目标：

1. 建立“决策点 → 消费者”的显式依赖判断，跳过无用决策计算。
2. 实现**分阶段路由评估**：先算短路所需 3 个点；能确定结果则不再计算其余点。
3. 保证路由/质量/输出与完整评估一致（真实数据验证）。
4. 与 AR007/AR009/AR010/AR011 组合，测量联合收益；无增益则回退。

## 2. 需求范围

**In Scope**
- `RuntimeConfig.decision_staged`（默认 false）。
- chat PRE 的消费者判断与分阶段评估。
- `CascadeEngine` 增加只读的短路判定方法。
- 观测：`X-Jev-Decision-Staged`、`X-Jev-Decisions` 反映实际计算点数。
- 真实 Laya 路由一致性回归、端到端矩阵实验、文档与 Gitee 同步。

**Out of Scope**
- 修改 Laya 权重、线协议、POST/MID 语义。
- 在 verifier/margin/prefill/draft 等需要完整 PRE 的场景强制裁剪。
- 7B/14B 实机运行（仅完善配置）。

## 3. 功能需求

| ID | 需求 | 验收 |
|----|------|------|
| FR12-1 | `decision_staged=false` 时行为与 AR011 完全一致 | 默认回归通过 |
| FR12-2 | 强制路由且无其他消费者时跳过 PRE | 真实响应头 `X-Jev-Decisions=0` 且输出一致 |
| FR12-3 | 自动路由先算 safety/intent/needs_reasoning | 单测 + 真实路由 |
| FR12-4 | safety=unsafe / intent=decision / needs_reasoning=reasoning / 预算不足 时提前退出 | 短路单测 |
| FR12-5 | 无法短路时补算 needs_generation/complexity | 单测 |
| FR12-6 | 需要 prompt/verifier/margin/risk/direct 渲染时，补齐全部 PRE | 单测 |
| FR12-7 | 分阶段路由与完整路由一致率 100%（真实数据） | assets+slow 测试 |
| FR12-8 | 端到端：质量/输出不降，延迟配对 CI 下界 >0 才 adopt | matrix 真实运行 |

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | 长 state PRE | 分阶段 ≤ 完整 60% |
| 一致性 | 路由/质量/输出 | 100% 一致 |
| 可靠性 | 缺失/注册点不足 | 自动回退完整评估 |
| 测试 | 后端 | 无 mock，真实 Laya/llama-server |

## 5. 约束与假设

- 分阶段只在 `runtime.enabled` 且 `decision_staged=true` 时生效。
- 短路规则必须与 `CascadeEngine.route_chat` 的优先级严格一致。
- 默认关闭，通过 `latency-fast` / `latency-best` 显式启用。
