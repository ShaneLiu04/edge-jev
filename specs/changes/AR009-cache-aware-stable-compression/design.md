# [AR009] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR009 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-27 |
| 状态 | Approved（用户授权全自动） |

## 1. 设计目标

1. **默认关闭**；关闭时与 AR007/AR008 行为一致。
2. **压缩与问题解耦**：同一文档的压缩结果不随问题变化，保证前缀逐字稳定。
3. **压缩一次、多次复用**：文档哈希 LRU，缓存命中不调用 embedding。
4. **真实可观测**：压缩前后 token、缓存命中、embedding 耗时、prefix `cache_n` 全部落台账。
5. **异构不混用**：brain 与 embedding 的 GPU 层数独立，实验配置可分别设置。
6. **无收益即回退**：组合选择仍走 bootstrap + LOO。

## 2. 数据流

```text
request.messages (last user = 文档 + 末尾问题)
        │
        ├─ stable_compression off ──► AR007 稳定原文（不变）
        │
        └─ on ──► split_document_query()
                    │ document, query
                    ▼
             doc_hash = sha256(document+config)
                    │
             StableCompressionCache ──hit──► compressed_doc
                    │miss
                    ▼
             embed(blocks) ──► centroid/rarity/MMR ──► compressed_doc
                    │                                  │
                    └──────── put(hash, compressed) ───┘
                    ▼
        messages' = compressed_doc + "\n\n" + query
                    ▼
        LlamaServerClient(cache_prompt=True)
                    ▼
        timings.cache_n / prompt_ms ──► headers + last_runtime
```

## 3. 模块设计

### 3.1 `kernel/prefill/stable.py`

```python
@dataclasses.dataclass(frozen=True)
class StableConfig:
    enabled: bool = False
    ratio: float = 0.6                # 文档保留比例
    block_tokens: int = 128           # 细粒度块，便于隔离 needle
    min_tokens: int = 512             # 低于此值不压缩
    min_blocks: int = 4
    mmr_lambda: float = 0.75          # 稀有度 vs 多样性
    cache_entries: int = 64           # 0 = 禁用压缩缓存

@dataclasses.dataclass(frozen=True)
class StableResult:
    messages: list[dict]
    active: bool
    cache: str                        # hit | miss | off
    before_tokens: int
    after_tokens: int
    doc_tokens: int
    ratio: float
    ms: float
    skipped: str | None = None
    fallback: str | None = None

class StableCompressionCache:
    def get(key: str) -> tuple[str, int] | None
    def put(key: str, text: str, tokens: int) -> None
    def clear() -> None
    stats: {"hits": int, "misses": int, "size": int}

class StableCompressor:
    def __init__(embed, config: StableConfig, cache=None)
    def compress(messages: Sequence[dict]) -> StableResult
```

纯函数（可单测）：

```python
def split_document_query(content: str) -> tuple[str, str] | None
def rarity_scores(vectors: Sequence[Sequence[float]]) -> list[float]
def stable_select(blocks, scores, vectors, ratio, mmr_lambda) -> set[int]
```

**切分规则：**
- 仅处理最后一个 `role=user` 的消息；
- 按 `\n\n` 分段，最后一段长度 ≤ `max(200, 25% 全文)` 且至少 2 段 → 视为问题；
- 用 `rsplit("\n\n", 1)` 保留文档原文（不 strip 内容，避免破坏逐字前缀）；
- 文档 token < `min_tokens` 或块数 < `min_blocks` → `skipped`，返回原消息。

**稀有度：**
- 块向量归一化；质心 `c = mean(v_i)`；
- `rarity_i = 1 - cos(v_i, c)`，再 min-max 到 `[0,1]`；
- 首块、末块强制入选；其余按 MMR：
  `utility = rarity_i - λ · max_{j∈selected} cos(v_i, v_j)`；
- 预算 `budget = ceil(doc_tokens * ratio)`；按 token 累加，超预算停止；
- 按原块顺序重组，块间 `\n\n`；
- 若压缩后 token ≥ 原文 ×0.95 → `fallback=no_reduction`，返回原文。

### 3.2 配置

`RuntimeConfig` 增：

```python
stable: StableConfig = StableConfig()
```

`configs/jev.toml [runtime]`：

```toml
stable_compression = false
stable_compression_ratio = 0.6
stable_compression_block_tokens = 128
stable_compression_min_tokens = 512
stable_compression_mmr_lambda = 0.75
stable_compression_cache_entries = 64
```

`Kernel._build_runtime` 解析；`Kernel` 构造 `StableCompressor`（embedding 可用时），
作为 `kernel.stable_compressor` 注入 `create_chat_router` → `_CascadeContext`。

### 3.3 服务端接入

`_CascadeContext.prepare_prompt` 中，`use_stable_prefix(...)` 为真的分支：

```python
if config.stable.enabled and self.stable is not None:
    result = self.stable.compress(self.messages)
    self.stable_result = result
    self.last_runtime["stable_compression"] = {...}
    return result.messages
```

- `prefix_cache_active=True` 保持不变（尾问仍走 KV 复用）；
- 问题尾部逐字保留 → 第二问起 `cache_n` 覆盖压缩文档；
- 失败/未启用 → 与 AR007 完全一致。

`_route_headers` 增：

```text
X-Jev-Stable-Compression: hit|miss|off;r=0.60;t=<after_tokens>
X-Jev-Stable-Cache: hit|miss|off
```

### 3.4 异构设备放置

- `[embedding] gpu_layers`（默认 0）→ `_build_embeddings` 追加 `--n-gpu-layers`；
- `MatrixConfig.embedding_gpu_layers`（默认继承 `gpu_layers`）→ `_spawn_embedding`；
- 实验配置显式写 `gpu_layers = 0` + `embedding_gpu_layers = 99`；
- 文档要求先 `llama-server --list-devices` 验证 CUDA0，再记录 `nvidia-smi` 显存/利用率。

### 3.5 实验设计

| 配置 | 数据 | brain | embedding | 臂 |
|------|------|-------|-----------|----|
| `casc.toml` | shared_prefix 12 | CPU | CPU | B1 / CACHE / AEB / CASC |
| `casc-gpu-emb.toml` | shared_prefix 12 | CPU | GPU 99 | CACHE / CASC |
| `casc-needles.toml` | needles 12 | CPU | GPU 99 | B1 / AEB / CASC |
| `casc-quality.toml` | quality 12 | CPU | CPU | B1 / CASC（短 prompt 不压缩） |

- 每 arm 独立重启大脑；压缩缓存随进程重建（不跨 arm 污染）；
- 判定：CASC 质量 ≥ baseline −2pt，tokens ≤0.75×CACHE，cache hit ≥11/12；
- CASC_GPU 相对 CASC_CPU 延迟目标 ↓≥10%；否则记录“GPU 无收益”并保留 CPU 配置；
- 选择器：`quality − 0.15·tokens_norm − 0.5·latency_norm`，配对 bootstrap 95%CI。

### 3.6 测试策略

**单元（纯逻辑）：**
- `split_document_query`：中英文、文档过短、无问题段；
- `rarity_scores`：正交/重复向量；
- `stable_select`：预算、首末块、确定性；
- 压缩确定性：同文档两次结果逐字相同；
- 缓存：第二次命中且不再调用 embed（用确定性哈希向量函数，不 mock 后端）；
- 回退：embed 抛错 → 原文；无收益 → 原文；
- 配置解析：`embedding_gpu_layers`、`stable_*`。

**真实集成（`@pytest.mark.assets`）：**
- 启动真实 llama-server + 真实 embedding；
- 同一文档两问：第一问 miss、第二问 hit，`cache_n>0`，两问答案均正确；
- 关闭 `stable_compression` 时行为与 AR007 一致；
- 缺 embedding 资产 → `JevAssetError`，不静默伪造。

## 4. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 文档级压缩丢证据 | 质量下降 | 稀有度保留独特事实 + 首末块 + MMR；真实 needles/shared 验证；不过门回退 |
| 压缩结果不确定 | 前缀不稳定 | 固定块大小/排序/tie-break；无随机性；单测逐字一致 |
| embedding 慢 | 首问延迟高 | GPU embedding + 压缩缓存；第二问不再 embedding |
| GPU 限频 | 收益低 | brain 保持 CPU；只把 prefill 密集的 embedding 放 GPU；实测记录 |
| 缓存污染 | 跨文档错误复用 | 键含文档哈希 + 压缩配置；问题不参与键 |
| 与 AEB 同时开 | 再次破坏前缀 | CASC 路径跳过 AEB；配置互斥由测试覆盖 |

## 5. 回退设计

- `stable_compression=false` 默认关闭；
- 压缩失败/无收益 → 原文，仅记录 fallback；
- 实验无正下界 → `rollback`，不写 profile；
- 若 GPU embedding 无收益 → 文档明确“本机不回退 GPU”，保留 CPU 配置。
