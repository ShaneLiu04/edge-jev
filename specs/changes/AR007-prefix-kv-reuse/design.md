# [AR007] 详细设计

| 字段 | 内容 |
|------|------|
| AR 编号 | AR007 |
| 关联 srs.md | ./srs.md |
| 日期 | 2026-10-27 |
| 状态 | Approved（用户授权全自动） |

## 1. 设计目标

1. 默认关闭；关闭时与 AR006 逐字节一致。
2. 复用推理引擎能力，不重造 KV/调度；Jev 负责**策略、可观测、回退**。
3. 会话模式必须**逐字稳定**：跳过一切按问题变化的上下文处理。
4. 缓存收益必须可验证：以服务器 `timings.cache_n` 为准，不估算、不伪造。

## 2. 数据流

```text
payload.user ──► session_id
                     │
runtime.prefix_cache && (session_affinity ? session_id : true)
                     │ yes                         │ no
        stable original messages               AEB/JCBA (AR006)
                     │
        LlamaServerClient(cache_prompt=True)
                     │
        llama-server 复用前缀 KV
                     │
        response.timings.cache_n ──► header/ledger
```

## 3. 模块设计

### 3.1 配置

`RuntimeConfig` 增：

```python
prefix_cache: bool = False
session_affinity: bool = False
```

`configs/jev.toml [runtime]` 同名键；`_build_runtime` 读取。

### 3.2 会话解析与纯策略函数

`server/chat.py` 的 `_validate_chat_request` 返回值增加 `user: str`（空串默认）；
`_CascadeContext` 增 `session_id` 与 `prefix_cache_active: bool`。

纯函数（可单测）：

```python
def use_stable_prefix(prefix_cache: bool, session_affinity: bool,
                      session_id: str) -> bool:
    return prefix_cache and (not session_affinity or bool(session_id))
```

放在 `kernel/runtime/prefixcache.py`，避免 server 逻辑难测。

### 3.3 稳定前缀

`prepare_prompt` 开头：

```python
if use_stable_prefix(...):
    self.prefix_cache_active = True
    self.last_runtime["prefill"] = {
        "selected": 0, "total": 0, "before_tokens": tokens, "after_tokens": tokens,
        "skipped": "prefix_cache", "selector": "prefix-cache",
        "budget_ratio": 1.0, "entropy": 0, "top_gap": 0, "fallback": None,
    }
    return [dict(m) for m in self.messages]
```

`_draft_messages` 不参与（会话模式不注入草稿），保证前缀稳定。

### 3.4 客户端 cache_prompt

- `build_serve_app`：`LlamaServerClient(base_url, cache_prompt=bool(runtime.prefix_cache))`；
- `BrainSupervisor` 增 `cache_prompt: bool | None`，内部创建 client 时透传；
- 矩阵每 arm 创建 client 时 `cache_prompt=bool(arm_cfg.runtime.prefix_cache)`；
- `cache_prompt=False` 时必须显式发送 `false`（防止服务器默认开启污染 B1 基线）。

### 3.5 命中采集

`_generate_full` / `_generate_light` 拿到 response 后：

```python
timings = response.get("timings") or {}
cache_n = int(timings.get("cache_n", 0))
context.record_prefix_cache(cache_n, context.prefix_cache_active)
```

`_CascadeContext.record_prefix_cache` 更新 `last_runtime["prefix_cache"]` 与累计值。
`_response_headers`：

```text
X-Jev-Prefix-Cache: off | miss | hit:<n>
```

缓存命中即使 `cache_n>0`；`prefix_cache_active` 为 False 时 `off`。

### 3.6 shared-prefix 数据集

`bench/prefixset.py: build_shared_prefix(count=12, paragraphs=25)`：

- 同一文档（含 `K-1000..K-1011` 多条记录，每条唯一校验码）；
- 问题只替换目标编号；文档部分逐字相同；
- `Gold` 为对应数字；target 不在首末段；
- 文档约 1.5k token，覆盖 prefill 阈值。

### 3.7 矩阵

`MatrixConfig` 增 `prompt_cache: bool`（默认 false）。
`_run_questions` 在 `prompt_cache=true` 时给 payload 加 `"user": f"jev-{arm}-session"`。
`_load_questions` 支持 `dataset="shared_prefix"`。

每 arm client：

```python
prefix_cache = bool(arm_cfg.get("runtime", {}).get("prefix_cache", False))
LlamaServerClient(..., cache_prompt=prefix_cache)
```

`_summarize` 增：
- `cache_prompt_hits`：`X-Jev-Prefix-Cache` 以 `hit:` 开头的请求数；
- `mean_cache_n`：解析 `hit:<n>` 的均值（miss/off 记 0）。

### 3.8 实验臂

| arm | runtime.prefix_cache | session_affinity | AEB | 目的 |
| --- | --- | --- | --- | --- |
| B1 | false | false | false | 基线 |
| CACHE | true | true | false | 完整前缀复用 |
| AEB | false | false | true | 单次压缩 |
| AEB_CACHE | true | false | true | 冲突对照（不同问题选块不同） |

`configs/experiments/prefix-cache.toml`（本机）与 `server-7b-prefix.toml` /
`server-14b-prefix.toml`（external，`max_tokens=128`，30 题）。

## 4. 失败与回退

| 场景 | 行为 |
|------|------|
| `cache_n=0` | 记 miss；不影响答案 |
| 服务器不识别 cache_prompt | 忽略字段，退化为普通请求 |
| 会话未传 user | 回退 AEB（`session_affinity` 安全默认） |
| 缓存导致假死 | Supervisor 健康探针/重启；缓存请求重试一次 |
| 无收益 | 选择器 rollback；profile 不写 |

## 5. 测试

- 单测：`use_stable_prefix` 真值表；shared-prefix 确定性/长度/前缀一致；
  矩阵 `prompt_cache` 解析与 session user 注入；缓存头解析。
- 集成（assets/slow）：真实 server，同前缀两次请求，断言第二次 `X-Jev-Prefix-Cache=hit:<n>`
  且答案一致；真实 Laya 走完整链路。
- 真实实验：B1/CACHE/AEB/AEB_CACHE 12 题；报告含 cache_n、延迟、质量。

## 6. 兼容

- `prompt_cache=false` 时 payload 不带 `user`、client 显式 `cache_prompt=false`，
  输出、头、台账与 AR006 一致（除新增默认键）。
- 头只增不改；`usage` 结构不变（cached_tokens 仍由上游可选返回，Jev 不改写）。
