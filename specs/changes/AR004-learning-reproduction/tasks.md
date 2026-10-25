# [AR004] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR004 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md（待生成，AR003 完成后） |
| 创建日期 | 2026-10-25 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | 需求细化与 design.md（飞轮/实验矩阵/复现） | - | pending | 承接 AR002 迁移任务后重新编号 |
| T002 | 难例优先采样 + 对比样本配对 | T001 | pending | AR002 T008 |
| T003 | numpy 真训练（逻辑回归/MLP）+ 交叉验证 + 校准发布 | T001 | pending | AR002 T007/T009 |
| T004 | 飞轮 CLI `jev distill --flywheel`（退出码=门结果） | T002,T003 | pending | AR002 T009 |
| T005 | 多基线 bench（brain_direct/cerebellum_only/cascade）+ 成对 CI + 指纹 | T001 | pending | 承接 AR002 T010/T012；AR002 T011（CI/指纹）已交付，直接复用 |
| T006 | 7B/14B 服务器实验矩阵与脚本（llama.cpp/vLLM） | T005 | pending | 本机 7B 真跑、14B 配置 |
| T007 | kev-0.8B LoRA 服务器训练脚本与评估 | T003 | pending | GPU 环境执行，不伪造 |
| T008 | README + REPRODUCTION + CI 质量门 + 一键脚本 + 推送方法论/最小包工具（脱敏）+ AGENTS 约束条目 | T005,T006 | pending | AR002 T013/T014/T015/T016 |
| T009 | 底线验收：全新克隆复现 + 全量门（含 LoRA 与 shadow 门） | T004,T007,T008,T010 | pending | AR002 T018 |
| T010 | 学习式 shadow 路由：特征抽取 + 真实训练产物 + shadow 报告（仅记录） | T003,T005 | pending | AR002 T007 承接；成对 CI 对比规则路由 |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中（当前会话）
- `passing`：开发完成，测试通过
- `failed`：测试失败，需修复

## 进度记录

### 2026-10-25 AR004 立项（骨架）

- 由 AR003 需求门控审查（范围孤儿项）驱动建立骨架，承接 AR002 迁移的 T007–T018
- AR003 完成后进入本 AR 需求细化与设计

## 阶段门控记录
