# [AR010] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR010 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-29 |
| 状态 | Approved（用户授权全自动） |

## 1. 设计目标

1. **先修正确性，再谈延迟**：POST 必须看到答案尾部，否则任何“跳过复核”的收益都建立在错误前提上。
2. **截断语义一致**：批处理 `system_one` 与单序列 `build_sequence` 使用同一窗口策略（保尾部）。
3. **质量兜底确定性化**：退化重复属于必然错误，用纯函数检测，不依赖小脑。
4. **默认零行为变化**：两个决策开关默认 true；fast 模式仅由显式配置开启。
5. **可观测、可回退、可复现**：头、台账、矩阵选择器三处一致。

## 2. 数据流

```text
chat request
   │
   ├─ PRE: evaluate(Stage.PRE)  ── decision_shadow=false 时只跑 active
   │
   ├─ prepare_prompt / prefix cache / stable compression
   │
   ├─ brain.chat()
   │
   └─ POST:
        decision_post=false ──► ACCEPT（跳过 Laya 与重试）
        decision_post=true  ──► 退化重复？
                                  ├─ 是 → RETRY（一次反馈重试）
                                  └─ 否 → Laya POST → handle_post
                                             ├─ ACCEPT
                                             └─ RETRY → 一次反馈重试
```

## 3. Laya 状态窗口修复

### 3.1 位置

- `src/jev/kernel/cerebellum/laya_onnx.py::system_one`
- `src/jev/kernel/cerebellum/laya_sequence.py::build_sequence`

### 3.2 变更

```python
# laya_onnx.py（预截断）
state_ids = state_ids[-self._max_state_tokens :]

# laya_sequence.py（序列预算内截断）
kept = state_ids[-room:] if room else []
```

### 3.3 为什么保尾部

- chat 的 state 是 `messages JSON + assistant`，**最新问题与答案都在尾部**；
- 旧实现保头部，512-token 预算下答案完全不可见，POST 退化为常量分类器；
- 保尾部后，正确长答与空答的 POST 决策可区分（真实回归测试锁定）。

### 3.4 兼容性

- 未超预算的 state 行为完全不变；
- `truncated` 标记逻辑不变，仍遵循 `truncate` / `refuse`；
- 选项 marker 布位与 `max_len` 约束不变。

## 4. 退化重复守卫

`src/jev/server/chat.py::_is_degenerate_repetition`：

```python
content = text.strip()
if len(content) < 8:
    return False
return any(
    all(content[i] == content[i % period] for i in range(len(content)))
    for period in range(1, len(content) // 3 + 1)
)
```

- 检测短周期全串重复（如 `5074507450745074`）；
- 命中则直接进入一次反馈重试（与 `needs_retry` 同一路径）；
- 重试后仍退化 → `DOWNGRADE`，`jev_degraded=true`，不无限重试；
- 只对确定性退化触发，不对正常短答触发。

## 5. 决策开关

- `RuntimeConfig.decision_shadow`（默认 true）
  - false：`_CascadeContext.evaluate` 不合并 shadow 点，chat 决策数下降；
  - `/v1/systemone` 不走该上下文，保持完整。
- `RuntimeConfig.decision_post`（默认 true）
  - false：`_post_review` 直接 ACCEPT，不做 Laya POST、不重试；
- `configs/jev.toml`：

```toml
[runtime]
decision_shadow = true
decision_post = true
```

## 6. 观测

| 观测 | 含义 |
|------|------|
| `X-Jev-Decision-Mode: fast` | chat 跳过 shadow 或 POST |
| `X-Jev-Decisions` | active 决策数（shadow 不计） |
| `X-Jev-Prefix-Cache` | `hit:<n>` / `miss` |
| `last_runtime.decision` | 本次决策模式与开关快照 |

## 7. 实验设计

配置：`configs/experiments/lads.toml`

| arm | runtime | 说明 |
|-----|---------|------|
| B1 | 关闭 | 基线，完整决策 |
| FAST | enabled, shadow/post=false | 快速决策 |
| CACHE | enabled, prefix_cache=true | 前缀 KV |
| CACHE_FAST | enabled, prefix_cache + shadow/post=false | 组合 |

- 数据：shared-prefix 12 题；`force_full=true` 固定路由，隔离路由差异；
- 质量：外部金标（数字匹配）；
- 等价性：`outputs_identical` 四臂对比；
- 选择器：质量 − λ_cost·tokens − λ_latency·latency，配对 bootstrap + LOO。

## 8. 测试策略

- 纯逻辑单测：
  - `test_laya_sequence.py`：超长 state 保留尾部；
  - `test_chat_cascade.py`：退化重复触发一次重试并累计 usage；
- 真实资产回归：
  - `tests/integration/test_laya_post_window_real.py`：长 state 下正确长答与空答
    的 POST applied 决策必须不同；
- 全量：`uv run pytest tests/unit`、`uv run ruff check .`；
- 真实实验：矩阵四臂 + AR006/AR007/AR009 复测。

## 9. 结果与边界

- shared-prefix：B1 54.0s / FAST 34.2s / CACHE 42.0s / CACHE_FAST 24.6s，输出一致；
- AR009 CASC：38.1s、1079 tokens、quality 1.0；
- AR007：prefill 8202→927ms、端到端 48.4→40.5s；
- AR006：quality 0.833（11/12），AEB token −45.6%；
- 边界：fast 模式不提供 POST 复核；短答的 Laya 自评不可靠；历史表需按修复后口径引用。
