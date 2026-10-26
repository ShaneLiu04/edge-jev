# REPRODUCTION — 从零复现 Jev 实验

本文档面向**全新克隆**与**GPU 服务器**，给出最短且可验证的复现路径。所有指标必须来自真实后端；失败即失败。

## 0. 前置条件

| 项 | 要求 |
| --- | --- |
| OS | Windows 10/11 或 Linux |
| Python | ≥3.11（`uv` 管理） |
| 磁盘 | ≥8GB（1.5B 实验）/ ≥40GB（7B/14B） |
| GPU | 可选；CPU 也可跑通，但长生成可能超时（调大 `timeout_s`） |
| 网络 | 企业代理需配置；资产可由 `jev fetch` 下载 |

## 1. 本机（1.5B）一键复现

```bash
git clone <repo> jev && cd jev
uv sync
uv run jev fetch --all
uv run jev doctor

# 纯逻辑测试（无资产也可）
uv run pytest tests/unit -q
uv run ruff check .

# 真实矩阵（先小样本）
uv run jev matrix --config configs/experiments/ar005.toml --limit 6

# 全量 30 题 × 4 方案
uv run jev matrix --config configs/experiments/ar005.toml
```

### 判定复现成功

1. `benchmarks/results/<run>/results.json` 存在，`schema == "jev.matrix/1"`；
2. 每个 arm 的 `metrics.quality`、`mean_completion_tokens`、`p50_latency_ms` 非空；
3. `selection.decision ∈ {adopt, rollback}`；
4. `report.md` 含环境指纹与实际命令。

### 常见问题

- **大脑启动探测超时**：GPU 被其他进程占用（`nvidia-smi` 查看）。释放 GPU，或在实验配置设置 `gpu_layers = 0` 走 CPU 并调大 `timeout_s`。
- **`models/laya/calibration.json` 缺失**：属于未校准状态，运行继续，但决策 margin 不启用。可用 `jev distill --calibrate` 生成。
- **embedding 启动失败**：语义缓存自动降级为精确层；矩阵若 `embedding="auto"` 会明确失败（不伪造向量）。

## 2. GPU 服务器（7B / 14B）

### 2.1 资产

```bash
# 目录约定（可改为绝对路径，同步修改实验 TOML）
/models/qwen2.5-7b-instruct-q4_k_m.gguf
/models/qwen2.5-14b-instruct-q4_k_m.gguf
/models/bge-m3-q8_0.gguf          # 或任一 --embeddings 兼容模型
```

> 模型资产不入仓；`configs/assets.toml` 管理 sha256 与下载源。

### 2.2 启动服务（真实进程）

```bash
# 7B 大脑
llama-server -m /models/qwen2.5-7b-instruct-q4_k_m.gguf \
  --host 0.0.0.0 --port 8388 --ctx-size 8192 --n-gpu-layers 99 \
  --parallel 4 --slot-prompt-similarity 0.5

# Embedding（独立实例）
llama-server -m /models/bge-m3-q8_0.gguf --host 127.0.0.1 --port 8389 \
  --embeddings --pooling mean --ctx-size 2048 --n-gpu-layers 99

# 14B 大脑（如显存允许）
llama-server -m /models/qwen2.5-14b-instruct-q4_k_m.gguf \
  --host 0.0.0.0 --port 8390 --ctx-size 8192 --n-gpu-layers 99 \
  --parallel 2 --slot-prompt-similarity 0.5
```

### 2.3 矩阵

```bash
uv run jev matrix --config configs/experiments/server-7b.toml
uv run jev matrix --config configs/experiments/server-14b.toml   # 资产缺失必须失败
```

产物同样写入 `benchmarks/results/server-7b-*/`。**14B 未实际运行前，不得对外宣称 14B 结论。**

### 2.4 AR006 组合实验（7B/14B）

```bash
# 组合：B1（路由基线）/ P60（固定 0.6 压缩）/ AEB（证据+自适应）/ AEB_ALLOC（+JCBA）
uv run jev matrix --config configs/experiments/server-7b-combos.toml
uv run jev matrix --config configs/experiments/server-14b-combos.toml
```

- 大脑/embedding 均为 `external`；先按 2.2 启动，端口与 TOML 的 `brain_base_url` /
  `embedding_base_url` 保持一致。
- 如需长上下文：把 `dataset` 改为 `"needles"`、`ctx_size` 提到 `16384`、`max_tokens` 降到 16；
  长 prompt 下务必保持 `timeout_s >= 600`，并启用服务端 `--parallel 4` 提升吞吐。
- 自适应预算默认范围 `0.22–0.60`；如需更保守，改 `[prefill] adaptive_min_ratio/adaptive_max_ratio`。
- 服务器 embedding 建议 `--batch-size 2048 --ubatch-size 1024`（本仓库矩阵已按此口径）。
- 若服务端长时间运行后假死，在 `configs/jev.toml` 打开 `[cerebrum] supervisor_enabled = true`
  并使用 `build_serve_app` 路径；矩阵每 arm 独立重启大脑，天然隔离。

## 3. 实验口径

| 方案 | 定义 |
| --- | --- |
| B0 | 全 full：`route_policy=full`，禁用能力门控/运行时/缓存 |
| B1 | AR002：能力门控路由（无 AR003 运行时） |
| B2 | AR003：验证器 + 缓存 + prefill + 记忆 |
| AR005 | B2 + Conformal 风险门/缓存准入 + JCBA + 方案选择 |

指标：

- `quality`：`configs/quality-set.yaml` 的 gold 判定（contains/regex/numeric）；
- `completion_tokens`：大脑生成成本（优先）；
- `latency_ms`：端到端墙钟（TestClient，含真实后端）；
- `selection`：效用 `quality - λ·tokens_norm`，配对 bootstrap 95% CI + 留一策略；无正下界 → `rollback`。

## 4. 推荐复现顺序

1. `pytest tests/unit`（全绿）；
2. `matrix --limit 6`（验证真实链路与产物）；
3. `matrix`（全量 30 题，得到 adopt/rollback）；
4. 服务器 7B；
5. 服务器 14B（显存允许时）。

## 5. 可重复性声明

- 随机性：`seed=20261026` 固定；生成温度 0；bootstrap 固定 seed。
- 指纹：`results.json.run.fingerprint` 记录 Python/平台/依赖/资产哈希前缀。
- 变更配置后必须重新跑矩阵；不得手工编辑 `results.json`。
