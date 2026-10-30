# [AR017] 设计说明书

## 1. 设计

```text
if state > threshold and int8:
    for window in [128, 384]:
        decision = needs_reasoning(int8, state 截到 window)
        if applied != no_reasoning:
            return FULL（记录实际链）
    return fp32 RDMP
```

- 门控结果只可能是 FULL 或“交给完整 RDMP”，不可能更便宜。
- 所有窗口共享同一个 ONNX session；`windowed()` 复制后端字段并覆盖 state 预算。

## 2. 模块改动

| 文件 | 改动 |
|------|------|
| `kernel/cerebellum/laya_onnx.py` | `windowed(max_state_tokens)` 共享 session |
| `kernel/runtime/config.py` | `decision_long_context_gate_windows: tuple[int, ...] = (128, 384)` |
| `kernel/cerebellum/registry.py` | 单 int8 后端 + windowed 列表 |
| `kernel/kernel.py` | 透传 windows 后端列表 |
| `server/chat.py` | 级联循环、header 记录链 |
| `configs/*` | 显式 windows；保留单窗口兼容 |
| `tests/*` | 共享 session、路由一致性、矩阵 |

## 3. 配置

- `decision_long_context_gate_windows = [128, 384]`（推荐）；
- 空数组：使用 `decision_long_context_gate_tokens` 单窗口（AR016 兼容）；
- 窗口必须 1..512 且升序；重复/非法 fail-fast。

## 4. 测试

- 单测：windowed 共享 session、配置校验、header 纯函数。
- 集成：真实 shared 12 题，级联命中、路由与 RDMP 一致、门控耗时。
- 矩阵：`cgw.toml` / `cgw-casc.toml`（GPU offload）对照 AGW。
