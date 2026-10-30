# Jev 实验矩阵报告

- run: ar016-gpu-cpu-20261030-163334
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 48667 | 8974 | 6.7 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| AGW_CASC_FAST | 1.000 | 5.0 | 1079.0 | 2032 | 726 | 7.8 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: AGW_CASC_FAST
- baseline: B1
- delta_ci: (0.5315273702110205, 0.568571047689635)
- reason: AGW_CASC_FAST 效用下界 0.5315 > 0

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
      "AGW_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\ar016-gpu-cpu.toml"
    ],
    "run": "ar016-gpu-cpu-20261030-163334"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
