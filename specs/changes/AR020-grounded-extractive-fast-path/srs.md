# [AR020] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR020 |
| AR 主题 | GEXA：接地的确定性抽取式快答路径 |
| 关联 SR | 端侧文档问答的低延迟/零模型调用快路径 |
| 日期 | 2026-11-02 |
| 状态 | Approved（用户授权自动化 harnessX，无需逐步确认） |

## 1. 背景与目标

当前最优组合在 shared-prefix 上 P50 约 0.7–1.0s，但其中大量请求是**文档中已存在
唯一答案的抽取式问答**（编号→校验码、日期、数量等）。这类问题不需要语言模型
生成，只要在文档中定位问题锚点、按答案类型抽取并做唯一性/上下文验证即可。

参考 RAG 的“retrieve-then-read”、抽取式 QA（SQuAD）、answer-type-aware
guardrail 与端侧程序化快路径思想，新增一个**确定性、可审计、可回退**的
Grounded Extractive Answerer（GEXA）：

- 只处理明确抽取型问题（数字/日期等）；
- 必须命中问题锚点 + 答案类型线索 + 唯一候选；
- 任何歧义/多候选/生成型请求直接 miss，交回现有级联；
- 命中时**不调用大脑**，节省全部 prefill/decode。

真实离线探测（真实数据集，非 mock）：

| 数据集 | 题数 | 命中 | 正确 | 误答 |
| --- | --- | --- | --- | --- |
| shared-prefix | 12 | 12 | 12 | 0 |
| needles | 30 | 30 | 30 | 0 |
| quality（普通问答） | 30 | 0 | — | 0 |
| shared-generation（详细说明） | 8 | 0 | — | 0 |

目标：在不降低质量的前提下，显著降低抽取式文档问答的 P50/mean 与 token 成本，
并与 CGW/GTB 组合；无命中时行为与 AR019 完全一致。

## 2. 需求范围

**In Scope：**
- `[extractive]` 配置（默认关闭）与真实确定性抽取器；
- 仅 full 路由、非流式 chat 注入；命中时零大脑调用；
- 答案类型（numeric/date）、锚点、答案线索、候选唯一性、邻近句验证；
- 保守拒绝：生成/解释/总结类问题、无文档、多候选、锚点缺失；
- 命中后仍走 POST 安全校验；拒绝则回退正常级联；
- 观测头 `X-Jev-Extract`、矩阵指标与真实实验；
- 高质量回退与单元/真实集成测试。

**Out of Scope：**
- 不抽取自由文本长答案、不做生成式总结；
- 不改变 direct/light/streaming/POST 既有语义；
- 不引入任何模型/网络调用；不提交数据集/答案到仓库；
- 不保证覆盖所有自然语言问法，未命中即回退。

## 3. 功能需求

### FR20-1 配置与默认关闭

**描述：** `[extractive] enabled`（默认 false）、`min_document_chars`、
`max_answer_chars`、`max_anchor_occurrences`。

**验收：**
- Given 默认配置, When 请求, Then 不运行抽取，行为与 AR019 一致；
- Given enabled, When 非法阈值, Then 启动 fail-fast；
- 关闭时不产生 `X-Jev-Extract`。

### FR20-2 锚点与答案类型识别

**描述：** 从最后一条 user 文档问题中拆分文档/问题，识别强锚点（含数字的
标识符、长数字、引号内容、稀有中文片段）与答案类型线索（校验码/日期/数量等）。

**验收：**
- Given `编号 K-1000 的校验码是多少？只输出数字`, Then 识别锚点 `K-1000` 与
  类型 `numeric`、线索 `校验码`；
- Given 生成/解释/详细说明类问题, Then 直接 miss（`non-extractive`）；
- Given 无文档的普通问答, Then miss（`no-doc`）。

### FR20-3 唯一候选抽取与接地验证

**描述：** 在所有锚点出现的句子中，按答案类型与线索抽取候选；排除锚点自身
数字；要求所有命中句的候选**完全一致**，且答案长度/类型合法。

**验收：**
- Given 目标句唯一且候选唯一, Then hit，返回原样值；
- Given 同一锚点出现在干扰句且候选不同, Then miss（`ambiguous`）；
- Given 目标句无答案线索, Then miss（`no-candidate`）；
- Given 日期问题, Then 返回原日期字符串且格式合法。

### FR20-4 与级联/POST 的组合

**描述：** 仅在 full 路由、非流式路径、prompt 压缩前尝试；命中后构造零 usage
响应，先经 POST 安全校验；POST accept 才直接返回，否则丢弃抽取结果并走原
full 生成。

**验收：**
- Given 抽取命中且 POST accept, Then 不调用大脑，`usage=0`，header
  `X-Jev-Extract: hit:<type>`；
- Given POST 拒绝, Then 回退级联，最终质量不受影响；
- Given 抽取 miss, Then 完全走 AR019 级联，无额外语义变化；
- streaming/direct/light 不启用抽取。

### FR20-5 真实实验与回退

**描述：** 新增 `configs/experiments/extractive.toml`，在 shared-prefix/needles
真实后端上对照 B1/CGW/GTB/GEXA；记录命中率、质量、P50/mean、tokens。

**验收：**
- 抽取 arm quality 1.0、tokens 显著低于 baseline、P50/mean 显著下降；
- 无命中数据集（quality/generation）质量不下降；
- 无正收益或误答 → `extractive.enabled=false` 回退 AR019/CGW。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 延迟 | 抽取命中请求 | 相对 CGW P50 下降 ≥50% |
| 质量 | 抽取命中 | 真实数据集 0 误答 |
| 成本 | 大脑 token | 命中时 0 |
| 可靠性 | 异常/歧义 | 100% 回退，不产生 5xx |
| 兼容 | 默认关闭 | 与 AR019 行为一致 |

## 5. 约束与假设

- 抽取器只做确定性文本处理，不依赖模型/网络；
- 答案类型先支持 numeric/date；其他类型 miss；
- 需要问题中有可定位强锚点；纯语义问题不覆盖；
- POST 校验复用现有决策引擎，不新增外部依赖；
- 结果必须由真实数据集验证，禁止针对单题硬编码。

## 6. 术语

| 术语 | 定义 |
|------|------|
| GEXA | Grounded Extractive Answerer，确定性抽取快答 |
| 锚点 | 问题中用于定位文档句子的强标识符/长数字/引号内容 |
| 答案线索 | 问题中的答案类型名词（校验码/日期/数量…） |
| 唯一候选 | 所有锚点命中句抽取出的值完全一致且仅一个 |
