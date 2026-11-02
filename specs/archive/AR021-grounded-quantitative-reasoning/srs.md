# [AR021] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR021 |
| AR 主题 | GQRA：文档接地的确定性数值推理快路径 |
| 关联 SR | 发票/巡检/报表类多记录定量问答 |
| 日期 | 2026-11-02 |
| 状态 | Approved（用户授权自动化 harnessX，无需逐步确认） |

## 1. 背景与目标

AR020 GEXA 已覆盖“单事实抽取”，但真实文档问答还包含大量**跨记录数值运算**：
两条记录之和/差、最大/最小校验码、哪条记录更大。这类问题不需要语言模型，
可由文档中已接地的数值确定性计算。

参考程序化推理、工具调用、table QA 与 RAG grounded execution 思想，在 GEXA
之上增加 GQRA（Grounded Quantitative Reasoning Answerer）：

- 先定位多个问题锚点，按答案线索从文档解析每个锚点的唯一数值；
- 按问题操作（sum/diff/max/min/which_max/which_min）确定性计算；
- 任一锚点缺失/歧义/操作不支持则整体 miss，回退 CGW/GTB 级联；
- 命中时零大脑调用，仍复用 AR020 的 POST 双档与观测。

真实离线探测（新构造的文档型 numeric_reasoning 12 题）：

| 路径 | 命中 | 正确 | 误答 |
| --- | --- | --- | --- |
| 原 GEXA（单事实） | 0/12（ambiguous） | — | 0 |
| **GQRA** | **12/12** | **12/12** | **0** |

## 2. 需求范围

**In Scope：**
- `[extractive] quantitative_enabled`（默认 false）；
- 多锚点唯一数值解析 + sum/diff/max/min/which_max/which_min；
- `numeric_reasoning` 真实确定性数据集与真实矩阵；
- 复用 fast/strict POST、`X-Jev-Extract`、异常回退；
- 单测/真实集成/文档/服务器配置。

**Out of Scope：**
- 不支持平均值/百分比/多步链式运算（未验证前不启用）；
- 不改变 GEXA 单事实、direct/light/streaming 行为；
- 不引入模型/网络；不支持文本型答案运算；
- 计算仅基于文档中唯一接地的数值，歧义即回退。

## 3. 功能需求

### FR21-1 配置与兼容

**描述：** `[extractive] quantitative_enabled` 默认 false；关闭时与 AR020
字节级一致；非法配置失败为 `JevProtocolError`。

**验收：**
- Given 默认配置, When numeric_reasoning 请求, Then 不启用 GQRA；
- Given enabled, When 合法问题, Then 进入 GQRA；
- 关闭时 shared/needles/quality 行为不变。

### FR21-2 多锚点数值解析

**描述：** 从问题识别 ≥2 个强锚点与答案线索（校验码/数量等），对每个锚点
在其线索所在句解析唯一数值；任一锚点无值/多值/超过出现上限即整体 miss。

**验收：**
- Given 两记录各唯一校验码, Then 两个值均可解析；
- Given 某锚点候选歧义, Then miss `arithmetic-unresolved`；
- 锚点自身数字不得作为候选。

### FR21-3 确定性运算

**描述：** 支持 sum/diff/max/min/which_max/which_min；使用 Decimal 计算，
整数结果不带小数；which 题返回记录编号。

**验收：**
- `K-1000` 与 `K-1001` 校验码之和正确；
- 差为绝对值；
- 最大/最小数值正确；`哪个更大/更小` 返回对应编号；
- 不支持的组合/单锚点 -> miss。

### FR21-4 与 GEXA/级联/POST 组合

**描述：** 仅在 full、非流式、prompt 压缩前尝试；操作型问题不会误走单事实；
命中零 usage，复用 fast/strict POST 与 `X-Jev-Extract`；拒绝/异常回退原级联。

**验收：**
- 命中不调用大脑、tokens=0；
- POST 拒绝/异常回退；
- GEXA 原有 shared/needles/quality/generation 指标不回归。

### FR21-5 真实实验与回退

**描述：** 新增 `numeric_reasoning` 数据集与 `quantitative.toml`；
对照 B1/CGW/GEXA/GQRA。

**验收：**
- GQRA quality 1.0、tokens 0、P50 显著低于 B1/CGW/GEXA；
- 无正收益或误答 -> `quantitative_enabled=false` 回退 AR020。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 延迟 | GQRA 命中 | 相对 CGW P50 下降 ≥50% |
| 质量 | numeric_reasoning | 0 误答 |
| 成本 | 大脑 token | 0 |
| 可靠性 | 异常/歧义 | 100% 回退，无 5xx |
| 兼容 | 默认 | 关闭时与 AR020 一致 |

## 5. 约束与假设

- 仅支持文档中已存在、唯一接地的数值；
- 操作语义由问题词面显式触发；未识别即 miss；
- 不做单位换算/货币/百分比；
- 数据集为确定性生成，答案可客观判分，不依赖模型输出。

## 6. 术语

| 术语 | 定义 |
|------|------|
| GQRA | Grounded Quantitative Reasoning Answerer |
| 多锚点 | 问题中至少两个用于定位文档记录的强标识符 |
| 唯一数值 | 所有锚点命中句解析出的值完全一致且仅一个 |
| which | 比较后返回记录编号而非数值 |
