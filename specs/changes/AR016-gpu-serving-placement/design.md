# [AR016] 设计说明书

## 1. 设计

- `[cerebrum] gpu_layers`（int，默认 0）→ `_build_brain_manager` 追加
  `--n-gpu-layers <N>`，与矩阵 `gpu_layers` 语义一致。
- 不改变小脑/决策路径；GPU 只影响大脑 llama-server 放置。
- 无 GPU 或显存不足：用户保持 0；启动失败由现有 supervisor/health 报错。

## 2. 模块改动

| 文件 | 改动 |
|------|------|
| `kernel/kernel.py` | 读取 `gpu_layers`，追加 llama-server 参数 |
| `configs/jev.toml` | `gpu_layers = 0` 显式项 |
| `configs/profiles/latency-best-gpu.toml` | GPU 部署档（含 AGW/CASC） |
| `configs/experiments/ar016-gpu-{cpu,offload}.toml` | 真实对照 |
| `docs/*` / `REPRODUCTION.md` | 放置说明、7B/14B 启动参数 |

## 3. 测试

- 单测：`_build_brain_manager` 的 extra_args 含期望 `--n-gpu-layers`；
  非法负值拒绝。
- 真实矩阵：同一 AGW_CASC_FAST，gpu_layers 0 vs 99；断言质量/输出一致，
  记录延迟与 prefill。

## 4. 回退

- `gpu_layers=0` 即 CPU；删除 GPU profile 不影响默认配置。
