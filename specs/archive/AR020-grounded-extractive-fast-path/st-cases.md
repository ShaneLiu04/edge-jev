# [AR020] ST 验收测试用例

| 字段 | 内容 |
|------|------|
| AR 编号 | AR020 |
| 关联 srs.md | ./srs.md |
| 生成日期 | 2026-11-02 |

## 测试用例列表

### ST-001：默认关闭不改变行为

**关联需求：** srs.md §3 FR20-1
**测试类型：** 正常路径
**优先级：** High

**前置条件：** 不设置 `[extractive] enabled` 或设为 false。

**测试步骤：**
1. 调用 `_build_extractor({})`；
2. 用 disabled extractor 处理 shared-prefix 问题。

**期望结果：** extractor disabled；`answer()` 返回 miss:disabled；无 `X-Jev-Extract`。

**实际结果：** `test_kernel_builder_default_off_and_enabled`、`test_disabled_and_short_document_miss` 通过。

**状态：** PASS

---

### ST-002：非法配置 fail-fast

**关联需求：** srs.md §3 FR20-1
**测试类型：** 异常处理
**优先级：** High

**前置条件：** `[extractive] max_answer_chars=0`。

**测试步骤：** 调用 `_build_extractor`。

**期望结果：** 抛 `JevProtocolError`，含 `extractive` 字段提示，不是裸 traceback。

**实际结果：** 单测断言通过；`ruff` 零告警。

**状态：** PASS

---

### ST-003：锚点/类型/线索识别

**关联需求：** srs.md §3 FR20-2
**测试类型：** 正常路径
**优先级：** High

**前置条件：** 真实 shared-prefix 数据集。

**测试步骤：** 对 12 题调用 `GroundedExtractor.answer()`。

**期望结果：** 12/12 命中 numeric，答案与 gold 一致。

**实际结果：** 12/12；`test_shared_prefix_all_extracted_and_correct` 通过。

**状态：** PASS

---

### ST-004：唯一候选接地

**关联需求：** srs.md §3 FR20-3
**测试类型：** 正常路径
**优先级：** High

**前置条件：** 真实 needles 30 题（含干扰编号）。

**测试步骤：** 对每题抽取并判分。

**期望结果：** 30/30 命中且正确，0 误答。

**实际结果：** 30/30；`test_needles_all_extracted_and_correct` 通过。

**状态：** PASS

---

### ST-005：歧义/无候选/锚点自身排除

**关联需求：** srs.md §3 FR20-3
**测试类型：** 边界条件
**优先级：** High

**前置条件：** 构造同锚点多候选、线索句无候选、日期锚点自身、超多锚点、超长答案。

**测试步骤：** 分别调用抽取器。

**期望结果：** `ambiguous` / `no-candidate` / 只返回非锚点日期 / `too-many-anchors` / `answer-too-long`。

**实际结果：** 5 个边界单测全部通过；中文紧邻数字用例通过。

**状态：** PASS

---

### ST-006：生成型/普通问答不误命中

**关联需求：** srs.md §3 FR20-2/FR20-5
**测试类型：** 异常处理 / 回归
**优先级：** High

**前置条件：** 30 条 quality、8 条 shared-generation。

**测试步骤：** 调用抽取器。

**期望结果：** quality 0 命中（no-doc）；generation 0 命中（non-extractive）。

**实际结果：** `test_quality_set_never_false_hits`、`test_generation_requests_are_rejected` 通过；真实矩阵 quality 0.207 与 CGW 完全一致。

**状态：** PASS

---

### ST-007：full 路由命中零大脑 + 观测头

**关联需求：** srs.md §3 FR20-4
**测试类型：** 正常路径
**优先级：** High

**前置条件：** 真实 Laya；不启动 llama-server；fast 档。

**测试步骤：** POST `/v1/chat/completions`，请求 shared-prefix 问题。

**期望结果：** 200、答案正确、`total_tokens=0`、`X-Jev-Extract: hit:numeric`、brain 未运行。

**实际结果：** `tests/integration/test_extractive_real.py` fast 分支 passed。

**状态：** PASS

---

### ST-008：strict POST 档

**关联需求：** srs.md §3 FR20-4
**测试类型：** 正常路径
**优先级：** High

**前置条件：** 真实 Laya；`decision_post=true`；不启动大脑。

**测试步骤：** 同 ST-007，使用 compact question+answer POST。

**期望结果：** 200、答案正确、tokens=0、`X-Jev-Extract` 命中。

**实际结果：** 集成测试 strict 分支 passed；真实矩阵 strict P50 739ms。

**状态：** PASS

---

### ST-009：POST 拒绝/抽取异常回退

**关联需求：** srs.md §3 FR20-4
**测试类型：** 异常处理
**优先级：** High

**前置条件：** POST 返回 DOWNGRADE；extractor.answer 抛 RuntimeError。

**测试步骤：** 调用 `_try_extractive`。

**期望结果：** 返回 None，回退原 full 生成，不产生抽取命中头。

**实际结果：** `test_chat_extractive_post_reject_falls_back`、`test_chat_extractive_exception_falls_back` 通过。

**状态：** PASS

---

### ST-010：shared/needles 真实组合收益

**关联需求：** srs.md §3 FR20-5 / §4 NFR
**测试类型：** 正常路径
**优先级：** High

**前置条件：** 真实 1.5B/0.5B llama-server、GPU offload、CGW/GTB 组合。

**测试步骤：**
1. `jev matrix --config configs/experiments/extractive.toml`；
2. `jev matrix --config configs/experiments/extractive-needles.toml`。

**期望结果：** quality 1.0；抽取命中 tokens=0；P50 显著低于 CGW；选择器 adopt。

**实际结果：**
- shared：GEXA_CGW P50 175ms / mean 176ms vs CGW 1101ms（−84.1%），tokens 0；
- needles：GEXA P50 3.7ms vs CGW 13089ms（−99.97%），tokens 0；
- 两个矩阵均 adopt，独立审查 PASS。

**状态：** PASS

---

### ST-011：非回归与干净浅克隆

**关联需求：** srs.md §4
**测试类型：** 回归
**优先级：** High

**前置条件：** 本机全量 + Gitee 干净浅克隆。

**测试步骤：** `pytest tests/unit`；浅克隆 `uv sync` + `ruff` + `pytest tests/unit`。

**期望结果：** 本机全绿；浅克隆仅 3 个资产/GPU 体检用例 skip，其余全绿。

**实际结果：** 本机 `759 passed`；浅克隆 `756 passed, 3 skipped`；lint 零告警；真实集成 2 passed；`jev.kernel` 总覆盖率 **88%**（`extract/grounded.py` 92%，目标 ≥85%）。

**状态：** PASS

---

### ST-012：服务器 7B/14B 配置与边界

**关联需求：** srs.md §3 FR20-5
**测试类型：** 边界条件
**优先级：** Medium

**前置条件：** 服务器 external brain/embedding；14B 资产未本机就位。

**测试步骤：** 检查配置、文档与断言口径。

**期望结果：** 7B/14B 配置存在；14B 明确标注未实测，禁止虚构指标；slot 路径与快照目录一致。

**实际结果：** 配置与 REPRODUCTION §2.12 已落库；14B 未实测声明存在。

**状态：** PASS

---

## ST 执行报告

| 字段 | 内容 |
|------|------|
| 执行日期 | 2026-11-02 |
| 执行结果 | PASS |
| 执行轮次 | 第 1 轮 |

### 需求覆盖矩阵

| 需求 ID | 需求描述 | 测试用例 | 结果 |
|--------|---------|---------|------|
| FR20-1 | 配置与默认关闭 | ST-001, ST-002 | PASS |
| FR20-2 | 锚点与类型识别 | ST-003, ST-006 | PASS |
| FR20-3 | 唯一候选接地 | ST-004, ST-005 | PASS |
| FR20-4 | 级联/POST 组合 | ST-007, ST-008, ST-009 | PASS |
| FR20-5 | 真实实验与回退 | ST-006, ST-010, ST-012 | PASS |

**需求覆盖率：** 5 / 5（100%）

### 测试执行汇总

| 类型 | 总计 | 通过 | 失败 | 阻塞 |
|------|------|------|------|------|
| 正常路径 | 5 | 5 | 0 | 0 |
| 边界条件 | 3 | 3 | 0 | 0 |
| 异常处理 | 3 | 3 | 0 | 0 |
| 回归测试 | 1 | 1 | 0 | 0 |
| **合计** | **12** | **12** | **0** | **0** |

### 遗留问题

| 严重性 | 描述 | 处理方式 |
|-------|------|---------|
| Minor | strict POST 需显式开启 `decision_post=true`；fast 档依赖确定性边界 | 已文档化；按负载选择 |
| Minor | 14B 服务器未本机实测 | 服务器资产就位后按 REPRODUCTION §2.12 执行 |

### 结论

> **Go**：5/5 需求覆盖、0 Critical/Major、真实矩阵质量 1.0、tokens 0、
> 显著优于 baseline；Minor 已记录。
