# Jev 实验矩阵报告

- run: ar006-needles-combos-gpu-20261027-215150
- lambda_cost: 0.15
- lambda_latency: 0.0
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 0.917 | 5.9 | 2457.3 | 50723 | 6773 | 4.0 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| P60 | 0.917 | 5.9 | 1493.2 | 47423 | 3425 | 5.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| AEB | 0.917 | 5.9 | 1204.2 | 65027 | 2558 | 5.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: AEB
- baseline: B1
- delta_ci: (0.057211001725362816, 0.11448289860956055)
- reason: AEB 效用下界 0.0572 > 0

## 校准


## 环境指纹

```json
{
  "python": "3.11.9",
  "versions": {
    "onnxruntime": "1.31.0",
    "httpx": "0.28.1",
    "numpy": "2.4.6",
    "yaml": "6.0.3"
  },
  "platform": "Windows-10-10.0.26200-SP0",
  "machine": "AMD64",
  "extra": {
    "arms": [
      "B1",
      "P60",
      "AEB"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\combos-gpu.toml"
    ],
    "run": "ar006-needles-combos-gpu-20261027-215150"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
