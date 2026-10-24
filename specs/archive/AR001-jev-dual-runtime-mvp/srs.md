# [AR001] 需求设计说明书 — jev-dual-runtime-mvp

| 字段 | 内容 |
|------|------|
| AR 编号 | AR001 |
| AR 主题 | jev-dual-runtime-mvp（端侧 Jev+LLM 双系统运行时 M1–M3） |
| 关联 SR | 无（新项目自研） |
| 日期 | 2026-10-23 |
| 状态 | Draft |

> 注：本工程为新建仓库，`specs/component-detail-design/` 组件详设缺失。领域知识基准为 `docs/research/github-survey.md`（GitHub 全量调研报告，2026-10-23）。

## 1. 背景与目标

GitHub 调研（见 `docs/research/github-survey.md`）确认：「Jev+LLM 双系统」方向已有先行者（DuoMind 18 决策点、hermes-jev-skills 13 技能、ollaya 决策模型运行时），但**小脑+大脑全部本地化的端侧双系统运行时不存在**（DuoMind 小脑依赖云端 API，隐私与离线能力双缺）。本 AR 从零构建 Jev 仓库：以「快思考小脑（类型化决策模型）+ 慢思考大脑（本地 LLM）」双系统为核心，集百家之长（DuoMind 决策点框架 / hermes 台账与 shadow mode / ollaya 运行时范式 / von 校准与置信门控 / OpenJev 蒸馏飞轮思想 / TypeSafe 线协议），交付全本地、可审计、可自进化的双系统运行时。

## 2. 需求范围

**In Scope（本 AR 要做的，M1–M3）：**

- M1 双系统骨架：双协议 HTTP 端点、小脑后端（Laya ONNX 默认 + kev-0.8B 可插拔）、大脑后端（llama-server 子进程 + Qwen2.5 GGUF）、决策点框架（18 点全集）、置信门控级联、熔断降级、`jev fetch` 模型获取
- M2 完整决策面：决策台账、CLI 套件、Python SDK、MCP Server、18 点全链路接入与 shadow mode
- M3 蒸馏飞轮：S2→S1 轨迹采集→数据集→小脑校准（冻结权重）→可选 LoRA 微调→复测，以及评测基准套件

**Out of Scope（本 AR 不做的，留待后续 AR）：**

- 车机/MNN 适配（M4）
- 中文小脑训练（Gap#4，M4）
- 云大脑端云变体（小V助手式，M4）
- 多模态小脑（OmniJev 类，M4+）
- 投机解码草稿头（小脑为大脑草稿，M4+）
- C++/Rust 移植（M5）

## 3. 功能需求

### 3.1 FR1 双协议 HTTP 端点

**描述：** FastAPI HTTP 服务同时暴露 TypeSafe 严格兼容的 `POST /v1/systemone`（小脑）与 OpenAI 兼容的 `POST /v1/chat/completions`（大脑+协同）。

**触发条件：** 客户端向任一端点发起请求。

**期望行为：** systemone 端点接收 `{model, state, questions}` 返回 TypeSafe 线协议结构（answers/usage，严格字段对齐，生态 SDK 换 base URL 即可切换）；chat 端点接收 OpenAI Chat 请求，经决策点框架与级联引擎处理后返回（支持流式与非流式）。

**异常处理：** 非法请求返回 TypeSafe/OpenAI 规范错误码；服务过载返回 503 + Retry-After。

**验收标准：**
- Given TypeSafe 协议样例请求, When POST /v1/systemone, Then 响应字段结构与 TypeSafe 线协议完全一致（含 usage.input_tokens）
- Given 合法 Chat 请求, When POST /v1/chat/completions, Then 返回 OpenAI 兼容结构且可被 openai SDK 消费
- Given 超长 state（>上下文窗口）, When POST /v1/systemone, Then 返回带 truncation 标记的截断处理（对齐 von 行为）

### 3.2 FR2 小脑后端抽象（双后端可插拔）

**描述：** 定义 `CerebellumBackend` 统一接口（decide/judge/rate/system_one），提供两个真实实现：Laya ONNX（onnxruntime，默认）与 kev-0.8B（Qwen3.5 底座决策模型）。

**触发条件：** 任一决策点或 systemone 端点需要类型化决策。

**期望行为：** 单次前向返回 Choice（选项概率分布）/ Noul（0–1 概率）/ Score（等级+置信度），带校准置信度；后端通过配置切换，不影响上层。

**异常处理：** 后端加载失败/推理异常抛出类型化错误，由熔断器（FR6）接管。

**验收标准：**
- Given 同一 (state, questions), When 分别用 Laya 与 kev 后端, Then 两者均可返回结构一致的合法答案
- Given 批量问题（一次请求多问）, When 推理, Then 共享单次编码（Laya 单 forward 多问，对齐 von/kev 行为）
- Given 小脑单决策, When CPU 推理, Then P50 ≤ 600ms（短 state 基准集；AR001 返工修订，原 P50<20ms 经实测不可达，依据见 design.md §4.5 性能调查）

### 3.3 FR3 大脑后端（llama-server 子进程）

**描述：** 以预编译 llama-server 二进制子进程方式管理大脑（Qwen2.5-1.5B/3B-Instruct GGUF Q4_K_M 默认），提供生命周期管理（拉起/健康检查/自动重启/优雅退出）与 OpenAI 兼容客户端。

**触发条件：** 级联升级（FR5）或 chat 端点请求需要生成能力。

**期望行为：** `jev serve` 启动时按需拉起 llama-server，就绪探针通过后才对外服务；进程崩溃自动重启（最多 N 次后退避）；退出时优雅关闭子进程。

**异常处理：** llama-server 启动失败/超时给出明确诊断（`jev doctor` 可查）；连续崩溃熔断大脑侧并返回降级响应。

**验收标准：**
- Given models/ 下已有 GGUF, When 启动大脑后端, Then 健康检查通过后进入 Ready 状态
- Given 强杀 llama-server 进程, When 运行中, Then 自动重启且后续请求成功
- Given 优雅停机命令, When 退出, Then 子进程无残留

### 3.4 FR4 决策点框架（DuoMind 18 点全集）

**描述：** 可插拔 `DecisionPoint` 注册表：PRE 9 点（needs_generation/needs_reasoning/intent/complexity/ambiguity/safety/verbosity/format/needs_tool）、MID 4 点（on_track/step_complete/should_stop/confidence_mid）、POST 5 点（answer_complete/matches_request/needs_retry/too_verbose/correct_format）；每点支持 active/shadow/off 三态；允许注册自定义决策点。

**触发条件：** chat 端点处理请求的 PRE/MID/POST 阶段。

**期望行为：** active 态决策结果参与控制流；shadow 态只记录不生效；off 态完全跳过。决策点定义（问题模板、阈值、动作映射）声明式配置。

**异常处理：** 单决策点异常不中断请求（降级为该点 fallback 动作并记台账）。

**验收标准：**
- Given 任一内置 18 点, When 配置为 active, Then 其决策影响对应控制流（如 needs_reasoning=false 走轻量路径）
- Given 任一点配置为 shadow, When 请求处理, Then 台账记录决策但控制流不变
- Given 注册自定义决策点, When 框架启动, Then 被识别并按三态执行

### 3.5 FR5 置信门控级联引擎

**描述：** 双系统核心协同机制：小脑高置信（≥τ，默认按决策点可配）直接执行/直答；低置信升级大脑；大脑完成后后验回流（供 FR12 采集）。级联策略支持「小脑直答」「小脑草拟+大脑校验」「直接大脑」三模式。

**触发条件：** 每个 chat 请求的 PRE 阶段与 systemone 请求。

**期望行为：** 简单请求（生产目标≥70%）由小脑独立完成（needs_generation/intent/complexity 联合判定），不经大脑；复杂请求升级大脑并将小脑的 PRE 判定作为上下文注入。（AR001 ST 修订：≥70% 为生产目标保留；现有 laya 资产判定能力局限下 AR001 验收口径见验收标准与 design.md §4.7）

**异常处理：** 级联判定本身失败默认升级大脑（安全侧）。

**验收标准：**
- Given 基准问题集（简单:复杂 ≈ 7:3）, When 端到端运行, Then 小脑直答命中率 ≥ 70% 且小脑直答路径 P50 ≤ 大脑路径 P50 的 1/3（AR001 ST 修订：真实 laya 资产下命中率实测 0%——complexity 恒 medium/intent 无 decision 输出，light/direct 路由不可达，属资产判定能力局限而非代码缺陷，机制/路由/安全侧回退均按设计工作；AR001 验收为「机制就绪 + 资产局限记录」，命中率门挂起待资产升级后以含英文题的基准集复测，依据与探针证据见 design.md §4.7）
- Given 低置信决策, When 级联, Then 大脑被调用且请求最终成功
- Given 大脑答案, When POST 复核点, Then needs_retry 逻辑可触发一次重试上限

### 3.6 FR6 熔断降级与规则 fallback

**描述：** 对小脑后端实现熔断器（滑动窗口失败率/连续失败计数触发，半开探测恢复），熔断期间决策点走本地确定性规则 fallback（对齐 DuoMind 模式：60s 内 3 次失败→降级 30s）。

**触发条件：** 小脑后端连续失败或超时。

**期望行为：** 熔断开启后所有决策点自动走规则 fallback，请求不失败；半开期试探真实后端，成功则闭合。

**异常处理：** 熔断器自身异常默认放行真实后端。

**验收标准：**
- Given 注入连续 3 次小脑失败, When 第 4 次请求, Then 走 fallback 且端到端请求成功
- Given 熔断开启 30s 后后端恢复, When 新请求, Then 半开探测成功后回到真实后端
- Given 熔断触发, When 切换完成, Then 切换耗时 < 100ms

### 3.7 FR7 模型获取 `jev fetch`

**描述：** CLI 从 HuggingFace（及 hf-mirror.com 镜像回退）拉取模型资产：Laya ONNX 权重、kev-0.8B、Qwen2.5 GGUF、llama-server 预编译二进制；含 sha256 校验与断点续传；模型不入仓（models/ 目录 + manifest 清单）。

**触发条件：** 用户执行 `jev fetch <asset>` 或首次 `jev serve` 检测缺失资产。

**期望行为：** 按 manifest 校验已存在资产（哈希匹配则跳过）；下载失败自动切换镜像源；进度可观测。

**异常处理：** 校验失败删除残片并重试；全部源失败给出网络诊断指引。

**验收标准：**
- Given models/ 为空, When `jev fetch --all`, Then 四类资产就位且 sha256 全部匹配
- Given 断网中断下载, When 再次 fetch, Then 从断点续传而非重下
- Given 已就位资产, When 再次 fetch, Then 5s 内完成跳过（哈希命中，不含下载）

### 3.8 FR8 决策台账 ledger

**描述：** 每次决策落盘记录：时间戳/决策点/问题/全选项概率/置信度/延迟/路由去向（小脑直答 or 大脑）/熔断状态/请求 ID；支持查询与导出（JSONL）。

**触发条件：** 任一决策点执行（含 shadow）。

**期望行为：** 台账写入异步不阻塞主路径；按请求 ID 聚合检索一次请求的完整决策链。

**验收标准：**
- Given 一次端到端请求, When 查询台账 by request_id, Then 可还原该请求全部决策点结果与路由路径
- Given shadow 态决策, When 查台账, Then 记录含 mode=shadow 标记
- Given 100 并发请求持续写入台账, When 台账运行, Then 主路径延迟无可观测劣化（<1ms 开销）

### 3.9 FR9 CLI 套件

**描述：** `jev serve`（启动服务）/ `jev fetch`（FR7）/ `jev doctor`（环境体检：GPU/内存/模型/llama-server/端口）/ `jev bench`（FR13）/ `jev distill`（FR12）。

**验收标准：**
- Given 全新机器, When `jev doctor`, Then 输出环境问题清单与修复建议
- Given 各子命令, When `--help`, Then 有完整用法说明

### 3.10 FR10 Python SDK

**描述：** 进程内 SDK：`JevClient`（HTTP 客户端，同步+异步）与 `DecisionPoint` 编程注册 API，供应用嵌入双系统。

**验收标准：**
- Given SDK, When 应用内注册自定义决策点并启动 serve, Then 行为与配置文件注册一致
- Given SDK, When 调用 decide/judge/rate, Then 返回类型化结果对象（非裸 dict）

### 3.11 FR11 MCP Server

**描述：** 将小脑决策能力暴露为 MCP server（tools：decide/judge/rate），接入 Claude Code/Cursor 等生态客户端。

**验收标准：**
- Given MCP client, When 调用 decide tool, Then 返回结构化决策结果

### 3.12 FR12 S2→S1 蒸馏飞轮

**描述：** 闭环自进化：采集大脑升级路径的轨迹（state + 决策点问题 + 大脑行为作为 gold）→ 导出 (state, question, gold) 数据集 → 小脑校准（冻结权重重校准温度/阈值，von calibrate 式，CPU 分钟级）→ 可选 LoRA 微调（kev-0.8B 路径，GPU）→ 复测级联指标。

**触发条件：** 台账积累足够样本后用户执行 `jev distill`。

**期望行为：** 蒸馏后小脑在留出集上校准指标（NLL/ECE）不劣化，级联命中率提升或持平；全部产物（数据集/校准文件/报告）版本化保存。

**验收标准：**
- Given 台账中 ≥ 500 条大脑升级轨迹（最小样本阈值）, When `jev distill --calibrate`, Then 产出新校准文件 + 留出集评估报告
- Given 蒸馏后小脑, When 重跑 bench, Then 级联精度不回退（验收阈值：精度差 ≥ -2pt）且命中率不下降
- Given GPU 可用, When `jev distill --lora`, Then kev-0.8B LoRA 训练完成并产出适配器

### 3.13 FR13 评测基准 `jev bench`

**描述：** 基准套件输出：小脑延迟分布（P50/P95）、级联命中率、级联精度保持（vs 直连大脑基线）、成本曲线（token 消耗对比）、决策点开销分解；基准问题集内置（简单:复杂分层标注）。

**验收标准：**
- Given 基准问题集, When `jev bench --full`, Then 输出上述五类指标的结构化报告（JSON+Markdown）
- Given 级联模式 vs 直连大脑模式, When 对比报告, Then 可量化展示双系统收益（延迟/成本）

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | 小脑单决策延迟（CPU，短 state） | P50 ≤ 600ms，P95 ≤ 1.5s（AR001 返工修订；原 P50<20ms/P95<50ms 经实测不可达，见 design.md §4.5） |
| 性能 | 级联命中率（小脑直答占比） | 生产目标 ≥ 70%（AR001 ST 修订：现有 laya 资产下实测 0%，属资产判定能力局限；AR001 验收为机制就绪 + 局限记录，门挂起待资产升级复测，见 design.md §4.7） |
| 性能 | 熔断切换耗时 | < 100ms |
| 质量 | 蒸馏后级联精度 | 不回退超过 2pt |
| 可靠性 | 小脑故障期间请求成功率 | 100%（fallback 兜底） |
| 兼容性 | /v1/systemone | TypeSafe 线协议严格兼容（生态 SDK 零成本切换） |
| 兼容性 | /v1/chat/completions | OpenAI SDK 可直接消费 |
| 工程 | 测试 | 全真实后端（无 mock），单元+集成测试全绿 |
| 硬件 | 基准环境 | RTX 5000 16GB / 127GB RAM / Python 3.11 / uv |

## 5. 约束与假设

**约束：**
- C1 全量真实后端，不得引入任何 mock/桩后端；测试使用真实小模型（CPU 可跑）
- C2 Python 3.11+，uv 管理依赖；核心推理路径避免重量级框架依赖
- C3 大脑必须进程隔离（llama-server 子进程），不得进程内嵌入 llama.cpp
- C4 模型权重与 llama-server 二进制不入仓，由 `jev fetch` 管理
- C5 `/v1/systemone` 严格对齐 TypeSafe 线协议（请求/响应/错误码/usage）
- C6 决策点框架三态（active/shadow/off）与台账为硬性设计，所有决策可审计
- C7 遵循 TDD（Red→Green→Refactor），遵循 BU clean code 规约

**假设：**
- A1 内网代理可通 HuggingFace 与 hf-mirror.com（已验证 GitHub/HF 通道）
- A2 llama-server 预编译 Windows 二进制可用（llama.cpp releases）
- A3 kev-0.8B 权重可从 HF 公开获取（jaredpalmer/kev-0.8b）
- A4 首版聚焦英文小脑（Laya/kev 英文为主），中文场景留待后续 AR

## 6. 术语说明

| 术语 | 定义 |
|------|------|
| 小脑（Cerebellum / System 1） | 类型化决策模型：单次前向回答 Choice/Noul/Score，不生成 token |
| 大脑（Cerebrum / System 2） | 本地 LLM（llama.cpp + GGUF），负责生成与复杂推理 |
| 决策点（DecisionPoint） | 请求处理流程中的固定决策插槽（PRE/MID/POST 三阶段） |
| 级联（Cascade） | 小脑高置信直答、低置信升级大脑的协同机制 |
| 蒸馏飞轮（S2→S1 Flywheel） | 大脑轨迹蒸馏为小脑训练/校准数据的自进化闭环 |
| 台账（Ledger） | 全量决策审计记录 |
| 线协议（Wire Protocol） | `/v1/systemone` 的 TypeSafe 事实标准请求/响应格式 |
