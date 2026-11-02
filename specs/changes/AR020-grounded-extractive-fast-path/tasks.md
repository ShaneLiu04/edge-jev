# [AR020] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR020 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-11-02 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T2001 | `ExtractiveConfig`/`GroundedExtractor` 确定性抽取与单测 | - | passing | numeric/date |
| T2002 | 数据集离线验证（shared/needles/quality/generation） | T2001 | passing | 0 误答 |
| T2003 | Kernel/app/chat 接线 + POST 校验 + `X-Jev-Extract` | T2001 | passing | full 非流式 |
| T2004 | Matrix 接线 + `extractive.toml` 真实实验 | T2003 | passing | tokens=0 |
| T2005 | 真实集成（无大脑也可抽取）+ 全量回归 | T2004 | passing | 752 unit + 2 real |
| T2006 | 文档/README/REPRODUCTION/服务器复刻说明 | T2005 | passing | 待提交 |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进度记录

- 需求阶段：离线探测 shared 12/12、needles 30/30、quality 0 误命中、
  shared-generation 0 误命中；据此确定保守型 GEXA。
- 开发阶段：抽取器/内核/chat/matrix 接线完成；真实 Laya + 无大脑命中通过；
- 真实矩阵：shared GEXA_GTB P50 176ms vs CGW 937ms（−81.1%）；
  needles GEXA P50 3.9ms vs CGW 10.16s（−99.96%）；quality 非回归；
- 回归：`752 passed`、ruff 零告警、真实集成 2 passed。

## 阶段门控记录

（自动化 harnessX：用户已授权跳过逐步确认与门控交互，仅记录本行）
