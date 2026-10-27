# [AR007] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR007 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-27 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | shared-prefix 数据集 + 矩阵 prompt_cache/session + 单测 | - | passing | 5 个单测 |
| T002 | RuntimeConfig prefix_cache/session_affinity + 稳定前缀 + 头/台账 | T001 | passing | 默认关闭 |
| T003 | 真实集成测试：同前缀第二次 cache_n>0 且答案一致 | T002 | passing | 真实进程 |
| T004 | 真实实验 B1/CACHE/AEB/AEB_CACHE + 冲突结论 | T003 | passing | prefill 27.7× |
| T005 | 服务器 7B/14B 配置/文档/推送 | T004 | passing | external + 复现说明 |
| T006 | 选择器延迟感知（lambda_latency） | T004 | passing | 修正 token-only 误选 |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进度记录

### 2026-10-27

- shared-prefix 12 题：B1 prefill 8848ms / 端到端 70.6s；CACHE prefill 319ms / 54.0s，
  12/12 命中，质量 1.0。
- AEB：prompt 3998（−25%）但延迟 153s；AEB_CACHE 0 命中，证明压缩破坏前缀。
- token-only 选择误选 AEB_CACHE；新增 `lambda_latency=0.5` 后 `adopt CACHE`。
- 真实集成：同 session 第二次 `cache_n>100` 且答案一致。
- 662+ 单测通过，ruff 零告警。

## 阶段门控记录

- 2026-10-27 req/design：用户授权全自动推进，跳过门控（不阻塞）。
