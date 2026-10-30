# [AR013] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR013 |
| AR 主题 | Route-Directed Minimal Probes（RDMP）：最小路由探针 + 保守确认 |
| 关联 SR | 端侧双系统在质量/路由不降的前提下继续降低真实端到端延迟 |
| 日期 | 2026-10-29 |
| 状态 | Approved（自动化 harnessX，用户已授权无需逐步确认） |

## 1. 背景与目标

AR012 CADP 后，shared-prefix 12 题为 10.0–10.6s。分段分解显示 wave1
（safety/intent/needs_reasoning）在 perm1 后仍有 **6 个 qid**，是 Laya 的主要成本。

真实探测（16 条多样 prompt，含中英/needles/生成/shared）：

- wave1 使用“单探针 + 单置换”（每点 1 qid，共 3 qid）：
  - 延迟 7.3s→5.2s；
  - 路由翻转 2/16，**全部为 LIGHT→FULL（更保守，不降质）**；
  - 未出现激进翻转（DIRECT/LIGHT 方向）。

目标：

1. 在 CADP 分阶段基础上，wave1 默认用最小探针（1 探针/1 置换）。
2. **保守确认**：若最小 wave1 判定为非 FULL（direct/light）或无法短路，
   则用完整 wave1（原探针/置换）复算，避免激进路由。
3. 仅 FULL 结果直接接受最小探针，从而在长上下文负载上省 2 个 qid 的 Laya 计算。
4. 真实数据验证：激进翻转 0；质量/输出不降；联合端到端提速。

## 2. 需求范围

**In Scope**
- `RuntimeConfig.decision_staged_minimal`（默认 false，依赖 `decision_staged`）。
- chat 最小 wave1 + 非 FULL 时完整确认。
- 观测 `X-Jev-Decision-Minimal: on|confirm`。
- 真实路由一致性/保守性测试（assets+slow）。
- 端到端矩阵实验、文档与 Gitee 同步。

**Out of Scope**
- 修改 Laya 权重/线协议。
- 在需要完整 PRE 的消费者场景启用。
- 7B/14B 实机运行。

## 3. 功能需求

| ID | 需求 | 验收 |
|----|------|------|
| FR13-1 | `decision_staged_minimal=false` 时行为与 AR012 一致 | 默认回归 |
| FR13-2 | 仅在 `decision_staged=true` 时生效 | 单测 |
| FR13-3 | 最小 wave1：每点 1 探针、1 置换 | 单测/真实计数 |
| FR13-4 | 最小结果=FULL → 直接接受，不确认 | 单测 |
| FR13-5 | 最小结果≠FULL 或 None → 用完整 wave1 确认 | 单测 |
| FR13-6 | 确认后仍 None → 进入 wave2（AR012 逻辑） | 单测 |
| FR13-7 | 真实数据：激进翻转 0；保守翻转允许并记录 | assets+slow |
| FR13-8 | 端到端质量/输出不降，延迟 CI 下界 >0 才 adopt | matrix |

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | 长 state wave1 | ≤ 完整 wave1 的 75% |
| 一致性 | 激进路由翻转 | 0 |
| 可靠性 | 确认失败 | 回退完整 PRE / 保持保守 FULL |
| 测试 | 后端 | 无 mock |

## 5. 约束与假设

- 最小探针可能把 light 判成 full（保守），允许但需统计；不得把 full/light 判成 direct。
- 默认关闭；通过 `latency-fast` / `latency-best` 显式启用。
