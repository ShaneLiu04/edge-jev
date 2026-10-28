# Jev 实验矩阵报告

- run: ar009-casc-final-gpu-20261028-194528
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 24.0 | 5354.0 | 51268 | 8455 | 24.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CACHE | 1.000 | 24.0 | 5354.0 | 36955 | 637 | 24.2 | 0.00 | 1.0 | 0 | 12 | 2660 | 0 |
| CASC | 1.000 | 24.0 | 3771.0 | 51494 | 1282 | 14.6 | 0.00 | 1.0 | 0 | 12 | 2456 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CACHE
- baseline: B1
- delta_ci: (0.13765870422460547, 0.16101153482767408)
- reason: CACHE 效用下界 0.1377 > 0

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
      "jev matrix --config configs\\experiments\\casc-final-gpu.toml"
    ],
    "run": "ar009-casc-final-gpu-20261028-194528"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
