# Jev 实验矩阵报告

- run: ar014-lcpt-needles-20261030-140824
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 6

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 0.833 | 8.2 | 3076.7 | 49756 | 5356 | 7.0 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| RDMP | 1.000 | 5.0 | 1839.7 | 38245 | 5367 | 10.1 | 0.00 | 0.8333 | 0 | 0 | 0 | 0 |
| LCPT | 1.000 | 5.0 | 1839.7 | 37880 | 5378 | 7.0 | 0.00 | 0.8333 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: LCPT
- baseline: B1
- delta_ci: (0.09822104492856593, 1.0828953682873423)
- reason: LCPT 效用下界 0.0982 > 0

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
      "RDMP",
      "LCPT"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\lcpt-needles.toml"
    ],
    "run": "ar014-lcpt-needles-20261030-140824"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
