# [AR001] 任务跟踪 — jev-dual-runtime-mvp

| 字段 | 内容 |
|------|------|
| AR 编号 | AR001 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md（已批准，design 门控 PASS 第 2 轮） |
| 创建日期 | 2026-10-23 |

## 任务列表

> 按里程碑 M1→M2→M3 分期；每个任务独立可测试，遵循 TDD（Red→Green→Refactor）。

### M1 双系统骨架

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | 工程脚手架：uv/pyproject、目录结构（src/jev, tests/）、AGENTS.md 开发规约、pytest 框架、CI 脚本、README（项目命名与总览）、组件详设补建（specs/component-detail-design/jev_spec.md） | - | passing | 2026-10-23 完成；命名 Jev（捷思） |
| T002 | 模型资产 manifest + `jev fetch` 下载器（HF→hf-mirror 回退、sha256 校验、断点续传、秒级跳过） | T001 | passing | 2026-10-23 完成；真实 URL 已核实回填 |
| T003 | `CerebellumBackend` 抽象接口 + Laya ONNX 实现（onnxruntime，单 forward 多问、校准、截断策略） | T001 | passing | 2026-10-23 完成 |
| T004 | kev-0.8B 小脑后端实现（可插拔第二实现，与 Laya 同接口契约） | T003 | passing | 2026-10-23 完成 |
| T005 | 大脑后端：llama-server 子进程管理器（拉起/健康检查/自动重启/优雅退出）+ OpenAI 兼容客户端 | T001 | passing | 2026-10-23 完成；FR3 |
| T006 | `/v1/systemone` 端点：TypeSafe 线协议严格兼容（请求/响应/错误码/usage/截断标记） | T003 | passing | 2026-10-23 完成；FR1 |
| T007 | `/v1/chat/completions` 端点：OpenAI 兼容（非流式先行），接决策点框架，预留级联引擎接口（级联由 T009/T013 接入） | T005, T008 | passing | 2026-10-23 完成；FR1 |
| T008 | 决策点框架：DecisionPoint 注册表 + 18 点定义 + active/shadow/off 三态 + 声明式配置 | T003 | passing | 2026-10-23 完成；FR4 |
| T009 | 置信门控级联引擎：三模式路由（直答/草拟+校验/直大脑）、后验回流信号、安全侧默认 | T006, T008 | passing | 2026-10-23 完成；FR5 |
| T010 | 熔断器 + 本地规则 fallback（滑动窗口、半开探测、<100ms 切换） | T003 | passing | 2026-10-23 完成；FR6 |
| T011 | M1 端到端集成测试：真实后端全链路（小脑直答 + 升级大脑 + 熔断场景），含性能断言（P50≤600ms，AR001 返工修订，见 design.md §4.5） | T002-T010 | passing | 2026-10-23 完成；M1 验收门；实测短 state P50=90~155ms |

### M2 完整决策面

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T012 | 决策台账 ledger：异步落盘、按 request_id 聚合检索、JSONL 导出、<1ms 主路径开销 | T008 | passing | 2026-10-23 完成；FR8 |
| T013 | MID/POST 决策点接入分段生成循环：should_stop/needs_retry 等 18 点全量生效于 chat 全链路（含流式） | T007, T008 | passing | 2026-10-24 完成；FR4/FR5 |
| T014 | shadow mode 全链路 + 对比报告（shadow 决策 vs 实际控制流差异统计） | T012, T013 | passing | 2026-10-24 完成；FR4/FR8 |
| T015 | CLI：`jev serve`（配置加载、服务编排）与 `jev doctor`（GPU/模型/llama-server/端口体检） | T011 | passing | 2026-10-24 完成；FR9 |
| T016 | Python SDK：`JevClient`（同步+异步）+ DecisionPoint 编程注册 API + 类型化结果对象 | T011 | passing | 2026-10-24 完成；FR10 |
| T017 | MCP Server（decide/judge/rate 三 tools） | T016 | passing | 2026-10-24 完成；FR11 |

### M3 蒸馏飞轮

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T018 | 轨迹采集与数据集导出：大脑升级路径 → (state, question, gold) JSONL 数据集（从台账抽取） | T012, T013 | passing | 2026-10-24 完成；FR12 |
| T019 | 蒸馏校准：冻结权重温度/阈值重校准（CPU 分钟级）+ 留出集评估（NLL/ECE） | T018 | passing | 2026-10-24 完成；FR12 |
| T020 | LoRA 微调路径：kev-0.8B LoRA 训练（GPU）+ 适配器加载进 T004 后端 | T019 | passing | 2026-10-24 完成（桥接+编排；GPU 训练执行验证留 ST）；FR12 可选路径 |
| T021 | `jev bench` 评测基准：延迟分布/命中率/精度保持/成本曲线/决策点开销分解 + 基线对比报告（级联 vs 直连大脑） | T013, T019 | passing | FR13，AR 验收门 |

## 状态说明

- `pending`：待开始
- `in_progress`：进行中（当前会话）
- `passing`：开发完成，测试通过
- `failed`：测试失败，需修复

## 进度记录

> 每个开发会话结束后追加，记录完成情况。

## 阶段门控记录

> 由 sdd-phase-gate skill 在阶段门控审查后追加，记录每轮审查结果（PASS/FAIL + 轮次）。格式见 sdd-phase-gate SKILL.md Step 6。

### 2026-10-23 req 门控记录

- 门控结果：PASS（第 1 轮）
- 审查项数：9 项（G1–G9 全 YES，G3/G7 为 WARN）
- 修复的问题：G3（FR5/FR7/FR8/FR12 验收标准量化补全）、G7（T007 依赖描述对齐）
- 审查代理：sdd-gate-reviewer

### 2026-10-23 design 门控记录

- 门控结果：FAIL（第 1 轮）
- 审查项数：15 项（G1/G2/G4/G5/G8/G9/G10/G11/G12 YES；G3/G6/G13/G14 Minor；G7/G15 Important NO）
- 修复的问题（6 项定向修复，不涉及架构返工）：
  - G7：§4.1 补"架构方案对比"决策记录表（A 单体/B 全守护/C 可嵌入内核，8 维权衡 + 否决理由）
  - G15：§6.2 补 IT-09（503+Retry-After）/IT-10（chat 4xx）/IT-11（空 questions）/IT-12（SDK/MCP 类型化异常）；§6.5 矩阵同步
  - G13：§6.4 补 EX-10（fetch 全源失败→诊断指引+非零退出）
  - G14：IT-13（--help）/IT-14（doctor 清单）显式用例 + EX-11（优雅退出无残留）；矩阵 FR3-3/FR9-1/FR9-2 改显式追溯
  - G3：§4.3.1 chat 补请求字段表与错误码（400/429/503）；§4.3.3/4.3.5 补 JevError 异常契约（JevUnavailableError/JevProtocolError/JevAssetError/BrainStartupError）
  - G6：§4.2.1 补流程图 E（大脑子进程管理状态机：Starting/Ready/Restarting/Fused）
- 审查代理：sdd-gate-reviewer
- 第 2 轮复审：**PASS**（G1–G15 复核全过，第 1 轮 6 项修复确认；新发现 2 项 Minor 已顺手补齐：EX-12 Fused 恢复边用例、IT-15 + 429/503 过载口径统一）
- 结论：设计阶段门控通过，进入开发阶段（sdd-task-develop）

### 2026-10-23 会话记录（develop）

- 完成任务：T001 工程脚手架（含 README 项目命名 Jev（捷思）、AGENTS.md、CI 脚本、组件详设补建 jev_spec.md、18 决策点 decision-points.toml 声明）
- 修改文件：pyproject.toml、.python-version、.gitignore、README.md、AGENTS.md、scripts/ci.ps1、configs/{jev,decision-points,assets}.toml、configs/bench-set.yaml、src/jev 全包骨架、tests/{conftest,unit/test_smoke}.py、specs/component-detail-design/jev_spec.md
- 测试：tests/unit 4 passed；ruff 零告警；覆盖率 stmt 100%（骨架）
- 备注：uv 依赖走内部镜像 `http://mirrors.tools.huawei.com/pypi/simple/`（系统代理拦截 pypi，已验证）；assets.toml 中各资产 URL/sha256 留待 T002 核实填全
- 进度记录：M1 完成 1/11（T001 passing）

### 2026-10-23 会话记录（develop 续）

- 完成任务：T002 资产 manifest + jev fetch 下载器
- 修改文件：src/jev/errors.py、src/jev/fetch/{manifest,downloader}.py、src/jev/cli/{__init__,main}.py、configs/assets.toml（真实 URL 回填：laya-onnx bundle 5 文件/kev-repo git/qwen GGUF/llama.cpp b11829 cuda-12.4×2）、configs/jev.toml（[fetch] proxy_ntlm）、pyproject（requests+requests-ntlm+console script）、tests/unit/test_{manifest,downloader,fetch_cli}.py
- 测试：34 passed；ruff 零告警；新增模块覆盖 89–100%
- 备注：① 资产 URL 全部经真实核实（hf-mirror 可达、llama.cpp b11829 有 win-cuda-12.4-x64 预编译）；② NTLM 代理走 requests-ntlm 1.3 HttpNtlmAuth(None,None) SSPI 单点登录（该版本已合并 407 代理质询处理，HTTPProxyAuth 已移除）；③ typer 0.27.3 CliRunner 对单命令 app 扁平化导致位置参数错位——注册 version 命令解决；④ kev 权重由 kev serve 首启自下载，manifest 仅管仓库克隆
- 进度记录：M1 完成 2/11（T002 passing）

### 2026-10-23 会话记录（develop 续 2）

- 完成任务：T003 小脑抽象 + Laya ONNX 后端
- 修改文件：src/jev/errors.py（+JevProtocolError）、src/jev/kernel/cerebellum/{base,laya_tokenizer,laya_onnx}.py、pyproject（dev 依赖 onnx 供测试构造微型模型）、tests/unit/test_{cerebellum_base,laya_tokenizer,laya_onnx}.py
- 测试：74 passed；ruff 零告警；cerebellum 模块覆盖 93–100%（真 onnxruntime 微型模型推理，无 mock）
- 备注：① confidence 公式对齐 TypeSafe 参考适配器（choice (p_max−1/K)/(1−1/K)、noul 2max(p,1−p)−1、score E|level−mode|/D）；② 校准温度在 log 空间重整（T=1 恒等）；③ 输入特征为分桶词袋（MVP 简化），真实 Laya bundle（int input_ids）适配留 T011 assets 集成；④ 超长 state truncate/refuse 双策略
- 进度记录：M1 完成 3/11（T003 passing）

### 2026-10-23 会话记录（develop 续 3）

- 完成任务：T004 kev HTTP 后端 + wire 线协议编解码 + 后端注册表
- 修改文件：src/jev/kernel/cerebellum/{wire,kev_http,registry}.py、tests/unit/test_{wire,kev_http,registry}.py、jev.errors（前置补 JevUnavailableError/BrainStartupError）
- 测试：103 passed；ruff 零告警；新模块覆盖 88–97%（真本地 HTTP 服务器协议对端，零 mock）
- 备注：wire.py 为 /v1/systemone 线格式单源（kev 客户端与 T006 端点共用）；kev 真实推理验证留 assets 集成
- 进度记录：M1 完成 4/11（T004 passing）

### 2026-10-23 会话记录（develop 续 4）

- 完成任务：T005 大脑后端 llama-server 子进程管理器 + OpenAI 兼容客户端
- 修改文件：src/jev/kernel/cerebrum/llama_server.py、tests/unit/test_{llama_server,llama_client}.py
- 测试：122 passed；ruff 零告警；llama_server.py 覆盖 85%（真 python.exe 子进程生命周期 + 真 http.server 协议对端，零 mock）
- 备注：① **uv venv python.exe 是 trampoline**——Popen 返回跳板 pid，真实解释器为其子进程，terminate 只杀跳板会遗留孤儿占端口；测试 fixture 用 `sys._base_executable`（C:\Python311\python.exe 真实解释器）保证被管对象是直接子进程；② stop() Windows 分支用 `taskkill /T /F` 整树终结（terminate() 本就是 TerminateProcess 无优雅语义可失），防包装层孤儿；③ ensure_running 指数退避重启链真实执行（attempts 文件计数验证），超 restart_max 抛 BrainStartupError（大脑侧熔断入口）；④ 后台 `jev fetch --all` 进行中：llama-server bin 已解压（llama-server.exe 就位）、qwen GGUF 1.07GB 完成、laya.onnx.data 1.61GB（收尾中）
- 进度记录：M1 完成 5/11（T005 passing）

### 2026-10-23 会话记录（develop 续 5）

- 完成任务：T006 /v1/systemone 端点（TypeSafe 线协议严格兼容）
- 修改文件：src/jev/server/systemone.py（create_app 端点工厂）、src/jev/kernel/cerebellum/wire.py（+decode_request/encode_response 双向单源）、src/jev/errors.py（+JevOverflowError）、src/jev/kernel/cerebellum/laya_onnx.py（refuse 改抛 JevOverflowError 子类）、tests/unit/test_systemone_endpoint.py
- 测试：139 passed；ruff 零告警（真实 TestClient ASGI 往返 + 真实 onnxruntime 微型模型推理，零 mock）
- 备注：① 错误口径落地：400 非法结构（字段级定位 message）/422 refuse 超长（JevOverflowError 专用子类，先于父类捕获）/503 未就绪（backend=None + Retry-After 头）；② 响应线格式与 wire.decode_response 互逆自洽（round-trip 用例）；③ 未知请求 model 回写实际后端名（backend.name()）
- 进度记录：M1 完成 6/11（T006 passing）

### 2026-10-23 会话记录（develop 续 6）

- 完成任务：T008 决策点框架（注册表 + 18 内置点 + 三态 + 声明式 TOML + tau 门控 + 安全侧回退）
- 修改文件：src/jev/kernel/decisions/{framework,builtin,fallback}.py、configs/decision-points.toml（+question_type/options 扩展字段）、tests/unit/test_decisions_{framework,builtin,fallback}.py
- 测试：196 passed；ruff 零告警；decisions 覆盖 91–100%（evaluate_point 走真实 onnx 推理，零 mock）
- 备注：① 18 点定型：choice 15 + score 2（complexity/confidence_mid）+ noul 框架支持但内置未用；fallback_action 全部落在 options 域内；② TOML 与 builtin 常量表双源同步由 test_toml_registry_matches_builtin_registry 持续保证；③ tau 门控：confidence >= tau 采信小脑答案否则 fallback_action（applied_value）；④ from_toml 重复 id 报错（声明不可重复），register 同 id 覆盖（运行态可覆盖）——两种语义并存是有意设计；⑤ Red 子代理 2 处测试笔误已修（defaults 用例与 helper 矛盾、shadow 过滤序违反声明序不变量）
- 进度记录：M1 完成 7/11（T008 passing）

### 2026-10-23 会话记录（develop 续 7）

- 完成任务：T007 /v1/chat/completions 端点（OpenAI 兼容非流式 + PRE 决策批量评估 + 级联预留接口）
- 修改文件：src/jev/server/chat.py（create_chat_router）、tests/unit/test_chat_endpoint.py
- 测试：219 passed；ruff 零告警；chat.py 覆盖 93%（真实 onnx 小脑 + 真实 http.server 大脑对端，零 mock）
- 备注：① PRE active 点合并单次 system_one 批量前向（CountingLayaBackend 计数子类锁定"单次前向多问"）；② X-Jev-Route/X-Jev-Decisions 头落地，route_policy 为级联引擎预留（M1 默认 full 仅写头不改行为）；③ 错误口径：400 OpenAI 形状 {error:{message,type,param,code}} 字段级 param 定位 / 429 并发超限（信号量非阻塞，仅 chat 端点）/ 503 大脑不可用 + Retry-After；④ 小脑缺失/异常 → PRE 点逐点安全侧回退，决策数不变、端点仍 200
- 进度记录：M1 完成 8/11（T007 passing）

### 2026-10-23 会话记录（develop 续 8）

- 完成任务：T009 置信门控级联引擎
- 修改文件：src/jev/kernel/cascade/engine.py（Route/MidAction/PostVerdict + CascadeEngine）、tests/unit/test_cascade_engine.py
- 测试：245 passed；ruff 零告警（纯逻辑映射 UT-02，Decision 直构 + 真实内置注册表 τ）
- 备注：① 三路径判定只认 ACTIVE 决策（SHADOW 只记录）；任一 from_fallback → FULL 安全侧；② DIRECT=intent applied "decision"（含 ==τ 边界取值语义）；LIGHT=no_reasoning+complexity low+safety 联合；③ MID 早停=should_stop/off_track；POST=RETRY（未重试且 needs_retry）/DOWNGRADE（已重试仍 incomplete/off_topic）/ACCEPT；④ posterior() 后验回流信号载体（route+每点六键含 applied）供 T012 台账/T018 蒸馏采集
- 进度记录：M1 完成 9/11（T009 passing）

### 2026-10-23 会话记录（develop 续 9）

- 完成任务：T010 小脑熔断器状态机
- 修改文件：src/jev/kernel/cascade/breaker.py（BreakerState + Breaker）、tests/unit/test_breaker.py
- 测试：258 passed；ruff 零告警（纯状态机，clock 依赖注入可控时钟）
- 备注：① CLOSED 60s 滑动窗口失败≥3 → OPEN（冷却 30s）→ HALF_OPEN 探测 → 成功闭合清窗/失败翻倍冷却（上限 120s）；② 切换=内存标志翻转实测 <100ms；③ 熔断器自身异常（clock 抛错等）安全侧放行真实后端；④ OPEN 期间 record 迟到忽略；成功不清窗（纯失败计数）；规则 fallback 决策由 fallback.py 承担（T011 接入级联主路径）
- 进度记录：M1 完成 10/11（T010 passing）

### 2026-10-23 会话记录（develop 续 10）

- 完成任务：T011 前置——线协议对齐 TypeSafe（T011-a 子步骤）
- 背景：侦察真实资产发现 laya.onnx 曾被 hf-mirror 200+HTML 错误页顶替且哈希被固化（已修复：真实文件 sha256=a874eb25…；downloader 新增 HTML 嗅探防护，TDD 2 用例）；依据 kev api.py（TypeSafe 对齐权威参照）与 receptron/laya TS 运行时源码，发现 T004/T006 线格式偏差
- 修改文件：base.py（ChoiceQuestion 描述可空/NoulQuestion 可选 criteria/ScoreAnswer=float E[level]+索引 legend+probabilities+mode_level）、wire.py（三型统一 criteria 字段、score 双向新形状、选项上限 255）、framework.py + chat.py（score 决策值取众数等级 mode_level）、laya_onnx.py（score 答案新形状）、测试 6 文件同步
- 测试：264 passed；ruff 零告警
- 备注：① 请求三型统一 criteria（choice {label:描述|null}、noul {true,false} 可选、score [等级文本]），旧 choices 字段显式 400；② score 线值=E[level] 浮点，legend/probabilities 以等级索引字符串为键，决策门控取众数等级（mode_level）保持级联引擎等级名比较零改动；③ 选项上限 255 对齐 TypeSafe
- 进度记录：T011 进行中（协议对齐完成；下一步真实 Laya 契约后端重写）

### 2026-10-23 会话记录（develop 续 11）

- 完成事项：T011-b 真实契约后端重写（commit 4178aef）+ FR2-3 性能预算返工修订（用户决策：修订指标+继续）
- 内容详情：① laya_sequence.py（序列布局/温度分桶/clamp/scrub 防注入/head_max_len 预算）+ laya_onnx.py 真实五输入契约 + tokenizers 0.23.3 + conftest.py 微型真实契约 bundle fixture，273 passed/ruff 零告警；② 性能调查：CPU EP 185ms@seq40/1110ms@seq256、CUDA EP 215~1075ms（launch 开销主导，~5000 节点/次 × WDDM ~40µs）、CUDA graph 微型模型 0.07ms 正常但 laya IMA（489 个 CPU 回退 shape 节点破坏捕获，ORT 1.20.2/1.23.2/1.26.0 三版一致）、DirectML Reshape E_INVALIDARG、TensorRT Windows 无 wheel、ORT≥1.29 切 CUDA 13 不兼容本机——P50<20ms 判定不可达
- 测试：273 passed；性能矩阵见 design.md §4.5
- 备注沉淀：Windows CUDA EP 可用配方=ORT 1.26.0 + cudart64_12/cublas64_12/cublasLt64_12/cuDNN9/cufft64_11 就位（pip nvidia-cudnn-cu12/nvidia-cufft-cu12 或 llama-server cudart 资产）；laya 静态形状重导出（可解锁 CUDA graph→预估 ~10ms）列为后续独立 spike
- 进度记录：M1 仍 10/11；T011 剩 Kernel 组合根 kernel.py + tests/integration；FR2-3 已修订为 P50≤600ms/P95≤1.5s（srs §3.2/§4、design §4.2/§4.5、BS-01 路径① <700ms、UT-10 断言同步修订）

### 2026-10-23 会话记录（develop 续 12）

- 完成任务：T011 M1 端到端集成测试（Kernel 组合根 + tests/integration 11 用例全过）——M1 完结 11/11
- 修改文件：src/jev/kernel/kernel.py（新增组合根）、tests/unit/test_kernel.py（11 用例）、tests/integration/{conftest,test_laya_real_chain,test_brain_real_chain,test_breaker_scenario}.py（11 用例）、configs/jev.toml（llama_server_bin 路径修正为 bin/）
- 内容详情：① Kernel 门面（from_config 单源装配 cerebellum/registry/engine/breaker/brain；system_one 熔断包装 OPEN 快速失败；自身满足 CerebellumBackend 契约直喂 create_app）；② IT-1 真实 laya bundle：三型正确性 + 非体育 state 语义锚点 + 截断标记 + 短 state P50 断言（实测 90~155ms，预算 600ms）；③ IT-2 真 llama-server：独享实例生命周期无残留 + 真实生成 + chat 全链路（PRE 真决策批量前向 + X-Jev-Route + 真大脑生成）；④ IT-3 熔断端到端：真连接拒绝×3→OPEN→503<100ms；小脑全挂时 chat 200 不失败（fallback→级联安全侧 full→真大脑）
- 测试覆盖率：全量 295 passed；kernel.py 97%、src/jev/kernel 各模块 ≥85%（TOTAL 91%）；ruff 零告警
- 备注沉淀：llama-server bin 不自带 cudart（bin/ 与 cudart/ 分离布局）——集成测试 conftest 注入 PATH，serve 编排（T015）归纳同款环境处理；llama-server spawn 就绪约 15~25s（1.07GB GGUF CUDA 加载）
- 进度记录：M1 11/11 全部 passing；下一步 T012 决策台账（M2）

### 2026-10-23 会话记录（develop 续 13，T012）

- 完成任务：T012 决策台账 ledger
- 修改文件：src/jev/kernel/ledger/{__init__,writer,query}.py、configs/jev.toml（[ledger]）、tests/unit/test_ledger.py
- 测试：新增 13 用例；全量 297 passed；ruff 零告警
- 备注：LedgerEntry 三 kind（decision/route/answer）；LedgerWriter 异步落盘（队列+写线程，主路径 enqueue <1ms）；LedgerQuery 按 request_id 聚合检索 + JSONL 导出
- 进度记录：M2 完成 1/6（T012 passing）

### 2026-10-24 会话记录（develop 续 14，T013）

- 完成任务：T013 chat 级联全链路——三路径/MID 早停/POST 复核/SSE 流式/台账接线
- 修改文件：src/jev/server/chat.py（全链路重写）、src/jev/kernel/cerebrum/llama_server.py（chat_stream）、src/jev/kernel/kernel.py（from_config [ledger]/serve_app/close）、tests/unit/test_chat_cascade.py、tests/integration/test_brain_real_chain.py（engine 模式升级）
- 测试：新增 13 用例；全量 310 unit + 11 integration passed；ruff 零告警
- 备注：engine 注入激活级联（None 保持 M1 直通）；direct=模板直答不经大脑、light=LIGHT_SEGMENT_TOKENS=128 分段+段间 MID、full=单次；POST needs_retry 一次上限（RETRY_FEEDBACK）重试后仍不过 jev_degraded 降级交付；SSE 流式（light 每 8 chunk 评估 MID、流式 POST 只记录不重试）
- 进度记录：M2 完成 2/6（T013 passing）

### 2026-10-24 会话记录（develop 续 15，T014）

- 完成任务：T014 shadow mode 全链路 + 反事实对比报告
- 修改文件：src/jev/kernel/ledger/report.py、tests/unit/test_shadow_report.py
- 测试：新增 3 用例；全量 313 passed；ruff 零告警
- 备注：active+shadow 合并单次批量前向（4/2/2 问一批）；shadow_report 反事实口径——shadow 提升为 active 重放 route/MID/POST vs 实际控制流差异统计 + 逐点 values/confidence_avg
- 进度记录：M2 完成 3/6（T014 passing）

### 2026-10-24 会话记录（develop 续 16，T015）

- 完成任务：T015 jev serve 服务编排 + jev doctor 环境体检
- 修改文件：src/jev/cli/{main,doctor}.py、src/jev/kernel/kernel.py（serve_app /health close [cerebrum] host/port）、src/jev/kernel/cerebrum/llama_server.py（_spawn_env cudart）、tests/unit/test_{doctor,serve}.py、tests/integration/test_serve_e2e.py
- 测试：新增 4+1+1 用例；全量 318 unit + 12 assets passed（serve E2E 13.89s）；ruff 零告警
- 备注：serve 大脑失败不阻断（chat 503 小脑决策仍可用）；doctor FAIL→exit 1；serve E2E 进程树终结端口全释放
- 进度记录：M2 完成 4/6（T015 passing）

### 2026-10-24 会话记录（develop 续 17，T016）

- 完成任务：T016 Python SDK（JevClient + 类型化结果 + 编程注册）
- 修改文件：src/jev/sdk/{__init__,results,client,programming}.py、src/jev/kernel/kernel.py（register_point）、tests/unit/test_sdk.py
- 测试：新增 9 用例；全量 327 passed；ruff 零告警
- 备注：httpx 同步+异步同实例混用；decide/judge/rate→typed Result；chat ChatResult（X-Jev-* 头级联元信息）；异常契约 503/连接失败→JevUnavailableError、其余非 2xx→JevProtocolError；register_point 编程注册与 TOML 等价（frozen 全等 + 注册即参与真前向）；测试用真实 uvicorn 线程服务器 + 真实 HTTP socket（零 mock）
- 进度记录：M2 完成 5/6（T016 passing）；下一步 T017 MCP Server

### 2026-10-24 会话记录（develop 续 18，T017）

- 完成任务：T017 MCP Server（jev_decide/jev_judge/jev_rate 三 tools，stdio）——M2 完结 6/6
- 修改文件：src/jev/mcp_server/{__init__,server}.py、src/jev/cli/main.py（mcp 子命令）、tests/unit/test_mcp_server.py
- 测试：新增 5 用例；全量 332 passed；ruff 零告警
- 备注：mcp 2.x（FastMCP→MCPServer 改名）；工具返回 SDK frozen Result → 结构化 output schema；JevError→ToolError 类型名入消息（mcp 2.x UnexpectedToolError 吞原始消息）；functools.wraps 保签名；测试为真实 in-proc MCP 会话（memory streams + ClientSession 握手 + 真往返，后端真实 uvicorn 微型 bundle）
- 进度记录：M2 6/6 全部 passing；下一步 M3 T018 蒸馏飞轮

### 2026-10-24 会话记录（develop 续 19，T018）

- 完成任务：T018 蒸馏数据集构造（轨迹抽取/gold 标注/去重/分层切分/导出）
- 修改文件：src/jev/distill/{__init__,dataset}.py、tests/unit/test_distill_dataset.py
- 测试：新增 5 用例；全量 337 passed；ruff 零告警
- 备注：gold 为 outcome 推导（升级路径 PRE 点取"需要大脑"侧、POST 复核通过取"通过"侧，GOLD_LABELS 单源）；样本随带小脑当时预测分布——T019 校准离线复算；gold_answer 供 T020 LoRA；去重 (state, point_id)；分层切分 point_id 70/15/15
- 进度记录：M3 完成 1/4（T018 passing）；下一步 T019 蒸馏校准

### 2026-10-24 会话记录（develop 续 20，T019）

- 完成任务：T019 蒸馏校准（温度缩放+阈值扫描+留出集评估+劣化拒绝）
- 修改文件：src/jev/distill/{calibrate.py, __init__.py}、src/jev/cli/main.py（distill 子命令）、tests/unit/test_distill_calibrate.py
- 测试：新增 7 用例；全量 344 passed；ruff 零告警
- 备注：温度按 question_type 拟合（softmax(log p/T) 平移等价，log 网格+三分细化确定性）；tau 逐点扫描取精度最优最小 tau；留出集 NLL/ECE 任一劣化即拒绝发布（保守侧）；CLI `jev distill --calibrate` 数据集+校准文件同目录版本化
- 进度记录：M3 完成 2/4（T019 passing）；下一步 T020 LoRA（可选）或 T021 bench

### 2026-10-24 会话记录（develop 续 21，T020）

- 完成任务：T020 kev LoRA 微调路径（可选路径：桥接+编排）
- 修改文件：src/jev/distill/{lora.py, __init__.py, calibrate.py（ids 成员检查修复）}、src/jev/cli/main.py（--lora/--kev-repo）、tests/unit/test_distill_lora.py
- 测试：新增 10 用例；全量 354 passed；ruff 零告警
- 备注：kev 需 py≥3.12+torch 栈（fcntl 阻断 Windows 本地导入）——桥接走 `kev.train --data` 用户自有 JSONL 路径（TypeSafe 请求形状+每问 label），训练经 `uv run --project` 独立环境子进程；适配器经 kev.serve --run + [cerebrum] default_backend="kev" 接入（T004 后端零改动）；真实 GPU 训练执行验证留 ST（assets+slow）
- 进度记录：M3 完成 3/4（T020 passing）；下一步 T021 jev bench（AR 验收门）

### 2026-10-24 会话记录（develop 续 22，T021）

- 完成任务：T021 `jev bench` 评测基准（M3 收官，全部 4/4）
- 修改文件：src/jev/bench/{__init__,metrics,runner,report}.py、src/jev/cli/main.py（bench 子命令）、configs/bench-set.yaml（20 题 7:3 分层：10 direct+4 light+6 full）、pyproject.toml（pyyaml）、tests/unit/test_bench_{metrics,runner}.py、tests/integration/test_bench_real.py
- 测试：新增 23 unit 用例（metrics 纯逻辑 13 + runner 真实组件 10）；全量 376 passed；ruff 零告警
- 备注：级联侧=TestClient ASGI 真实往返；基线侧=直连 LlamaServerClient；精度保持=同一把尺子（POST 决策点真前向+handle_post 双侧复核，成对可复核样本差 pt）；决策点开销=PRE active 批量真前向重复计时；BS-05 门=命中率≥70% + 直答 P50≤大脑路径 P50×1/3（大脑路径取 full 路由实际延迟，无 full 退基线）；顺手修复 runner 基线延迟把未运行题计为 0ms 假样本的 bug（改按 baseline_ok 过滤）；CLI `jev bench [--full --set --out --config]`（--full 才做基线对比与门判定，门 FAIL exit 1）；真实资产全量基准（BS-05 门断言）在 tests/integration/test_bench_real.py（assets+slow），留 ST 执行
- 进度记录：M3 完成 4/4（T021 passing）；下一步 sdd-task-review 合规审查 → ST 验收测试 → 归档

### 2026-10-24 会话记录（review 修复，独立审查子 Agent 5 Important+6 Minor 全量处置）

- 完成任务：合规审查 FAIL 项全量修复（S1/S4/S5 用例补齐、D1 design §4.6 口径修订、C2 依赖方向修正、EX-04/EX-07/EX-12 实现、/health 五字段、calibration_path 接线、[cascade] 死配置清理）
- 修改文件：src/jev/server/app.py（新建：build_systemone_app/build_serve_app+/health 五字段）、src/jev/kernel/kernel.py（移除 server 反向依赖；fallback_cerebellum/generation_timeout_s；kind=fallback 台账事件）、src/jev/server/chat.py（EX-04 deadline/timed_out/allow_retry；evaluate_decisions 公共化）、src/jev/kernel/cerebellum/registry.py（calibration_path 接线）、src/jev/kernel/ledger/writer.py（backend_fallback slot）、configs/jev.toml（[cascade] 单源 generation_timeout_s）、pyproject.toml（+openai>=1.40,<2）、design.md §4.6（审查驱动修订与 MVP 裁剪记录）、tests/unit/{test_kernel,test_chat_cascade,test_llama_server,test_systemone_endpoint,test_serve,test_cli_help,test_openai_compat}.py、tests/integration/{test_serve_e2e,test_breaker_scenario,test_laya_real_chain,test_bench_real}.py（BS-01 量级+BS-02 聚合断言）、tests/integration/test_distill_bench_bs06.py（新建 BS-06 闭环）
- 测试：全量 390 unit passed；ruff 零告警；test_llama_server taskkill 竞态改 _wait_not_running 有界等待（全量下偶发消除）
- 备注：EX-04 流式超时断流与路径③技能注入/路径②压缩 prompt 为 MVP 裁剪（design §4.6 记录）；BS-02 口径=PRE9/MID4×段数/POST5（light 单段 18 条、full 14、direct 9）；BS-06 ST 环境 min_samples 下调（生产 ≥500 不变），校准拒绝发布时门平凡满足；openai 3.x 依赖 httpx2 不可用故 <2，SDK 兼容测试改真 uvicorn 线程服务器；assets 级用例（BS-01/02/06 + EX-09 100 并发等）留 ST 执行
- 进度记录：review 修复完成待复审；下一步重派审查子 Agent（只审修复维度）→ PASS 后 ST 验收测试 → 归档

### 2026-10-24 会话记录（review 复审 PASS + Minor 处置）

- 完成任务：独立审查子 Agent 复审修复 commit 58f1b52——**12/12 项全部 PASS**（BS-06 闭环/openai SDK/--help/BS-01 量级/BS-02 聚合/EX-04/EX-07/EX-09/EX-12/design §4.6/依赖方向//health 五字段/死配置清理）；复审执行全量 390 unit passed + ruff 零告警独立复核
- Minor 立即处置（2 项）：①registry._load_calibration 补 6 个纯逻辑用例（缺失警告降级/rejected 拒载/accepted 取温度表/裸映射/非法 JSON/非数值温度表）→ test_registry.py；②EX-07 OPEN 兜底用例移除含 ONNX 前向噪声的 <100ms 断言（FR6 <100ms 口径由纯 503 路径用例覆盖，兜底用例只断言连续性与 OPEN 保持）
- 测试：全量 396 unit passed；ruff 零告警
- 进度记录：合规审查闭环（FAIL→修复→复审 PASS）；观察项备忘：bench→server.chat（evaluate_decisions 契约居 server 层）为后续重构候选；下一步 sdd-task-st ST 验收测试（assets 全量 + BS-05/06/07 门 + T020 GPU LoRA）→ 归档

### 2026-10-24 会话记录（ST 验收执行：33 例 31 PASS / 2 FAIL，Go/No-Go 决策处置）

- 完成任务：ST 验收测试全流程——就绪检查、st-cases.md 生成（33 例）、逐例执行、结果回填、Go/No-Go 决策呈报与执行
- 执行结果：**31 PASS / 2 FAIL / 0 BLOCKED**；覆盖率 src/jev/kernel 语句 92% ≥ 85% 门（396 unit passed，ruff 零告警）
- FAIL 2 例（ST-014 BS-05 门 / ST-023 BS-02 light 口径）：同一根因为 laya 资产判定能力局限（complexity 恒 medium→light 不可达；intent 无 decision→direct 不可达；命中率 0% < 70%），代码/机制侧无缺陷；**Go/No-Go 用户决策选项 A**——srs FR5-1 / design BS-05 门修订为「机制就绪 + 资产局限记录」（门挂起待资产升级后以含英文题基准集复测），srs.md §3.5/§4 与 design.md §4.7/§6.3/追溯矩阵同步修订
- ST 真实发现与修复：①kev.serve 无 /health 路由（TypeSafe 面仅 /v1/systemone 与 /v1/models），KevHttpBackend.warmup 改探 /v1/models（test_kev_http.py 假对端对齐真实路由表）——原 BLOCKED ST-005 解除并 PASS（真实双后端结构一致 3 用例）；②kev 上游 POSIX-only 缺陷本地补丁（train.py/suite.py/experiment.py 的 resource/fcntl 条件化，msvcrt 等价锁）——ST-032 真实 LoRA 训练 PASS（CPU 路径，33s，head.pt + r=8 adapter + training_metrics 产物齐全）；③ST-009 补建真实 llama-server 强杀恢复用例（taskkill 异步竞态需 _wait_not_running 同口径消除）
- 环境适配：Python 3.12.10 经 CloudArtifact 内部源 per-user 安装（`opensource_general/Python/v3.12.10/package/python-3.12.10-amd64.exe`）；kev 独立 venv（torch 2.8.0+cpu——内网 pytorch 镜像仅 Linux CUDA 轮，Windows GPU torch 不可得）；HF 下载走内网镜像 `http://mirrors.tools.huawei.com/huggingface`（HF_ENDPOINT + HF_HUB_DISABLE_XET=1）；hf-mirror.com 在 ST 期间被代理白名单移除（jev fetch 时点可用，漂移已记录）
- 修改文件：tests/integration/{test_brain_recovery_st.py（新建）, test_kev_dual_backend_st.py（新建）, test_distill_lora_st.py（新建）}；tests/unit/test_kev_http.py（假对端对齐真实路由）；src/jev/kernel/cerebellum/kev_http.py（warmup /v1/models）；specs st-cases.md（33 例回填+摘要）；srs.md/design.md（FR5-1 修订）；models/ 资产侧补丁（不入仓）
- 备注与观察项：llama-server 持续负载下 >120s 瞬态超时（503）隔离复跑未复现（chat.py httpx 120s 与级联 deadline 120s 竞态），留观察；BS-06 首轮同因超时 FAIL，重试 PASS（33m44s）；assets.toml 主源 hf-mirror.com 白名单漂移，后续 AR 跟进镜像源治理
- 进度记录：ST 验收完成（2 FAIL 经用户决策处置为口径修订，非缺陷遗留）→ 下一步 AR 归档（copy 4 文件至 specs/archive/、删 changes/、提交）
