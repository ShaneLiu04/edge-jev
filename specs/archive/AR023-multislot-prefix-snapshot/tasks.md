# [AR023] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR023 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-11-05 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T2301 | SnapshotClient 按请求 slot 恢复/保存 + 每槽状态/在途计数 | - | passing | 13 单测 |
| T2302 | Kernel/app/matrix 接线，移除互斥并保留兼容 | T2301 | passing | 默认关闭 |
| T2303 | 真实并发冷重启脚本与实验配置 | T2302 | passing | 无 mock |
| T2304 | 本地真实组合实验与 adopt/rollback | T2303 | passing | GPU 6.7x |
| T2305 | 文档/README/服务器复现更新 | T2304 | passing | 真实数据 |
| T2306 | Review + ST + 归档 | T2305 | passing | harnessX |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中
- `passing`：开发完成且测试通过
- `failed`：测试失败需修复

## 进度记录

- 2026-11-05：需求与任务建立。
- 2026-11-05：T2301-T2304 完成；真实 GPU 12 题冷重启两次：p4 auto 20.96s / 21.02s → MSPP 3.12s / 3.15s（6.7x），restore=4，quality 1.0；稳态 P50 0.90–1.05s；全量 789 passed。

## Review 记录

| 轮次 | 结论 | 主要问题 | 处理 |
|------|------|---------|------|
| 1 | FAIL | serve 未组合 Snapshot+调度；忙槽恢复；save/restore 竞态；LRU 状态失效；stderr 未 join | 全部修复 |
| 2 | PASS | 仅文档/极低风险 | 已更新 |

## ST 记录

- 10/10 PASS；真实 GPU 两次冷重启 6.67–6.71x；全量 789 passed。

## 阶段门控记录

- req/design/develop/review/ST 自动化完成；review 第 2 轮 PASS。
