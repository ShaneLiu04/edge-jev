# Jev 实验矩阵报告

- run: ar014-lcpt-casc-20261030-135323
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 44434 | 7743 | 7.8 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| RDMP_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 6630 | 1150 | 6.6 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| RDMP_CASC_FAST | 1.000 | 5.0 | 1079.0 | 6431 | 675 | 7.5 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |
| LCPT_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 2525 | 1155 | 6.5 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| LCPT_CASC_FAST | 1.000 | 5.0 | 1079.0 | 2415 | 683 | 7.2 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: LCPT_CASC_FAST
- baseline: B1
- delta_ci: (0.4921288363443989, 0.5669883598767665)
- reason: LCPT_CASC_FAST 效用下界 0.4921 > 0

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
      "RDMP_CACHE_FAST",
      "RDMP_CASC_FAST",
      "LCPT_CACHE_FAST",
      "LCPT_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\lcpt-casc.toml"
    ],
    "run": "ar014-lcpt-casc-20261030-135323"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
