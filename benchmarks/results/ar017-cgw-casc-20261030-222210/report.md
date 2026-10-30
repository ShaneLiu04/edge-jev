# Jev 实验矩阵报告

- run: ar017-cgw-casc-20261030-222210
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 52644 | 7407 | 5.7 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| AGW_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 1849 | 636 | 7.3 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| AGW_CASC_FAST | 1.000 | 5.0 | 1079.0 | 1658 | 363 | 7.8 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |
| CGW_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 1044 | 665 | 7.1 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| CGW_CASC_FAST | 1.000 | 5.0 | 1079.0 | 1001 | 351 | 7.9 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CGW_CASC_FAST
- baseline: B1
- delta_ci: (0.5309733423651312, 0.6027335158047228)
- reason: CGW_CASC_FAST 效用下界 0.5310 > 0

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
      "AGW_CACHE_FAST",
      "AGW_CASC_FAST",
      "CGW_CACHE_FAST",
      "CGW_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\cgw-casc.toml"
    ],
    "run": "ar017-cgw-casc-20261030-222210"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
