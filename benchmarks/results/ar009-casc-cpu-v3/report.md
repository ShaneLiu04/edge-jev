# Jev 实验矩阵报告

- run: ar009-casc-cpu-20261028-183950
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 24.0 | 5354.0 | 70191 | 10388 | 6.8 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CACHE | 1.000 | 24.0 | 5354.0 | 52691 | 705 | 6.8 | 0.00 | 1.0 | 0 | 12 | 2660 | 0 |
| CASC | 1.000 | 24.0 | 4298.0 | 52917 | 1808 | 6.8 | 0.00 | 1.0 | 0 | 12 | 2464 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CACHE
- baseline: B1
- delta_ci: (0.11839526009097008, 0.1491148026495218)
- reason: CACHE 效用下界 0.1184 > 0

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
      "CACHE",
      "CASC"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\casc-cpu.toml"
    ],
    "run": "ar009-casc-cpu-20261028-183950"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
