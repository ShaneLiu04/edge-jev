# [AR012] 任务清单

| 字段 | 内容 |
|------|------|
| AR 编号 | AR012 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-29 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | `route_short_circuit` 纯逻辑 + 单测 | - | passing | 安全短路 |
| T002 | `RuntimeConfig.decision_staged` + 配置 | T001 | passing | 默认 false |
| T003 | chat 消费者判断 + 分阶段 PRE | T001,T002 | passing | 缺失回退 |
| T004 | 观测头 + 台账点数 | T003 | passing | `X-Jev-Decision-Staged` |
| T005 | 真实 Laya 路由一致性测试（assets+slow） | T003 | passing | 100% |
| T006 | 端到端 `cadp.toml` / `cadp-casc.toml` | T003 | passing | 4 arms |
| T007 | 文档/复刻/服务器配置 | T006 | passing | Gitee |
| T008 | 全量测试 + ruff + 推送 + 浅克隆验证 | T007 | in_progress | master |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进展记录

### 2026-10-29

- 依赖分析：路由 5 点存在安全短路；PRE 10 qid 可在 reasoning=reasoning 时只算 5 qid。
- 设计：消费者感知 + wave1(3 点)/wave2(2 点) 分阶段；强制路由且无消费者直接跳过 PRE。
- 默认关闭，`latency-fast` / `latency-best` 显式启用。

### 2026-10-29 ???????

- T001?`CascadeEngine.route_short_circuit` + 6 ???????unsafe/direct/reasoning/budget/fallback??
- T002-T004?`RuntimeConfig.decision_staged`?chat `evaluate_pre`?wave1/wave2??
  `X-Jev-Decision-Staged`??????????? PRE ????? 3 vs ?? 7??
- T005??? Laya 6 ??? prompt??????????? **100% ??**?189.8s??
- T006 ???`ar012-cadp-20261029-230014`?12 ??force_full=false??
  B1 52.6s / ADB 42.1s / CADP 30.9s?7 ??/ **CADP_FAST 21.7s?3 ??**?
  quality 1.0???????? full 12/12?adopt CADP_FAST?
- T007 ???`ar012-cadp-casc-20261029-233045`?12 ???
  B1 51.6s / ADB_CACHE_FAST 13.5s / **CADP_CACHE_FAST 10.0s?P50 8.9s?** /
  **CADP_CASC_FAST 10.6s?P50 8.8s?tokens 1079?**?quality 1.0??????
  adopt CADP_CASC_FAST?+0.4440??
- ???RESULTS/EXPERIMENTS/PORTFOLIO/ARCHITECTURE/README/REPRODUCTION/jev_spec?
  ?? `server-{7b,14b}-cadp-casc.toml`?
- ??????????? arm ?? 300s ?????????????????????
