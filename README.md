# Jev（捷思）— 端侧快慢双系统 AI 运行时

> **System 1（小脑）+ System 2（大脑）** 的全本地双系统运行时：类型化决策、级联路由、风险可控降级、前缀/KV 复用、稳定压缩、双脑接地、确定性快答与可复现实验矩阵。

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/unit-780%20passed-brightgreen)](#测试与质量)
[![Backends](https://img.shields.io/badge/backends-real%20only-orange)](#设计原则)

## 目录

- [为什么是 Jev](#为什么是-jev)
- [设计原则](#设计原则)
- [架构](#架构)
- [请求生命周期](#请求生命周期)
- [最终综合最优方案](#最终综合最优方案)
- [快速开始](#快速开始)
- [API 与 SDK](#api-与-sdk)
- [配置](#配置)
- [实验与复现](#实验与复现)
- [关键结果](#关键结果)
- [AR 状态总览](#ar-状态总览)
- [测试与质量](#测试与质量)
- [目录结构](#目录结构)
- [文档地图](#文档地图)
- [诚实边界](#诚实边界)
- [许可](#许可)

## 为什么是 Jev

大多数「小模型 + 大模型」方案停留在**云端路由**或**单点缓存**。Jev 把「何时不用大模型、用什么上下文、能否复用 KV、结果能否复用、并发如何调度」做成一个**可校准、可回退、可复现的运行时**。

- **全本地**：Laya ONNX 进程内小脑 + llama-server/Qwen GGUF 大脑；资产带 sha256、可离线。
- **快慢协同**：18 个声明式决策点（PRE 9 / MID 4 / POST 5），三路径 `direct / light / full`。
- **零大脑快答**：GEXA 单事实抽取、GQRA 多记录数值推理命中时不调用任何大脑 LLM。
- **上下文复用**：前缀 KV + 会话亲和 + 查询无关稳定压缩 + 压缩 LRU。
- **级联决策压缩**：LADS fast、ADB 预算、CADP 分阶段、RDMP 最小探针、LCPT 长文 int8、CGW 级联门控。
- **双脑接地**：0.5B 小模型先答，确定性证据验证通过才接受，否则升级 1.5B。
- **服务化能力**：GPU offload、持久化 KV 快照、并行槽/文档亲和、熔断与自愈。
- **可复现**：`jev matrix` 在真实后端跑对照，产出原始 JSON、报告、环境指纹，选择器自动 `adopt/rollback`。

## 设计原则

1. **零 mock、真实指标、失败即失败**：缺失资产或不可用后端明确报错，不产生伪造结果。
2. **新能力默认关闭**：所有优化通过 profile / 配置显式启用，默认行为保持稳定。
3. **无增益即回退**：选择器使用配对 bootstrap 95% CI + LOO；无正下界或质量下降 → `rollback`。
4. **不盲目全量叠加**：按负载选择组合；冲突组合明确禁止（见 [PORTFOLIO §6](docs/PORTFOLIO.md)）。
5. **依赖方向强制**：`server/sdk/cli/mcp_server → kernel → 子模块`；薄壳不得绕过 Kernel 访问小脑/大脑。
6. **协议只增不改**：`/v1/systemone` 严格对齐 TypeSafe 线协议；`/v1/chat/completions` 保持 OpenAI 兼容。

## 架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 薄壳：FastAPI Server · Python SDK · Typer CLI · MCP Server                │
├──────────────────────────────────────────────────────────────────────────┤
│ Kernel（组合根）                                                         │
│                                                                          │
│  ┌──────────────────┐   ┌──────────────────────────────┐   ┌───────────┐ │
│  │ CascadeEngine    │   │ Runtime                      │   │ Ledger    │ │
│  │ route / verifier │   │ prefix KV · CASC · LADS      │   │ JSONL     │ │
│  │ POST / retry     │   │ ADB · CADP · RDMP · LCPT     │   │ 台账      │ │
│  └────────┬─────────┘   │ CGW · GTB · GEXA · GQRA      │   └───────────┘ │
│           │             └──────────────┬───────────────┘                 │
│  ┌────────▼─────────┐   ┌──────────────▼───────────────┐   ┌───────────┐ │
│  │ 小脑 Cerebellum  │   │ 大脑 Cerebrum                │   │ Cache     │ │
│  │ Laya ONNX / kev  │   │ llama-server · 0.5B/1.5B     │   │ 语义/精确 │ │
│  └──────────────────┘   │ GPU offload · parallel slots │   └───────────┘ │
│                         └──────────────────────────────┘                 │
├──────────────────────────────────────────────────────────────────────────┤
│ 基础设施：AssetFetcher · Bench/Matrix · Selector · Distiller · Doctor     │
└──────────────────────────────────────────────────────────────────────────┘
```

关键模块：

| 模块 | 职责 |
| --- | --- |
| `kernel/cerebellum` | Laya ONNX / kev 小脑后端、类型化问题与决策框架 |
| `kernel/cerebrum` | llama-server 管理、tiered brain、快照、并行槽调度 |
| `kernel/cascade` | 级联引擎、路由、验证器、POST 复核/重试 |
| `kernel/runtime` | 前缀 KV、CASC、LADS、ADB、CADP、RDMP、LCPT、CGW |
| `kernel/extract` | GEXA 抽取、GQRA 数值推理 |
| `kernel/cache` | 精确/语义缓存与准入 |
| `kernel/ledger` | 异步 JSONL 台账 |
| `bench` | 数据集、矩阵、选择器、报告 |
| `server` | 双协议 API、健康检查、响应头 |
| `sdk` / `mcp_server` | 进程外客户端与 MCP 工具 |

## 请求生命周期

```text
POST /v1/chat/completions
  │
  ├─ 1. 语义缓存（可选）          命中 → 直接返回，tokens=0
  │
  ├─ 2. Laya PRE 决策             18 决策点 / 三态 / 预算 / 分阶段
  │       └─ direct → 小脑模板直答
  │
  ├─ 3. GEXA / GQRA 快答          仅 full 路由、可确定性回答
  │       └─ 命中 → 零大脑 LLM；fast 模式跳过 POST，strict 模式走 POST
  │
  ├─ 4. light / full 路由
  │       ├─ light：受限生成 + MID 早停
  │       └─ full ：CASC 压缩 → 前缀 KV → GTB/CGW → 大脑生成
  │
  ├─ 5. POST 复核（可配）          needs_retry → 一次反馈重试 / 降级交付
  │
  └─ 6. 台账 + 响应头             X-Jev-Route / Extract / Tier / Cache ...
```

## 最终综合最优方案

完整分析见 [docs/PORTFOLIO.md §6](docs/PORTFOLIO.md)。结论是：**不存在单一配置在全负载全域支配**；最终方案是公共前栈 + 两个 miss 档。

```text
GQRA + GEXA（零大脑 LLM 快路径，位于 Laya 路由后）
  └─ miss → CASC + prefix KV + session affinity
             + LADS fast / ADB perm=1 / CADP / RDMP / LCPT / CGW
             └─ F1: 0.5B GTB（交互 P50 档）
             └─ F2: 1.5B CGW（平均/独立文档档）
                └─ GPU offload
部署：F4 PPKS 冷启动（parallel=1） / F3 parallel=4 并发（与 PPKS 互斥）
```

| 档位 | 组合 | 适用负载 | 入口 |
| --- | --- | --- | --- |
| **F1 交互默认** | GQRA + GEXA + GTB_CASC_FAST + GPU | 交互助手、短输出、重复/共享文档 | `configs/profiles/latency-best-gpu-grounded.toml` |
| **F2 平均稳定** | GQRA + GEXA + CGW_CASC_FAST + GPU | 批处理、独立长文档、mean 优先 | 同档 `tier_enabled=false` |
| **F3 多用户吞吐** | GTB_CASC_FAST + `parallel=4`（ctx 16384） | 并发服务 | `latency-best-gpu-throughput.toml` |
| **F3b 文档亲和** | GTB_CASC_FAST + `parallel=2` + `id_slot` 轮转 | 同文档并发 | `latency-best-gpu-affinity.toml` |
| **F4 冷启动** | GTB/CGW_CASC_FAST + PPKS 快照 | 滚动重启/实验冷启动 | `configs/experiments/ppks.toml` |
| **F5 无 GPU** | CGW_CASC_FAST（CPU 基础档） | CPU-only / 显存不足 | `configs/profiles/latency-best.toml` |
| **F6 一次性长文** | AEB（可选叠加 GEXA/GQRA） | 单次长文、token 成本优先 | `configs/profiles/aeb.toml` |

> F3/F4/F5/F6 的 profile 默认未开启 GEXA/GQRA；如需零大脑快答，额外设置
> `[extractive] enabled=true, quantitative_enabled=true`。F3 的 1.86x 是裸大脑并发实测，
> 完整应用级并发尚未单独矩阵验证。

明确不默认叠加：

- GEXA/GQRA 只接**唯一答案/可确定性运算**，生成型请求必须 miss 回退；
- AEB 与前缀缓存/CASC 冲突（查询相关选块破坏前缀稳定性）；
- PPKS 与 `parallel>1` / `slot_scheduler` 互斥（快照固定 slot 0）；
- GTB 用于一次性独立长文档时 mean 变差，回退 CGW；
- 投机解码、GPU Laya、全量决策点、head+tail 状态、多探针同行均已实测负收益/安全翻转。

## 快速开始

### 环境要求

- Python >= 3.11；包管理使用 **uv**
- Windows / Linux；可选 NVIDIA GPU（CUDA 版 llama-server）
- 模型资产不入仓，由 `jev fetch` 管理

### 安装与自检

```bash
uv sync

# 拉取真实资产：Laya ONNX / Qwen GGUF / llama-server / kev
uv run jev fetch --all

# 环境与资产体检
uv run jev doctor

# 启动双协议服务（默认 127.0.0.1:8300）
uv run jev serve

# 仅小脑决策服务（不拉起大脑）
uv run jev serve --no-brain
```

`jev serve` 启动后：

| 端点 | 说明 |
| --- | --- |
| `POST /v1/systemone` | 小脑类型化决策（Choice / Noul / Score） |
| `POST /v1/chat/completions` | OpenAI Chat + SSE，快慢协同 |
| `GET /health` | 组件就绪、熔断、uptime、supervisor 状态 |
| `GET /docs` | FastAPI 自动文档 |

### 使用生产档

```toml
# configs/jev.toml
[portfolio]
profile = "configs/profiles/latency-best-gpu-grounded.toml"
```

```bash
uv run jev serve
```

无 GPU 时改用 `configs/profiles/latency-best.toml`；并发服务改用 `latency-best-gpu-throughput.toml`。

## API 与 SDK

### Chat Completions

```bash
curl -s http://127.0.0.1:8300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "请阅读文档并回答问题……"}],
    "max_tokens": 128,
    "temperature": 0.0
  }'
```

流式：

```bash
curl -N http://127.0.0.1:8300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}],"stream":true}'
```

响应头携带级联元信息（节选）：

| 响应头 | 含义 |
| --- | --- |
| `X-Jev-Route` | `direct` / `light` / `full` |
| `X-Jev-Decisions` | 本次 active 决策数 |
| `X-Jev-Extract` | `hit:<kind>` / `miss:<reason>` |
| `X-Jev-Tier` / `X-Jev-Tier-Reason` | 双脑分派与升级原因 |
| `X-Jev-Prefix-Cache` | `hit:<cache_n>` / `miss` |
| `X-Jev-KV-Snapshot` | `restore` / `save` / `miss` / `error` |
| `X-Jev-Decision-Mode` | `fast` 时表示跳过 shadow/POST |
| `X-Jev-Stable-Compression` | CASC 压缩比例/状态 |
| `X-Jev-U` | 选择器效用 |

### Python SDK

```python
from jev.sdk import JevClient

with JevClient("http://127.0.0.1:8300") as client:
    # 类型化三问
    decision = client.decide(
        state="用户请求……",
        choices={"yes": "需要生成", "no": "无需生成"},
        instructions="该请求是否需要生成新文本？",
    )
    print(decision.choice, decision.confidence)

    result = client.chat([
        {"role": "user", "content": "总结这份文档"}
    ], max_tokens=128)
    print(result.content, result.route, result.usage)
```

SDK 同时提供异步方法（`adecide` / `ajudge` / `arate` / `achat`）与类型化结果对象。

### MCP Server

```bash
uv run jev mcp --url http://127.0.0.1:8300
```

## 配置

单源配置：`configs/jev.toml`。核心段落：

```toml
[server]
host = "127.0.0.1"
port = 8300

[cerebellum]
default_backend = "laya_onnx"        # laya_onnx | kev

[extractive]
enabled = false                      # AR020 GEXA
quantitative_enabled = false         # AR021 GQRA

[cerebrum]
gpu_layers = 0                       # 0=CPU；99=尽量 offload
parallel = 1                         # AR022；>1 会分割 ctx
slot_scheduler_enabled = false       # 文档亲和 id_slot
kv_snapshot_dir = ""                 # AR019；空=关闭；需 parallel=1
tier_enabled = false                 # AR018 GTB

[runtime]
prefix_cache = false                 # AR007
session_affinity = false
stable_compression = false           # AR009 CASC
decision_shadow = true
decision_post = true
decision_permutations = 0
decision_staged = false
decision_staged_minimal = false
decision_long_context_tokens = 0
decision_long_context_gate_windows = []

[portfolio]
profile = ""                         # 指向生产 profile
```

约束：

- `slot_scheduler_enabled=true` 需要 `parallel>=2`；与 `kv_snapshot_dir` 互斥；
- KV 快照需要 `parallel=1`，并让 llama-server 使用 `--slots --slot-save-path`；
- `parallel=N` 会把 `ctx_size` 分给 N 个 slot；
- 非法组合与越界配置会 `fail-fast`，不静默降级。

## 实验与复现

所有实验均为**真实后端**。完整命令见 [REPRODUCTION.md](REPRODUCTION.md) 与 [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)。

```bash
# 单元测试（无需资产）
uv run pytest tests/unit -q
uv run ruff check .

# 真实资产集成 / 长测
uv run pytest -m assets
uv run pytest -m "assets and slow"

# 通用矩阵
uv run jev matrix --config configs/experiments/ar005.toml --limit 6
uv run jev matrix --config configs/experiments/ar005.toml
```

按能力选择实验：

| 能力 | 实验配置 | 关键产物 |
| --- | --- | --- |
| 风险门 / JCBA | `ar005.toml`、`needles.toml`、`combos.toml` | `risk.json` / `admission.json` |
| 前缀 KV / 会话 | `prefix-cache.toml`、`prefix-cache-gpu.toml` | prefill/cache_n |
| CASC 压缩 | `casc-cpu.toml`、`casc-final-gpu.toml` | tokens / 压缩率 |
| LADS / ADB / CADP / RDMP | `lads.toml`、`adb*.toml`、`cadp*.toml`、`rdmp*.toml` | 决策数 / 路由 |
| LCPT / AGW / CGW | `lcpt*.toml`、`agw*.toml`、`cgw*.toml` | 门控链 / P50 |
| GPU 放置 | `ar016-gpu-cpu.toml`、`ar016-gpu-offload.toml` | prefill/decode |
| 双脑 GTB | `gtb.toml`、`gtb-casc.toml`、`gtb-needles*.toml` | small/escalate |
| 抽取 / 数值 | `extractive*.toml`、`quantitative.toml` | hit 率 / tokens |
| 冷启动 | `scripts/bench_ppks.py`、`ppks.toml` | 首请求延迟 |
| 并发 | `scripts/bench_concurrency.py` | wall / req/s |
| 服务器 7B/14B | `server-{7b,14b}-*.toml` | 本机未跑；按服务器显存执行 |

矩阵产物在 `benchmarks/results/<run>/`：

- `results.json`：逐题 route / quality / tokens / 延迟 / 响应头 / utility；
- `report.md`：汇总与 `adopt / rollback` 结论；
- `profile.toml`：可回灌配置；
- `progress.json`：断点进度；
- `risk.json` / `admission.json`：Conformal 校准（样本充足时）。

> 选择器无正效用下界或质量下降时输出 `rollback`，**不覆盖**默认 profile。

## 关键结果

> 环境：Quadro RTX 5000 16GB（限频 645MHz）/ Xeon Gold 6234（8C/16T）/ Windows 10。
> 绝对延迟受本机 GPU 限频与外部争用影响；以下为同轮真实对照，详见 [docs/RESULTS.md](docs/RESULTS.md)。

| 场景 | 方案 | 指标 | 对照 |
| --- | --- | --- | --- |
| 共享文档抽取 | GEXA | P50 **175ms**、tokens **0**、quality 1.0 | CGW 1.101s |
| 长文档 needles 抽取 | GEXA | P50 **3.7ms**、tokens **0** | CGW 13.089s |
| 多记录数值推理 | GQRA | P50 **3.8ms**、tokens **0**、quality **1.0** | B1 36.6s / 0.364 |
| 交互 miss 路径 | GTB_CASC_FAST + GPU | P50 **0.61–0.72s**，quality 1.0 | B1 36–52s |
| 平均/独立文档 miss | CGW_CASC_FAST + GPU | P50 **1.00–1.49s**，tokens 1079 | B1 2662 tokens |
| 共享文档压缩 | CASC + prefix KV | tokens 2662→**1079（−59.5%）**，cache 11/12 | B1 cache 0/12 |
| 冷启动首请求 | PPKS | 6.13s→**0.88s（−85.7%）** | 无快照冷重启 |
| 并发 4 | parallel=4 | 吞吐 **1.86x**，quality 1.0 | parallel=1 |
| 前缀复用 | prefix KV | prefill 8202→927ms（**8.8×**） | 无缓存 |

负结果同样保留：AR008 投机解码、GPU Laya、全量决策点、head+tail 状态窗口等均因**无稳定收益或安全翻转**回退。

## AR 状态总览

| AR | 主题 | 状态 | 最终栈角色 |
| --- | --- | --- | --- |
| AR001 | 双系统骨架 / 双协议 | 已归档 | 基础 |
| AR002 | 能力门控 / 验证器 / 草稿 | 已交付 | 门控保留，verifier/draft 关 |
| AR003 | 运行时智能：缓存 / 记忆 / prefill | 已交付主体 | 语义缓存可选；其余默认关 |
| AR004 | 学习与复现基建 | 工具链 | 不进请求路径 |
| AR005 | Conformal / JCBA / 选择回退 | 已交付 | 风险门可选；JCBA 由 CASC 覆盖 |
| AR006 | AEB 证据预算 + 自愈 | 已交付 | 一次性长文专项 |
| AR007 | 前缀 KV + 会话亲和 | 已交付 | 保留 |
| AR008 | 异构投机解码 | 实验后回退 | 默认关 |
| AR009 | CASC 稳定压缩 | 已交付 | 保留 |
| AR010 | LADS 决策调度 + POST 修复 | 已交付 | 保留 |
| AR011 | ADB 决策预算 | 已交付 | perm=1 保留 |
| AR012 | CADP 分阶段决策 | 已交付 | 保留 |
| AR013 | RDMP 最小探针 | 已交付 | 保留 |
| AR014 | LCPT 长文 int8 | 已交付 | 保留 |
| AR015 | AGW 自适应窗口 | 已交付 | 作为 CGW 输入 |
| AR016 | GPU 服务放置 | 已交付 | 有 GPU 保留 |
| AR017 | CGW 级联窗口 | 已交付 | F2 miss 档 |
| AR018 | GTB 双脑接地 | 已交付 | F1 交互档 |
| AR019 | PPKS 持久化快照 | 已归档 | F4 冷启动 |
| AR020 | GEXA 抽取快答 | 已归档 | 公共前栈 |
| AR021 | GQRA 数值推理 | 已归档 | 公共前栈 |
| AR022 | PSS 并行槽吞吐 | 已归档 | F3 并发 |

## 测试与质量

```bash
uv run pytest tests/unit -q                 # 纯逻辑，无需资产：780 passed
uv run pytest -m assets                     # 真实资产集成
uv run pytest -m "assets and slow"          # 端到端 / 长耗时
uv run pytest --cov                         # kernel 目标 >=85%
uv run ruff check .                         # 零告警
```

当前基线：

- 本机全量 **780 passed**；`ruff` 零告警；
- Gitee 干净浅克隆 **777 passed, 3 skipped**（3 个仅需本机 Laya 资产）；
- `kernel/extract/grounded.py` 覆盖率 91–92%；`kernel` 约 92%；
- 测试纪律：**严禁 mock 后端**；纯逻辑单测不触后端；真实后端用例打 `@pytest.mark.assets`。

## 目录结构

```text
src/jev/
  kernel/       小脑/大脑/级联/运行时/缓存/prefill/台账/extract
  server/       FastAPI 双协议 + /health
  sdk/          JevClient、类型化结果、决策点编程注册
  bench/        bench + matrix + selector + 数据集
  cli/          serve/fetch/doctor/prepare-laya-int8/bench/matrix/distill/mcp
  fetch/        资产清单、sha256、镜像、断点续传
  distill/      飞轮/校准/LoRA
  mcp_server/   MCP 工具
configs/
  jev.toml              单源配置
  assets.toml           模型资产清单
  decision-points.toml  18 决策点声明
  quality-set.yaml      质量金标
  profiles/             生产 profile
  experiments/          可复现实验矩阵
tests/unit|integration
specs/                  SDD 需求/设计/任务/归档
benchmarks/results/     真实矩阵产物
scripts/                PPKS / 并发等专项脚本
```

## 文档地图

| 文档 | 内容 |
| --- | --- |
| [docs/PORTFOLIO.md](docs/PORTFOLIO.md) | 全部 AR 组合、冲突、最终综合最优方案 |
| [docs/RESULTS.md](docs/RESULTS.md) | 真实实验数据与负结果 |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | 实验步骤与参数 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构与各 AR 设计要点 |
| [REPRODUCTION.md](REPRODUCTION.md) | 从零复现、服务器 7B/14B |
| [specs/component-detail-design/jev_spec.md](specs/component-detail-design/jev_spec.md) | 组件详细设计 |
| [specs/archive/](specs/archive/) | 已归档 AR 的 srs/design/tasks/st-cases |
| [AGENTS.md](AGENTS.md) | 开发规约与测试纪律 |

## 诚实边界

- 7B/14B 仅提供配置与复现步骤，**未在本机实测，禁止外推指标**；
- AR022 的 1.86x 是**裸大脑并发**结果；`parallel=4 + GTB + GEXA/GQRA + CASC` 的完整应用级并发尚未单独矩阵验证；
- 本机 GPU 被硬限制在 645MHz，绝对延迟不代表正常服务器 GPU；
- GEXA/GQRA 只覆盖可确定性回答的问题，不接管生成/解释/歧义请求；
- 所有新增叠加必须按选择器复验；无正下界或质量下降即回退，不更新默认 profile。

## 许可

MIT
