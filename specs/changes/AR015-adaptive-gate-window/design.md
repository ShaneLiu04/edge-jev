# [AR015] 设计说明书

## 1. 设计

AR014 的 int8 门控后端以 `max_state_tokens=gate_tokens` 构建；仅该后端使用短窗口。
主 fp32 后端与 RDMP 兜底保持 512。

```text
state > 4000 tokens?
  └─ int8 门控：state 截到 gate_tokens（默认 384）
       ├─ reasoning/fallback → FULL
       └─ no_reasoning → fp32 RDMP（完整 512 state）
```

窗口变短只会改变门控输入：命中 FULL 安全；未命中走完整兜底。
不会出现比 AR014 更便宜的路由。

## 2. 模块改动

| 文件 | 改动 |
|------|------|
| `kernel/runtime/config.py` | `decision_long_context_gate_tokens: int = 384` + 校验 |
| `kernel/cerebellum/registry.py` | `create_laya_int8(..., max_state_tokens=...)` |
| `kernel/kernel.py` | 读取 gate tokens 并构建门控后端 |
| `server/chat.py` | header 带窗口 `int8-reason-full:w384` |
| `configs/*` | 显式写入 384，保证复刻 |
| `tests/*` | 单测 + 真 shared 12 题一致性 |

## 3. 安全与回退

- `decision_long_context_gate_tokens=0` → 使用 int8 默认 512，等价 AR014。
- 缺 int8 资产/门控异常 → fp32 RDMP。
- 若联合矩阵无正收益，profile/实验配置回退 0。

## 4. 测试

- 单测：默认值、0 语义、负值拒绝；门控后端 max_state_tokens 传递。
- 集成：真实 shared 12 题，384 窗口命中与 AR014 路由一致，记录门控耗时。
- 矩阵：`agw.toml` / `agw-casc.toml`，对照 LCPT。
