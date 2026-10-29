# Jev 实验矩阵报告

- run: ar011-adb-casc-20261029-201140
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 49562 | 9112 | 6.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| ADB_CACHE | 1.000 | 5.0 | 2662.0 | 29365 | 1297 | 6.9 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| ADB_CASC | 1.000 | 5.0 | 1079.0 | 28972 | 737 | 7.6 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |
| ADB_CASC_FAST | 1.000 | 5.0 | 1079.0 | 12130 | 891 | 7.8 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: ADB_CASC_FAST
- baseline: B1
- delta_ci: (0.4053813907816543, 0.4683764046648828)
- reason: ADB_CASC_FAST 效用下界 0.4054 > 0

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
      "ADB_CACHE",
      "ADB_CASC",
      "ADB_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\adb-casc.toml"
    ],
    "run": "ar011-adb-casc-20261029-201140"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
