# Jev 实验矩阵报告

- run: ar007-cache-full-20261027-131509
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 24.0 | 5354.0 | 70452 | 8848 | 0 | 0 | 0 | 0 |
| CACHE | 1.000 | 24.0 | 5354.0 | 53340 | 319 | 0 | 12 | 2660 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: CACHE
- baseline: B1
- delta_ci: (0.10803458016756495, 0.12359230537917154)
- reason: CACHE 效用下界 0.1080 > 0

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
      "CACHE"
    ],
    "commands": [
      "jev matrix --config C:\\Users\\L30086~1\\AppData\\Local\\Temp\\opencode\\ar007-cache-full.toml"
    ],
    "run": "ar007-cache-full-20261027-131509"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
