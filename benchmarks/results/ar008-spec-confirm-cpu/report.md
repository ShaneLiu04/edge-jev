# Jev 实验矩阵报告

- run: ar008-spec-confirm-cpu-20261027-235551
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 4

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 192.0 | 50.0 | 33433 | 990 | 8.1 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| S2 | 1.000 | 192.0 | 50.0 | 43655 | 1498 | 5.8 | 0.62 | 1.0 | 0 | 0 | 0 | 0 |
| S4 | 1.000 | 192.0 | 50.0 | 47976 | 1404 | 5.0 | 0.47 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **rollback**
- chosen_arm: B1
- baseline: B1
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
      "B1",
      "S2",
      "S4"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\spec-confirm-cpu.toml"
    ],
    "run": "ar008-spec-confirm-cpu-20261027-235551"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
