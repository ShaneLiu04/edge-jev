# Jev 实验矩阵报告

- run: ar015-agw-casc-20261030-154608
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 52388 | 13625 | 6.8 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| LCPT_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 2214 | 1671 | 6.9 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| LCPT_CASC_FAST | 1.000 | 5.0 | 1079.0 | 1984 | 861 | 8.2 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |
| AGW_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 2168 | 1637 | 7.2 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| AGW_CASC_FAST | 1.000 | 5.0 | 1079.0 | 1969 | 843 | 8.5 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: AGW_CASC_FAST
- baseline: B1
- delta_ci: (0.519048081553915, 0.5712676301779039)
- reason: AGW_CASC_FAST 效用下界 0.5190 > 0

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
      "LCPT_CACHE_FAST",
      "LCPT_CASC_FAST",
      "AGW_CACHE_FAST",
      "AGW_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\agw-casc.toml"
    ],
    "run": "ar015-agw-casc-20261030-154608"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
