# [AR003] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR003 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md（已确认） |
| 创建日期 | 2026-10-25 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | 不确定性量化：per-point/请求级 U（归一化凸组合：1-conf / 1-margin / disagreement / 1-self_agree） | - | pending | FR1-1；U∈[0,1]、单调性、确定性单测 |
| T002 | 三带策略与 RoutePolicy v2：confident/borderline/uncertain + 阈值/开关配置 | T001 | pending | FR1-2；默认关闭（保持 AR002 回归） |
| T003 | 小脑自证信号：置换一致性 + 交叉一致性（复用 AR002 置换产物，无额外前向） | T001 | pending | FR1-2；默认 observe，`gate` 模式显式开启 |
| T004 | 大脑验证器：真实 verify prompt + 严格解析 + 预算共享 + 台账 | T002 | pending | FR1-3/FR1-4；真实 HTTP；解析失败=REJECT |
| T005 | chat 集成验证路由 + 头/台账 + 真实验证集（命中/精度/成本门） | T003,T004 | pending | FR1 验收 |
| T006 | 前缀指纹 + `cache_prompt` 接线 + 复用证据（/slots 或 proxy 标注）+ 单测 | - | pending | FR2-1 |
| T007 | 槽复用：`--parallel`/`--slot-prompt-similarity` 配置 + 多轮真实 TTFT 实测 | T006 | pending | FR2-2/FR2-3；含 A/B 会话隔离断言 |
| T008 | prefill 规划器：块分割/显著性/预算选择（确定性、首尾必选） | - | pending | FR3-1；纯逻辑 |
| T009 | 压缩接入 full/light + 真实 token/质量实测（needles 集、单轮/多轮双门） | T008 | pending | FR3-2/FR3-3 |
| T010 | embedding 客户端 + 可用性探测 + 独立 `--embeddings` 服务配置 | - | pending | FR4-1；禁用态安全 |
| T011 | 语义缓存：精确+近似 LRU/TTL + 复述/负例集 + 命中门 | T010 | pending | FR4-2 |
| T012 | 长程记忆：结构化摘要 + 检索 + 10 轮真实对话线性增长验证 | T010 | pending | FR4-3 |
| T013 | 指标实验：验证路由 / KV 复用 / prefill / 缓存的真实数据采集（分项脚本 + 原始 JSON） | T005,T007,T009,T011,T012 | pending | 产出原始指标，不做结论 |
| T014 | EdgePrefill 协同配置与文档（服务器执行；本机校验命令拼装与 schema） | T009 | pending | FR3-4；`deferred-server` 标注 |
| T015 | 门控汇总报告：对照 SRS §4 全部门，含环境指纹、CI、失败样本清单 | T013,T014 | pending | 结论与门值判定 |
| T016 | 回归与覆盖率：全量单测 + ruff + kernel 覆盖 ≥85% + 真实子集 bench + AR002 逐例回归 | T015 | pending | 收尾 |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中（当前会话）
- `passing`：开发完成，测试通过
- `failed`：测试失败，需修复

## 进度记录

> 每个开发会话结束后追加，记录完成情况。

### 2026-10-25 需求阶段会话记录（AR003 立项）

- 拆分为 AR003（运行时智能）+ AR004（学习与复现）；本 AR 聚焦：验证器路由 / KV 复用 / 稀疏 prefill 协同 / 语义缓存与长程记忆
- 基线：AR002 能力门控（真实 40 题命中 80%、误降级 7）；门控全部真实达标（命中≥60%、精度≥-2pt、TTFT↓≥30%、token↓≥40%、缓存命中≥30%）
- 本机范围：laya + Qwen2.5-1.5B 真跑；7B/14B 迁移 AR004（14B 仅配置）
- 关键约束：零 mock；embedding 用独立真实 llama-server；无 torch（训练类任务归 AR004）

## 阶段门控记录

### 2026-10-25 req 门控记录

- 门控结果：PASS（第 3 轮）
- 审查项数：9 项（G1–G9）
- 修复的问题：第 1 轮（U 公式越界/阈值冲突/范围孤儿等）→ 第 2 轮（语义缓存空转门/U 缺省口径/命中率冲突/shadow 断链/台账 schema/needles 粒度/PRE 基线）→ 第 3 轮仅余 Minor（已同步修正 W-2~W-7）
- 审查代理：sdd-gate-reviewer（只读子代理）
- 备注：AR004 仅通过「承接骨架」校验，须在 AR004 T001 细化后单独执行 req 门控

### 2026-10-25 design 门控记录

- 门控结果：PASS（第 2 轮）
- 审查项数：15 项（G1–G15）+ 第 1 轮 11 Important 复核
- 修复的问题：第 1 轮 I-01~I-11（τ 边界/route_chat_v2 真值表/AR002 回归/gate 语义/台账 schema/影响文件/编排顺序/异常契约/测试分层/缺失用例/分支覆盖）；第 2 轮仅余 12 Minor（已全部收敛：指纹格式/参数命名/RuntimeConfig/实例释放/覆盖范围/配置越界/追溯/首末块预算/证据优先级/组件详设/tau 命名/槽参数单测）
- 审查代理：sdd-gate-reviewer（只读子代理）
