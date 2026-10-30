# Jev 实验矩阵报告

- run: ar013-rdmp-casc-20261030-101917
- lambda_cost: 0.15
- lambda_latency: 0.5
- seed: 20261026
- limit: 12

## 指标

| arm | quality | completion tok | prompt tok | P50 ms | pref ms | decode tps | spec acc | identical | cache hits | prefix hits | mean cache_n | risk denies |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1.000 | 5.0 | 2662.0 | 37531 | 8259 | 22.4 | 0.00 | 1.0 | 0 | 0 | 0 | 0 |
| CADP_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 6016 | 997 | 21.4 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| RDMP_CACHE_FAST | 1.000 | 5.0 | 2662.0 | 4335 | 938 | 24.2 | 0.00 | 1.0 | 0 | 11 | 2425 | 0 |
| RDMP_CASC_FAST | 1.000 | 5.0 | 1079.0 | 4329 | 480 | 28.2 | 0.00 | 1.0 | 0 | 11 | 974 | 0 |

## 选择结果

- decision: **adopt**
- chosen_arm: POLICY
- baseline: B1
- delta_ci: (0.47839362335614594, 0.5368892747815384)
- reason: POLICY 效用下界 0.4784 > 0

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
      "CADP_CACHE_FAST",
      "RDMP_CACHE_FAST",
      "RDMP_CASC_FAST"
    ],
    "commands": [
      "jev matrix --config configs\\experiments\\rdmp-casc.toml"
    ],
    "run": "ar013-rdmp-casc-20261030-101917"
  }
}
```

> 原始逐题数据见 `results.json`；后端失败时本报告不会生成。
