# Jev 实验矩阵报告

- run: ar010-lads-20261029-120142
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 54389 | 12911 | 6.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| FAST | 1.000 | 5.0 | 2662.0 | 35127 | 12719 | 11.0 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CACHE | 1.000 | 5.0 | 2662.0 | 41790 | 1602 | 6.7 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| CACHE_FAST | 1.000 | 5.0 | 2662.0 | 24311 | 1646 | 6.5 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CACHE_FAST
- baseline: B1
- delta_ci: (0.253912101814651, 0.282493258214229)
- reason: CACHE_FAST 效用下界 0.2539 > 0

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
      "FAST",
      "CACHE",
      "CACHE_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\lads.toml"
    ],
    "run": "ar010-lads-20261029-120142"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
