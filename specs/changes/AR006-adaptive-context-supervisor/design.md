# [AR006] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR006 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-26 |
| 状态 | Approved（用户授权全自动） |

## 1. 设计目标与原则

1. **只加不改**：默认关闭；关闭时 `plan_prefill`/`allocate`/prompt 输出与 AR005 逐字节一致。
2. **真实优先**：证据向量只来自真实 embedding HTTP；失败显式降级并记录，不造指标。
3. **确定性**：同输入同配置选择顺序稳定；MMR 的并列按块索引。
4. **可解释**：每个请求记录 selector/ratio/entropy/gap/anchor，可在台账与响应头核对。
5. **自愈不掩盖错误**：监督器只对「进程不健康/超时」重试一次；重试失败按原错误上抛。

## 2. 模块设计

### 2.1 `kernel/prefill/evidence.py`（新增）

```python
@dataclass(frozen=True)
class EvidenceConfig:
    embed_weight: float = 0.55
    lexical_weight: float = 0.25
    position_weight: float = 0.10
    decision_weight: float = 0.10
    mmr_lambda: float = 0.25
    min_ratio: float = 0.35
    max_ratio: float = 0.75

@dataclass(frozen=True)
class EvidenceResult:
    scores: tuple[float, ...]
    vectors: tuple[tuple[float, ...], ...]
    fallback: str | None = None
    entropy: float = 0.0
    top_gap: float = 0.0

class EvidenceSelector:
    def __init__(self, embed: Callable[[list[str]], list[list[float]]],
                 config: EvidenceConfig | None = None) -> None: ...
    def score(self, blocks: Sequence[Block], query: str,
              applied: Mapping[str, str | float | None]) -> EvidenceResult: ...
```

- `score` 内部 `try: embed([query, *texts])`，捕获 `JevError` → `fallback=str(exc)`，`scores=()`；
- 余弦：`dot/(|a||b|)`，零向量 → 0；
- min-max 归一（全等 → 全 0.5）；
- 融合：`w_e*emb_norm + w_l*lexical_overlap + w_p*position_prior + w_d*decision_signal`（后三者复用 `salience.py`）；
- 统计：对归一 emb 分数做 softmax 得 `p`；`H = -Σp·ln p / ln n`（n<=1 → 0）；
  `top_gap=(s1-s2)/(s1+1e-9)`，`s` 为融合分降序且 ≥0；
- `adaptive_ratio(result, config)`（模块函数，纯逻辑）：
  `ratio = min + (max-min)*clamp(0.5H + 0.5(1-gap), 0, 1)`。

**为什么不用 token 级压缩**：会破坏可审计文本与结构化模板；块级预算已足够覆盖长文档收益，且与 JCBA 背包天然契合。

### 2.2 `kernel/prefill/planner.py`

`PrefillConfig` 增字段（全部有默认，向后兼容）：

```
adaptive: bool = False
min_ratio: float = 0.35
max_ratio: float = 0.75
evidence: bool = False
evidence_config: EvidenceConfig = field(default_factory=EvidenceConfig)
anchor: bool = True
```

`plan_prefill(..., evidence: EvidenceSelector | None = None)`：

1. 计算 salience 分 `base`；
2. 若 `config.evidence` 且 `evidence` 非空：`result = evidence.score(...)`；
   - `result.fallback is None` → 用 `result.scores`，`selector="evidence"`，按 `adaptive` 选 ratio；
   - 否则 `selector=f"fallback:{reason}"`，用 base 与固定 ratio；
3. 必选集 `{0, n-1}`，若 `anchor` 加 `argmax(scores)`；
4. MMR 贪心（仅证据路径）：按 `score - λ·max_sim` 选块（需要 `vectors`）；
   非证据路径保持原「按分数降序」；
5. 预算按 `ceil(before * ratio)`；必选超预算 → 原输入（`mandatory_over_budget`）；
6. `PrefillPlan` 增 `selector: str = "salience"`、`budget_ratio: float = 0.0`、
   `entropy: float = 0.0`、`top_gap: float = 0.0`、`fallback: str | None = None`。

兼容：所有新字段有默认；`plan_prefill` 的 `evidence` 关键字默认 `None`。

### 2.3 `kernel/runtime/prompt.py`

- `PromptConfig` 增：`evidence_enabled`、`adaptive`、`min_ratio`、`max_ratio`、`mmr_lambda`、`anchor`。
- `build_prompt(..., evidence: EvidenceSelector | None = None)`。
- `_allocate_prompt`：
  - `scores` 由 evidence 结果（或 salience）提供；
  - ratio 由 adaptive 选（否则 `allocator_budget_ratio`）；
  - 锚点块 `mandatory=True`（配置开启时）；
  - `AllocatorConfig(budget_ratio=ratio)`；
  - `Allocation` 增 `budget_ratio`、`selector`、`entropy`、`top_gap`（默认 0/""），`stats()` 返回。

### 2.4 `kernel/cerebrum/supervisor.py`（新增）

```python
class BrainSupervisor:
    def __init__(self, client: LlamaServerClient, manager: LlamaServerManager | None,
                 *, health_interval_s: float = 0.0,
                 max_recoveries: int = 5, cooldown_s: float = 30.0) -> None
    def health(self) -> bool
    def chat(self, messages, *, max_tokens=..., temperature=..., stop=None,
             timeout_s=None, extra_body=None) -> ChatResponse
    def chat_stream(...): ...
    def stats(self) -> dict
    def close(self) -> None
```

- `_ensure_healthy()`：若 `manager` 非空且距上次探针 ≥ interval 或强制，调用 `client.health()`；
  失败 → `manager.stop()`、`manager.start()`，计数；连续恢复超 `max_recoveries` → 抛 `BrainStartupError`；
- `chat`：先 `_ensure_healthy`；调用 client；捕获 `(JevTimeoutError, JevUnavailableError)` 且 manager 非空 →
  强制恢复一次 → 重试一次；再失败上抛原异常；
- 无 manager（外部大脑）只做健康检查，不重启，错误照常上抛；
- `stats()`：`{"restarts": int, "retries": int, "last_recovery_ms": float, "healthy": bool}`。

**为什么不重试协议错误 4xx**：请求本身非法，重启无意义；只对超时/不可达恢复。

### 2.5 接线

- `Kernel`：
  - 属性 `evidence_selector`：当 `[prefill].evidence=true` 且 `embeddings` 非空时构造
    `EvidenceSelector(embeddings.embed, ...)`（惰性，第一次调用才启动 embedding 子进程）；
  - 属性 `supervisor`：当 `[brain].supervisor_enabled=true` 且 brain 非空时构造；
  - `_build_runtime` 从 `[prefill]` 读 AEB 字段到 `PrefillConfig` 与 `PromptConfig`。
- `server/chat.py`：`_CascadeContext` 增 `evidence`；`prepare_prompt` 传入；
  台账 `prefill` 增 `selector/budget_ratio/entropy/top_gap/fallback`；响应头增
  `X-Jev-Prefill-Mode`（`{selector};r={ratio:.2f}`）。
- `server/app.py`：`build_serve_app` 若 `kernel.supervisor` 非空，用它包装 brain；
  `/health` 增 `brain` 监督器统计（仅当启用）。

### 2.6 矩阵组合（`bench/matrix.py`）

- `ARMS` 仍为内置预设；`load_matrix_config` 校验改为：
  `arm in ARMS or arm in overlays`；错误信息列出可用组合。
- `_arm_config`：未知 arm 且无预设 → 以 `{}` 为 overlay 起点（纯 base + 配置覆盖），
  再由 `_ARM_OVERLAYS.get(arm, {})` 合并。
- `_build_client`：`embedding_service` 传入条件改为
  `cache.enabled or runtime 证据/自适应 or prefill.evidence`；
- `force_full` 仅对 `B0`；`AR005` 专属校准逻辑保留（仅 arm 名精确 `AR005`）。
- 结果 `config.arms` 原样记录，报告中逐组合列出质量/词元/延迟/路由。

### 2.7 复现与服务器

- `REPRODUCTION.md` 增：
  - 本机小模型组合实验（无 GPU 也可 CPU，给 `gpu_layers=0`）；
  - 服务器 7B/14B：`brain=external` + 预启动命令、`ctx_size`、`max_tokens`、显存估算、
    数据集、预计时长、失败重试；
  - 产物路径与校验（HEAD/指纹/结果 JSON）。
- 新增 `configs/experiments/combos.toml`（本机 AEB 组合）与
  `configs/experiments/server-7b-combos.toml`、`server-14b-combos.toml`（external 大脑）。

## 3. 错误处理

| 场景 | 行为 |
|------|------|
| embedding 超时/不可达 | 降级 salience，`fallback` 记原因，不重试（避免放大延迟） |
| 证据块为空/维度不符 | `EvidenceSelector` 抛 `JevProtocolError` → 降级 |
| 必选块超预算 | 原输入 + `mandatory_over_budget` |
| 大脑假死 | 监督器 stop/start + 重试一次；失败上抛 |
| 重启超上限 | `BrainStartupError`，服务 503 |
| 组合配置未知 arm | `JevError`，列出可选 |

## 4. 测试策略

- 纯逻辑单测（确定性）：余弦/归一/MMR/自适应比例/降级、allocator 新字段、matrix 组合解析；
  证据测试注入**纯函数**（哈希词袋向量），不触后端。
- 真实后端集成（`assets`）：同 1.5B `--embeddings` 服务对 needles 选择；
  监督器用真实 spawn 后 `manager.stop()` 模拟崩溃并恢复（真实进程，无 mock）。
- 真实矩阵：combos（needles 12 题）与 quality 30 题；记录结果与回退。

## 5. 回滚

- 所有能力默认关闭；配置回滚只需删 `[prefill] evidence/adaptive` 与 `[brain] supervisor_enabled`。
- 实验选择器无正收益时 `rollback` 到基线组合，`configs/runtime-profile.toml` 不更新。
