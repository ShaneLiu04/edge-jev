# Jev 实验矩阵报告

- run: ar011-adb-route-20261029-194947
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 53280 | 13281 | 10.1 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| ADB | 1.000 | 5.0 | 2662.0 | 38104 | 9043 | 7.3 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| ADB_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 12829 | 1293 | 6.8 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: ADB_CACHE_FAST
- baseline: B1
- delta_ci: (0.3269192970483814, 0.39731093681080115)
- reason: ADB_CACHE_FAST 效用下界 0.3269 > 0

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
      "ADB_CACHE_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\adb-route.toml"
    ],
    "run": "ar011-adb-route-20261029-194947"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
