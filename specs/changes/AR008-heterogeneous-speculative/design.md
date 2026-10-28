# [AR008] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR008 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-27 |
| 状态 | Verified（rollback，默认关闭） |

## 1. 设计目标

1. 默认关闭；关闭时与 AR007 配置/行为一致。
2. 复用 llama.cpp 投机解码，不重造验证器；Jev 负责**配置、观测、选择、回退**。
3. 投机收益必须由真实接受率与 tps 证明，不以单次运行或估算代替。
4. GPU 是否真正启用必须可验证（设备列表 + 运行库 + 时钟）。

## 2. 数据流

```text
[cerebrum.spec] enabled ──► spec_server_args ──► llama-server
                                                   │ --spec-draft-model ...
request ──► /v1/chat/completions ──► response.timings
                                                   │ draft_n / accepted
                                                   ▼
                                   X-Jev-Spec / X-Jev-Decode-Tps
                                                   │
                                        last_runtime.spec + matrix 指标
```

## 3. 模块设计

### 3.1 配置与参数

`configs/jev.toml`：

```toml
[cerebrum.spec]
enabled = false
draft_model = "models/qwen/qwen2.5-0.5b-instruct-q4_k_m.gguf"
type = "draft-simple"
draft_n_max = 8
draft_gpu_layers = 0
draft_threads = 8
sampling = "greedy"
```

`kernel/cerebrum/llama_server.py` 的 `spec_server_args(...)` 生成参数；
`kernel.py::_build_brain_manager` 读取并合并；draft 路径相对仓库根解析，
缺失抛 `JevAssetError`。

### 3.2 响应与台账

`server/chat.py` 从 `timings` 提取：

- `draft_n = timings.draft_n`；
- `accepted = timings.draft_n_accepted`；
- `decode_tps = timings.predicted_per_second`；
- 头：`X-Jev-Spec: <accepted>/<draft>`，`X-Jev-Decode-Tps: <tps>`。

未启用时不输出头，`last_runtime.spec` 不存在。

### 3.3 矩阵与组合

`MatrixConfig` 支持 `[overlays.<ARM>.cerebrum.spec]`，每 arm 独立重启大脑；
`_spawn_brain(config, arm_cfg)` 读取该 arm 的 spec 配置并调用 `spec_server_args`。
汇总新增：

- `mean_decode_tps`
- `spec_accept_rate`
- `outputs_identical`（相对 B1 同题同 pass 的答案）

### 3.4 数据集

`bench/generation.py`：

- `build_long_generation`：中文短 prompt + 长输出；
- `build_long_generation_en`：英文短 prompt + 长输出，gold 为
  `(?i)(hit|reuse)` 内容守卫（避免小模型不遵守“结尾输出校验码”导致全 0 质量）。

### 3.5 实验设计

| 配置 | 目的 | 臂 |
|------|------|----|
| `spec-short.toml` | 中文长输出 | B1 / SPEC |
| `spec-short-en.toml` | 英文长输出 | B1 / SPEC |
| `spec-tuning-cpu.toml` | draft 长度扫描 | B1 / S2 / S4 / S8 |
| `spec-confirm-cpu.toml` | 复现确认 | B1 / S2 |
| `spec-decode.toml` | 与缓存组合 | B1 / SPEC / CACHE / CACHE_SPEC |

选择规则：配对 bootstrap + LOO，CI 下界 >0 且质量不降才 adopt，否则 rollback。

### 3.6 测试策略

- 单测：参数生成、缺资产异常、头解析、指标汇总、generation 数据集；
- 真实集成：`tests/integration/test_spec_decode_real.py`（真实 draft 资产，
  断言 `draft_n>0`，打 `assets`）；
- 无 mock；真实实验数据落 `benchmarks/results/`。

## 4. 环境结论（关键）

真实排查发现：

1. `llama-server --list-devices` 最初返回 `(none)`，`ggml-cuda.dll` 依赖的
   `cudart64_12.dll` / `cublas64_12.dll` / `cublasLt64_12.dll` 缺失；
   因此 **AR005–AR007 的历史主表均为 CPU-only**。
2. 安装 CUDA 12.4 redistributable 后可见 `CUDA0: Quadro RTX 5000`，
   但 GPU 被硬限制在 **645 MHz / ~32 W**，无权限改时钟；
   `llama-bench` 实测 `tg64` GPU **5.06 t/s** vs CPU **9.47 t/s**。
3. 因此本机 GPU target 反而更慢；异构 spec 的“target GPU”假设不成立。
   结论按 CPU-only 与 capped-GPU 两组分别记录，不混用。

## 5. 回退设计

- 默认 `enabled=false`；
- 实验无正下界 → `rollback`，profile 不写入；
- 文档与结果显式标注“未超越 B1”，不宣称投机收益；
- 保留代码/配置/指标，供正常 GPU 主机复现。
