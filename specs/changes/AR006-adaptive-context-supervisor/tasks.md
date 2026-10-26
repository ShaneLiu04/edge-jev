# [AR006] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR006 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-26 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | `prefill/evidence.py`：真实 embedding 融合打分 + 降级 + 单测 | - | passing | 13 个证据/规划单测 |
| T002 | 自适应预算比例（margin/top-gap）+ MMR + 锚点 + 单测 | T001 | passing | 范围 0.22–0.60，确定性 |
| T003 | planner/prompt/allocator 接线与台账字段 | T002 | passing | 关闭时行为不变 |
| T004 | `cerebrum/supervisor.py` 自愈 + 真实进程集成测试 | - | passing | 真 kill→重启→重试通过 |
| T005 | 矩阵任意组合 arm + 组合选择/回退 + 单测 | T003 | passing | overlays 定义即合法 |
| T006 | 真实实验：needles AEB 组合 + quality 组合 | T003,T005 | passing | prompt↓51%，Δq=0 |
| T007 | 文档/配置/服务器复现指南 + 推送 Gitee | T006 | passing | 7B/14B external 配置就绪 |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中
- `passing`：开发完成且测试通过
- `failed`：测试失败需修复

## 进度记录

### 2026-10-26 全流程

- 单测：`655 passed`（AR005 基线）→ 新增证据/矩阵/检索 6 个，最终 `661 passed`。
- 真实集成：大脑自愈（真 stop→start→retry）通过；真实 embedding 选择 needles 通过。
- needles 12 题：B1 2457 → P60 1493（−39.2%）→ **AEB 1204（−51.0%）**，quality 全为 0.917。
- AEB_ALLOC 与 AEB 同解（1204），组合一致性成立。
- quality 12 题：四组合均 141 tokens，选择器 `rollback B1`（短 prompt 无增益）。
- 关键修复：embedding 长请求分块、embedding 子进程假死自愈、锚点排除末块问题、检索意图抽取。

## 阶段门控记录

- 2026-10-26 req/design：用户明确授权全自动推进，跳过门控（不阻塞）。
