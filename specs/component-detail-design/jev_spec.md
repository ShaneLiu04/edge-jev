# Jev 组件详设（jev_spec）

> 本文件为组件级设计基准，由 T001 依据 `specs/changes/AR001-jev-dual-runtime-mvp/design.md` 补建。
> 完整决策记录、流程图与测试追溯矩阵以 design.md 为准；本文是日常开发的速查契约。

## 1. 组件定位

Jev（捷思）是端侧快慢双系统 AI 运行时：进程内 `Kernel` 为组合根，薄壳（server/sdk/cli/mcp_server）只做协议适配。小脑 = 类型化决策模型（Laya ONNX 默认 / kev-0.8B 可插拔）；大脑 = llama-server 子进程 + Qwen2.5 GGUF。

**依赖方向（强制）**：`server/sdk/cli/mcp_server → kernel → 子模块`。薄壳不得绕过 Kernel 直接访问小脑/大脑。

## 2. 模块职责

| 模块 | 职责 | 关键文件 |
| --- | --- | --- |
| `kernel` | 组合根：构建并持有小脑池/决策点注册表/级联引擎/熔断器/台账 | `kernel.py` |
| `kernel/cerebellum` | 小脑后端抽象与实现（Laya ONNX 进程内默认；kev HTTP 子进程） | `base.py` `laya_onnx.py` `kev_http.py` `registry.py` |
| `kernel/cerebrum` | 大脑管理：llama-server 子进程生命周期 + HTTP client | `llama_server.py` `client.py` |
| `kernel/decisions` | DecisionPoint 注册表（18 内置点）、三态（active/shadow/off）、fallback 规则；多探针/置换/融合 | `framework.py` `builtin.py` `fallback.py` `probes.py` `evaluate.py` `calibration.py` |
| `kernel/runtime` | 运行时智能（AR003）：不确定性/自证/三带/验证器/prompt 编排 | `uncertainty.py` `selfcheck.py` `bands.py` `verifier.py` `prompt.py` |
| `kernel/cache` | 语义缓存（精确+近似）与长程记忆 | `semantic.py` `memory.py` |
| `kernel/prefill` | 稀疏 prefill 规划：块分割/显著性/预算选择 | `blocks.py` `salience.py` `planner.py` |
| `kernel/cascade` | 三路径级联引擎 + 路由 v2 + 前缀指纹 + 熔断状态机 + 预算/草稿 | `engine.py` `breaker.py` `prefix.py` `budget.py` `draft.py` |
| `kernel/ledger` | 决策台账：JSONL 队列写线程 + 查询 | `writer.py` `query.py` |
| `server` | FastAPI：`/v1/systemone`（TypeSafe 严格）+ `/v1/chat/completions`（OpenAI 兼容）+ `/health` | `app.py` `systemone.py` `chat.py` `schemas.py` |
| `sdk` | `JevClient`（httpx 双模）+ 类型化结果 + 决策点编程注册 | `client.py` `results.py` `programming.py` |
| `cli` | typer 套件：serve/fetch/doctor/bench/distill | `main.py` 等 |
| `mcp_server` | FastMCP（stdio）：jev_decide/jev_judge/jev_rate | `server.py` |
| `fetch` | 资产 manifest 解析 + 下载器（sha256/断点续传/镜像回退） | `manifest.py` `downloader.py` |
| `distill` | S2→S1 蒸馏飞轮：数据集/校准/LoRA/报告 | `dataset.py` 等 |
| `bench` | 评测基准：五类指标（命中/延迟/熔断/台账开销/蒸馏收益） | `runner.py` 等 |

## 3. 线协议契约

### 3.1 POST /v1/systemone（TypeSafe 严格对齐）

请求：`{model, state, questions}`；questions 三型：
- choice：`{choices: {name: description}, instructions}` → 概率分布
- noul：`{instructions}` → 0–1 概率
- score：`{criteria: [有序等级], instructions}` → 等级+置信度

响应：`{answers: {qid: {type, ...类型字段, confidence}}, usage: {input_tokens}}`。
错误码：`400`（必选字段缺失/类型非法/空 questions）；`422`（超长 state refuse 模式）；`503`（未就绪，含 Retry-After）。

### 3.2 POST /v1/chat/completions（OpenAI 兼容）

请求/响应遵循 OpenAI Chat 规范；扩展响应头（只增不改）：
- 基线：`X-Jev-Route`（direct/light/full）、`X-Jev-Decisions`
- AR002：`X-Jev-Margin`（校准 margin 启用时）、`X-Jev-Budget`（预算启用时，`ok|capped`）
- AR003：`X-Jev-Verified`（`accept|reject|skipped|off`）、`X-Jev-Cache`（`hit-exact|hit-semantic|miss`）、`X-Jev-U`（0–1）、`X-Jev-Prefill`（`selected/total|skipped:<reason>`）

错误码：`400`（messages 非法，OpenAI 规范错误体）；`429`（并发超限，仅 chat）；`503`（熔断/重启/过载 + Retry-After，过载统一口径）。

## 4. 级联三路径语义（关键澄清）

小脑**不生成散文**：
- **路径① direct**：结构化决策类意图 → 小脑直答 + 模板合成（不经大脑）
- **路径② light**：简单生成 → 压缩 prompt / 无推理 / 小 max_tokens / MID 早停 / POST 轻复核
- **路径③ full**：复杂推理 → 技能注入 / MID 批循环 / POST 全量复核（needs_retry 恰一次）

级联命中率 = 路径①+② 占比；「不经大脑」严格口径 = 路径①。安全侧默认：PRE 失败 → 路径③。

## 5. 核心内部接口（Kernel 契约）

```python
class CerebellumBackend(ABC):
    def system_one(self, state, questions: dict) -> SystemOneResponse   # 单次前向多问
    def name(self) -> str; def warmup(self) -> None

class CerebrumBackend(ABC):
    def chat(self, messages, *, max_tokens, stop, skill) -> ChatResult
    def health(self) -> bool

class CascadeEngine:
    def route_chat(self, pre: DecisionBatch) -> Route
    def route_chat_v2(self, pre, *, uncertainty=None, policy=BandPolicy(), margin_tau=0.0, budget=None) -> RouteDecision  # AR003
    def handle_mid(self, seg: Segment) -> MidAction
    def handle_post(self, ans, post: DecisionBatch) -> PostVerdict

class Breaker: allow() -> bool; record(ok: bool); state -> CLOSED/OPEN/HALF_OPEN
class Ledger: log(entry) -> None; query(request_id) -> list[Entry]
class AssetFetcher: fetch(name, force=False) -> Path; verify(name) -> bool
```

## 6. 错误类型族

| 类型 | 语义 |
| --- | --- |
| `JevError` | 基类 |
| `JevUnavailableError` | 后端不可达/超时 |
| `JevTimeoutError` | 大脑调用超时（AR002；按类型判定截断降级） |
| `JevProtocolError` | 响应不符合线协议 |
| `JevAssetError` | 资产缺失/校验失败 |
| `BrainStartupError` | llama-server 启动失败（携带诊断） |

## 7. 关键参数（单源 `configs/jev.toml`）

| 参数 | 默认值 |
| --- | --- |
| 熔断窗口/阈值 | 60s / 3 失败 → OPEN |
| 熔断冷却 | 30s 起步，指数翻倍上限 120s → HALF_OPEN 探测 |
| 直答置信门控 τ | 0.70 |
| 路径② max_tokens | 512 |
| 大脑生成超时 | 120s |
| 大脑重启上限 | 3 次（退避 2s/4s/8s）→ 大脑侧熔断 Fused |
| 蒸馏最小样本 | 500 |
| 台账 | JSONL，队列写线程，主路径 put_nowait；AR003 新增 `runtime` 结构化列（7 键 schema） |
| 运行时（AR003，默认关闭） | 验证器 `verifier_enabled=false`；三带 τ 0.35/0.65；缓存 similarity 0.93、TTL 1800s、容量 256；预填 budget_ratio 0.6；embedding 独立实例端口 8389 |
| 槽复用 | `[cerebrum] parallel=1`、`slot_prompt_similarity=0.5`（llama-server `--parallel`/`--slot-prompt-similarity`） |

## 8. 18 决策点

声明于 `configs/decision-points.toml`（DuoMind 清单）：
- **PRE（9）**：needs_generation / needs_reasoning / intent / complexity / ambiguity / safety / verbosity / format / needs_tool
- **MID（4）**：on_track / step_complete / should_stop / confidence_mid
- **POST（5）**：answer_complete / matches_request / needs_retry / too_verbose / correct_format

每点字段：stage / question / tau / mode（active·shadow·off）/ fallback_action。


## 9 AR003 运行时智能（增量契约）

- **路由 v2**：`CascadeEngine.route_chat_v2(pre, uncertainty=None, policy=BandPolicy(), margin_tau, budget) -> RouteDecision`；`route_chat` 委托 v2（`uncertainty=None`）保持 AR002 逐例语义。
- **不确定性/自证**：纯函数 `estimate_uncertainty(decisions, fused)`、`self_agreement(fused)`；`FusedPoint.agreement` 为纯增字段。
- **验证器**：仅依赖 `ChatFn` 协议（`LlamaServerClient` 满足）；解析失败/超时一律 REJECT，不重试。
- **缓存/记忆**：仅依赖 `EmbeddingProvider` 协议；embedding 服务不可用 → 缓存 disabled（绝不伪造向量）。
- **预填**：确定性块显著性，首末块必选；块数 <4 或 prompt <512 不压缩。
- **依赖方向**：`server → kernel.runtime/cache/prefill/cascade/cerebrum`，新模块禁止反向 import server。

## 10 AR005 自适应编排（增量契约）

- **Conformal 风险门**：`conformal_risk_threshold(scores, losses, alpha, direction)` 返回有限样本风险上界与阈值；`RiskController.from_file/disabled()`；缺失或样本 <20 → 回退 AR003 三带。硬信号永远 full。
- **缓存准入**：`SemanticCache.lookup(..., admission=None)` 可注入 `AdmissionPolicy`；命中需过阈值 + 间隔 + TTL；未注入保持固定 `similarity` 语义。
- **JCBA**：`allocate(units, config)` 确定性 0/1 背包；强制单元超预算 → 不压缩；prompt 顺序 draft→memory→blocks 不变。
- **选择/回退**：`select(outcomes, lambda_cost, alpha, baseline_arm, seed)` 产出 `adopt|rollback`；profile 导出 `configs/runtime-profile.toml`；默认不覆盖仓库配置。
- **矩阵**：`jev matrix --config <toml> [--limit N] [--write-profile]`；产出 `benchmarks/results/<run>/{results.json,report.md,profile.toml}`；后端失败退出码非 0，不写 adopt。
- **响应头**：`X-Jev-Risk: allow|deny|off`、`X-Jev-Plan: <arm>`（仅启用时）；其余头语义不变。

## 11 AR006 证据自适应与自愈（增量契约）

- **证据选择**：`EvidenceSelector(embed, EvidenceConfig).score(blocks, query, applied) -> EvidenceResult`；
  真实 embedding 余弦 + 词法/位置/决策融合，MMR 去冗余，证据锚点必选（排除末块问题）；
  embedding 失败返回 `fallback` 并回退 salience，绝不伪造向量；长文本按字符切块并分批聚合。
- **自适应预算**：`adaptive_ratio(result, config)`；范围默认 `[0.22, 0.60]`；
  `PrefillPlan`/`Allocation` 增 `selector/budget_ratio/entropy/top_gap/fallback`。
- **大脑自愈**：`BrainSupervisor(manager, client, health_interval_s, health_timeout_s, max_recoveries)`
  提供 `chat/chat_stream/health/stats`；探针失败或调用超时 → `stop/start` 并重试一次；恢复超限抛
  `BrainStartupError`；`EmbeddingService.embed` 对超时/不可达做一次子进程重启重试。
- **组合矩阵**：`arms` 允许非内置名称，但必须在 `[overlays.<arm>]` 定义；每 arm 独立重启大脑；
  无正效用下界 → `rollback`；profile 依据实际 chosen arm 生成。
- **响应头**：增 `X-Jev-Prefill-Mode: <selector>;r=<ratio>`；`/health` 增 `supervisor` 子对象（启用时）。

## 12 AR007 前缀 KV 复用（增量契约）

- **配置**：`RuntimeConfig.prefix_cache` / `session_affinity`（默认 false）。
- **稳定前缀**：`use_stable_prefix(prefix_cache, session_affinity, session_id)` 为纯函数；
  为真时服务端跳过 AEB/记忆/草稿/分配，发送原始 messages，保证前缀逐字一致。
- **会话键**：OpenAI 兼容 `user` 字段；未传则回退 AEB。
- **客户端**：`LlamaServerClient(cache_prompt=bool)` 显式开关；矩阵按 arm 覆盖。
- **观测**：`X-Jev-Prefix-Cache: hit:<cache_n>|miss|off`、`X-Jev-Prefill-Ms`、`X-Jev-Prompt-N`；
  `last_runtime.compute = {prompt_ms, prompt_n, cache_n}`；`usage` 不变。
- **选择器**：`select(..., lambda_latency=0.0)`，效用 = quality − λ_t·tokens − λ_l·latency；
  `jev matrix` 支持 `lambda_latency` 配置与报告列。
- **数据/配置**：`bench.prefixset.build_shared_prefix`；`prefix-cache.toml`、
  `server-7b-prefix.toml`、`server-14b-prefix.toml`。

## 13 AR008 异构投机解码（增量契约）

- **配置**：`[cerebrum.spec] enabled/draft_model/type/draft_n_max/draft_gpu_layers/draft_threads/sampling`；
  默认 `enabled=false`；draft 资产缺失抛 `JevAssetError`。
- **参数**：`spec_server_args(...)` 生成 `--spec-draft-model`、`--spec-type`、
  `--spec-draft-n-max`、`--spec-draft-ngl`、`--spec-draft-threads`、`--spec-draft-sampling`。
- **观测**：`X-Jev-Spec: <accepted>/<draft>`、`X-Jev-Decode-Tps`；
  `last_runtime.spec = {draft_n, accepted, decode_tps}`。
- **矩阵**：per-arm `[overlays.<ARM>.cerebrum.spec]`；指标 `mean_decode_tps`、
  `spec_accept_rate`、`outputs_identical`；数据 `build_long_generation[_en]`。
- **环境要求**：必须核对 `llama-server --list-devices`；CUDA 需 `cudart64_12.dll` /
  `cublas64_12.dll` / `cublasLt64_12.dll`。本机 GPU 限频 645 MHz（tg 5.06 t/s < CPU 9.47 t/s），
  历史 AR005–AR007 主表为 CPU-only。
- **结论**：真实实验无稳定解码收益，`rollback B1`，默认关闭；配置/指标保留供正常 GPU 复现。
- **数据/配置**：`spec-short.toml`、`spec-short-en.toml`、`spec-tuning-cpu.toml`、
  `spec-confirm-cpu.toml`、`spec-decode.toml`、`server-7b-spec.toml`、`server-14b-spec.toml`。

## 14 AR009 CASC + 异构放置（增量契约）

- **配置**：`[runtime] stable_compression/ratio/block_tokens/min_tokens/min_blocks/mmr_lambda/
  cache_entries/embed_batch`；默认关闭。
- **压缩**：`StableCompressor.compress(messages) -> StableResult`；只压缩最后 user 消息的文档段，
  查询无关（文档质心稀有度 + MMR + 首末块），确定性重建。
- **缓存**：`StableCompressionCache` 以 `sha256(doc+config)` 为键的线程安全 LRU；命中不再 embedding。
- **观测**：`X-Jev-Stable-Compression: <hit|miss|off>;r=<ratio>;t=<tokens>`、
  `X-Jev-Stable-Cache`、`X-Jev-Stable-Ms`；`last_runtime.stable_compression`。
- **异构**：`[embeddings] gpu_layers` 独立于 `[cerebrum] gpu_layers`；矩阵 `embedding_gpu_layers`。
- **结论**（POST 修复后）：shared-prefix 2662→1079 tok（−59.5%）、11/12 命中、quality 1.0、
  端到端 52.9s→38.1s；needles 1847→762 tok（−58.7%）、quality 1.0、延迟略高于 B1。
- **数据/配置**：`casc-cpu.toml`、`casc-final-gpu.toml`、`casc-order.toml`、`casc-tune.toml`、
  `casc-needles6.toml`、`server-7b-casc.toml`、`server-14b-casc.toml`、`profiles/casc.toml`。

## 15 AR010 LADS + Laya POST 窗口（增量契约）

- **缺陷修复**：超长 state 预算（`max_len=512`）原保留头部，assistant 答案在尾部被截掉，
  POST 恒定 `needs_retry`；`laya_sequence.build_sequence` 与 `laya_onnx.system_one`
  统一改为**保留尾部**。
- **退化守卫**：`server/chat.py::_is_degenerate_repetition` 检测短周期全串重复，
  触发一次反馈重试；重试后仍退化则标记 `jev_degraded`。
- **配置**：`[runtime] decision_shadow / decision_post`（默认 true）。
  `false` 时 chat 跳过 Laya shadow 评估 / POST 复核；`/v1/systemone` 仍返回完整 active+shadow。
- **观测**：`X-Jev-Decision-Mode: fast`、`X-Jev-Decisions`；`last_runtime.decision`。
- **结论**：shared-prefix 12 题，B1 54.0s、FAST 34.2s、CACHE 42.0s、CACHE_FAST **24.6s**；
  四臂 quality 1.0、`outputs_identical=1.0`；选择器 adopt CACHE_FAST（效用下界 +0.2539）。
- **配置模板**：`configs/experiments/lads.toml`、`configs/profiles/latency-fast.toml`。
- **回归**：`tests/integration/test_laya_post_window_real.py`（真实 Laya，长 state POST 可区分答案）。





## 16 AR011 ADB 决策计算预算（增量契约）

- **配置**：`[runtime] decision_permutations`（0=不限制，1=每点最多 1 次置换）、
  `decision_escalate_margin`（>0 时对低 margin 点升级）；默认 0/0。
- **实现**：`kernel/decisions/probes.py::apply_permutation_budget`；
  chat `_evaluate_points` 对 active+shadow 统一裁剪；可选升级仅重算被裁剪的低 margin active 点。
- **观测**：`X-Jev-Decision-Permutations`、`X-Jev-Decision-Escalated`；
  `last_runtime.decision.permutations/escalate_margin`。
- **真实一致性**：预算 1 与完整预算在 14 条多样 prompt 上 applied 决策 100% 一致；
  PRE 22.1s→11.3s。
- **联合结论**：shared-prefix 12 题：B1 49.2s → ADB_CACHE_FAST 13.1s（−71.3%）→
  ADB_CASC_FAST 14.1s（P50 12.1s，tokens 2662→1079）；quality 1.0、输出一致。
- **默认策略**：ADB 关闭；`profiles/latency-fast.toml` 与 `profiles/latency-best.toml`
  显式启用；服务器配置 `server-{7b,14b}-adb-casc.toml`。
- **负结果**：GPU Laya、槽位绑定、head+tail 状态窗口均不采用。


## 17 AR012 CADP ????????????????

- **??**?`[runtime] decision_staged`??? false??
- **?????**?prompt draft/memory/prefill/allocator?verifier?margin?risk ???? ?
  ?? PRE?????????? ? PRE ???`X-Jev-Decisions` ?? PRE??
- **??**?`CascadeEngine.route_short_circuit`?
  safety=unsafe?full?intent=decision?direct?needs_reasoning=reasoning?full?
  ?????light???? needs_generation/complexity?
- **??**?`X-Jev-Decision-Staged: on|skipped`?`X-Jev-Decisions` ????????
- **??**?shared-prefix 12 ? B1 51.6s ? CADP_CACHE_FAST **10.0s** ?
  CADP_CASC_FAST **10.6s?P50 8.8s?tokens 1079?**?quality 1.0??????
- **???**??? Laya 6 ??? prompt ???/???? 100% ???
- **????**?`cadp.toml`?`cadp-casc.toml`?`server-{7b,14b}-cadp-casc.toml`?
  `profiles/latency-fast.toml`?`profiles/latency-best.toml`?


## 18 AR013 RDMP：最小路由探针 + 保守确认

- **开关**：`[runtime] decision_staged_minimal`（默认 false，依赖 `decision_staged`）。
- **实现**：`framework.minimize_points`（每点 1 探针、1 置换）；
  `engine.needs_minimal_confirmation`（非 FULL 需确认）；
  `chat.evaluate_pre`：最小 wave1 → 非 FULL 用完整 wave1 复算 → wave2。
- **观测**：`X-Jev-Decision-Minimal: on|confirm`。
- **安全**：16 条真实 prompt 仅 2 次保守翻转（LIGHT→FULL）；确认路径保证无激进翻转；
  `tests/integration/test_rdmp_route_real.py` 真实 Laya 回归。
- **结果**：隔离 B1 36.8s → CADP 21.2s → RDMP 19.7s（P50 19.5s）；
  联合 CADP_CACHE_FAST 6.85s → RDMP_CACHE_FAST 5.05s（P50 4.33s，−26%）；
  RDMP_CASC_FAST P50 4.33s、tokens 1079、quality 1.0。
- **负结果**：wave1 状态窗口缩至 384/320/256 会改变 needle 路由，不采用。
- **配置**：`rdmp.toml` / `rdmp-casc.toml`；`profiles/latency-{fast,best}.toml` 已启用。


## 19 AR014 LCPT-RFG：长上下文精度分层 + reasoning-first 门控

- **量化**：`jev prepare-laya-int8` 从 fp32 Laya 生成 QInt8/per-channel 动态量化模型
  （~427MB，`models/laya/laya_int8.onnx`，不入仓）。
- **开关**：`[cerebellum.laya_onnx] int8_model_path`；
  `[runtime] decision_long_context_tokens`（默认 0；推荐 4000）。
- **门控**：仅 staged、无其他 PRE 消费者、state token > 阈值且 int8 就位时，
  先用 int8 单行计算 `needs_reasoning`；applied≠no_reasoning → FULL；
  否则回退 fp32 RDMP。
- **安全**：门控只可能产生 FULL；缺 int8/阈值 0/推理失败均回退 AR013，不可能更激进。
- **观测**：`X-Jev-Decision-Long: int8-reason-full`。
- **结果**：隔离 B1 44.36s → RDMP 23.00s → LCPT 19.46s；
  联合 RDMP_CACHE_FAST 6.63s → LCPT_CACHE_FAST 2.53s；
  LCPT_CASC_FAST **P50 2.42s、tokens 1079、quality 1.0**。
- **回归**：needles（~2810 tokens）不触发门控，LCPT 37.88s vs RDMP 38.25s，质量 1.0。
- **配置**：`lcpt.toml` / `lcpt-casc.toml` / `lcpt-needles.toml`；
  `server-{7b,14b}-lcpt-casc.toml`（完整 overlay）。


## 20 AR015 AGW：自适应门控窗口

- **开关**：`[runtime] decision_long_context_gate_tokens`（默认 384，0=完整 512）。
- **实现**：`create_laya_int8(max_state_tokens=...)` 仅缩短 int8 门控窗口；
  fp32 RDMP 兜底保持完整 state。
- **安全**：窗口只影响“能否直接判 FULL”；未命中回退完整 RDMP，不产生更便宜路由。
- **观测**：`X-Jev-Decision-Long: int8-reason-full:w384`。
- **结果**：shared 12 题门控命中 12/12；门控 1.51s→1.05s；
  联合 AGW_CASC_FAST mean 3.765s / P50 **1.969s**（相对 LCPT_CASC mean −1.9%），
  tokens 1079、quality 1.0。
- **取舍**：隔离矩阵无收益；联合矩阵小幅正收益，选择器 adopt；不宣称大幅提升。
- **配置**：`agw.toml` / `agw-casc.toml`；`latency-{fast,best}.toml` 已启用。


## 21 AR016 GSP：GPU 服务放置

- **开关**：`[cerebrum] gpu_layers`（默认 0）→ llama-server `--n-gpu-layers`。
- **实测**：AGW_CASC_FAST 同题集 CPU mean 3.462s/P50 2.032s →
  GPU offload **mean 3.054s/P50 1.753s**（−11.8%/−13.7%），prefill 726ms→357ms，
  quality 1.0、输出一致。
- **结论**：短输出 + 长 prefill 场景 GPU 放置有效；纳入当前最优。
- **回退**：无 GPU/显存不足用 `latency-best.toml`（gpu_layers=0）。
- **配置**：`latency-best-gpu.toml`、`ar016-gpu-{cpu,offload}.toml`、
  `server-{7b,14b}-agw-casc.toml`。

## 22 AR017 CGW：级联门控窗口

- **开关**：`[runtime] decision_long_context_gate_windows`（默认 `[128, 384]`）；
  空列表=单窗口，兼容历史配置。
- **实现**：`windowed(max_state_tokens)` 共享 int8 ONNX session；chat 按升序逐级判定，
  命中 FULL 即停，全部未中回退 fp32 RDMP。
- **观测**：`X-Jev-Decision-Long` 记录实际链（如 `int8-reason-full:w128`）。
- **实测**：联合矩阵 CGW_CASC_FAST P50 **1.001s** vs AGW_CASC_FAST 1.658s
  （−39.6%），mean 2.063s vs 2.970s，tokens 1079、quality 1.0。
- **回退**：窗口为空或无 int8 资产时退化为 AR015/AR013，不影响正确性。
- **配置**：`cgw.toml` / `cgw-casc.toml`；当前 profile/服务器配置启用 `[128, 384]`。

## 23 AR018 GTB：证据接地的双脑级联

- **开关**：`[cerebrum] tier_enabled`（默认 false）、`small_model`、`small_gpu_layers`、
  `tier_ground_window`、`tier_max_answer_chars`、`tier_prewarm`。
- **实现**：`TieredBrain` 在 full 路由先调用真实 0.5B llama-server；
  `grounded_verify` 用问题锚点所在文档段落的局部窗口验证短答案，通过即采用，
  否则升级 1.5B；小模型超时/不可达直接回退；light/streaming 不启用。
- **观测**：`X-Jev-Tier: small|small->large|small-error->large`、
  `X-Jev-Tier-Reason`。
- **实测**：联合 `GTB_CASC_FAST` P50 0.721s vs CGW 1.493s（quality 1.0，select adopt）；
  外部争用复跑 P50 0.684s vs 1.035s，mean 1.062s vs 1.030s（选择器 mean 回退 CGW）。
- **回退**：关闭 tier 或使用 `latency-best.toml`；独立文档推荐 CGW。
- **配置**：`gtb.toml` / `gtb-casc.toml` / `gtb-needles*.toml`；
  `latency-best-gpu.toml` 启用 GTB。
