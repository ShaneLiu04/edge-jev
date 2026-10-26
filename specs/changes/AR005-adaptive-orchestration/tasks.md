# [AR005] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR005 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md（待生成） |
| 创建日期 | 2026-10-26 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | Conformal 校准核心：分位数/风险上界/留出评估（纯逻辑 + 单测） | - | passing | Conformal 核心 + 8 单测 |
| T002 | 风险门接入路由：校准文件加载、失败安全、头 `X-Jev-Risk`、AR003 逐例回归 | T001 | passing | 风险门接入 chat/kernel + 头；单测 9 |
| T003 | Conformal 缓存准入：负例/复述分数校准、阈值导出、台账标注 | T001 | passing | 缓存准入校准 + 16 单测 |
| T004 | 缓存准入接入 chat/cache：conformal 阈值优先、未校准回退固定阈值 | T003 | passing | 缓存接入 + conformal 元数据；单测 7 |
| T005 | JCBA 分配器：候选单元/0-1 背包/确定性/首末块与预算约束（纯逻辑 + 单测） | - | passing | JCBA + 10 单测 |
| T006 | JCBA 接入 prompt 编排：与记忆/草稿/prefill 顺序契约一致、失败回退 | T005 | passing | prompt 接入 + 单测；真实指标待 T011 |
| T007 | 数据集：`quality-set.yaml`（≥30 gold）+ `cache-set.yaml`（正/复述/负 ≥10）与校验 | - | passing | 30 gold + 20/20/20 缓存集 + 判分单测 |
| T008 | 矩阵执行器：真实多方案跑 gold 集、原始 JSON、指标汇总、指纹 | T007 | passing | 矩阵执行器 + CPU 真实冒烟（rollback） |
| T009 | 选择器：效用/成对 bootstrap CI/留一 CV/rollback + profile 导出（纯逻辑 + 单测） | T001,T008 | passing | 选择器 + 8 单测；真实全量待 T011 |
| T010 | CLI `jev matrix`：参数、退出码、`--limit/--write-profile/--server` | T008,T009 | passing | CLI jev matrix + 退出码 + profile 导出 |
| T011 | 真实实验：B0/B1/B2/AR005 全矩阵 + needles/cache 专项，产出 benchmarks/results | T007,T008,T009 | passing | 真实 20 题 + 重复 10×2 + needles 12；长上下文 prompt↓39% 质量不变；见 docs/RESULTS.md |
| T012 | 服务器大模型档：7B/14B 配置、GPU 参数、deferred-server 口径与脚本 | T010 | passing | server-7b/14b 配置与命令；14B deferred |
| T013 | 复现文档：README/REPRODUCTION/EXPERIMENTS + 环境指纹与一键命令 | T010,T011,T012 | passing | README/REPRODUCTION/EXPERIMENTS/ARCHITECTURE |
| T014 | 回归与门禁：全量单测 + ruff + 覆盖 ≥85% + AR002/AR003 逐例回归 + 组合回退验证 | T011,T013 | passing | 644 单测 + 真实集成 3 + ruff + kernel 93% + 回退验证 |

## 迁移与整理说明

- AR003 T013（指标实验）→ AR005 T008/T011；AR003 T014（EdgePrefill 协同）→ AR005 T012 文档/配置一并覆盖；AR003 T015（门控汇总）→ AR005 T013；AR003 T016（回归覆盖）→ AR005 T014。
- AR003 仅保留 T007（槽复用 TTFT）、T009（prefill 真实 token/质量）、T012（10 轮记忆），其真实指标由 AR005 矩阵产出后结项。
- AR004 保留飞轮/LoRA/多基线；AR005 的 profile 与实验结果作为 AR004 的学习输入，不重复实现训练。
- AR002 已交付项作为 B1 基线，不重复开发。

## 状态说明

- `pending`：待开始
- `in_progress`：进行中（当前会话）
- `passing`：开发完成，测试通过
- `failed`：测试失败，需修复

## 进度记录

> 每个开发会话结束后追加，记录完成情况。

### 2026-10-26 需求阶段会话记录（AR005 立项）

- 目标：把 AR002/AR003 的独立启发式升级为「风险可控 + 联合预算 + 代价感知选择」的自适应编排，并建立可复现实验矩阵。
- 核心门：质量不降（≥-1pt）、成本 ≤B2、风险违规 ≤α、无提升自动 rollback、全新克隆可复现。
- 约束：零 mock；默认关闭新能力；7B/14B 服务器执行，不伪造。

## 阶段门控记录

> 由 sdd-phase-gate skill 在阶段门控审查后追加。

### 2026-10-26 会话记录（AR005 设计+开发）

- 产出：srs.md / design.md / 组件详设 §10；实现 conformal/allocator/portfolio/dataset/selector/matrix/CLI。
- 数据：quality-set 30 题；cache-set 20 原句 + 20 复述 + 20 负例。
- 真实运行：CPU 模式矩阵跑通（B0/B1/B2/AR005，限 6 题冒烟），选择器输出 rollback；全量 30 题后台运行。
- 说明：GPU 被外部进程占用时使用 CPU（`gpu_layers=0`，`timeout_s=900`），报告中记录真实延迟；GPU 释放后应重跑以取得正式数字。

### 2026-10-26 实验与收尾记录

- 真实运行 1（唯一问题 20 题）：B0 质量 0.70 / B1 0.60 / B2 0.50 / AR005 0.50；选择器 adopt B0（当前最优）。
- 真实运行 2（重复 10×2）：B2/AR005 缓存命中 10 次，completion tokens 13.2 vs B0 20.6（↓36%），延迟 9.3s vs 12.2s（↓25%）；选择器 rollback B2（质量未达门）。
- 结论：选择/回退机制按设计工作；Conformal 校准在本次条件（样本/embedding/截断）下未启用，AR005≡B2，未宣称超越 B2。详见 docs/RESULTS.md。
- 质量门：644 单测通过；真实集成 3 通过；ruff 零告警；kernel 语句 93%。
- 已知实验偏差：max_tokens=32 截断 light，正式复现应使用 128；GPU 外部负载导致 llama-server 偶发假死，矩阵已改为每 arm 重启并写 progress 检查点。

### 2026-10-26 长上下文补充实验

- needles 12 题：B1 prompt 2457 → B2/AR005 1493（↓39.2%），quality 0.917 不变，延迟 ↓14%。
- 选择器 rollback B2（AR005 与 B2 同解）；环境限制：长 prompt 下 verifier/memory 触发 llama-server 假死，实验显式关闭并标注。
