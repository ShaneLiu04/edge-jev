# Jev 架构说明（含 AR005 增量）

## 1. 分层

```text
薄壳（server/sdk/cli/mcp_server）
  └─ Kernel（组合根）
       ├─ decisions（18 决策点 / 探针 / 校准）
       ├─ cascade（三路径 / route_v2 / 熔断 / 预算 / 草稿 / 前缀指纹）
       ├─ cerebrum（llama-server 生命周期 / OpenAI client / embedding）
       ├─ cerebellum（Laya ONNX / kev HTTP）
       ├─ runtime（不确定性 / 自证 / 验证器 / prompt）
       │    └─ AR005：conformal / allocator / portfolio
       ├─ cache（精确+语义 / 长程记忆）
       ├─ prefill（块/显著性/规划）
       └─ ledger（JSONL 异步写）
基础设施：fetch / bench(matrix, selector, dataset) / distill
```

禁止反向依赖：`kernel.*` 不得 import `server`。

## 2. 请求主链路（AR005）

1. PRE 决策（真实小脑）→ U/band；
2. 语义缓存查询（可选 Conformal 准入）→ 命中直接返回；
3. 路由 v2 → 硬信号 full；否则风险门（Conformal）裁决；
4. borderline + 验证器 → 草稿验证 → ACCEPT/回退 full；
5. prompt 编排：记忆 → 草稿 → JCBA/prefill；
6. 生成 → POST 复核（full）→ 写缓存/台账；
7. 响应头：`X-Jev-Route/Verified/Cache/Prefill/U` + AR005 `Risk/Plan`。

## 3. AR005 三个核心算法

### 3.1 Conformal Risk Control

- 降级方向：`s ≤ t` 视为安全，风险 = 降级样本质量损失；
- 选最大覆盖 `t` 使 `(Σloss + 1)/(n+1) ≤ α`；
- 缓存方向：`s ≥ t` 准入，风险 = 负例误准入率；
- 样本不足/无可行阈值 → 显式 disabled（回退既有行为）。

### 3.2 JCBA

- 单元：mandatory / recent / memory / draft / prefill blocks；
- 预算：`ceil(total_tokens × ratio)`；
- 0/1 背包 + 固定 tie-break；首末块强制；
- 无法获益 → 原样返回（`no_gain`）。

### 3.3 选择与回退

- 逐题效用；配对 bootstrap CI；
- 留一 band→arm 策略，防小样本过拟合；
- 无正下界 → rollback，不改变默认 profile。

## 4. 失败安全

| 失败 | 行为 |
| --- | --- |
| 校准缺失/损坏 | 启动期 `JevProtocolError`（显式启用时）；否则 disabled |
| embedding 不可用 | 精确缓存；语义层禁用 |
| 验证器超时/解析失败 | REJECT → full |
| 分配器异常/不可获益 | 原 prompt + `skipped` |
| 矩阵后端 503 | 非 0 退出，不写 adopt |
| 14B 资产缺失 | `JevAssetError`/deferred，不出指标 |

## 5. 协议兼容

只增不改：新增 `X-Jev-Risk`、`X-Jev-Plan`；未启用 AR005 时响应与 AR003 完全一致（逐例回归测试保障）。

## 6. 数据与台账

- `LedgerEntry.runtime` 为结构化 dict（AR003 起）；
- AR005 追加 `risk` / `plan` / `admission` 子键；
- 台账用于离线分析、校准与 AR004 飞轮，不进入主路径阻塞。
