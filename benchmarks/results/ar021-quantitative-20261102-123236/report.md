# Jev 实验矩阵报告

- run: ar021-quantitative-20261102-123236
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 0.364 | 9.3 | 3064.5 | 36608 | 8743 | 4.7 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CGW_CASC_FAST | 0.545 | 8.7 | 1131.6 | 1310 | 255 | 5.8 | 0.00 | 0.3636 | 0 | 11 | 1106 | 0 |
| GEXA_CGW_CASC_FAST | 0.545 | 8.7 | 1131.6 | 1225 | 252 | 5.9 | 0.00 | 0.3636 | 0 | 11 | 1106 | 0 |
| GQRA_CGW_CASC_FAST | 1.000 | 0.0 | 0.0 | 4 | 0 | 0.0 | 0.00 | 0.3636 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: GQRA_CGW_CASC_FAST
- baseline: B1
- delta_ci: (0.9753980681042538, 1.5930023520699452)
- reason: GQRA_CGW_CASC_FAST 效用下界 0.9754 > 0

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
      "CGW_CASC_FAST",
      "GEXA_CGW_CASC_FAST",
      "GQRA_CGW_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\quantitative.toml"
    ],
    "run": "ar021-quantitative-20261102-123236"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
