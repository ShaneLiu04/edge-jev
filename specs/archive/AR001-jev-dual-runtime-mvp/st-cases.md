# [AR001] ST 验收测试用例

| 字段 | 内容 |
|------|------|
| AR 编号 | AR001 |
| 关联 srs.md | ./srs.md |
| 生成日期 | 2026-10-24 |

> 执行环境：RTX 5000 16GB / 127GB RAM / Windows / Python 3.11 / uv；资产经 `jev fetch --all` 就位
> （laya bundle / kev repo / llama-server bin+cudart / qwen2.5-1.5b GGUF）。C1 全真实后端，无 mock。

## 测试用例列表

### ST-001：/v1/systemone TypeSafe 线协议结构

**关联需求：** srs.md §3.1 FR1（验收 1）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 真实 Laya bundle 资产就位，Kernel.from_config 装配完成

**测试步骤：**
1. 构造 TypeSafe 协议样例请求（model/state/questions 三型混合）
2. POST /v1/systemone（build_systemone_app + TestClient）

**期望结果：**
- 响应含 answers（三型答案结构合法）与 usage.input_tokens，字段与 TypeSafe 线协议完全一致

**实际结果：** tests/integration/test_laya_real_chain.py 真实链全绿：answers 三型结构与 usage.input_tokens 齐全，与 TypeSafe 线协议一致
**状态：** PASS
**自动化锚点：** tests/integration/test_laya_real_chain.py::TestSystemoneRealChain

---

### ST-002：/v1/chat/completions OpenAI SDK 可消费

**关联需求：** srs.md §3.1 FR1（验收 2）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 真实 uvicorn 线程服务器承载 serve 应用

**测试步骤：**
1. openai SDK（>=1.40,<2）指向本地 base_url
2. client.chat.completions.create 发起真实请求

**期望结果：**
- 返回可被 SDK 解析的 ChatCompletion 对象（id/choices/usage 字段齐全）

**实际结果：** test_openai_compat 通过（真 uvicorn 线程服务器）；openai SDK 返回 ChatCompletion（id/choices/usage 齐全）
**状态：** PASS
**自动化锚点：** tests/unit/test_openai_compat.py（真 uvicorn 服务器）

---

### ST-003：超长 state 截断处理

**关联需求：** srs.md §3.1 FR1（验收 3）
**测试类型：** 边界条件
**优先级：** Medium

**前置条件：**
- 真实 Laya bundle 就位

**测试步骤：**
1. 构造超长 state（> 上下文窗口）
2. POST /v1/systemone

**期望结果：**
- 截断处理成功应答，带 truncation 标记（对齐 von 行为）

**实际结果：** 超长 state 用例通过：截断应答成功，truncation 标记对齐 von 行为
**状态：** PASS
**自动化锚点：** tests/integration/test_laya_real_chain.py（long_state 用例）

---

### ST-004：非法请求错误码与过载 503

**关联需求：** srs.md §3.1 FR1（异常处理）
**测试类型：** 异常处理
**优先级：** High

**前置条件：**
- serve 应用运行中

**测试步骤：**
1. POST 非法 JSON / 缺字段请求
2. 并发超过 max_concurrency 上限

**期望结果：**
- 非法请求返回规范错误码（TypeSafe/OpenAI 错误结构）；过载返回 503 + Retry-After

**实际结果：** 单测全绿（396 passed）：非法请求规范错误结构；限流 503+Retry-After；熔断 503
**状态：** PASS
**自动化锚点：** tests/unit/test_systemone_endpoint.py、test_chat_endpoint.py（限流/错误路径）、test_kernel.py（熔断 503+Retry-After）

---

### ST-005：Laya 与 kev 双后端结构一致

**关联需求：** srs.md §3.2 FR2（验收 1）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- Laya bundle 就位；kev 运行时（py≥3.12 独立环境）可用

**测试步骤：**
1. 同一 (state, questions) 分别经 Laya 与 kev 后端推理

**期望结果：**
- 两者均返回结构一致的合法答案（Choice/Noul/Score 三型）

**实际结果：** 通过（tests/integration/test_kev_dual_backend_st.py，3 用例）：同一 (state, questions) 经真实 Laya ONNX 与真实 kev.serve（jaredpalmer/kev-0.8b，Qwen3.5-0.8B-Base，T=2.35，CPU）双后端推理，结构一致（三型答案合法、选项集合对齐、概率归一、confidence∈[0,1]）；KevHttpBackend.warmup 对真实服务器通过；原生 /v1/systemone 可被 httpx 消费（x-typesafe-request-id 头齐全）。**ST 发现并修复真实缺陷**：kev.serve 无 /health 路由（TypeSafe 面仅 /v1/systemone 与 /v1/models），KevHttpBackend.warmup 改探 /v1/models（假对端单测同步对齐真实路由表）。环境：Python 3.12.10（CloudArtifact 内部源 per-user 安装）+ kev 独立 venv + HF 内网镜像
**状态：** PASS
**自动化锚点：** tests/integration/test_kev_dual_backend_st.py（assets+slow；kev serve fixture 拉起真实子进程）

---

### ST-006：批量问题共享单次编码

**关联需求：** srs.md §3.2 FR2（验收 2）
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- 真实 Laya bundle 就位

**测试步骤：**
1. 单次 systemone 请求携带三型混合多问
2. 观察前向次数与应答

**期望结果：**
- 多问共享单次编码（单 forward 多问），全部应答合法

**实际结果：** 通过：三型混合多问共享单次编码（单 forward 多问），应答全部合法
**状态：** PASS
**自动化锚点：** tests/integration/test_laya_real_chain.py::TestSystemoneRealChain（混合请求）+ tests/unit/test_laya_sequence.py

---

### ST-007：小脑 CPU 单决策延迟 P50≤600ms

**关联需求：** srs.md §3.2 FR2（验收 3）+ §4 NFR
**测试类型：** 边界条件（性能）
**优先级：** High

**前置条件：**
- 真实 Laya bundle，CPU EP，短 state 基准集

**测试步骤：**
1. 5 条短 state 逐条推理计时
2. 计算 P50/P95

**期望结果：**
- P50 ≤ 600ms，P95 ≤ 1.5s

**实际结果：** 通过：真实 laya CPU EP 短 state P50≤600ms 断言命中（P95≤1.5s）
**状态：** PASS
**自动化锚点：** tests/integration/test_laya_real_chain.py（短 state P50 断言）

---

### ST-008：大脑健康检查就绪

**关联需求：** srs.md §3.3 FR3（验收 1）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- models/ 下 GGUF 与 llama-server 二进制就位

**测试步骤：**
1. 拉起大脑后端（LlamaServerManager.start）
2. 探针过后检查 /health

**期望结果：**
- 健康检查通过，Ready 状态（is_running + /health 200）

**实际结果：** 通过：llama-server 拉起→探针→/health 200（TestLlamaServerLifecycle）
**状态：** PASS
**自动化锚点：** tests/integration/test_brain_real_chain.py::TestLlamaServerLifecycle

---

### ST-009：大脑强杀自动恢复

**关联需求：** srs.md §3.3 FR3（验收 2）
**测试类型：** 异常处理
**优先级：** High

**前置条件：**
- 真实 llama-server 运行中

**测试步骤：**
1. taskkill /F 强杀真实 llama-server 进程
2. ensure_running 触发自动重启
3. chat 请求验证

**期望结果：**
- 自动重启且后续请求成功

**实际结果：** 通过（tests/integration/test_brain_recovery_st.py，ST 补建）：taskkill /F 真实强杀→ensure_running 2.0s 自动重启→新 chat 200。注意：强杀瞬间既有 httpx 连接池陈旧 socket 复用会 10054（请求级失败，池自动淘汰自愈）；taskkill 异步语义需等待 poll 观测到退出（EX-12 同口径）
**状态：** PASS
**自动化锚点：** tests/unit/test_llama_server.py（EX-12 真子进程恢复）+ ST 补充真实 llama-server 恢复用例

---

### ST-010：优雅停机无残留

**关联需求：** srs.md §3.3 FR3（验收 3）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 真实 llama-server 运行中

**测试步骤：**
1. manager.stop() 优雅停机
2. 检查进程与端口

**期望结果：**
- 子进程无残留、端口释放

**实际结果：** 通过：stop 后进程无残留、端口释放（taskkill /T 整树终结）
**状态：** PASS
**自动化锚点：** tests/integration/test_brain_real_chain.py::test_spawn_health_stop_clean、test_serve_e2e.py

---

### ST-011：决策点 active 影响控制流

**关联需求：** srs.md §3.4 FR4（验收 1）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 18 点注册表按 configs/decision-points.toml 装配（active 12 + shadow 6）

**测试步骤：**
1. 复核注册表三态分布与 18 点全集
2. 端到端请求验证 active 决策参与路由

**期望结果：**
- PRE9/MID4/POST5 全集注册；active 决策影响控制流（如低置信升级）

**实际结果：** 通过：18 点注册（active 12 + shadow 6），PRE9/MID4/POST5 全集；active 决策参与路由（低置信安全侧 full）
**状态：** PASS
**自动化锚点：** tests/unit/test_decisions_builtin.py、test_cascade_engine.py + tests/integration/test_bench_real.py（BS-02 聚合）

---

### ST-012：shadow 态只记录不生效

**关联需求：** srs.md §3.4 FR4（验收 2）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 含 shadow 态决策点的注册表

**测试步骤：**
1. shadow 点在请求处理中被评估
2. 查台账

**期望结果：**
- 台账记录决策（mode=shadow）但控制流不变

**实际结果：** 通过：shadow 点台账记录 mode=shadow，控制流不变
**状态：** PASS
**自动化锚点：** tests/unit/test_shadow_report.py、test_ledger.py（shadow 标记）

---

### ST-013：自定义决策点注册

**关联需求：** srs.md §3.4 FR4（验收 3）
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- 编程注册 API（SDK register_point）

**测试步骤：**
1. 注册自定义决策点（TOML 等价）
2. 启动并请求

**期望结果：**
- 被识别并按三态执行

**实际结果：** 通过：register_point 注册自定义点，三态行为与 TOML 等价
**状态：** PASS
**自动化锚点：** tests/unit/test_sdk.py（register_point）、test_decisions_framework.py

---

### ST-014：级联命中率 ≥70% + 直答 P50≤大脑×1/3（BS-05 门）

**关联需求：** srs.md §3.5 FR5（验收 1）+ §4 NFR
**测试类型：** 边界条件（性能/质量门）
**优先级：** High

**前置条件：**
- 真实双系统（laya + llama-server）+ 20 题基准集（7:3 分层）

**测试步骤：**
1. run_bench 全量（级联 + 直连基线）
2. 判定 BS-05 门

**期望结果：**
- 小脑直答命中率 ≥ 70%；直答路径 P50 ≤ 大脑路径 P50 × 1/3；BS-01 量级（①<700ms、②<③）

**实际结果：** FAIL（资产能力局限，非代码缺陷）：真实 laya 资产下 20/20 题全部路由 full，小脑直答命中率 0% < 70% 门。探针证据：complexity（score 型）恒输出 medium 且 conf 0.3–0.6 < τ=0.60 → 不应用（安全侧 full），light 不可达；intent 从不输出 decision（conf 多 < 0.70）→ direct 不可达（0/10）；英文题同样失败（en-light-05 complexity=low 但 conf 0.56 < τ）。choice 型模板实验预估上限 ~65% 仍不达门且 full-01 有误路由 light 质量风险。tau 校准无法修复（value 判定偏差而非置信度问题）
**状态：** FAIL
**自动化锚点：** tests/integration/test_bench_real.py::test_full_bench_set_meets_bs05_gates（assets+slow）

---

### ST-015：低置信升级大脑成功

**关联需求：** srs.md §3.5 FR5（验收 2）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 真实双系统运行

**测试步骤：**
1. 发起复杂请求（低置信）
2. 验证路由与应答

**期望结果：**
- 大脑被调用（X-Jev-Route=full/light），请求最终 200

**实际结果：** 通过：复杂请求低置信升级大脑，X-Jev-Route=full，最终 200
**状态：** PASS
**自动化锚点：** tests/integration/test_brain_real_chain.py::TestChatEndpointFullChain、test_bench_real.py（full 路由）

---

### ST-016：needs_retry 重试上限

**关联需求：** srs.md §3.5 FR5（验收 3）
**测试类型：** 异常处理
**优先级：** Medium

**前置条件：**
- POST 复核点评估大脑答案

**测试步骤：**
1. 构造 needs_retry 触发场景
2. 验证重试次数

**期望结果：**
- needs_retry 逻辑可触发一次重试上限

**实际结果：** 通过：needs_retry 重试上限逻辑真实触发
**状态：** PASS
**自动化锚点：** tests/unit/test_chat_cascade.py（真实后端重试路径）

---

### ST-017：熔断 3 失败 fallback 端到端成功

**关联需求：** srs.md §3.6 FR6（验收 1）
**测试类型：** 异常处理
**优先级：** High

**前置条件：**
- 小脑指向真实死端口（连接拒绝）

**测试步骤：**
1. 连续 3 次真实失败
2. 第 4 次请求（熔断开启）

**期望结果：**
- 走 fallback 且端到端请求成功（chat 不失败，安全侧 full）

**实际结果：** 通过：真实死端口 3 失败→熔断 OPEN→第 4 次走 fallback 端到端成功
**状态：** PASS
**自动化锚点：** tests/integration/test_breaker_scenario.py

---

### ST-018：半开探测恢复

**关联需求：** srs.md §3.6 FR6（验收 2）
**测试类型：** 异常处理
**优先级：** High

**前置条件：**
- 熔断开启且后端恢复

**测试步骤：**
1. 冷却期满后半开探测
2. 探测成功后新请求

**期望结果：**
- 回到真实后端（CLOSED）

**实际结果：** 通过：半开探测成功回 CLOSED，恢复真实后端
**状态：** PASS
**自动化锚点：** tests/unit/test_breaker.py（状态机全遍历）

---

### ST-019：熔断切换耗时 <100ms

**关联需求：** srs.md §3.6 FR6（验收 3）+ §4 NFR
**测试类型：** 边界条件（性能）
**优先级：** High

**前置条件：**
- 熔断已开启

**测试步骤：**
1. 熔断期发起请求计时

**期望结果：**
- 快速 503 耗时 < 100ms

**实际结果：** 通过：熔断期快速 503 < 100ms 断言命中
**状态：** PASS
**自动化锚点：** tests/integration/test_breaker_scenario.py（<100ms 断言）

---

### ST-020：fetch --all 四类资产 sha256 匹配

**关联需求：** srs.md §3.7 FR7（验收 1）
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- manifest 清单（configs/assets.toml）

**测试步骤：**
1. 资产就位状态下校验 manifest 哈希

**期望结果：**
- 四类资产（laya/kev/llama-server/qwen）sha256 全部匹配

**实际结果：** 通过：manifest sha256 校验全匹配；jev doctor 资产项全绿
**状态：** PASS
**自动化锚点：** tests/unit/test_manifest.py + `jev doctor`（ST 执行）

---

### ST-021：断点续传

**关联需求：** srs.md §3.7 FR7（验收 2）
**测试类型：** 异常处理
**优先级：** Low

**前置条件：**
- 下载器支持 Range 续传

**测试步骤：**
1. 中断下载后再次 fetch

**期望结果：**
- 从断点续传而非重下

**实际结果：** 通过：真本地 HTTP Range 服务器断点续传
**状态：** PASS
**自动化锚点：** tests/unit/test_downloader.py（真本地 HTTP Range 服务器）

---

### ST-022：已就位资产跳过 <5s

**关联需求：** srs.md §3.7 FR7（验收 3）
**测试类型：** 边界条件
**优先级：** Medium

**前置条件：**
- 全部资产已就位

**测试步骤：**
1. 再次执行 `jev fetch --all` 并计时

**期望结果：**
- 5s 内完成跳过（哈希命中）

**实际结果：** 通过：已就位资产哈希命中跳过，实测 1.63s < 5s
**状态：** PASS
**自动化锚点：** ST 真实执行 `jev fetch --all` 计时

---

### ST-023：台账 request_id 聚合还原决策链（BS-02）

**关联需求：** srs.md §3.8 FR8（验收 1）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 真实 18 点注册表 + 台账写入

**测试步骤：**
1. 端到端请求（简单生成题，max_tokens=128 单段）
2. 台账按 request_id 聚合查询

**期望结果：**
- 还原全部决策点结果与路由：light=PRE9+MID4+POST5（18 条）、full=14、direct=9；route/answer 条目齐全

**实际结果：** FAIL（与 ST-014 同根因，资产能力局限）：light=18 条口径不可达——4 道预期 light 题全部路由 full（complexity 恒 medium）。台账聚合机制本身已验证：request_id 聚合可完整还原决策条目 + route + answer（full=14 条口径正常）
**状态：** FAIL
**自动化锚点：** tests/integration/test_bench_real.py::TestLedgerAggregationRealAssets

---

### ST-024：shadow 决策台账标记

**关联需求：** srs.md §3.8 FR8（验收 2）
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- shadow 态点被评估

**测试步骤：**
1. 查询台账 shadow 条目

**期望结果：**
- 记录含 mode=shadow 标记

**实际结果：** 通过：shadow 决策台账 mode=shadow 标记
**状态：** PASS
**自动化锚点：** tests/unit/test_ledger.py、test_shadow_report.py

---

### ST-025：100 并发台账写入无劣化（EX-09）

**关联需求：** srs.md §3.8 FR8（验收 3）
**测试类型：** 边界条件（性能）
**优先级：** High

**前置条件：**
- 真实 laya systemone 端点

**测试步骤：**
1. 100 并发（真 ASGI asyncio）持续请求

**期望结果：**
- 全部请求成功，台账写入异步不阻塞主路径

**实际结果：** 通过：100 并发（真 ASGI asyncio）全部成功，台账异步写入无阻塞
**状态：** PASS
**自动化锚点：** tests/unit/test_systemone_endpoint.py（100 并发 ASGI）

---

### ST-026：jev doctor 环境体检

**关联需求：** srs.md §3.9 FR9（验收 1）
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- CLI 安装完成

**测试步骤：**
1. 执行 `jev doctor`

**期望结果：**
- 输出环境问题清单与修复建议（资产就位时全绿/或列问题）

**实际结果：** 通过：jev doctor 全绿（Python/onnxruntime/GPU/资产/llama-server b11829/cudart/端口/台账目录）
**状态：** PASS
**自动化锚点：** tests/unit/test_doctor.py + ST 真实执行

---

### ST-027：CLI --help 完整用法

**关联需求：** srs.md §3.9 FR9（验收 2）
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- CLI 安装完成

**测试步骤：**
1. serve/fetch/doctor/bench/distill --help

**期望结果：**
- 均有完整用法说明（exit 0）

**实际结果：** 通过：serve/fetch/doctor/bench/distill --help 均 exit 0
**状态：** PASS
**自动化锚点：** tests/unit/test_cli_help.py + tests/unit/test_fetch_cli.py、test_bench_runner.py、test_distill_calibrate.py

---

### ST-028：SDK 注册决策点行为一致 + 类型化结果

**关联需求：** srs.md §3.10 FR10（验收 1/2）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- JevClient（同步+异步）可用

**测试步骤：**
1. SDK 注册自定义决策点并启动 serve
2. 调用 decide/judge/rate

**期望结果：**
- 行为与配置文件注册一致；返回类型化 Result 对象（非裸 dict）

**实际结果：** 通过：SDK 注册与配置文件注册行为一致，返回类型化 Result
**状态：** PASS
**自动化锚点：** tests/unit/test_sdk.py（真 uvicorn 线程服务器 + 真实 HTTP socket）

---

### ST-029：MCP decide tool 结构化结果

**关联需求：** srs.md §3.11 FR11
**测试类型：** 正常路径
**优先级：** Medium

**前置条件：**
- MCP server（stdio）可用

**测试步骤：**
1. MCP client 会话调用 jev_decide/jev_judge/jev_rate

**期望结果：**
- 返回结构化决策结果（frozen Result 序列化 schema）

**实际结果：** 通过：MCP 真 in-proc 会话返回结构化决策结果
**状态：** PASS
**自动化锚点：** tests/unit/test_mcp_server.py（真 in-proc MCP 会话 + 真实 uvicorn 微 bundle）

---

### ST-030：distill 校准产物与留出集评估

**关联需求：** srs.md §3.12 FR12（验收 1）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 台账含足量升级轨迹（ST 环境以 bench-set 复杂题构造，min_samples 下调；生产口径 ≥500）

**测试步骤：**
1. `jev distill --calibrate`
2. 检查产物

**期望结果：**
- 产出新校准文件 + 留出集评估报告（NLL/ECE 不劣化才 accepted）

**实际结果：** 通过：distill --calibrate 产出校准文件 + 留出集评估报告（CLI 真实台账端到端）
**状态：** PASS
**自动化锚点：** tests/unit/test_distill_calibrate.py（CLI 真实台账端到端）+ ST-031 闭环执行

---

### ST-031：蒸馏后精度不回退 + 命中率不降（BS-06 门）

**关联需求：** srs.md §3.12 FR12（验收 2）+ §4 NFR
**测试类型：** 边界条件（质量门）
**优先级：** High

**前置条件：**
- 真实轨迹 → build_dataset → calibrate → 校准后端

**测试步骤：**
1. 生成升级轨迹并校准
2. bench 复测（pre vs post）

**期望结果：**
- 精度差 ≥ -2pt 且命中率不下降；校准被拒绝发布时保守侧平凡满足

**实际结果：** 通过（重试）：真实轨迹(6×full 96tok)→build_dataset→calibrate→bench 复测（6 题子集级联+基线×2），精度差 ≥ -2pt 且命中率不下降断言命中，33m44s。首轮失败为大脑持续负载下 >120s 瞬态超时（隔离复跑未复现，已记录基础设施观察项）
**状态：** PASS
**自动化锚点：** tests/integration/test_distill_bench_bs06.py（assets+slow）

---

### ST-032：GPU LoRA 训练（可选路径）

**关联需求：** srs.md §3.12 FR12（验收 3）
**测试类型：** 正常路径（可选）
**优先级：** Low

**前置条件：**
- GPU 可用 + kev 运行时（py≥3.12 + torch 栈）

**测试步骤：**
1. `jev distill --lora`（kev.train 子进程）

**期望结果：**
- LoRA 训练完成并产出适配器

**实际结果：** 通过（tests/integration/test_distill_lora_st.py）：真实蒸馏样本集（choice+score 点，按 request 聚合 2 条记录）→ train_lora 桥接 → 真实 kev.train 子进程（models/kev/repo 独立 venv，Python 3.12 + torch 2.8.0，LoRA r=8，1 epoch，33s）→ 适配器产物齐全（head.pt + adapter_config.json[r=8] + training_metrics.json），kev.serve --run 可加载形状。**CPU 路径**：Windows 无 GPU torch（内网 pytorch 镜像仅 Linux CUDA 轮），编排/数据桥/产物契约与 GPU 路径同代码。环境适配：kev 上游 POSIX-only 缺陷本地补丁（resource/fcntl 条件化 import，msvcrt 等价锁语义）
**状态：** PASS（CPU 路径；GPU 速度口径留待 Linux/GPU 环境复测）
**自动化锚点：** tests/integration/test_distill_lora_st.py（assets+slow；真实 kev.train 子进程）

---

### ST-033：bench --full 五类指标报告（BS-07）

**关联需求：** srs.md §3.13 FR13（验收 1/2）
**测试类型：** 正常路径
**优先级：** High

**前置条件：**
- 真实双系统 + 基准问题集

**测试步骤：**
1. 执行全量基准（级联 + 直连基线）
2. 检查报告

**期望结果：**
- 输出五类指标（延迟/命中率/精度保持/成本/决策点开销）结构化报告（JSON+Markdown），可量化展示双系统收益

**实际结果：** 通过：全量 run_bench（级联+直连基线）真实执行，五类指标（延迟/命中率/精度保持/成本/决策点开销）报告 JSON+Markdown 落盘（test_bench_runner 断言）；BS-05 门断言失败见 ST-014（报告机制本身正常）
**状态：** PASS
**自动化锚点：** tests/integration/test_bench_real.py（run_bench 全量含基线）+ tests/unit/test_bench_runner.py（报告落盘）

## 执行摘要

| 总计 | 通过 | 失败 | 阻塞 |
|------|------|------|------|
| 33 | 31 | 2 | 0 |

- **FAIL 2 例（ST-014 / ST-023）**：同一根因——真实 laya 资产判定能力局限（complexity 恒
  medium、intent 无 decision 输出），light/direct 路由在当前资产下不可达，命中率 0% < 70% 门。
  代码/机制侧无缺陷：路由判定、台账聚合、安全侧回退 full 均按设计工作。处置经 Go/No-Go
  决策定为 **选项 A：修订验收口径**（机制就绪 + 资产局限记录，命中率门挂起待资产升级后
  复测，含英文基准集口径）——srs FR5-1 / design BS-05 门同步修订。
- **原 BLOCKED 2 例（ST-005 / ST-032）已解除并 PASS**：经 CloudArtifact 内部源安装
  Python 3.12.10（per-user，`opensource_general/Python/v3.12.10`），kev 独立 venv
  （torch 2.8.0）搭建完成。ST-005 发现并修复真实缺陷：kev.serve 无 /health 路由，
  KevHttpBackend.warmup 改探 /v1/models（test_kev_http.py 假对端同步对齐真实路由表）。
  ST-032 真实 kev.train LoRA 训练产出适配器（head.pt + r=8 adapter + training_metrics）。
- **环境适配记录（models/ 下资产，不入仓）**：
  - Windows 无 GPU torch：内网 pytorch 镜像仅 Linux CUDA 轮，kev 训练/推理走 CPU 路径
    （ST-005 无延迟门；ST-032 编排/产物契约与 GPU 路径同代码，仅设备与速度不同）；
  - kev 上游仓 POSIX-only 缺陷本地补丁：train.py/suite.py/experiment.py 的
    resource/fcntl 无条件 import 改条件化（Windows 用 msvcrt.locking 等价语义）；
  - HF 下载走内网镜像 `http://mirrors.tools.huawei.com/huggingface`（HF_ENDPOINT，
    禁 xet）；hf-mirror.com 在 ST 期间被代理白名单移除（jev fetch 时点可用，白名单漂移
    已记录——assets.toml 的 laya/qwen URL 依赖 hf-mirror.com，复测 fetch 需注意）。
- **基础设施观察项**：llama-server 在持续负载（首轮 slow 批）下出现 >120s 瞬态超时（503），
  隔离复跑与 BS-06 重试均未复现；与 chat.py:588 的 httpx 120s 超时和级联 deadline 120s 竞态
  有关，记录待观察，不阻塞验收。
- 覆盖率：src/jev/kernel 语句覆盖 92% ≥ 85% 门（396 unit passed）；ruff 零告警。
