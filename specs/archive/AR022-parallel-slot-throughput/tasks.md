# [AR022] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR022 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-11-02 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T2201 | SlotScheduler/ScheduledClient + 配置 | - | passing | 默认关 |
| T2202 | Kernel/app/matrix 接线与互斥校验 | T2201 | passing | PPKS 互斥 |
| T2203 | 真实并发脚本与 profile | T2202 | passing | 1.86x |
| T2204 | 真实 id_slot 集成 + 全量回归 | T2203 | passing | 2 slots |
| T2205 | 文档/README/REPRODUCTION/服务器 | T2204 | passing | Gitee |
| T2206 | Review + ST + 归档 | T2205 | passing | harnessX |

## 进度记录

- 探测：llama-server 支持 `id_slot`，slot 2 实际接收请求；
- 并发：parallel4 auto 1.86x；parallel2 scheduled 1.22x（优于 auto 0.99x）；
- 真实集成：两次请求落到两个不同 slot；
- 回归：本机全量 780 passed；待浅克隆。

## Review 记录

| 轮次 | 结论 | 主要问题 | 处理 |
|------|------|---------|------|
| 1 | FAIL | matrix 缺快照互斥；配置错误类型/静默降级；supervisor 静默；边界测试缺失；计数器无界 | 全部修复 |
| 2 | PASS | 仅低风险建议 | 补 LRU、配置项、warning 文案、测试 |

## 阶段门控记录

（自动化 harnessX：用户已授权跳过逐步确认与门控交互）
