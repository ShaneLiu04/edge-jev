# Jev 实验矩阵报告（AR005）

- run: ar005-local-1.5b-gpu20-20261026-144226
- lambda_cost: 0.15
- seed: 20261026
- limit: 20

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | cache hits | risk denies |
|---|---|---|---|---|---|---|
| B0 | 0.700 | 24.9 | 57.6 | 12783 | 0 | 0 |
| B1 | 0.600 | 29.2 | 107.8 | 15757 | 0 | 0 |
| B2 | 0.500 | 26.0 | 86.8 | 20193 | 2 | 0 |
| AR005 | 0.500 | 26.0 | 86.8 | 18355 | 2 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: B0
- baseline: B2
- delta_ci: (0.08012048192771085, 0.41752464403066825)
- reason: B0 效用下界 0.0801 > 0

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
      "B0",
      "B1",
      "B2",
      "AR005"
    ],
    "commands": [
      "jev matrix --config C:\\Users\\L30086~1\\AppData\\Local\\Temp\\opencode\\ar005-gpu-20.toml"
    ],
    "run": "ar005-local-1.5b-gpu20-20261026-144226"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
