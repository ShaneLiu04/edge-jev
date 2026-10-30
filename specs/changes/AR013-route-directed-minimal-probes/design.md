# [AR013] 设计说明书

## 1. 设计概述

在 AR012 CADP 分阶段之上增加 **RDMP**：wave1 先用最小探针（每点 1 探针、1 置换）评估；
若最小结果短路为 FULL 则直接采用；否则用完整 wave1 复算确认，再继续 wave2。
**保守翻转（LIGHT→FULL）允许**，**激进翻转（→DIRECT/LIGHT）由确认路径消除**。

## 2. 模块改动

| 文件 | 改动 |
|------|------|
| `kernel/decisions/framework.py` | 新增 `minimize_points(points)`：`permutations=1`，`probes=()` |
| `kernel/cascade/engine.py` | 新增 `needs_minimal_confirmation(short)`：`None`/非 FULL → True |
| `kernel/runtime/config.py` | `decision_staged_minimal: bool = False` |
| `kernel/kernel.py` | 读取 `decision_staged_minimal` |
| `server/chat.py` | `evaluate_pre` 最小 wave1 + 确认；`X-Jev-Decision-Minimal` |
| `configs/jev.toml`, profiles, experiments | 开关与实验配置 |

## 3. 关键流程

```text
staged && minimal:
  first = evaluate(minimal_wave1)          # 3 qid
  short = route_short_circuit(first)
  if short is FULL:
      accept(first); mark minimal=on
  else:
      first = evaluate(wave1)              # 6 qid，完整探针/预算
      short = route_short_circuit(first)
      mark minimal=confirm
  if short is None: evaluate(wave2) ...     # AR012 原逻辑
```

只对**最终采用**的决策记账/写台账，避免最小结果重复计数。
`minimal` 仅当 `decision_staged=true` 且无其他 PRE 消费者时生效；否则完全走 AR012。

## 4. 正确性

- 最小结果=FULL：可能把真实 LIGHT 判成 FULL（保守，不降质，仅多算）。
- 最小结果≠FULL：完整复算，保证不会出现最小探针造成的激进路由。
- 真实数据（16 条含 needles/生成/shared）：翻转 2/16，全部保守；确认后激进翻转 0。

## 5. 兼容与回退

- 默认关闭，线协议只新增头。
- 若端到端无正收益或质量下降，profile 关闭 `decision_staged_minimal` 即回退 AR012。

## 6. 测试

- 单测：`minimize_points`、`needs_minimal_confirmation`、`RuntimeConfig` 校验、
  `evaluate_pre` 三条分支（最小接受/确认后短路/确认后 wave2）。
- 真实：`tests/integration/test_rdmp_route_real.py` 断言激进翻转 0、保守翻转可记录。
- 端到端矩阵：CADP vs CADP_MIN vs CADP_MIN_CASC。
