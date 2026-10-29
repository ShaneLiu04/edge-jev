# [AR010] 软件需求说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR010 |
| AR 名称 | Latency-Aware Decision Scheduling（LADS）+ Laya POST 窗口修复 |
| 关联 SR | 端侧快慢双系统持续降低真实端到端延迟，同时守住答案质量 |
| 日期 | 2026-10-29 |
| 状态 | Approved（用户授权全自动推进） |

## 1. 背景与目标

AR009 之后，shared-prefix 场景的端到端延迟仍主要由三部分组成：
LLM prefill、LLM decode、以及 Laya 小脑决策（PRE active/shadow、POST 复核）。
本机实测 Laya 阶段成本：PRE active 约 16.7s、PRE shadow 约 4.6s、POST 约 6.6s
（12 题合计口径），决策开销与生成开销同量级。

在尝试降低该开销时发现一个更严重的缺陷：

- `kernel/cerebellum/laya_sequence.py::build_sequence` 与
  `kernel/cerebellum/laya_onnx.py::system_one` 对超长 state **保留头部**，
  预算为 `max_len=512`；
- 真实 shared-prefix 输入约 2.66k tokens，assistant 答案位于 JSON 尾部，
  被整体截掉；
- 因此 POST 对“正确长答 / 短答 / 英文 / 空串”给出**完全相同**的判定，
  `needs_retry` 恒为 `retry`；
- 每次 chat 请求都被无条件生成第二次，`usage.prompt_tokens` 约为单次的 2 倍，
  历史长 prompt 实验的绝对延迟与 token 均被系统性放大。

AR010 的目标：

1. **修复 POST 盲区**：让 Laya 在长 state 下能看到最新问题与答案尾部。
2. **保留质量兜底**：对确定性退化输出（短周期全串重复）触发一次重试。
3. **可选低延迟决策**：允许 chat 跳过无动作的 shadow 与 POST 复核，
   `/v1/systemone` 仍保持完整决策。
4. **真实可观测**：暴露决策模式、决策数、前缀命中、端到端延迟与输出一致性。
5. **可回退**：所有新行为默认关闭；选择器按质量与延迟效用决定 adopt/rollback。

## 2. 需求范围

**In Scope**

- Laya state 截断语义修正（批处理与单序列一致，保尾部）。
- `server/chat.py` 退化重复守卫。
- `RuntimeConfig.decision_shadow` / `decision_post` 及 `configs/jev.toml` 配置。
- chat 快速决策模式与 `X-Jev-Decision-Mode` 观测头。
- 矩阵实验：B1 / FAST / CACHE / CACHE_FAST；profile `latency-fast`。
- 受影响的真实实验复测：AR006 / AR007 / AR009 / AR010。
- 单测、真实 Laya 回归、文档与追溯更新。

**Out of Scope**

- 修改 Laya 模型权重或重新训练。
- 为 Laya 引入 GPU EP（DirectML / CUDA 均因运行库或驱动不兼容被否决）。
- 在 7B/14B 服务器资产上运行（仅保留配置）。
- 改变 `/v1/systemone` 的线协议或返回的决策集合。

## 3. 功能需求

| ID | 需求 | 验收 |
|----|------|------|
| FR10-1 | 超长 state 必须保留尾部，答案/最新问题可见 | 真实测试中正确长答与空答的 POST 决策至少一项不同 |
| FR10-2 | 截断仍须标记 `truncated=true`，不得静默丢数据 | 既有 truncate/refuse 测试保持通过 |
| FR10-3 | 短周期全串重复输出须触发一次重试 | 单元测试：退化答案后返回正常答案，且累计 usage |
| FR10-4 | 重试后仍退化须标记降级 | `jev_degraded=true`，不无限重试 |
| FR10-5 | `decision_shadow=false` 时 chat 不评估 shadow | 决策数下降，输出不变 |
| FR10-6 | `decision_post=false` 时 chat 不做 POST 复核/重试 | 单次大脑调用，输出不变 |
| FR10-7 | 两开关默认 true，行为与修复前一致 | 默认配置回归通过 |
| FR10-8 | `/v1/systemone` 不受两开关影响 | 现有集成测试通过 |
| FR10-9 | 提供 `X-Jev-Decision-Mode: fast` 与 `last_runtime.decision` | 实验可采集 |
| FR10-10 | 矩阵支持 B1/FAST/CACHE/CACHE_FAST 与延迟效用选择 | `lads.toml` 可复现 |

## 4. 非功能需求

| ID | 需求 |
|----|------|
| NFR10-1 | 任何新能力默认关闭；fast 模式必须显式开启 |
| NFR10-2 | 不做后端 mock；真实回归使用真实 Laya / llama-server |
| NFR10-3 | 修复后 shared-prefix 12 题四臂输出完全一致 |
| NFR10-4 | 单元测试全绿、ruff 零告警 |
| NFR10-5 | 历史受污染结果必须标注并给出修复后复测表 |

## 5. 验收结果摘要

- shared-prefix 12 题：B1 54.0s、FAST 34.2s、CACHE 42.0s、**CACHE_FAST 24.6s**；
  四臂 quality 1.0、`outputs_identical=1.0`；选择器 adopt CACHE_FAST（效用下界 +0.2539）。
- AR009 复测：CASC 38.1s / 1079 tokens（−59.5%）/ quality 1.0。
- AR007 复测：prefill 8202→927ms（8.8×），端到端 48.4→40.5s。
- AR006 复测：三臂 quality 0.833（11/12），AEB tokens 2921→1590（−45.6%），adopt AEB；
  质量较旧表低 1 题，旧表 0.917 依赖无条件重试的偶然修复。
- 单测 688 passed；ruff 零告警。

## 6. 风险与回退

- fast 模式放弃 Laya 自评，只适用于“输出一致性 + 外部金标”可验证的场景；
  默认仍为完整决策。
- Laya 对短答仍偏向 `off_topic`，不能单独作为质量判据。
- 若后续场景出现质量回退，关闭 `decision_post` / `decision_shadow` 即可回到完整路径。
