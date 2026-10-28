# Jev 实验矩阵报告

- run: ar009-casc-needles6-20261028-205309
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 6

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 8.7 | 1846.7 | 45808 | 6124 | 7.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CASC | 1.000 | 5.0 | 762.3 | 53263 | 4222 | 8.4 | 0.00 | 0.6667 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CASC
- baseline: B1
- delta_ci: (0.018418441115599416, 0.05624619202909812)
- reason: CASC 效用下界 0.0184 > 0

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
      "CASC"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\casc-needles6.toml"
    ],
    "run": "ar009-casc-needles6-20261028-205309"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
