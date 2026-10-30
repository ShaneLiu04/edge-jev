# [AR014] 设计说明书

## 1. 设计概述

**LCPT-RFG**：对超长 state 使用 int8 Laya 先做 `needs_reasoning` 门控；若判 reasoning
（或低置信 fallback）直接 FULL，否则回退 fp32 RDMP。短/中上下文完全不变。

```text
state token > threshold?
  ├─ 否 → AR013 RDMP（fp32）
  └─ 是且 int8 可用:
       needs_reasoning(int8, 1 行)
         ├─ applied != no_reasoning → FULL（staged=on, long=int8-reason-full）
         └─ 否则 → AR013 RDMP（fp32，安全兜底）
```

## 2. 模块改动

| 文件 | 改动 |
|------|------|
| `kernel/cerebellum/quantize.py` | `prepare_laya_int8(src, out)`：合并外部数据 + 动态 int8 |
| `kernel/cerebellum/laya_onnx.py` | 公共 `count_tokens(state)` |
| `kernel/runtime/config.py` | `decision_long_context_tokens: int = 0` |
| `kernel/kernel.py` | 读取 int8 路径并构建第二后端；透传 `decision_int8` |
| `server/chat.py` | `_CascadeContext.decision_int8`；长上下文门控分支；`X-Jev-Decision-Long` |
| `server/app.py` / `create_chat_router` | 透传 int8 后端 |
| `cli/main.py` | `jev prepare-laya-int8` |
| `configs/*` | int8 路径、阈值、实验/profile/server 配置 |

## 3. 量化实现

- 输入 `models/laya/laya.onnx`（+ `.onnx.data`）。
- 先 `onnx.load(load_external_data=True)` 合并为单文件（临时）。
- 动态量化 `QInt8`、per-channel、外部数据输出。
- 上游 Laya 图存在 shape-inference 形状冲突；量化器需要跳过形状推导，
  并在 `extra_options` 指定 `DefaultTensorType=FLOAT`（已实测可生成、可加载）。
- 输出 `models/laya/laya_int8.onnx` + `.data`；临时合并文件删除。
- 失败抛 `JevAssetError`，不得静默生成坏文件。

## 4. 门控与安全

- 仅当 `decision_staged=true`、无其他 PRE 消费者、int8 后端存在且阈值 >0。
- 门控只可能产生 FULL（保守）；不可能产生 DIRECT/LIGHT。
- 未命中门控严格回退 AR013 RDMP，路由与质量不变。
- 决策计数/台账只记录实际采用的 reasoning 决策。
- 缺 int8 文件：启动不报错（仅禁用该优化）；显式 `prepare` 命令才校验。

## 5. 测试

- 单测：配置校验、`count_tokens`、量化命令参数校验（不实际量化）、门控纯判定。
- 集成（assets+slow）：真实 fp32/int8 Laya，6 条 shared 长文，断言
  `long route == RDMP route`、`decisions == 1`、header 正确；无 int8 资产时明确 skip。
- 端到端：`lcpt.toml`、`lcpt-casc.toml`，选择器对照 B1/RDMP。

## 6. 回退

- `decision_long_context_tokens=0` 或删除 int8 文件即回到 AR013。
- 若 shared 端到端无正收益或质量下降，profile 关闭阈值。
