# Jev 实验矩阵报告

- run: ar014-lcpt-20261030-133457
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 44359 | 8279 | 7.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| RDMP | 1.000 | 5.0 | 2662.0 | 22996 | 8276 | 7.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| LCPT | 1.000 | 5.0 | 2662.0 | 19455 | 8529 | 6.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: LCPT
- baseline: B1
- delta_ci: (0.25233521543178544, 0.28273045198362495)
- reason: LCPT 效用下界 0.2523 > 0

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
      "RDMP",
      "LCPT"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\lcpt.toml"
    ],
    "run": "ar014-lcpt-20261030-133457"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
