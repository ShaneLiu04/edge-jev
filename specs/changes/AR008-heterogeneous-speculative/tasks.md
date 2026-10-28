# [AR008] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR008 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-27 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | `[cerebrum.spec]` + `spec_server_args` + 缺资产 `JevAssetError` | - | passing | 默认关闭 |
| T002 | `chat.py` spec 头/台账 + matrix spec 指标 | T001 | passing | accepted/draft、tps、ident |
| T003 | 单测：参数/异常/指标/generation | T001 | passing | 无 mock |
| T004 | 真实集成：draft_n>0（真实 0.5B 资产） | T002 | passing | `@assets` |
| T005 | 中文/英文/draft 长度/GPU 对照真实实验 | T004 | passing | 结论 rollback |
| T006 | 服务器 7B/14B spec 配置与文档 | T005 | passing | 未本机运行 |
| T007 | 环境更正：CUDA 运行库/设备/限频核查 + 历史结果标注 | T005 | passing | CPU-only 更正 |
| T008 | AR008 三文档 + RESULTS/EXPERIMENTS/PORTFOLIO 落盘 | T007 | passing | 诚实记录 |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进度记录

### 2026-10-27

- 入选路径：0.5B draft（CPU）+ 1.5B target（GPU）；greedy；默认关闭。
- 中文长输出 12 题（n8/t8）：B1 8.51 tps / 21.0s；SPEC 9.07 tps / 21.9s，
  接受率 0.187 → 回退 B1。
- 英文长输出 12 题（GPU target）：B1 5.36 tps / 56.2s；SPEC 4.78 tps / 61.0s，
  接受率 0.39 → 回退 B1。
- CPU draft 对照（4 题）：B1 8.10 tps；S2 5.83（接受率 0.624）；S4 5.03（0.470）
  → 全部慢于 B1，`rollback`。
- 环境更正：CUDA 运行库缺失导致历史结果实为 CPU-only；补齐后 GPU 限频
  645 MHz，tg 5.06 tps < CPU 9.47 tps，故本机不采用 GPU target。
- 单测与 ruff 通过；真实集成 `test_spec_decode_real` 通过。

## 阶段门控记录

- 2026-10-27 req/design：用户授权全自动推进，跳过门控（不阻塞）。
- 2026-10-27 review：以真实实验结果为准，判定 rollback（不宣称超越 B1）。
