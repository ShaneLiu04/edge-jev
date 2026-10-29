# [AR011] 任务清单

| 字段 | 内容 |
|------|------|
| AR 编号 | AR011 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-29 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | 真实 Laya 验证置换 4→1 的决策一致性 | - | passing | 14 题 applied 100% |
| T002 | 纯逻辑单测：预算裁剪 `min(orig,budget)` | - | passing | 0=不限制 |
| T003 | `RuntimeConfig` + `configs/jev.toml` 接入 | T002 | passing | 默认关闭 |
| T004 | chat PRE/POST 裁剪 + 可选低 margin 升级 | T003 | passing | 台账去重 |
| T005 | 真实 Laya 一致性回归测试（assets+slow） | T004 | passing | 一致率 ≥95% |
| T006 | 端到端矩阵 `adb.toml`（隔离） | T004 | passing | 4 arms |
| T007 | 真实路由矩阵 `adb-route.toml`（force_full=false） | T004 | passing | route parity |
| T008 | 组合选择与回退（含 AR009/AR010） | T006 | passing | 质量/延迟门 |
| T009 | 文档/复刻/大模型服务器配置完善 | T008 | passing | RESULTS/PORTFOLIO 等 |
| T010 | 全量测试 + ruff + 提交推送 + 浅克隆复刻验证 | T009 | in_progress | Gitee master |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进展记录

### 2026-10-29

- 瓶颈定位：Laya PRE 约 22s/请求（10 个 qid，intent 占 8 个置换）。
- 真实验证：`permutations=1` 在 6 条 shared + 8 条多样题上 applied 决策 **100% 一致**，
  PRE 22.1s→11.3s（2.0×）；单探针 95%，暂不采用。
- 负结果：GPU Laya 30.6s > CPU 25.7s；槽位绑定无增益；状态窗口 head+tail 一致率 84% 且有 safety 翻转。
- 决策：AR011 只做“可配置决策计算预算 + 可选升级”，默认关闭。

### 2026-10-29 ???????

- T002-T005?`apply_permutation_budget` ????0=??????????? probes??
  `RuntimeConfig.decision_permutations/decision_escalate_margin`?chat ?????? margin ???
  `X-Jev-Decision-Permutations/Escalated` ????? 5 ????
- ?? Laya ???assets+slow?6 ????? 1 ????? applied ?? **100% ??**?116.9s??
- T006 ?????shared-prefix 12 ??force_full??
  - B1 45.6s / ADB 31.5s??30.9%?/ ADB_CACHE 29.9s / **ADB_CACHE_FAST 13.1s??71.3%?**?
  - ?? quality 1.0?`outputs_identical=1.0`???? adopt ADB_CACHE_FAST?+0.3135??
- T007 ?????force_full=false??B1 51.1s / ADB 38.0s / **ADB_CACHE_FAST 13.4s**?
  12/12 ??? B1 ?????quality 1.0??????
- T008 ?????+AR009 CASC??
  - B1 49.2s / ADB_CACHE 29.9s / ADB_CASC 30.3s / **ADB_CASC_FAST 14.1s?P50 12.1s?**?
  - prompt 2662?1079?11/12 ???quality 1.0??????
  - ??? adopt ADB_CASC_FAST?+0.4054??token ????????
- ????GPU Laya?CUDA 30.6s > CPU 25.7s?????????????? head+tail ??? 84%?
