# [AR010] 任务清单

| 字段 | 内容 |
|------|------|
| AR 编号 | AR010 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-29 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | 定位 POST 盲区：真实 Laya 对正确/空答决策相同 | - | passing | 根因：保头部截断 |
| T002 | 单测锁定保尾部语义（`test_laya_sequence`） | T001 | passing | Red→Green |
| T003 | 修复 `build_sequence` / `system_one` 保尾部 | T002 | passing | 两处一致 |
| T004 | 真实 Laya 回归：长 state POST 可区分答案 | T003 | passing | `test_laya_post_window_real` |
| T005 | 退化重复守卫 + 单次重试 | T003 | passing | 纯函数检测 |
| T006 | `decision_shadow` / `decision_post` 配置与 chat 接入 | T003 | passing | 默认 true |
| T007 | `lads.toml` + `profiles/latency-fast.toml` + 选择器 | T006 | passing | B1/FAST/CACHE/CACHE_FAST |
| T008 | 真实复测 AR010（shared-prefix 12 题） | T007 | passing | adopt CACHE_FAST |
| T009 | 真实复测 AR006/AR007/AR009（POST 修复后） | T007 | passing | 见下 |
| T010 | 更新 RESULTS/EXPERIMENTS/PORTFOLIO/ARCHITECTURE/README/REPRODUCTION/spec | T008 | passing | 标注历史污染 |
| T011 | 全量单测 + ruff + review/ST 记录 | T010 | passing | 688 unit |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进展记录

### 2026-10-29

- 发现 Laya POST 盲区：`state_ids[:max_len]` 保留头部，assistant 答案在尾部被截掉；
  真实探测中正确长答/短答/英文/空串的 POST 决策完全相同，`needs_retry` 恒定 `retry`。
- 修复：`laya_sequence.build_sequence` 与 `laya_onnx.system_one` 均改为保留尾部；
  新增 `test_laya_sequence` 尾部断言与真实 Laya POST 回归测试。
- 新增退化重复守卫：短周期全串重复触发一次反馈重试；重试后仍退化则标记降级。
- 新增 `decision_shadow` / `decision_post`，默认 true；chat 快速模式头 `X-Jev-Decision-Mode`。
- 真实复测（POST 修复后，同一 12 题，输出一致）：
  - AR010 LADS：B1 54.0s / FAST 34.2s（−36.7%）/ CACHE 42.0s / CACHE_FAST **24.6s（−54.4%）**；
    四臂 quality 1.0、`outputs_identical=1.0`；选择器 adopt CACHE_FAST（效用下界 +0.2539）。
  - AR009 CASC：B1 52.9s / CACHE 42.2s / CASC **38.1s**；tokens 2662→1079（−59.5%）；
    quality 1.0；needles 6 题 CASC 1847→762（−58.7%）、quality 1.0、延迟 50.0s vs B1 47.6s。
  - AR007：B1 48.4s / CACHE 40.5s；prefill 8202→927ms（8.8×）、端到端 −16.3%。
  - AR006：三臂 quality 0.833（11/12）；AEB tokens 2921→1590（−45.6%），adopt AEB；
    旧表 0.917 依赖无条件重试的偶然修复。
- 全量单测 **688 passed**，ruff 零告警。
- 历史污染说明：所有 2026-10-29 之前的长 prompt 表（needles / shared-prefix）绝对延迟与
  prompt token 被 POST 无条件重试放大；文档已标注，以修复后复测表为准。
