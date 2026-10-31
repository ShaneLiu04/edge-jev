# [AR018] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR018 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md（待生成） |
| 创建日期 | 2026-10-31 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T1801 | 纯逻辑 grounding verifier（锚点/窗口/归一化/边界） | - | passing | 先写单测 |
| T1802 | `[cerebrum]` tier 配置与校验（默认关闭） | T1801 | passing | 兼容历史配置 |
| T1803 | 小模型 manager 生命周期 + Kernel/CLI/close 接线 | T1802 | passing | 真实 llama-server |
| T1804 | `TieredBrain` full 路由分派、升级、错误回退 | T1803 | passing | streaming/light 不变 |
| T1805 | 响应头 `X-Jev-Tier*` 与观测接缝 | T1804 | passing | 流式不伪造 |
| T1806 | Matrix 小模型 arm 支持与真实隔离/联合矩阵 | T1805 | passing | shared-prefix |
| T1807 | needles/quality 质量回归 + 失败则回退 | T1806 | passing | 无错误接受 |
| T1808 | 全量单测/lint/集成、文档、推送与浅克隆复刻 | T1807 | passing | Gitee |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中（当前会话）
- `passing`：开发完成，测试通过
- `failed`：测试失败，需修复

## 进度记录

（开发会话结束后追加）

## 阶段门控记录

（自动化 harnessX：req/design 门控按用户授权跳过，仅记录本行）
