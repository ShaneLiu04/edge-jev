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

## 7. AR006 增量（AEB + 自愈 + 组合实验）

```text
prefill/evidence.py  真实 embedding 打分 + MMR + 证据锚点 + 检索意图抽取
prefill/planner.py   自适应预算比例（相对均值 margin + top-gap）
runtime/prompt.py    AEB 接入 JCBA（统一预算，输出 selector/ratio/entropy/gap）
cerebrum/supervisor.py  健康探针 → 重启 → 重试一次（显式失败，不掩盖）
cerebrum/embeddings.py  长跑假死时重启 embedding 子进程并重试一次
bench/matrix.py      任意组合 arm（overlays 定义即合法）+ 组合选择/回退
```

### 7.1 证据选择

1. `retrieval_query` 从「长文 + 末尾问题」提取真正意图；
2. 长块按字符切块，分批真实 embedding 后按 owner 均值聚合（避免超出服务 batch）；
3. 融合分 = `0.55·emb + 0.25·lexical + 0.10·position + 0.10·decision`；
4. MMR 抑制相邻冗余；首末块 + 证据锚点（排除末块问题）强制保留；
5. embedding 失败 → 回退 salience，记录 `fallback`，绝不伪造向量。

### 7.2 自适应预算

- `concentration = 0.5·top_gap + 0.5·(top−mean)/top`；
- `ratio = min + (max−min)·(1−concentration)`，默认 `[0.22, 0.60]`；
- 证据集中 → 更激进；分布均匀 → 保持上界（与固定 0.6 相同的安全侧）。

### 7.3 组合实验

- `arms` 中非内置名称必须在 `[overlays.<arm>]` 出现，否则启动即报错；
- 每 arm 独立重启大脑并写检查点；选择器统一采用配对 bootstrap + 留一；
- 无正收益 → `rollback`；`profile.toml` 记录实际生效节。

### 7.4 新增可观测

- 响应头 `X-Jev-Prefill-Mode: <selector>;r=<ratio>`；
- `last_runtime.prefill` 增 `selector/budget_ratio/entropy/top_gap/fallback`；
- `/health` 在启用监督器时返回 `supervisor` 统计（restarts/retries/last_recovery_ms）。

## 8. AR007 前缀 KV 复用（增量）

```text
kernel/runtime/prefixcache.py     稳定前缀纯策略
bench/prefixset.py                shared-prefix 数据集
server/chat.py                    session(user) → 稳定原文；cache_n/prefill_ms 观测
matrix                            prompt_cache + 缓存命中指标 + λ_latency
```

- **稳定前缀**：`prefix_cache && session_affinity && user` 时跳过 AEB/记忆/草稿/分配，
  直接发送原文；保证同 session 前缀逐字一致。
- **客户端**：`LlamaServerClient(cache_prompt=...)` 显式开关，避免服务器默认值污染基线。
- **观测**：`timings.cache_n/prompt_ms/prompt_n` → `X-Jev-Prefix-Cache`、`X-Jev-Prefill-Ms`、
  `X-Jev-Prompt-N` 与 `last_runtime.compute`；`usage` 结构不变。
- **选择器**：新增 `lambda_latency`，效用 = `q − λ_t·tokens − λ_l·latency`，避免“省 token 但更慢”被误采纳。
- **冲突**：AEB 按问题选块会破坏前缀；AEB_CACHE 实测 0 命中。会话用 CACHE，单次长文用 AEB。
- **服务器建议**：`--parallel 1` 最大化同前缀命中；多槽需 sticky slot/固定前缀，
  否则跨槽无法复用 KV（大模型配置见 REPRODUCTION.md）。

## 9. AR008 异构投机解码（增量）

```text
kernel/cerebrum/llama_server.py  spec_server_args（draft-simple）
server/chat.py                   timings.draft_n/accepted → X-Jev-Spec/Decode-Tps
bench/generation.py              长生成数据集（zh/en）
matrix                           per-arm spec overlay + accept/tps/identical 指标
```

- **默认关闭**；`[cerebrum.spec] enabled=false` 时行为与 AR007 一致。
- **参数**：draft 模型/线程/ngl/采样全部显式传入；缺资产抛 `JevAssetError`。
- **观测**：`X-Jev-Spec: accepted/draft`、`X-Jev-Decode-Tps`；矩阵汇总接受率与输出一致性。
- **结论**：本机 0.5B CPU draft 无稳定解码收益（S2 接受率 0.624 仍 5.83 tps < B1 8.10），
  默认回退 B1；GPU 复测受 645 MHz 限频影响，历史 AR005–AR007 主表为 CPU-only。

## 10. AR009 CASC + 异构放置（增量）

```text
kernel/prefill/stable.py        文档/问题切分 + 稀有度 + MMR + 压缩 LRU
kernel/runtime/config.py        RuntimeConfig.stable
kernel/kernel.py                StableCompressor 组装（raw embed 批量）
server/chat.py                  稳定前缀路径内压缩；X-Jev-Stable-*
bench/matrix.py                 per-arm 压缩 + embedding_gpu_layers
```

- **查询无关**：压缩只用文档块向量与文档质心，不使用当前问题 → 同文档前缀逐字稳定。
- **二次收益**：压缩结果按 `sha256(doc+config)` LRU；同文档第二问不再 embedding。
- **与 AR007 叠加**：压缩前缀 + 问题尾部，`cache_prompt=true`，11/12 命中。
- **异构放置**：`[embeddings] gpu_layers` 独立；本机 brain CPU 解码、embedding GPU（4.20→3.09s）。
- **组合结论**（POST 修复后）：shared-prefix 上 CASC 38.1s < CACHE 42.2s < B1 52.9s，
  tokens 2662→1079（−59.5%），quality 1.0；needles 上 token −58.7%、quality 1.0、延迟略差。
  禁止把 AEB 的查询相关选块与缓存直接叠加；CASC 是冲突的可执行解法。

## 11. AR010 LADS + POST 窗口修复（增量）

```text
kernel/cerebellum/laya_sequence.py  超长 state 保留尾部（原保头部）
kernel/cerebellum/laya_onnx.py      批处理预截断同样保尾部
server/chat.py                      decision_shadow/decision_post；退化重复守卫
bench/matrix.py                     per-arm 决策开关
```

- **根因**：旧窗口保头部，`max_len=512` 下 assistant 答案被截掉，POST 对任何答案恒定
  `needs_retry`，B1/CACHE 每次白跑第二次生成；历史长 prompt 延迟/ token 被放大。
- **修复**：批处理与序列构造均保尾部；尾部的“最新问题/答案”始终可见。
- **退化守卫**：短周期全串重复（如 `50745074...`）触发一次重试，重试后仍退化则标记降级。
- **LADS**：`decision_shadow=false` 跳过 Laya shadow，`decision_post=false` 跳过 POST 复核；
  `/v1/systemone` 不受影响，chat 头 `X-Jev-Decision-Mode: fast`。
- **结果**：12 题输出完全一致；CACHE_FAST 54.0s→24.6s（−54.4%），选择器 adopt。



## 12. AR011 ADB 决策计算预算（增量）

```text
kernel/decisions/probes.py       apply_permutation_budget(points, budget)
kernel/runtime/config.py         decision_permutations / decision_escalate_margin
server/chat.py                   预算裁剪 + 低 margin 升级 + X-Jev-Decision-*
configs/profiles/latency-best    联合最优档
```

- **问题**：Laya PRE 10 个 qid 中 `intent` 占 8 个置换，长 state 下约 22s/请求。
- **预算**：`decision_permutations=1` 把每点置换裁剪为 1；真实 14 题 applied 决策 100% 一致。
- **升级**：`decision_escalate_margin>0` 时，对低 margin 且被裁剪的 active 点用完整预算重算。
- **默认**：0（不限制），不改变现有部署；`latency-fast` / `latency-best` 显式启用 1。
- **联合**：CASC + 前缀 KV + LADS fast + ADB，shared-prefix 49.2s→14.1s（P50 12.1s），
  quality 1.0、输出一致、tokens 2662→1079。
- **负结果**：GPU Laya 更慢；槽位绑定无增益；head+tail 状态窗口一致率 84% 且有 safety 翻转。


## 13. AR012 CADP ??????????????

```text
kernel/cascade/engine.py       route_short_circuit????????
kernel/runtime/config.py       decision_staged??? false?
server/chat.py                 evaluate_pre?wave1/wave2 + ?????
```

- **???**?prompt/verifier/margin/risk ???? ? ?? PRE?????????? ? ?? PRE?
- **??**?safety=unsafe?full?intent=decision?direct?needs_reasoning=reasoning?full?
  ?????light???? needs_generation/complexity?
- **??**????`latency-fast` / `latency-best` ???
- **??**?B1 51.6s ? CADP_CACHE_FAST 10.0s ? CADP_CASC_FAST 10.6s?P50 8.8s??
  quality 1.0???????? 12/12 ????? AR011 ?? 26%?
- **???**??? Laya ?? prompt ?????????? 100% ???

## 14. AR013 RDMP：最小路由探针 + 保守确认

```text
kernel/decisions/framework.py  minimize_points：1 探针 + 1 置换
kernel/cascade/engine.py       needs_minimal_confirmation
kernel/runtime/config.py       decision_staged_minimal（默认 false）
server/chat.py                 evaluate_pre：最小 wave1 → 非 FULL 完整确认
```

- **触发**：仅 `decision_staged=true` 且无其他 PRE 消费者。
- **流程**：最小 wave1 判 FULL → 直接采用；DIRECT/LIGHT/None → 完整 wave1 复算。
- **安全**：真实 16 题仅 2 次保守翻转（LIGHT→FULL），确认路径保证无激进翻转。
- **观测**：`X-Jev-Decision-Minimal: on|confirm`。
- **结果**：CADP_CACHE_FAST 6.85s → RDMP_CACHE_FAST 5.05s（P50 4.33s，−26%）；
  RDMP_CASC_FAST P50 4.33s、tokens 1079、quality 1.0。
- **负结果**：wave1 状态窗口缩到 384/320/256 会改变 needle 路由，不采用。

## 15. AR014 LCPT-RFG：长上下文精度分层 + reasoning-first 门控

```text
kernel/cerebellum/quantize.py  fp32 → int8（合并外部数据 + 动态量化）
kernel/cerebellum/registry.py  create_laya_int8（缺失则禁用）
kernel/runtime/config.py       decision_long_context_tokens（默认 0）
server/chat.py                 _long_context_gate：int8 单行 reasoning → FULL
cli/main.py                    jev prepare-laya-int8
```

- **触发**：staged && 无其他 PRE 消费者 && state token > 阈值 && int8 就位。
- **安全**：门控只可能产生 FULL；未命中回退 fp32 RDMP，不可能产生更便宜路由。
- **观测**：`X-Jev-Decision-Long: int8-reason-full`。
- **结果**：隔离 P50 23.00s→19.46s；联合 RDMP_CACHE_FAST 6.63s→**2.53s**；
  LCPT_CASC_FAST **P50 2.42s、tokens 1079、quality 1.0**（B1 44.43s，−94.6%）。
- **回归**：needles（~2810 tokens）不触发门控，37.88s vs RDMP 38.25s，质量 1.0。
- **资产**：int8 模型 427MB，不入仓，命令生成；服务器配置
  `server-{7b,14b}-lcpt-casc.toml`。

## 16. AR015 AGW：自适应门控窗口

```text
kernel/runtime/config.py       decision_long_context_gate_tokens（默认 384）
kernel/cerebellum/registry.py  create_laya_int8(max_state_tokens=...)
server/chat.py                 header 记录 w384
```

- **作用域**：只缩短 int8 门控后端窗口；fp32 兜底与短问完全不变。
- **安全**：窗口变短只影响“是否直接判 FULL”；未命中仍完整 RDMP。
- **结果**：shared 门控命中 12/12、门控 1.51s→1.05s；联合
  AGW_CASC_FAST mean 3.765s / P50 1.969s（相对 LCPT_CASC mean −1.9%）。
- **取舍**：隔离矩阵无收益；联合矩阵小幅正收益，选择器 adopt。
- **配置**：`agw.toml` / `agw-casc.toml`；profile 已启用 384。
