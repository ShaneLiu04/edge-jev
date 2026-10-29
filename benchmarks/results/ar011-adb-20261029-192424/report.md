# Jev 实验矩阵报告

- run: ar011-adb-20261029-192424
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 48035 | 8997 | 9.9 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| ADB | 1.000 | 5.0 | 2662.0 | 30185 | 8527 | 20.1 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| ADB_CACHE | 1.000 | 5.0 | 2662.0 | 29645 | 1217 | 8.6 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| ADB_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 12616 | 1259 | 8.2 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: ADB_CACHE_FAST
- baseline: B1
- delta_ci: (0.313458492828115, 0.38778461872540465)
- reason: ADB_CACHE_FAST 效用下界 0.3135 > 0

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
      "ADB",
      "ADB_CACHE",
      "ADB_CACHE_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\adb.toml"
    ],
    "run": "ar011-adb-20261029-192424"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
