# Jev（捷思）— 端侧快慢双系统 AI 运行时

> **System 1（小脑）+ System 2（大脑）** 的全本地双系统运行时：类型化决策、级联路由、风险可控降级、KV/前缀复用、稀疏 prefill、语义缓存与可复现实验矩阵。

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 为什么是 Jev

大多数「小模型 + 大模型」方案停留在**云端路由**或**单点缓存**。Jev 把「何时不用大模型、用什么上下文、能否复用 KV、结果能否复用」做成一个**可校准、可回退的运行时**：

- **全本地**：Laya ONNX（进程内）+ llama-server/Qwen GGUF；资产可校验、可离线。
- **快慢协同**：18 个决策点（PRE 9 / MID 4 / POST 5），三路径 `direct / light / full`，预算与熔断。
- **AR003 运行时智能**：不确定性三带、大脑自证验证器、前缀指纹、稀疏 prefill、语义缓存、长程记忆。
- **AR005 风险可控编排**：Conformal 风险门、Conformal 缓存准入、联合上下文预算（JCBA）、方案矩阵与**自动回退**。
- **可复现**：`jev matrix` 在真实后端上跑 B0/B1/B2/AR005，产出原始 JSON、报告、环境指纹；7B/14B 服务器档完整。

> 核心原则：**零 mock、真实指标、失败即失败**。任何缺失资产或不可用后端都会明确报错，不产生伪造结果。

## 架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│  薄壳：FastAPI Server · Python SDK · Typer CLI · MCP Server          │
├─────────────────────────────────────────────────────────────────────┤
│  Kernel（组合根）                                                    │
│  ┌───────────────┐  ┌────────────────────────────┐  ┌────────────┐  │
│  │ CascadeEngine │  │ Runtime (AR003/AR005)      │  │ Ledger     │  │
│  │ route/v2      │  │ conformal · JCBA · prompt  │  │ JSONL      │  │
│  └──────┬────────┘  └────────────┬───────────────┘  └────────────┘  │
│         │                        │                                   │
│  ┌──────▼────────┐      ┌────────▼─────────┐      ┌──────────────┐  │
│  │ 小脑 Cerebellum│      │ 大脑 Cerebrum    │      │ Cache/Memory │  │
│  │ Laya ONNX/kev │      │ llama-server     │      │ exact/semantic│ │
│  └───────────────┘      └──────────────────┘      └──────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│  基础设施：AssetFetcher · Bench/Matrix · Distiller                   │
└─────────────────────────────────────────────────────────────────────┘
```

依赖方向（强制）：`server/sdk/cli/mcp_server → kernel → 子模块`；薄壳不得绕过 Kernel 直接访问小脑/大脑。

## 快速开始

```bash
# 1) 安装（uv）
uv sync

# 2) 拉取真实资产（Laya ONNX / Qwen GGUF / llama-server / kev）
uv run jev fetch --all

# 3) 自检
uv run jev doctor

# 4) 启动双协议服务
uv run jev serve            # /v1/systemone + /v1/chat/completions + /health
```

协议：

| 端点 | 协议 | 说明 |
| --- | --- | --- |
| `POST /v1/systemone` | TypeSafe 严格 | 小脑决策（Choice / Noul / Score） |
| `POST /v1/chat/completions` | OpenAI Chat + SSE | 快慢协同；响应头 `X-Jev-Route` 等 |
| `GET /health` | JSON | 组件就绪与熔断状态 |

## 复现实验（推荐路径）

```bash
# 单元测试（纯逻辑，无需资产）
uv run pytest tests/unit -q
uv run ruff check .

# 真实后端快速冒烟（需要资产；CPU 可跑，先核对 CUDA 设备，见 REPRODUCTION §1.1）
uv run jev matrix --config configs/experiments/ar005.toml --limit 6

# 完整矩阵（30 题 × B0/B1/B2/AR005；真实 Laya + llama-server + embedding）
uv run jev matrix --config configs/experiments/ar005.toml

# 采纳结果后写入 profile（仅当选择器判定有正效用下界）
uv run jev matrix --config configs/experiments/ar005.toml --write-profile

# AR006 长上下文组合（B1 / 固定压缩 / 证据+自适应预算 / +JCBA）
uv run jev matrix --config configs/experiments/combos.toml

# AR006 短问答回归（验证不在短 prompt 上引入变化）
uv run jev matrix --config configs/experiments/quality-combos.toml

# AR007 前缀 KV 复用（同文档多问；真实 cache_n/prefill_ms）
uv run jev matrix --config configs/experiments/prefix-cache.toml

# AR008 投机解码（需 0.5B draft；本机真实结论 rollback，默认关闭）
uv run jev matrix --config configs/experiments/spec-confirm-cpu.toml

# AR009 CASC：稳定压缩 + 前缀缓存（CPU 解码 + GPU embedding）
uv run jev matrix --config configs/experiments/casc-final-gpu.toml

# AR010 LADS：完整决策 / 快速决策 / 前缀缓存组合
uv run jev matrix --config configs/experiments/lads.toml

# AR011 ADB：决策计算预算 + 联合最优（推荐先跑隔离实验）
uv run jev matrix --config configs/experiments/adb.toml
uv run jev matrix --config configs/experiments/adb-casc.toml

# AR012 CADP：消费者感知 + 路由分阶段
uv run jev matrix --config configs/experiments/cadp.toml
uv run jev matrix --config configs/experiments/cadp-casc.toml

# AR013 RDMP：最小 wave1 + 保守确认
uv run jev matrix --config configs/experiments/rdmp.toml
uv run jev matrix --config configs/experiments/rdmp-casc.toml

# AR014 LCPT-RFG：长上下文 int8 精度分层
uv run jev prepare-laya-int8          # 生成 models/laya/laya_int8.onnx（~427MB，不入仓）
uv run jev matrix --config configs/experiments/lcpt.toml
uv run jev matrix --config configs/experiments/lcpt-casc.toml
uv run jev matrix --config configs/experiments/lcpt-needles.toml

# AR015 AGW：自适应门控窗口
uv run jev matrix --config configs/experiments/agw.toml
uv run jev matrix --config configs/experiments/agw-casc.toml

# AR016 GPU 服务放置（GPU offload）
uv run jev matrix --config configs/experiments/ar016-gpu-cpu.toml
uv run jev matrix --config configs/experiments/ar016-gpu-offload.toml
# 有 GPU 部署档：configs/profiles/latency-best-gpu.toml

# AR017 CGW：级联门控窗口（当前最优）
uv run jev matrix --config configs/experiments/cgw.toml
uv run jev matrix --config configs/experiments/cgw-casc.toml

# AR018 GTB: grounded tiered brain (P50 profile; requires 0.5B asset)
uv run jev matrix --config configs/experiments/gtb.toml
uv run jev matrix --config configs/experiments/gtb-casc.toml
# mixed/unique-document workloads: use configs/profiles/latency-best.toml (CGW)
```

产物目录 `benchmarks/results/<run>/`：

- `results.json`：逐题原始指标（route、quality、tokens、延迟、响应头、U）
- `report.md`：汇总与选择结论（adopt / rollback）
- `profile.toml`：可回灌的运行时配置
- `risk.json` / `admission.json`：Conformal 校准（样本充足时生成）

> **自动回退**：若候选组合相对上一最优没有正效用下界（或质量下降），选择器输出 `rollback`，不覆盖默认 profile。

详见 [REPRODUCTION.md](REPRODUCTION.md)、[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) 与真实结果 [docs/RESULTS.md](docs/RESULTS.md)。
AR 组合与最优配置（避免盲目叠加）见 [docs/PORTFOLIO.md](docs/PORTFOLIO.md)。

## 服务器大模型实验（7B / 14B）

本机默认 1.5B 真跑；7B/14B 通过**外部真实服务**接入，配置见：

- `configs/experiments/server-7b.toml`
- `configs/experiments/server-14b.toml`（本机不执行，标 `deferred`）

启动示例（GPU 服务器）：

```bash
# 大脑（7B）
llama-server -m /models/qwen2.5-7b-instruct-q4_k_m.gguf \
  --host 0.0.0.0 --port 8388 --ctx-size 8192 --n-gpu-layers 99 \
  --parallel 4 --slot-prompt-similarity 0.5

# Embedding（独立真实实例；语义缓存/Conformal 准入需要）
llama-server -m /models/bge-m3-q8_0.gguf --host 127.0.0.1 --port 8389 \
  --embeddings --pooling mean --ctx-size 2048 --n-gpu-layers 99

# 矩阵
uv run jev matrix --config configs/experiments/server-7b.toml
```

缺少 14B 资产时，运行必须报 `JevAssetError`/`deferred-server`，**不得**产出指标。

## 关键设计

- **路由**：`direct`（小脑直答模板）/`light`（受限生成 + MID 早停）/`full`（技能注入 + 全量 POST 复核）。
- **风险门**：Conformal Risk Control 选择阈值，给出有限样本质量损失上界；样本不足自动禁用。
- **缓存准入**：用负例控制假命中率，正例保召回；未校准时回退固定 similarity。
- **JCBA**：记忆摘要/草稿/prefill 块统一预算（确定性 0/1 背包），消除组件冲突。
- **选择器**：效用 = 质量 − λ·归一化成本；按题配对 bootstrap CI + 留一策略评估；无增益回退。

## 配置

单源 `configs/jev.toml`；所有 AR005 能力默认关闭：

```toml
[runtime]
risk_control = false
allocator_enabled = false

[cache]
conformal_admission = false

[portfolio]
profile = ""   # 指向 configs/runtime-profile.toml 时启用组合 profile
```

## 测试与质量

```bash
uv run pytest tests/unit -q                 # 纯逻辑（全绿门槛）
uv run pytest -m assets                     # 真实资产集成
uv run pytest -m "assets and slow"          # 端到端（长时）
uv run pytest --cov                         # Kernel 语句覆盖目标 ≥85%
uv run ruff check .
```

## 目录

```text
src/jev/kernel/     小脑/大脑/级联/运行时/缓存/prefill/台账
src/jev/server/     FastAPI 双协议
src/jev/bench/      bench + matrix + selector + dataset
src/jev/cli/        serve/fetch/doctor/bench/matrix/distill
configs/            jev.toml、decision-points.toml、quality-set、experiments
tests/unit|integration
specs/              SDD 需求/设计/任务与归档
```

## 路线图

- **AR001** 双系统骨架（已归档）
- **AR002** 级联质量与能力门控（已交付）
- **AR003** 运行时智能：不确定性/验证器/复用/缓存/记忆（已交付主体）
- **AR004** 学习与复现：飞轮 / 多基线 / LoRA / 大模型矩阵
- **AR005** 风险可控自适应编排：Conformal + JCBA + 选择/回退 + 可复现矩阵
- **AR006** 证据驱动自适应上下文（AEB）+ 大脑/embedding 自愈 + 组合最优与回退
- **AR007** 前缀 KV 复用 + 会话亲和稳定前缀 + AEB/缓存冲突分析与延迟感知选择
- **AR008** 异构投机解码（0.5B draft）实验：真实测量后无稳定收益，默认关闭并回退 B1
- **AR009** CASC：查询无关文档压缩 + 压缩 LRU + 前缀 KV + CPU/GPU 异构放置（token −59.5%~−58.7%，质量 1.0）
- **AR010** LADS：修复 Laya 长 state POST 盲区（保尾部）+ 可配跳过 shadow/POST（CACHE_FAST 端到端 −54.4%）
- **AR011** ADB：Laya 决策计算预算（置换 4→1，applied 100% 一致）+ 联合最优档
  （shared-prefix 49.2s→14.1s，P50 12.1s，tokens 2662→1079，quality 1.0）
- **AR012** CADP：消费者感知 + 路由分阶段决策
  （shared-prefix 51.6s→10.0–10.6s，P50 8.8s，quality 1.0）
- **AR013** RDMP：wave1 最小探针 + 保守确认
  （CADP 6.85s→5.05s，P50 4.33s；CASC 档 tokens 1079，quality 1.0）
- **AR014** LCPT-RFG：超长上下文 int8 reasoning 门控，联合最优
  （RDMP_CACHE_FAST 6.63s→2.53s；LCPT_CASC_FAST P50 2.42s，tokens 1079，quality 1.0）

## 许可

MIT
