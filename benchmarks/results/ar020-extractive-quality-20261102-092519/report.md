# Jev 实验矩阵报告

- run: ar020-extractive-quality-20261102-092519
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 30

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CGW_CASC_FAST | 0.207 | 9.4 | 77.3 | 1581 | 69 | 2.3 | 0.00 | - | 0 | 12 | 21 | 0 |
| GEXA_CGW_CASC_FAST | 0.207 | 9.4 | 77.3 | 1547 | 68 | 2.3 | 0.00 | - | 0 | 12 | 21 | 0 |

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
      "GEXA_CGW_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\extractive-quality.toml"
    ],
    "run": "ar020-extractive-quality-20261102-092519"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
