# [AR018] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR018 |
| AR 主题 | GTB：证据接地的双脑级联（Grounded Tiered Brain） |
| 关联 SR | 端侧综合最优组合的进一步延迟优化 |
| 日期 | 2026-10-31 |
| 状态 | Approved（自动 harnessX，用户已授权无需逐步确认） |

## 1. 背景与目标

AR017 后当前联合最优为 `CGW_CASC_FAST + GPU offload`：shared-prefix P50 1.001s。
其中压缩后 prefill 约 350ms，**短输出解码约 600ms**，解码已成为主要成本。

真实探测（本机，GPU offload）：

| 模型 | shared 12 题质量 | shared 稳态 P50 | needles 12 题质量 | needles 稳态 P50 | 解码 tps |
| --- | --- | --- | --- | --- | --- |
| Qwen2.5-1.5B q4_k_m | 1.000 | 1001ms | 1.000 | 4616ms | 9.5 |
| Qwen2.5-0.5B q4_k_m | 0.750（联合压缩） | 497ms | 1.000 | 2514ms | 21.5 |

0.5B 明显更快，但 shared 上会产生错误答案（多为其它针的编号）。
SOTA 级联（FrugalGPT / RouteLLM）与 RAG 接地（Self-RAG）的核心思想是：
**小模型先答，只有证据不足或答案不可验证时才升级大模型**。本项目已有完整文档上下文，
可以用确定性的“问题锚点局部证据窗口”验证短抽取答案，无需额外模型或 mock。

目标：在不降低质量的前提下，把可验证的 short-answer full 请求交给 0.5B，
失败时回退 1.5B；与 CGW_CASC + GPU offload 组合，使最终 P50/mean 优于 AR017。

## 2. 需求范围

**In Scope：**
- `[cerebrum]` 新增小脑级联配置（默认关闭）；
- 小模型 llama-server 的真实生命周期管理（启动/健康/关闭/端口）；
- 纯确定性 grounding verifier（不调用任何外部服务）；
- full 路由的 tier dispatch、失败升级、错误回退；
- 观测头与矩阵实验支持（shared-prefix / needles / quality）；
- 与 `CGW_CASC_FAST + GPU offload` 的联合矩阵与选择器判定。

**Out of Scope：**
- 不改变 PRE/POST 决策点、线协议字段、压缩算法、prefix KV 协议；
- 不改变 light 分段生成与 streaming 语义（streaming 始终走大模型）；
- 不引入云端模型、不下载新模型、不使用 mock 后端；
- 不承诺在无证据（非抽取式）任务上加速，此类请求安全升级大模型。

## 3. 功能需求

### FR18-1 级联配置（默认关闭，向后兼容）

**描述：** `[cerebrum]` 增加 `tier_enabled`、`small_model`、`small_gpu_layers`、
`small_port`、`tier_ground_window`、`tier_max_answer_chars`。

**期望行为：** 默认 `tier_enabled=false`，不创建小模型进程；所有历史配置行为不变。
`small_model` 相对路径按仓库根解析，缺失时明确 `JevAssetError`。

**验收标准：**
- Given 未启用, When 启动 Kernel/serve, Then 只启动原大模型，响应头无 tier 字段；
- Given 启用且资产缺失, When 启动, Then 抛出 `JevAssetError`，不静默降级。

### FR18-2 真实小模型生命周期

**描述：** 小模型使用同一 `llama-server` 二进制，独立端口/进程，真实健康探测。

**期望行为：** 启动失败不阻断大模型（serve 记录告警，tiering 自动关闭）；
Kernel.close/CLI 退出时终止小模型；矩阵每个 arm 独立重启并清理。

**验收标准：**
- Given 小模型正常, When 请求 full, Then 实际 HTTP 命中 0.5B 进程；
- Given 强制停止小模型, When 请求 full, Then 自动走 1.5B 且不返回 5xx。

### FR18-3 证据接地验证器（纯确定性）

**描述：** 从问题行提取锚点（标识符、引号片段、长数字/英文词），在文档中定位锚点，
检查小模型答案是否出现在锚点邻域窗口内；答案过长/为空/无锚点/无命中均判定不通过。

**期望行为：** 不调用模型/网络；对 `K-1005` 类抽取问答题可验证；
通用生成题因答案不可局部接地而安全升级。

**验收标准：**
- Given 正确答案位于目标锚点窗口, When 验证, Then accept；
- Given 答案来自其它锚点或不在窗口, When 验证, Then reject 并升级；
- Given 空答案/无锚点, When 验证, Then reject。

### FR18-4 full 路由级联与回退

**描述：** full 路由且 tiering 启用时，先调用小模型；验证通过采用小模型结果，
否则调用大模型；小模型超时/不可用/协议错误直接回退大模型。

**期望行为：** light 分段、streaming、POST 重试保持原大模型路径；
小模型返回 usage/content 与大模型结构一致；输出质量以最终答案为准。

**验收标准：**
- Given 可接地答案, When full, Then 只调用小模型，`X-Jev-Tier=small`；
- Given 不可接地答案, When full, Then 调用大模型，`X-Jev-Tier=small->large`；
- Given 小模型超时, When full, Then 大模型回答成功，`X-Jev-Tier=small-error->large`。

### FR18-5 可观测性

**描述：** 响应头新增 `X-Jev-Tier` 与 `X-Jev-Tier-Reason`；矩阵 results 保留。
**验收标准：** 每请求可区分 small/升级/错误回退；流式请求不伪造 tier 头。

### FR18-6 真实实验与联合判定

**描述：** 新增 `gtb.toml`（隔离）与 `gtb-casc.toml`（与 CGW_CASC + GPU 联合），
对 shared-prefix 12 题做真实矩阵；另跑 needles/quality 质量回归。

**验收标准：**
- 联合矩阵 quality 不低于 CGW_CASC_FAST（1.0），输出一致率可解释；
- 选择器在配对 bootstrap 95%CI 下给出 adopt 才保留，否则回退 AR017 组合。

### FR18-7 回退与兼容

**描述：** 若联合性能无正收益或质量下降，默认关闭 tiering 并保留 CGW 组合。
**验收标准：** `tier_enabled=false` 时结果与 AR017 基线一致；profile 可控启用。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | shared 联合 P50 | 相对 AR017 下降 ≥10%，且 quality 1.0 |
| 内存 | 小模型 | 0.5B q4 约 0.5GB，不与大模型资产混用 |
| 可靠性 | 小模型故障 | 100% 回退大模型，不产生 5xx |
| 安全 | 错误接受 | 真实实验中被接受的错误答案 = 0 |
| 兼容 | 默认行为 | 关闭时与 AR017 字节级一致 |

## 5. 约束与假设

**约束：**
- 禁止 mock 后端；所有验证使用真实 llama-server/ONNX；
- 不能针对数据集硬编码答案；verifier 只使用通用锚点/窗口规则；
- 新能力默认关闭，profile/实验显式启用；
- 不修改 OpenAI 线协议既有字段，仅新增响应头。

**假设：**
- 端侧抽取式 RAG 问题在本项目负载中占多数；
- 0.5B 在已有压缩上下文下仍能复制部分答案；
- 大模型 prefix/KV 可在升级请求中复用。

## 6. 术语说明

| 术语 | 定义 |
|------|------|
| GTB | Grounded Tiered Brain，证据接地的双脑级联 |
| 小模型 | Qwen2.5-0.5B q4_k_m，只用于 full 路由首答 |
| 锚点 | 问题中可定位到文档的标识符/引号片段/长数字/英文词 |
| 接地窗口 | 锚点在文档中的位置 ± `tier_ground_window` 字符 |
| 升级 | 小模型答案不可验证时改由 1.5B 生成 |
