# Jev 实验矩阵报告（AR005）

- run: ar005-repeat-gpu-20261026-150619
- lambda_cost: 0.15
- seed: 20261026
- limit: 10

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | cache hits | risk denies |
|---|---|---|---|---|---|---|
| B0 | 0.600 | 20.6 | 57.8 | 13801 | 0 | 0 |
| B1 | 0.400 | 26.4 | 133.4 | 15193 | 0 | 0 |
| B2 | 0.350 | 13.2 | 66.7 | 12406 | 10 | 0 |
| AR005 | 0.350 | 13.2 | 66.7 | 12180 | 10 | 0 |

## 选择结果

- decision: **rollback**
- chosen_arm: B2
- baseline: B2
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
      "B0",
      "B1",
      "B2",
      "AR005"
    ],
    "commands": [
      "jev matrix --config C:\\Users\\L30086~1\\AppData\\Local\\Temp\\opencode\\ar005-repeat-gpu.toml"
    ],
    "run": "ar005-repeat-gpu-20261026-150619"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
