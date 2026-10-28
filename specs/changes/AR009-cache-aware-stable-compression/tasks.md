# [AR009] 任务跟踪

| 字段 | 内容 |
|------|------|
| AR 编号 | AR009 |
| 关联 srs.md | ./srs.md |
| 关联 design.md | ./design.md |
| 创建日期 | 2026-10-27 |

## 任务列表

| ID | 任务描述 | 依赖 | 状态 | 备注 |
|----|---------|------|------|------|
| T001 | `stable.py`：切分/稀有度/稳定选择/LRU 缓存/回退 | - | passing | 9 单测 |
| T002 | `RuntimeConfig` + Kernel 构造注入 StableCompressor | T001 | passing | 默认关闭 |
| T003 | chat 接入 + 压缩/缓存观测头 | T002 | passing | 前缀逐字稳定 |
| T004 | brain/embedding 独立 gpu_layers（kernel+matrix） | T002 | passing | CPU 解码 + GPU embedding |
| T005 | 单测：确定性/缓存/回退/配置 | T001 | passing | 无 mock |
| T006 | 真实集成：同文档两问 miss→hit、cache_n>0 | T003 | passing | `test_casc_real` |
| T007 | 真实实验：shared_prefix / needles / 权重扫描 | T006 | passing | CACHE/CASC 分工 |
| T008 | 7B/14B CASC 配置 + 复刻文档 | T007 | passing | external |
| T009 | 结果文档/PORTFOLIO/README/REPRODUCTION | T007 | passing | 诚实记录 |
| T010 | 全量测试+ruff+review+ST+归档+推送 | T009 | passing | 687 unit |

## 状态说明

- `pending` / `in_progress` / `passing` / `failed`

## 进度记录

### 2026-10-27 / 2026-10-28

- 需求/设计完成：CASC = 查询无关文档压缩 + 压缩 LRU + 前缀 KV；异构放置拆分 embedding/brain。
- `stable.py`：文档/问题切分、质心稀有度、MMR、线程安全 LRU、embedding 失败/无收益回退。
- 接入：`RuntimeConfig.stable`、Kernel `StableCompressor`、chat 稳定前缀路径、`X-Jev-Stable-*`。
- 异构：`[embeddings] gpu_layers` + matrix `embedding_gpu_layers`；首问 embedding 改为批量 raw 调用。
- 真实集成：同文档两问，第二问 stable cache + prefix cache 命中，答案正确。
- shared-prefix 12 题（CPU 解码 + GPU embedding，ratio 0.4）：
  - B1 5354 tok / 53.1s；CACHE 5354 tok / 37.2s / 12 命中；
  - CASC 3771 tok（−29.6%）/ 45.8s / 12 命中 / quality 1.0。
- needles 6 题：B1 1847 tok；CASC 762 tok（−58.7%），quality 1.0，`adopt CASC`。
- 权重扫描：λ_cost=0.15/0.30 → CACHE；λ_cost=0.50 → CASC。默认延迟场景回退 CACHE。
- GPU embedding 实测 4.20s → 3.09s（−26.5%）；GPU 显存 2.1GB，确认真实参与。
- 687 单测通过、ruff 零告警；`test_casc_real`、`test_prefix_cache_real`、`test_spec_decode_real` 通过。

## 阶段门控记录

### 2026-10-27 req 门控记录

- 门控结果：PASS（自动化模式，用户授权跳过逐阶段确认）
- 审查项数：srs/tasks 完整性、验收可测性、约束与回退
- 修复的问题：无
- 审查代理：自动化 harnessX（用户指定无需人工确认）

### 2026-10-27 design 门控记录

- 门控结果：PASS（自动化模式）
- 审查项数：数据流、模块接口、异常/回退、实验设计、测试策略
- 修复的问题：无
- 审查代理：自动化 harnessX

### 2026-10-28 review 记录

- 结果：PASS
- 检查：需求/设计符合性、默认关闭、线协议只增、依赖方向、无 mock、文档与真实结果一致
- 发现并修复：matrix 未注入 StableCompressor（导致首轮 CASC 实际未压缩）；embedding 批处理导致首问 31 次 HTTP；`X-Jev-Stable-Ms` 缺失
- 审查代理：自动化 harnessX

### 2026-10-28 ST 记录

- 结果：PASS
- 真实测试：`test_casc_real`、`test_prefix_cache_real`、`test_spec_decode_real`
- 全量：687 unit passed；ruff clean
- 验收：quality 1.0；shared tokens −29.6%、12/12 命中；needles tokens −58.7%；
  默认延迟场景 CACHE 最优、λ_cost ≥0.5 CASC 最优；7B/14B 配置未运行不外推
- 审查代理：自动化 harnessX
