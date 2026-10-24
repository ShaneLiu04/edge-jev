# Jev 端侧双系统（快思考小脑 + 慢思考大脑）— GitHub 全量调研报告

> 调研日期：2026-10-23
> 调研方式：GitHub Search/Repos REST API（经企业代理），15 组检索词、40+ 仓库元数据、13 份核心 README 全文深读，辅以内部知识库（小V助手端云协同案例、DriveVLM 车载双系统、GR00T System1/2 混合架构）交叉佐证。
> 目的：为构建「端侧 Jev + LLM 双系统」（小脑=快思考决策模型，大脑=慢思考生成模型）奠定基础。

---

## TL;DR 五条关键结论

1. **"Jev" 在 GitHub 上已是一个成熟生态**：Jev 是 TypeSafe AI 的 System One 类型化决策模型（非生成式，输入 state + 类型化问题，输出 Choice/Noul/Score + 校准置信度，70–500ms，`/v1/systemone` 线协议）。围绕它已形成 600+ 项目的应用模式库（awesome-jev，2442★）。
2. **开源"小脑"已被验证可端侧化**：Laya（ModernBERT 类，7–13ms/决策）、von（395M，CPU/OpenVINO，已归档→LiquidAI d1-omni-600M）、kev（Qwen3.5 系 0.8B–27B）、OmniJev（Qwen3.5-VL 多模态）、甚至 ESP32 单片机固件（jevlike-esp32）——毫秒级本地决策模型技术栈完备。
3. **Jev+LLM 双系统已有先行者，但全部依赖云端 Jev**：DuoMind（本地小 LLM + 云端 Jev API，18 个决策点）是最接近的先行项目；hermes-jev-skills（1151★）证明了模型路由/记忆筛选/技能选择等 13 类决策外包给小脑的价值。**"小脑+大脑全部本地化的端侧双系统运行时"是明确空白**。
4. **快慢思考在三个层面都有成熟技术可借鉴**：系统级（DuoMind/SwiftSage/GR00T/DriveVLM）、解码级（投机解码 EAGLE/DeepSpec/云边端分层投机 DuoDecoding）、模型内（Qwen3 thinking 开关、AnyJev adaptive 低置信升级 CoT）。
5. **架构启示**：Jev 仓库应做成「双系统运行时」而非又一个推理引擎——标准化决策点框架（PRE/MID/POST）、置信度门控与级联、S2→S1 蒸馏飞轮（OpenJev "explore first, crystallize later"）、双协议端点（`/v1/systemone` + `/v1/chat/completions`），底层复用 llama.cpp/MNN/ONNX Runtime 而非自研。

---

## 一、Jev / System One 生态全景

### 1.1 Jev 本体（TypeSafe AI，闭源托管 API）

- 定位：**不是聊天模型**。输入非结构化 state + 类型化问题，输出类型化决策（三选一）：
  - `Choice`：多选一，返回全选项概率分布
  - `Noul`：是/否判断，返回 0–1 概率
  - `Score`：有序等级评分，带置信度
- 性能：70–500ms、单次前向、不生成 token；计费仅输入 token（~$0.15/M tokens）
- 生态协议事实标准：`POST /v1/systemone`，任何兼容客户端换 base URL 即可切换后端
- 架构揭秘（社区逆向分析《Jev's Architecture Unmasked》：~10,000 次 API 调用推断）：**保留 LLM 世界知识、彻底移除 token 生成**——这正是 kev（Qwen3.5 底座微调）复刻的路线
- kev 实测对比：kev-27B 新数据集准确率 0.851 vs Jev 0.857（几乎打平）；kev-4B 在 4GB GPU / 32GB Mac 即可运行

### 1.2 开源决策模型（"小脑"候选底座）

| 仓库 | ★ | 技术 | 对 Jev 的价值 |
|---|---|---|---|
| jaredpalmer/kev | 9459 | Qwen3.5/3.8 0.8B–27B，适配器微调，温度校准，TypeSafe SDK 兼容 | 准确率最接近 Jev 的开源复刻；kev-0.8B 可上笔记本 |
| mizorewww/laya-mlx | 7003 | MLX 原生 encoder，421M/322M，**13.4ms / 7.4ms P50**，0 输出 token | 端侧延迟标杆；Snake 实时决策 75 moves/s |
| TheoLeeCJ/SemIf-OpenJev | 4877 | 开放模型语义判断，3090 家用可训 | 训练路线参考 |
| nokia-applied-research/AnyJev | 1472 | **training-free**：任意 LLM 两旋转读出 + 秩不变校准；自蒸馏 Tacit 1.7B–9B | "免训练获得决策模型"与 adaptive 快慢混合（低置信→CoT，上限 20%） |
| ollaya-dev/ollaya | 1382 | Rust 单二进制，"决策模型的 Ollama"，拉取/服务 laya/kev/winnow，`/v1/systemone` 兼容 | 端侧小脑的运行时/分发范式直接照抄 |
| receptron/laya | 988 | Node/TS + ONNX Runtime 运行 Laya | JS 生态接入参考 |
| wfzyx/von | 918 | 395M ModernBERT，OpenVINO CPU，Noul 置信带映射，**已归档** → 接班者 LiquidAI/d1-omni-600M（含多模态） | CPU 端侧校准决策的完整工程细节（置信门控 0.80→92.4% 保持精度） |
| Valen-Team/Valen | 874 | 可自训的 Jev-like 多模态 System One（视觉） | 多模态小脑训练方案 |
| tinnel123666888/OmniJev | 168 | Qwen3.5-VL 0.8B/2B/4B，截图/视频/机器人相机 → 类型化问题 | 屏幕理解/GUI Agent 小脑 |
| 1Panel-dev/laya-server | 108 | 自托管 `/v1/systemone` API 服务器 | 协议服务参考 |
| BudEcosystem/Bud-Decision-Studio | 107 | 跨平台桌面决策模型管理与 serving | 工具链参考 |
| itsmostafa/system-one-connector | 354 | MCP connector：Jev/D1/CLM/Laya 快评 | MCP 接入参考 |

### 1.3 生态模式库（awesome-jev，15 类 600+ 条目，直接可抄的"小脑职责清单"）

按类目规模排序的核心模式（括号内为实测数据代表）：

| 模式 | 典型用法 | 实测 |
|---|---|---|
| **Classification & Routing**（75 条） | 邮件分流/工单路由/意图分类/模型选择 | DocJev 40/40 正确，182ms p50；jev-oncall p50 418ms、$0.04/千告警 |
| **Infra/SDK**（119 条） | `/v1/systemone` 网关、各类语言 SDK、MCP server | new-api 原生端点；Trio-Spark <0.6 置信转人工 |
| **Agent Decisions**（63 条） | 技能选择（377 技能 2.8s）、上下文压缩选段（71 轮 0.95s）、工具调用门控 | mu：38 个决策点，日志裁剪 40–46% 无损 |
| **Verification & Guardrails**（59 条） | 提示注入检测（80 条真实网页 70/79 拦截 vs 自身扫描 11）、安全否决 | web screening ~0.2s/条 |
| **Scoring & Ranking**（44 条） | 检索段落排序、简历评分、严重度分级 | — |
| **Evaluation**（45 条）/ **Calibration**（54 条） | 决策模型基准（Decision Index、JevBench）、温度校准、阈值扫描 | von：0.80 阈值保留 25% 样本 92.4% 精度 |
| **端侧/机器人**（11 条） | ESP32 固件（jevlike-esp32）、Android 无障碍（JevBystander/jev-chat）、移动 GUI（mobile-jev/jev-mobile）、机械臂技能选择（jev_robot：decider-2b）、无人机 2.5Hz 控制环（jev-drone）、万台机器人舰队分诊（0.527s p50，$24.57/M 决策） | 全部依赖云端 API——**端侧本地化是我们的切入点** |
| 其他 | 语音对话轮次检测（打断率 52%→11%）、SRE 根因诊断（80/105，14.6s 中位）、实时 UI（DOM 广告剔除、语音+手势画布 300–550ms） | — |

### 1.4 关键教训（来自生态实测）

- **置信度必须可执行**：`confidence_gate`（自动/升级两分）是所有项目的通用骨架；阈值要实测校准而非拍脑袋
- **Noul 无 criteria 描述是最弱路径**（von 明确警告）：布尔判断必须给正/反例描述，否则退化为表面词匹配
- **顺序不变性（order invariance）**：自回归模型候选顺序翻转导致 15–20% 结果翻转（AnyJev 实测 0.230→0.073）；OpenJev 以 100% 秩不变作为核心卖点
- **低置信不是失败而是路由信号**：低置信 → 转大脑（LLM）或转人工，形成级联
- **Jev 的能力边界**：复杂场景世界知识不足、无解释性（只给数字）——这正是需要大脑协同的原因

---

## 二、Jev+LLM 双系统先行项目详解

### 2.1 DuoMind（j1s4nn/duomind）— 最接近我们目标的先行者

- **架构**：System 2 = 本地小 LLM（llama.cpp，Qwen2.5-Coder-3B 等）；System 1 = Jev 云 API 负责"所有决策"
- **18 个决策点三层框架**（`DECISION_REGISTRY` 可插拔，值得直接继承的抽象）：
  - **PRE**（请求前）：needs_generation / needs_reasoning / intent / complexity / ambiguity / safety / verbosity / format / needs_tool
  - **MID**（分段生成中）：on_track / step_complete / should_stop / confidence_mid
  - **POST**（生成后）：answer_complete / matches_request / needs_retry / too_verbose / correct_format
- **技能路由**：Jev 为每个请求选择注入的工作流（webdev/coding/debug/git/webfetch/general），Jev 关闭时降级为本地关键词匹配
- **实测**（3B 模型，100 请求）：Jev ON 时输出完整度 +60%、生成速度 +17%、prompt 更省 15%，但总延迟 +36%（Jev 决策开销 ~0.238s/请求）
- **工程细节**：熔断器（60s 内 3 次失败→本地降级 30s）、`X-DuoMind-Jev`/`X-DuoMind-Decisions` 响应头、shadow 对比基准脚本、OpenAI 兼容 + 工具调用引导
- **致命缺陷（= 我们的机会）**：**Jev 走云端**（隐私声明明言 prompt 离机）；决策与生成进程间无统一内存管理；无 S2→S1 学习闭环

### 2.2 OpenJev（zhangcy122/OpenJev，44★）— 决策飞轮思想源

- 核心主张："**Explore First, Crystallize Later**"（先探索后结晶）：把 System 2 的推理轨迹蒸馏成 System 1 永久快路径，零重复成本——**快慢双系统的自进化闭环**，正是我们"小脑越用越强"的理论基础
- 100% 顺序不变（候选隔离评分）、sub-35ms、严格类型 `Choice<T>/Noul/Score`、可对接 vLLM/SGLang/Ollama/Laya、离线 mock 模式（CI 无 GPU 可测）

### 2.3 hermes-jev-skills（1151★）— 决策外包的最全技能清单

13 个技能（SKILL.md 形式，跨 Claude Code/Codex 通用）：模型路由（0.4s/轮）、搜索结果筛选（1.9s/轮）、记忆过滤（60 段/请求）、注入检测、转录压缩选段（71 轮 0.95s，胜过按近因 11:4）、技能选择（377 技能 2.8s）、分诊（$0.00006/条）、邮箱分道、GUI 动作选择（0.5s/决策）、浏览器动作（0.4s/步）。
**关键工程实践**：shadow mode（决定并记录但不切换，零风险上线）、决策台账（ledger 记录每次路由及理由）、五工具接口（memory_filter/compact_select/choose_action/supervise/escalate）。
**反例同样有价值**：实测"由 Jev 写交接摘要"劣于全文转录（58.7% vs 75.0% 召回）——**小脑做选择，不做创作**。

### 2.4 system1-agents（ThinkFlowLab，170★）— 快慢分工收益基准

Jev（小脑）替代聊天模型跑 Agent 循环的实测：浏览器任务 3.89× 快 16.4× 便宜、工单路由 5.14× 快 19.4× 便宜、机器人 ALFWorld 3.47× 快、GUI 操作 1.75× 快——**"同分数下最高 6× 快 25× 便宜"** 是双系统价值的最直接论据。配套 OmniJev（视觉小脑）接入浏览器 Agent 每步截图决策。

### 2.5 AnyJev / Tacit（Nokia）— 单模型内的快慢混合

`adaptive=True`：首遍单次前向；Top-2 选项 log-prob 差 < tau 时**升级为开启思考的第二遍**，且 `max_cot_share=0.2` 硬性封顶（流量再难也不超）——把"快慢切换"做成模型内机制，置信度差作为切换信号。与我们"小脑判断是否请出大脑"在机制上同构，可作为单模型降级路径。

### 2.6 学术源头与其他

- **SwiftSage**（NeurIPS 2023 Spotlight，330★）：快（Swift 小模型）慢（Sage 大模型）双系统 + 反馈桥接的最早 Agent 实现，V2 转向上下文强化学习
- **latent-bridge-games**：MiniCPM-o 4.5（快）+ Qwen3-VL-8B-Thinking（慢）之间学一个 33M 的 latent 桥——"快慢之间可以只有一个小桥"的启发
- **jev-as-quant**：Jev 做判断层 + Claude 做推理层的量化研究栈（与我们"小脑+大脑"分工完全同构的应用示范）
- **smriti**（14★）：本地优先 System1+2 agentic RAG
- **taoGPT**（Zillow）：hybrid System 1/2 Agent

---

## 三、端侧推理引擎底座（大脑 + 小脑的载体）

| 引擎 | ★ | 语言 | 定位 | 在 Jev 中的角色 |
|---|---|---|---|---|
| ggml-org/llama.cpp | 132059 | C++ | GGUF 通用推理，支持**内置投机解码** | 大脑首选；DuoMind 已验证 |
| mlc-ai/mlc-llm | 23264 | Python | ML 编译，多后端统一部署 | 跨平台编译备选 |
| mlc-ai/web-llm | 19303 | TS | 浏览器内推理 | Web 端延伸 |
| huggingface/smolagents | 29996 | Python | 极简代码思考 Agent 库 | Agent 循环参考实现 |
| OpenBMB/MiniCPM | 11669 | — | 端侧 LLM 全家桶（含端侧 Agent 套件） | 中文端侧模型来源 |
| alibaba/MNN | 16269 | C++ | 移动端高性能推理（车机/手机实战检验） | **车载/Android 端大小脑统一运行时** |
| pytorch/executorch | 5114 | Python | PyTorch 官方 on-device | iOS/Android 部署备选 |
| cactus-compute/cactus | 6149 | C++ | 手机/穿戴/智能家居/机器人 | 微型端侧参考 |
| NVIDIA/TensorRT-Edge-LLM | 599 | C++/Py | Physical AI 端侧 LLM/VLM | Jetson 类硬件路线 |
| OpenBMB/CPM.cu | 242 | CUDA | 稀疏+投机采样+量化端侧 CUDA | 高端端侧加速参考 |
| RunanywhereAI/runanywhere-sdks | 10333 | C++ | 本地 AI 生产工具包 | 集成参考 |
| SqueezeAILab/TinyAgent | 500 | Python | EMNLP'24：端侧函数调用 | 端侧工具调用范式 |
| OpenBMB/AgentCPM-GUI | 1430 | Python | 端侧 Android GUI Agent（RFT 增强） | GUI 场景参考 |
| droidrun/mobilerun | 9740 | Python | 移动设备自然语言自动化 | 移动 Agent 集成面 |
| memovai/mimiclaw | 5802 | C | $5 芯片无 OS 硬件 Agent | 极端端侧标杆 |
| callstackincubator/ai | 1418 | TS | React Native 端侧 LLM（Vercel AI SDK 兼容） | RN 应用接入 |
| Helldez/BigMoeOnEdge | 648 | C++ | 超内存 MoE 流式上端（stock llama.cpp 之上） | 大模型上端技巧 |
| hopkiw/PocketPal-AI | — | RN | 手机本地 LLM 聊天 App | 移动 UI 参考 |
| kizuna-ai-lab/sokuji | 1423 | TS | 完全离线双向语音翻译 | 端侧语音实时性参考 |

**选型结论**：小脑（encoder 单次前向）用 **ONNX Runtime / CoreML / MNN / OpenVINO**（von、laya 已验证 CPU 毫秒级）；大脑用 **llama.cpp（GGUF）**（通用性）或 **MNN**（车机/移动）；两者共享 MNN 可得最薄的单运行时栈。

---

## 四、快慢思考的三个技术层（可全部用于 Jev）

### 4.1 系统级双过程（我们的主赛道）
- DriveVLM（CoRL'24，清华/理想）：车端 System 1（简单场景低时延）+ System 2（5% 复杂场景强逻辑）——**内部知识库佐证：车载"快慢双系统"与"端云协同"已被列为大模型上车两形态**，且两大问题正是我们要解的：端侧算力上限、何时该信大脑
- NVIDIA GR00T N1.5：System 2（冻结 VLM）+ System 1（DiT 扩散动作头，200Hz）深度耦合联合训练——分层而非拼接
- 银河通用三层架构：响应快的小模型群（感知/建图/抓取）+ 慢速规划 LLM，LLM 调度小模型 API 并消化其结果
- **小V助手（内部案例）**：端侧 UI（流式渲染/TPS 展示）→ 云侧网关（RAG 编排/LLM Pool：基础模型+推理模型双池，透传 reasoning_content 折叠渲染）——端云双系统的交互范式可直接复用到 Jev 的"大脑在云、小脑在端"变体

### 4.2 解码级：投机解码（"机械版快慢结构"）
- deepseek-ai/DeepSpec（7243★）：投机解码训练评估全栈
- z-lab/dflash（6192★）：块扩散 Flash 投机解码
- SafeAILab/EAGLE（2561★，ICML/EMNLP/NeurIPS 三连）：草稿头事实标准；llama.cpp 已内置
- sgl-project/SpecForge（1233★）、MTPLX（Apple Silicon 原生 MTP，2626★）、CPM.cu（端侧稀疏+投机）
- **DuoDecoding / CEE-SD**：**云-边-端三层协同的分层投机解码**（端侧草稿、云端验证）——与 Jev 端云变体直接同构
- CATS：内存受限级联自适应树投机
- hemingkx/SpeculativeDecodingPapers（1302★）：论文索引

### 4.3 路由级：级联与门控（小脑的核心职责）
- lm-sys/RouteLLM（5630★）：LLM 路由服务+评估框架
- Stanford-FutureData/FrugalGPT（291★）：级联省钱的源头
- ymoslem/awesome-llm-routing-cascading（survey，持续更新）
- aws Nemotron-Claude 两层级联 workshop：小模型全量、路由标记难例升大模型
- von 的级联实测：**Von-first + Qwen3.5-4B 级联，0.75 阈值起即可打平 4B 单干——小脑是成本杠杆而非精度杠杆**（关键认知，应写入 Jev 设计原则）

### 4.4 模型内混合思考
- Qwen3 thinking on/off 双模、GPT-OSS reasoning effort 三档
- AnyJev adaptive（见 2.5）：置信差触发 CoT + 占比封顶
- Thinkless / AdaptThink 方向（路由器决定"想不想"）
- MingLiiii/Layer_Gradient（ACL'25 Oral）：快慢思考训练时模型内部各层发生了什么

---

## 五、Gap 分析 → Jev 仓库的定位

| # | 空白点 | 现状证据 | Jev 应做什么 |
|---|---|---|---|
| 1 | **全本地端侧 Jev+LLM 双系统运行时** | DuoMind 小脑在云（隐私/离线双输）；ollaya 只有决策模型没有 LLM；没有任何项目把本地小脑+本地大脑装进一个进程组 | 核心定位：单仓库交付"本地小脑（ONNX/GGUF encoder）+ 本地大脑（GGUF LLM）+ 协同运行时"，可选"大脑在云"的端云变体 |
| 2 | **决策点框架未标准化** | DuoMind 18 点、mu 38 点，各自硬编码；无 PRE/MID/POST 的公共抽象 | 抽象为可插拔 `DecisionPoint` 注册表 + per-point active/shadow/off 三态（hermes 的 shadow mode） |
| 3 | **S2→S1 蒸馏飞轮无端侧落地** | OpenJev 有思想无端侧实现；AnyJev 有单模型版 | 端侧闭环：大脑轨迹 → 决策数据集 → 小脑增量微调/校准（`von calibrate` 式 CPU 分钟级重校准） |
| 4 | **中文端侧决策模型缺位** | Laya 英文为主（judge 无 criteria 即弱）；中文场景项目（JevBystander 等）全用云 API | 端侧中文小脑（Qwen 系底座 kev 路线或 Laya 换中文 encoder）是差异化机会 |
| 5 | **车载快慢双系统无开源实现** | 内部知识库已确认 DriveVLM 形态（95% 快/5% 慢）为业界共识方向，但 GitHub 无车规级开源双系统 | 车机（MNN/llama.cpp 车载版）上落地"95% 小脑直答 + 5% 大脑深思" |
| 6 | 评测缺统一 | Decision Index / JevBench 只测小脑单点 | 建立双系统整体评测：快慢命中率、级联精度保持率、端到端延迟分布、成本曲线 |

---

## 六、Jev 架构蓝图（v0 建议）

```
┌─────────────────────────── Jev Runtime（单仓库分层）───────────────────────────┐
│                                                                                │
│  应用层    CLI / SDK(Python·TS) / MCP server / OpenAI 兼容端点                  │
│           POST /v1/systemone（小脑）+ POST /v1/chat/completions（大脑）          │
│                                                                                │
│  协同层    DecisionPoint 注册表（PRE/MID/POST，active·shadow·off 三态）           │
│           置信度门控 cascade：小脑≥τ 直接执行 / <τ 升级大脑 / 大脑后验回流          │
│           决策台账 ledger + 熔断降级 + 本地规则 fallback                          │
│           蒸馏飞轮：大脑轨迹 → (state, question, gold) 数据集 → 小脑微调/校准      │
│                                                                                │
│  模型层    小脑 Cerebellum：encoder 单次前向（Laya/kev/von 类，ONNX·CoreML·MNN）  │
│           大脑 Cerebrum：GGUF LLM（llama.cpp/MNN），可选投机解码（小脑草稿头）      │
│           端云变体：大脑=云 LLM（小V助手式网关），小脑仍本地                      │
│                                                                                │
│  评测层    双系统基准：路由准确率 / 级联精度保持 / P50·P95 延迟 / 成本曲线 /        │
│           顺序不变性 / 注入防护（对齐 awesome-jev 的 Evaluation 类目）             │
└────────────────────────────────────────────────────────────────────────────────┘
```

**直接复用清单**：
- 小脑底座：Laya（最快）/ kev-0.8B（最准的端侧尺寸）/ d1-omni-600M（多模态）三选一做首发的可插拔 backend
- 大脑底座：llama.cpp（GGUF，内置投机解码）
- 运行时范式：ollaya（拉取/服务/单二进制）+ DuoMind（决策点+熔断+shadow）+ hermes（台账+五工具接口）
- 校准：von 的 `calibrate`（冻结权重 CPU 分钟级重校准）+ 阈值扫描脚本
- 协议：`/v1/systemone` 线协议保持兼容（生态 600+ 客户端零成本切换）

**里程碑建议**：
1. **M1（双系统骨架）**：本地 Laya 小脑 + llama.cpp 大脑 + 5 个 PRE 决策点（intent/complexity/safety/needs_tool/format）+ 置信门控级联
2. **M2（完整决策点 + 台账）**：18 点全量 PRE/MID/POST + shadow mode + ledger + 熔断
3. **M3（飞轮）**：大脑轨迹采集 → 小脑增量校准/微调；复测级联精度与延迟
4. **M4（端云与车载）**：云大脑变体（小V助手式）+ MNN 车机适配 + 中文小脑
5. **M5（生态）**：MCP server、SDK、双系统基准开源

---

## 七、附录：本次检索索引

**检索词（15 组）**：on-device llm / edge llm inference / speculative decoding / llm routing small large / fast slow thinking llm / routellm / llm cascade inference / cloud edge collaborative llm / system 1 system 2 llm / on-device agent / jev llm / jev model / laya model / tinyagent / mobile agent llm

**原始数据**：检索 JSON 与 13 份 README 存于 `C:\Users\L30086~1\AppData\Local\Temp\opencode\gh\`（含 awesome-jev 完整 15 类目 600+ 条目清单，可继续挖掘具体模式实现）

**内部知识库佐证**：小V助手端云协同全链路（WIKI2026062517080303）、大模型时代的自动驾驶-苏鹏（车端快慢双系统两形态）、国泰海通人形机器人报告（GR00T/银河通用分层端到端）
