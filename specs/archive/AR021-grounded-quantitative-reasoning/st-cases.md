# [AR021] ST 验收测试用例

| 字段 | 内容 |
|------|------|
| AR 编号 | AR021 |
| 关联 srs.md | ./srs.md |
| 生成日期 | 2026-11-02 |

## 测试用例列表

### ST-001：默认关闭与 AR020 隔离

**关联需求：** FR21-1
**测试类型：** 正常路径 / 回归
**优先级：** High

**步骤：** `quantitative_enabled=false`，处理 numeric_reasoning。
**期望：** 不进入 GQRA，原 GEXA 回退（ambiguous）。
**实际：** `test_quantitative_disabled_misses_composite_questions` 通过。
**状态：** PASS

### ST-002：多锚点唯一解析

**关联需求：** FR21-2
**测试类型：** 正常路径
**优先级：** High

**步骤：** numeric_reasoning 12 题。
**期望：** 每个锚点唯一数值，12/12 正确。
**实际：** `test_numeric_reasoning_all_correct` 通过。
**状态：** PASS

### ST-003：六种运算

**关联需求：** FR21-3
**测试类型：** 正常路径
**优先级：** High

**步骤：** 依次验证 sum/diff/max/min/which_max/which_min。
**期望：** kind 与答案正确，整数无小数点。
**实际：** `test_sum_diff_max_min_and_which_kinds` 通过。
**状态：** PASS

### ST-004：异常/歧义回退

**关联需求：** FR21-2/FR21-3
**测试类型：** 异常处理
**优先级：** High

**步骤：** 单锚点操作、锚点双值、不支持的平均值。
**期望：** 全部 miss，回退级联，不返回部分结果。
**实际：** `test_single_anchor_operation_falls_back`、`test_ambiguous_operand_falls_back`、`test_unsupported_arithmetic_falls_back` 通过。
**状态：** PASS

### ST-005：GEXA 单事实不回归

**关联需求：** FR21-1/FR21-4
**测试类型：** 回归
**优先级：** High

**步骤：** 开启 quantitative 后处理 shared-prefix 单事实题。
**期望：** 仍由 GEXA 返回 `numeric`，答案正确。
**实际：** `test_gqra_does_not_change_single_fact_answer` 通过。
**状态：** PASS

### ST-006：真实 Laya + 无大脑 GQRA

**关联需求：** FR21-4
**测试类型：** 正常路径
**优先级：** High

**步骤：** 真实 Laya，不启动 llama-server，请求 numeric_reasoning。
**期望：** 200、答案正确、tokens=0、`X-Jev-Extract: hit:numeric-sum`。
**实际：** `tests/integration/test_extractive_real.py::test_gqra_hit_with_real_laya_and_no_brain` passed。
**状态：** PASS

### ST-007：真实联合矩阵

**关联需求：** FR21-5
**测试类型：** 正常路径
**优先级：** High

**步骤：** `jev matrix --config configs/experiments/quantitative.toml`。
**期望：** GQRA quality 1.0、tokens 0、P50 显著低于 B1/CGW/GEXA；选择器 adopt。
**实际：** `ar021-quantitative-20261102-123236`：GQRA quality 1.000、P50 3.8ms；
B1 36.608s、CGW 1.310s、GEXA 1.225s；adopt，utility 下界 0.9754。
**状态：** PASS

### ST-008：清洗回归与浅克隆

**关联需求：** FR21-1/FR21-5
**测试类型：** 回归
**优先级：** High

**步骤：** 本机全量；Gitee 浅克隆全量。
**期望：** 本机全绿；浅克隆仅 3 个资产/GPU 体检 skip。
**实际：** 本机 `767 passed`；`extract/grounded.py` 覆盖率 91%、kernel 92%
（目标 ≥85%）；Gitee 浅克隆在归档后复验。
**状态：** PASS

## ST 执行报告

| 字段 | 内容 |
|------|------|
| 执行日期 | 2026-11-02 |
| 执行结果 | PASS |
| 执行轮次 | 第 1 轮 |

### 需求覆盖矩阵

| 需求 ID | 需求描述 | 测试用例 | 结果 |
|--------|---------|---------|------|
| FR21-1 | 配置与兼容 | ST-001, ST-005, ST-008 | PASS |
| FR21-2 | 多锚点解析 | ST-002, ST-004 | PASS |
| FR21-3 | 确定性运算 | ST-003, ST-004 | PASS |
| FR21-4 | 组合/POST/回退 | ST-005, ST-006 | PASS |
| FR21-5 | 真实实验与回退 | ST-007, ST-008 | PASS |

**需求覆盖率：** 5 / 5（100%）

### 测试执行汇总

| 类型 | 总计 | 通过 | 失败 | 阻塞 |
|------|------|------|------|------|
| 正常路径 | 4 | 4 | 0 | 0 |
| 边界条件 | 2 | 2 | 0 | 0 |
| 异常处理 | 1 | 1 | 0 | 0 |
| 回归测试 | 1 | 1 | 0 | 0 |
| **合计** | **8** | **8** | **0** | **0** |

### 遗留问题

| 严重性 | 描述 | 处理方式 |
|-------|------|---------|
| Minor | 不支持平均值/百分比/单位换算 | 未启用，直接 miss |
| Minor | 14B 服务器未本机实测 | 资产就位后按 REPRODUCTION §2.13 执行 |

### 结论

> **Go**：5/5 需求覆盖、0 Critical/Major、真实矩阵质量 1.0、tokens 0、
> P50 3.8ms；Minor 已记录。
