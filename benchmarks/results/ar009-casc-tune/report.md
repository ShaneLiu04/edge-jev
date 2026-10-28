# Jev 实验矩阵报告

- run: ar009-casc-tune-20261028-192002
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 6

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C40 | 1.000 | 24.0 | 3771.0 | 52904 | 1927 | 6.8 | 0.00 | - | 0 | 6 | 2267 | 0 |
| C50 | 1.000 | 24.0 | 4034.0 | 52702 | 1825 | 6.7 | 0.00 | - | 0 | 6 | 2282 | 0 |
| C60 | 1.000 | 24.0 | 4298.0 | 52954 | 1856 | 6.7 | 0.00 | - | 0 | 6 | 2282 | 0 |

## 选择结果

- decision: **rollback**
- chosen_arm: C40
- baseline: C40
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
      "C40",
      "C50",
      "C60"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\casc-tune.toml"
    ],
    "run": "ar009-casc-tune-20261028-192002"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
