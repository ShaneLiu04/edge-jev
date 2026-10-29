# [AR011] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR011 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-29 |
| 状态 | Approved（自动化 harnessX） |

## 1. 设计目标

1. **默认零变化**：`decision_permutations=0` 时不裁剪，保持 AR010 行为。
2. **可验证的等价性**：预算裁剪以“applied 决策一致率”为硬指标，而不是只看推理耗时。
3. **可升级**：消费方需要置信度时，对低 margin 点用完整预算重算。
4. **可组合**：与 AR007/AR009/AR010 独立开关，按组合实验选择。
5. **真实可复现**：所有结论来自真实 Laya + 真实 llama-server。

## 2. 参考与取舍（SOTA）

| 来源 | 核心思想 | 本项目取舍 |
|------|---------|-----------|
| SGLang RadixAttention | 前缀树 + KV 复用 | llama.cpp 已原生多前缀缓存；实测槽位绑定无增益 → 不做 |
| vLLM PagedAttention | 分页 KV | 引擎内部已由 llama.cpp 管理 → 不重复实现 |
| FrugalGPT / RouteLLM | 级联与成本感知路由 | 已有 AR002/AR005；本 AR 降低路由所需决策成本 |
| Adaptive Computation | 按需分配计算 | 采用：决策置换预算 + 低 margin 升级 |
| LLMLingua / LongLLMLingua | 提示压缩 | 已由 AR009 CASC 覆盖 |
| Speculative decoding | 小模型提议 + 大模型验证 | AR008 实测无收益 → 保持关闭 |
| StreamingLLM / H2O | KV 淘汰/注意力汇 | 与 llama.cpp 内部策略冲突，暂不做 |

**已排除方向（真实测量）**
- GPU Laya：ORT CUDA 可加载（cuDNN 在 `models/cudnn9/bin`），但限频 645 MHz +
  部分节点回退 CPU，PRE 30.6s > CPU 25.7s。
- 槽位绑定：`--parallel 2` 与 `id_slot` 无额外收益；llama.cpp 在单槽下仍能命中
  A/B/C 多个文档前缀。
- 状态窗口压缩：head+tail（96+288）一致率 84%，出现 safety 翻转 → 拒绝。

## 3. 数据流

```text
registry.active(stage) / shadow(stage)
        │
        ▼
_budgeted_points(points)
  permutations = min(point.permutations, budget)   # budget>0
        │
        ▼
evaluate_stage(cerebellum, points, state)
        │
        ├─ escalate_margin <= 0 → 直接返回
        └─ > 0：
             对 active 且 margin < escalate_margin 且曾被裁剪的点
             用原始 permutations 重新 evaluate_stage
             → 按 point_id 合并 decisions / fused / latency
```

## 4. 配置

`src/jev/kernel/runtime/config.py`：

```python
decision_permutations: int = 0        # 0=不限制；1=每点最多 1 次置换
decision_escalate_margin: float = 0.0 # >0 时对低 margin 点升级
```

`configs/jev.toml`：

```toml
[runtime]
decision_permutations = 0
decision_escalate_margin = 0.0
```

`configs/profiles/latency-fast.toml`：

```toml
decision_permutations = 1
```

## 5. 预算裁剪

- 在 `_CascadeContext` 内新增 `_budgeted_points(points)`：
  - 读取 `runtime_config.decision_permutations`；
  - `0` → 原样返回；
  - `>0` → `dataclasses.replace(point, permutations=min(orig, budget))`；
  - 仅 choice 点有 `permutations`，其他点不变。
- `evaluate` 与 `evaluate_detailed` 都先裁剪再送小脑。
- 裁剪不改变探针数量（默认 intent 仍有 2 个视角），只减少选项重排。

## 6. 低 margin 升级

- 触发条件：`decision_escalate_margin > 0` 且存在
  - `mode == ACTIVE`
  - 原始 `permutations > budget`
  - `decision.margin < escalate_margin`
- 升级：只对这些点调用一次 `evaluate_stage`（完整置换），合并结果。
- 合并：
  - 按 `point_id` 替换 decisions；
  - fused 同步替换；
  - latency 相加；
  - 台账只记录最终决策，避免重复条目。
- 失败安全：升级调用异常时保留裁剪结果并记录 warning（不阻断请求）。

## 7. 观测

- `X-Jev-Decisions`：active 决策数（不因裁剪变化）。
- `X-Jev-Decision-Mode`：fast / 完整（AR010）。
- `last_runtime.decisions`：新增 `permutations_budget`、`escalated` 计数。
- 矩阵 `metrics`：沿用 quality / latency / prompt_tokens / route。

## 8. 实验设计

### 8.1 隔离实验（`adb.toml`，force_full=true）

| arm | 预算 | 其他 |
|-----|------|------|
| B1 | 完整 | 基线 |
| ADB | 1 | 决策预算 |
| ADB_CACHE | 1 | + 前缀 KV |
| ADB_CACHE_FAST | 1 | + LADS fast |

数据：shared-prefix 12 题；质量金标；输出一致性。

### 8.2 真实路由实验（`adb-route.toml`，force_full=false）

- 验证裁剪后 route 与完整预算 route 的一致性；
- 记录 light/full 分布与质量；
- 若 route 不一致导致质量下降 → rollback。

### 8.3 多样本决策一致性（真实 Laya）

- `tests/integration/test_decision_budget_real.py`（assets + slow）：
  - 8 条真实 prompt（quality/needles/shared_generation/long_en）；
  - 对比完整预算与预算 1 的 applied 决策；
  - 断言一致率 ≥ 0.95，并输出实际值。

## 9. 风险与回退

- 置换减少可能改变低 margin 决策；以 applied 一致率 + 端到端质量为门。
- 默认预算 0，不改变现有部署；`latency-fast` 与实验配置显式开启 1。
- 若联合实验无正收益，选择器 rollback 到 AR010 最优组合。
