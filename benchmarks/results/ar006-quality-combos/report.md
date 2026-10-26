# Jev 实验矩阵报告

- run: ar006-quality-combos-20261026-230017
- lambda_cost: 0.15
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | cache hits | risk denies |
|---|---|---|---|---|---|---|
| B1 | 0.333 | 27.3 | 141.2 | 8595 | 0 | 0 |
| P60 | 0.333 | 27.3 | 141.2 | 8673 | 0 | 0 |
| AEB | 0.333 | 27.3 | 141.2 | 8601 | 0 | 0 |
| AEB_ALLOC | 0.333 | 27.3 | 141.2 | 8962 | 0 | 0 |

## 选择结果

- decision: **rollback**
- chosen_arm: B1
- baseline: B1
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
      "B1",
      "P60",
      "AEB",
      "AEB_ALLOC"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\quality-combos.toml"
    ],
    "run": "ar006-quality-combos-20261026-230017"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
