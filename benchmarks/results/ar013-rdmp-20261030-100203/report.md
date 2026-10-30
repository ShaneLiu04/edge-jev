# Jev 实验矩阵报告

- run: ar013-rdmp-20261030-100203
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 36743 | 8507 | 23.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CADP | 1.000 | 5.0 | 2662.0 | 20868 | 8546 | 23.6 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| RDMP | 1.000 | 5.0 | 2662.0 | 19540 | 8566 | 22.2 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: POLICY
- baseline: B1
- delta_ci: (0.221412167023553, 0.24403109711254287)
- reason: POLICY 效用下界 0.2214 > 0

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
      "CADP",
      "RDMP"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\rdmp.toml"
    ],
    "run": "ar013-rdmp-20261030-100203"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
