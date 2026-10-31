# Jev 实验矩阵报告

- run: ar018-gtb-20261031-102551
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 43861 | 5960 | 6.9 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| AGW | 1.000 | 5.0 | 2662.0 | 16528 | 5968 | 7.1 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CGW | 1.000 | 5.0 | 2662.0 | 13080 | 5905 | 7.0 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| GTB | 1.000 | 5.0 | 2662.0 | 10363 | 4414 | 12.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CGW
- baseline: B1
- delta_ci: (0.3470887226924873, 0.3525170800585731)
- reason: CGW 效用下界 0.3471 > 0

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
      "AGW",
      "CGW",
      "GTB"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\gtb.toml"
    ],
    "run": "ar018-gtb-20261031-102551"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
