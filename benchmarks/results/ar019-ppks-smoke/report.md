# Jev 实验矩阵报告

- run: ar019-ppks-20261031-180703
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 3

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CGW_CASC_FAST | 1.000 | 5.0 | 1079.0 | 905 | 176 | 8.1 | 0.00 | - | 0 | 2 | 1063 | 0 |
| GTB_CASC_FAST | 1.000 | 5.0 | 1079.0 | 1088 | 74 | 15.7 | 0.00 | - | 0 | 2 | 1063 | 0 |

## 选择结果

- decision: **rollback**
- chosen_arm: CGW_CASC_FAST
- baseline: CGW_CASC_FAST
- delta_ci: None
- reason: 候选无显著正下界，回退基线

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
      "CGW_CASC_FAST",
      "GTB_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\ppks.toml"
    ],
    "run": "ar019-ppks-20261031-180703"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
