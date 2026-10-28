# [AR008] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR008 |
| AR 主题 | 端侧异构投机解码（0.5B draft + 1.5B target） |
| 关联 SR | 核心创新：端侧快慢双系统的真实低延迟与可复现 |
| 日期 | 2026-10-27 |
| 状态 | Verified（真实实验完成，结论 rollback，默认关闭） |

## 1. 背景与目标

AR005–AR007 已把上下文与 prefill 成本压低，但**解码阶段**仍是端侧瓶颈：
1.5B Q4 在本机 CPU 上约 7–9 tok/s，长输出场景端到端延迟仍高。

投机解码（speculative decoding）用一个小 draft 模型先草拟若干 token，再由 target 一次验证，
在**接受率高**时可减少 target 的前向次数。本 AR 要回答三个问题：

1. 0.5B draft 放在空闲 CPU、1.5B target 放在 GPU 的异构方案，在本机是否真的更快？
2. 中文/英文、不同 draft 长度下，接受率与解码吞吐如何变化？
3. 若无稳定收益，是否按设计回退且不污染默认路径？

对标与取舍：

| 来源 | 核心思想 | 本项目落地 |
|------|---------|-----------|
| Leviathan et al. / Chen et al. | draft-then-verify 投机解码 | 复用 llama.cpp `--spec-draft-model`，Jev 只做配置、观测、选择与回退 |
| llama.cpp speculative | draft-simple / draft 系列 | 采用 `draft-simple` + greedy，避免采样差异破坏验证 |
| EAGLE/Medusa | 训练/额外头 | 不训练、不改模型结构，Out of Scope |

## 2. 需求范围

**In Scope：**
- `[cerebrum.spec]` 配置与 `spec_server_args`（默认关闭；缺资产抛 `JevAssetError`）。
- `server/chat.py` 记录 `draft_n` / `accepted` / `decode_tps`，输出 `X-Jev-Spec`、`X-Jev-Decode-Tps`。
- `bench/matrix.py` 支持 per-arm spec overlay、spec 指标与 `outputs_identical`。
- 中文/英文长生成数据集与真实实验；GPU/CPU 对照。
- 无增益回退 B1，并在文档中如实记录。

**Out of Scope：**
- 训练 draft 模型或新增 Medusa/EAGLE 头；
- 修改 llama.cpp 推理引擎；
- 在无资产的 7B/14B 上宣称真实收益（仅提供配置）。

## 3. 功能需求

### 3.1 投机解码开关（FR8-1）

**描述：** `[cerebrum.spec] enabled=true` 时启动参数追加 draft 模型与投机参数。
**期望行为：**
- 参数：`--spec-draft-model`、`--spec-type`、`--spec-draft-n-max`、
  `--spec-draft-ngl`、`--spec-draft-threads`、`--spec-draft-sampling`；
- `enabled=false` 时不追加任何参数，行为与 AR007 一致；
- draft 资产缺失 → `JevAssetError`，不静默降级为普通解码。

### 3.2 可观测（FR8-2）

- 响应头 `X-Jev-Spec: <accepted>/<draft>`、`X-Jev-Decode-Tps: <tps>`；
- `last_runtime.spec = {draft_n, accepted, decode_tps}`（启用时）；
- 矩阵汇总 `mean_decode_tps`、`spec_accept_rate`、`outputs_identical`。

### 3.3 真实评测（FR8-3）

- 数据集：中文/英文「短 prompt + 长输出」，gold 为内容守卫（命中/复用语义）；
- 真实 Laya（或 off）+ 真实 llama-server + 真实 draft 资产；
- 必跑臂：B1（无投机）、SPEC（draft n_max 扫 2/4/8）；
- 对照：CPU-only 与 CUDA target；
- 指标：quality、decode tps、端到端延迟、接受率、输出一致性。

### 3.4 选择与回退（FR8-4）

- 沿用配对 bootstrap 95%CI；**无正下界或质量下降 >1pt → rollback**；
- 不允许以单次异常快跑作为 adopt 依据；
- 回退后默认配置保持 `spec.enabled=false`。

### 3.5 环境真实性（FR8-5）

- 必须验证 `llama-server --list-devices` 与 `ggml_cuda.dll` 依赖；
- CUDA 运行库缺失时不得声称 GPU 结果；
- GPU 时钟被限制时必须记录实测 tg/pp，不得把 CPU 结果标为 GPU。

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 兼容 | 默认行为 | `enabled=false` 时输出与 AR007 一致 |
| 正确性 | greedy 下验证一致性 | 记录 `outputs_identical`，差异必须可解释 |
| 资产 | 缺失处理 | 抛 `JevAssetError`，不下载、不猜测 |
| 可观测 | 头/台账 | accepted/draft、decode_tps 可见 |
| 回退 | 失败安全 | 无增益自动 rollback，不写 profile |

## 5. 约束与假设

- draft 与 target 必须同 tokenizer 族（Qwen2.5），否则验证语义不成立；
- greedy 采样下验证更稳定，但 draft/target 分布差异仍会带来输出差异；
- 本机 GPU 为 Quadro RTX 5000，实测被限频，不能代表正常 GPU 性能；
- CPU 上 draft 与 target 共享核心，投机收益受线程竞争影响。

## 6. 术语

| 术语 | 定义 |
|------|------|
| 接受率 | accepted / draft_n，target 验证通过的草拟 token 比例 |
| draft-simple | llama.cpp 的基础投机实现 |
| 异构 | draft 与 target 使用不同设备（本 AR 为 CPU draft / GPU target） |
| outputs_identical | 与 B1 基线答案逐字一致的比例 |
