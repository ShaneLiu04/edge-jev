# Jev 实验矩阵报告

- run: ar006-needles-combos-gpu-20261029-145415
- lambda_cost: 0.15
- lambda_latency: 0.0
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 0.833 | 8.1 | 2921.3 | 53157 | 6230 | 4.6 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| P60 | 0.833 | 8.1 | 1896.7 | 50777 | 4150 | 4.5 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| AEB | 0.833 | 8.1 | 1589.7 | 58635 | 3307 | 4.6 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: AEB
- baseline: B1
- delta_ci: (0.04810684721076441, 0.10031007310898077)
- reason: AEB 效用下界 0.0481 > 0

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
    "run": "ar006-needles-combos-gpu-20261029-145415"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
