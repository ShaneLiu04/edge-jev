# Jev 实验矩阵报告

- run: ar020-extractive-needles-20261102-091540
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 1839.5 | 30547 | 3837 | 7.7 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CGW_CASC_FAST | 1.000 | 5.0 | 753.6 | 10158 | 1582 | 8.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| GEXA_CGW_CASC_FAST | 1.000 | 0.0 | 0.0 | 4 | 0 | 0.0 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: GEXA_CGW_CASC_FAST
- baseline: B1
- delta_ci: (0.6491228433374244, 0.6508626989412868)
- reason: GEXA_CGW_CASC_FAST 效用下界 0.6491 > 0

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
      "CGW_CASC_FAST",
      "GEXA_CGW_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\extractive-needles.toml"
    ],
    "run": "ar020-extractive-needles-20261102-091540"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
