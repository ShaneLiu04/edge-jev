# Jev 实验矩阵报告

- run: ar008-spec-short-en-20261027-174820
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 249.0 | 50.0 | 59431 | 376 | 5.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| SPEC | 1.000 | 244.0 | 50.0 | 60708 | 676 | 4.8 | 0.39 | 0.0 | 0 | 0 | 0 | 0 |

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
      "SPEC"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\spec-short-en.toml"
    ],
    "run": "ar008-spec-short-en-20261027-174820"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
