# [AR012] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR012 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-29 |
| 状态 | Approved（自动化 harnessX） |

## 1. 设计目标

1. **默认零变化**：`decision_staged=false` 保持 AR011 行为。
2. **只跳过无消费者/可短路决策**，不改变路由语义。
3. **失败安全**：注册点缺失、路由策略复杂、消费者需要完整 PRE 时回退完整评估。
4. **可观测**：实际计算点数、是否分阶段、是否短路。
5. **可组合**：与 ADB、CACHE、CASC、LADS 独立开关。

## 2. 依赖分析

路由 `CascadeEngine.route_chat` 的优先级：

1. 任一 active 决策 `from_fallback` → full
2. `margin_tau>0` 且 margin 不足 → full
3. `safety=unsafe` → full
4. `intent=decision` → direct
5. capability gate 且 `needs_generation=no_generation` 且
   `needs_reasoning=no_reasoning` 且 `complexity=low` → direct
6. `needs_reasoning=no_reasoning`（必要时 complexity=low）→ light
7. 预算不足 → light
8. 否则 full

**安全短路**（前提：safety 已知且 non-unsafe、intent 已知且非 decision）：

- `needs_reasoning=reasoning` → 一定 full（light/direct 都要求 no_reasoning）；
- 预算不足 → 一定 light；
- `needs_reasoning=no_reasoning` → 需要 needs_generation + complexity 才能区分 direct/light/full。

因此 wave1 = `safety, intent, needs_reasoning`；wave2 = `needs_generation, complexity`。

## 3. 消费者判断

若存在以下任一情况，需要完整 PRE（不做裁剪）：

- `runtime` 关闭；
- prompt 的 draft/memory/prefill/allocator 任一开启；
- `verifier` 非空或 `bands.verifier_enabled`；
- `margin_tau > 0`；
- 风险控制/准入开启；
- 路由强制且将走 direct（需要完整 applied 渲染）——由 `_needs_full_pre` 的 prompt/verifier 判断覆盖；
- 注册表缺少短路所需点。

否则：

- 路由被强制（`route_policy` 非空）→ PRE 结果无消费者，直接返回 `[]`；
- 自动路由且 `decision_staged=true` → 分阶段评估。

## 4. 实现

### 4.1 `CascadeEngine.route_short_circuit`

```python
def route_short_circuit(self, pre, *, budget=None) -> Route | None:
    active = [d for d in pre if d.mode is Mode.ACTIVE]
    if any(d.from_fallback for d in active):
        return Route.FULL
    if self._applied(pre, "safety") == "unsafe":
        return Route.FULL
    if self._applied(pre, "intent") == "decision":
        return Route.DIRECT
    reasoning = self._applied(pre, "needs_reasoning")
    if reasoning == "no_reasoning":
        if budget is not None and not budget.allows_full():
            return Route.LIGHT
        return None
    return Route.FULL
```

- 只读，不改变现有 `route_chat`；
- 缺注册点时由调用方回退，不进入该方法。

### 4.2 chat 分阶段评估

在 `_CascadeContext` 新增：

```python
def evaluate_pre(self) -> list[Decision]:
    if not staged or needs_full_pre:
        return self.evaluate_detailed(Stage.PRE, self.state_json()).decisions
    if route_policy is not None:
        return []                      # 无消费者
    wave1_points = [safety, intent, needs_reasoning]
    wave1 = self._evaluate_points(state, wave1_points)  # 复用 AR011 预算
    route = self.engine.route_short_circuit(wave1.decisions, budget=self.budget())
    if route is None:
        wave2 = [needs_generation, complexity]
        combined = wave1 + self._evaluate_points(state, wave2)
        return combined sorted by registry order
    if route is Route.DIRECT and needs_full_pre:
        ...  # 理论上已被 needs_full_pre 拦截
    return wave1.decisions
```

- `decision_count` 只累计实际评估的 active 点；
- `last_fused` 合并实际评估的 fused；
- 台账只记录实际计算的决策（`X-Jev-Decisions` 相应减少）；
- 短路后仍调用 `route_decision(partial)`，由于短路条件保证缺失点不影响结论，
  `route_chat` 对缺失点使用 fallback 也不会改变路由（单测锁定）。

### 4.3 观测

- `X-Jev-Decision-Staged: on`（分阶段且实际跳过）；
- `X-Jev-Decisions`：实际计算点数；
- `last_runtime.decision.staged`。

## 5. 实验设计

| 配置 | arms | 说明 |
|------|------|------|
| `cadp.toml` | B1 / ADB / CADP / CADP_FAST | force_full=false，真实路由 |
| `cadp-casc.toml` | B1 / ADB_CACHE_FAST / CADP_CACHE_FAST / CADP_CASC_FAST | 联合最优 |

- 真实 Laya 路由一致性测试：同一批 prompt，完整 PRE 与 CADP 的 route 必须完全一致；
- 质量：外部金标；输出一致性；
- 选择器：质量 − λ_cost·tokens − λ_latency·latency。

## 6. 风险与回退

- 短路规则与 route_chat 优先级不一致 → 路由单测 + 真实一致性测试拦截；
- 缺失点导致 `route_chat_v2` 不确定性升高 → 可能返回 full（安全侧，不降质）；
- 默认关闭；无正收益则选择器回退 AR011 最优组合。
