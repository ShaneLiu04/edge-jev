# [AR002] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR002 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md（已确认）|
| 创建日期 | 2026-10-24 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | 探针引擎：DecisionPoint 多探针声明 + 批内并行 + 置换（permutations）生成与归并 | - | passing | FR1-1/FR1-2；复用单前向多问；配置向后兼容 |
| T002 | 探针融合与路由校准：加权融合、per-point 温度/τ/margin 拟合与扫描落盘 | T001 | passing | FR1-1/FR1-3；复用 distill calibrate |
| T003 | 双语分层基准集 v2：中英各半 + 分层标签 + 校验（含 order 扰动集） | - | passing | FR1-2/FR4；修正 srs A4 口径错配 |
| T004 | 级联路由增强：Top-2 margin 升级 + 请求级代价预算 | T002 | passing | FR2-1/FR2-2；默认关闭预算、margin 可配 |
| T005 | PRE 草稿注入：结构化结论压缩为 system 前缀 + token 计量 | T004 | passing | FR2-3；路径②③ |
| T006 | 慢响应可靠性：HTTP 超时与级联 deadline 统一预算 + 真实慢对端测试 | - | passing | FR2-4；修复 ST 观察项 |
| T007 | 学习式 shadow 路由：台账特征抽取 + numpy 逻辑回归 + shadow 报告 | T002 | pending | FR2-5；仅记录不切换 |
| T008 | 蒸馏数据深化：难例优先采样 + 对比样本配对与报告 | - | pending | FR3-1/FR3-2 |
| T009 | 飞轮闭环 CLI：`jev distill --flywheel`（采集→数据集→校准→复测→门→发布/拒绝） | T002,T008 | pending | FR3-3；退出码反映门 |
| T010 | bench 多基线：brain_direct / cerebellum_only / cascade + 统一指标 | T003 | pending | FR4-1 |
| T011 | 统计显著性与环境指纹：成对 bootstrap CI + 版本/哈希指纹 | T010 | pending | FR4-2 |
| T012 | 报告升级：JSON+Markdown 对比汇总表 + 失败样本清单 | T010,T011 | pending | FR4-3 |
| T013 | README 重写：定位/架构/创新点/快速开始/实验矩阵/指标口径 | - | pending | FR5-1 |
| T014 | REPRODUCTION.md + 实验矩阵配置（0.8B/4B/9B、ablation、种子） | T012 | pending | FR5-1/FR5-2 |
| T015 | 一键脚本：run_experiment.(sh/ps1) + CI 质量门脚本升级 | T014 | pending | FR5-2/FR5-4 |
| T016 | 推送方法论与最小包工具入库（脱敏）+ AGENTS.md 推送约束条目 | - | pending | FR5-3；C5 |
| T017 | 大模型实验：kev 0.8B CPU 小规模蒸馏/LoRA 验证 + 4B/9B 服务器配置与脚本 | T009,T014 | pending | 本机仅小规模；4B/9B 交付配置 |
| T018 | 回归与门验收：全量单测/覆盖率/真实资产 bench（含命中率门） | T001-T017 | pending | NFR 门；ST 阶段复测 |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中（当前会话）
- `passing`：开发完成，测试通过
- `failed`：测试失败，需修复

## 进度记录

> 每个开发会话结束后追加，记录完成情况。

### 2026-10-25 开发会话记录（T001/T002 完成 + 路由口径修订）

- 完成任务：T001 探针引擎（多探针/置换规划/对数意见池融合，支持异构选项集——按标签加权平均，未覆盖选项按 EPS 补位）；T002 融合+校准贯穿（`evaluate_stage` 迁入 kernel、calibration v1/v2 加载、chat shim 委托）
- 关键缺陷修复：`fuse_point` 跨探针标签集不一致（intent 的 binary 探针缺 `simple`）导致 `KeyError`——改为标签并集 + 逐标签加权 log 平均
- 真实资产实测（20 题，真实 Laya 权重）：多探针使 `complexity` 首次出现 low/medium/high 三档（AR001 恒 medium）；`needs_generation` 对决策类 10/10 稳定 `no_generation`
- 路由口径修订（design §4.5.1）：能力门控 `RoutePolicy.capability_gate`，命中率 10.0% → 75.0%，误降级 5 → 3；`capability_gate=false` 可逐字恢复 AR001
- 测试：全量单测 427 passed；`ruff check .` 零告警；新增 UT 覆盖异构探针融合、能力门控/关闭回退、unsafe 阻断 direct
'- 追加 T004（同日）：margin/预算接线到 chat 全链路（非流式+流式）——`X-Jev-Margin`/`X-Jev-Budget` 仅在启用时出现；预算不足降级 light 并回 `capped`，完成 token 受 `effective_max_tokens` 约束；Kernel 暴露 `[cascade] max_brain_tokens/max_latency_ms`（默认关闭）
- 测试：431 passed；ruff 零告警
'- 追加 T006（同日）：大脑客户端支持单次 `timeout_s` 覆盖，chat 侧按剩余 deadline 推导（分段/重试各自推导）；消除 120s 客户端超时与级联截止竞态——旧行为「大脑 0.5s 迟到仍当成功」，现 0.15s 内截断降级；light 次段超时保留首段
- 测试：432 passed；ruff 零告警
'- 追加 T003（同日）：`configs/bench-set.yaml` v2（40 题：中英各 20；每条标注 `lang`，多轮题标注 `turns`；三路径 × 两语种全覆盖 7:3 分层）；加载器 v1 兼容（缺 lang 默认 zh）+ `validate_bench_set`（id 唯一/双语占比 ≥40%）
- 测试：440 passed；ruff 零告警；集成用例口径同步（40 题/light 8/full 12）
'- 追加 T005（同日）：新增 `kernel/cascade/draft.py`（确定性 token 粗估 4 字符/token；轮次切分压缩超期历史 ≤120 字/轮；PRE 结论→`系统预判：…` system 前缀）；chat full 路径接入，默认关闭；另按 FR2-4 细化 `JevTimeoutError`（按异常类型而非时钟判定截断降级，消除 deadline 边界抖动误判 503）
- 测试：460 passed；ruff 零告警
- 下一步：T007（shadow 学习路由）、T011（bootstrap CI + order_flip_rate）、真实大脑 bench 精度门实测''''

### 2026-10-24 需求阶段会话记录（Gitee 初版交付 + AR002 立项）

- 完成任务：现有 AR001 交付物同步 Gitee（edge-jev，25 提交/112 文件/树哈希一致，全新克隆验证通过）；AR002 需求澄清与 srs/tasks 生成
- 关键事实：企业代理拦截 >~100KB POST；uv.lock 单对象 108.9KB zlib 为硬阻塞——以「四步前缀增长 + 可续跑分片上传」重建整洁历史，原历史存 bundle（temp）与本地 `legacy/ar001` 分支
- 需求要点：A 判定质量（多探针/顺序不变性/路由校准/双语基准）×B 智能路由（margin/预算/草稿注入/超时可靠性/shadow 学习路由）×C 飞轮深化（难例/对比样本/一键闭环）×D 多基线评测（CI/指纹）×E 复现工程
- 门口径：命中率 ≥60%（分阶段 40%→60%，CI 显著优于 0% 基线）；精度保持 ≥-2pt；顺序翻转率降 ≥50%；brain token 降 ≥40%
- 下一步：需求门控（可选）→ design.md

## 阶段门控记录

> 由 sdd-phase-gate skill 在阶段门控审查后追加，记录每轮审查结果（PASS/FAIL + 轮次）。格式见 sdd-phase-gate SKILL.md Step 6。
