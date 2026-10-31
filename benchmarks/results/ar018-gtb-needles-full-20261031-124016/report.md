# Jev 实验矩阵报告

- run: ar018-gtb-needles-full-20261031-124016
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CGW_CASC_FAST | 1.000 | 5.0 | 753.6 | 15489 | 2486 | 4.7 | 0.00 | - | 0 | 0 | 0 | 0 |
| GTB_CASC_FAST | 1.000 | 5.0 | 753.8 | 14596 | 1410 | 10.6 | 0.00 | - | 0 | 0 | 0 | 0 |

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
      "GTB_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\gtb-needles-full.toml"
    ],
    "run": "ar018-gtb-needles-full-20261031-124016"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
