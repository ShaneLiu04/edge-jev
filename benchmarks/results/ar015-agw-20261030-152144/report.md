# Jev 实验矩阵报告

- run: ar015-agw-20261030-152144
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 47942 | 9028 | 6.9 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| RDMP | 1.000 | 5.0 | 2662.0 | 24751 | 8954 | 6.8 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| LCPT512 | 1.000 | 5.0 | 2662.0 | 19996 | 8853 | 7.1 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| AGW384 | 1.000 | 5.0 | 2662.0 | 20200 | 9669 | 7.1 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: LCPT512
- baseline: B1
- delta_ci: (0.2889966711725793, 0.29529249442023153)
- reason: LCPT512 效用下界 0.2890 > 0

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
      "LCPT512",
      "AGW384"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\agw.toml"
    ],
    "run": "ar015-agw-20261030-152144"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
