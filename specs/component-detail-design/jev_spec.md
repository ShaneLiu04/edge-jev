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


