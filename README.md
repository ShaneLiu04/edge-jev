# Jev（捷思）

> 端侧快慢双系统 AI 运行时 — 本地类型化决策小脑（System 1）+ 本地 LLM 大脑（System 2），全本地、可审计、可自进化。

## 为什么是 Jev

GitHub 全量调研（见 [docs/research/github-survey.md](docs/research/github-survey.md)）确认：「小脑 + 大脑」双系统方向已有先行者（DuoMind、hermes-jev-skills、ollaya、von），但**小脑与大脑全部本地化的端侧双系统运行时仍是空白**（DuoMind 的小脑依赖云端 API）。Jev 填补这一空白：

- **全本地**：小脑（Laya ONNX / kev-0.8B）与大脑（llama-server + Qwen2.5 GGUF）都跑在本机，零云端依赖，隐私与离线能力完备
- **快慢协同**：18 个决策点（PRE9/MID4/POST5）置信门控级联——结构化决策直答（P50 < 50ms）、简单生成走轻量路径、复杂推理才唤醒大脑
- **可审计**：全决策台账（JSONL），active/shadow/off 三态支持安全灰度
- **可自进化**：S2→S1 蒸馏飞轮——从台账抽取大脑轨迹，校准/微调小脑，复测不回退才发布

## 架构

```
┌─ 薄壳 ──────────────────────────────────────────────┐
│  FastAPI Server   Python SDK   Typer CLI   MCP Server │
├─ 进程内内核 Kernel ─────────────────────────────────┤
│  决策点注册表(18)   级联引擎(三路径)   熔断器        │
│  小脑: Laya ONNX(进程内,默认) / kev-0.8B(子进程)      │
│  大脑: llama-server(子进程) + Qwen2.5 GGUF           │
│  决策台账(JSONL 队列写)                              │
├─ 基础设施 ─────────────────────────────────────────┤
│  AssetFetcher   Distiller(蒸馏飞轮)   Bench(基准)    │
└─────────────────────────────────────────────────────┘
```

## 快速开始

```bash
# 1. 安装（uv 管理）
uv sync

# 2. 拉取模型资产（Laya ONNX / Qwen GGUF / llama-server / kev）
uv run jev fetch --all

# 3. 体检
uv run jev doctor

# 4. 启动双端点服务
uv run jev serve
```

## 双协议端点

| 端点 | 协议 | 说明 |
| --- | --- | --- |
| `POST /v1/systemone` | TypeSafe 线协议 | 小脑类型化决策：Choice（选项概率）/ Noul（0–1 概率）/ Score（等级+置信度），生态 SDK 换 base URL 即可切换 |
| `POST /v1/chat/completions` | OpenAI Chat 兼容 | 大脑 + 快慢协同；响应头 `X-Jev-Route` 标记级联路径（direct/light/full） |

## CLI 套件

```bash
jev serve [--daemon]    # 启动服务（可选守护）
jev fetch [--all]       # 模型资产管理：sha256 校验 / 断点续传 / 镜像回退
jev doctor              # 体检：GPU/内存/资产/端口
jev bench [--full]      # 评测基准：命中率/延迟/熔断/台账开销/蒸馏收益
jev distill [--lora]    # 蒸馏飞轮：轨迹→数据集→校准（可选 LoRA）→复测
```

## 开发

```bash
uv run pytest                    # 单元测试（纯逻辑）
uv run pytest -m assets          # 集成测试（需先 jev fetch --all）
uv run pytest -m "assets and slow"   # 端到端
uv run ruff check .              # lint
```

详见 [AGENTS.md](AGENTS.md)（开发规约）与 [specs/](specs/)（SDD 需求/设计/任务文档）。

## 路线图

- **M1** 双系统骨架：双端点 / 双小脑后端 / 大脑子进程 / 决策点框架 / 级联 / 熔断 / fetch
- **M2** 完整决策面：台账 / CLI / SDK / MCP / 18 点全链路 + shadow mode
- **M3** 蒸馏飞轮与评测基准

后续（Out of Scope 本版）：车机 MNN 适配、中文小脑、云大脑变体、多模态小脑、投机解码、C++/Rust 移植。

## 许可

MIT
