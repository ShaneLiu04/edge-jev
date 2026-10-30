# [AR014] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR014 |
| AR 主题 | LCPT-RFG：长上下文精度分层 + reasoning-first 门控 |
| 关联 SR | 端侧长文档场景继续降低真实端到端延迟，且不改变短问/needles 路由与质量 |
| 日期 | 2026-10-30 |
| 状态 | Approved（自动化 harnessX，用户已授权无需逐步确认） |

## 1. 背景

AR013 后联合最优 RDMP_CASC_FAST：shared-prefix P50 4.33s、tokens 1079、quality 1.0。
真实分解显示 Laya wave1（3 个决策行）仍是主要成本。

真实探测（16 条多样 prompt，真实 Laya）：

1. 动态 int8 Laya（ORT `quantize_dynamic`）：1.5× 加速；路由翻转仅保守方向。
2. 长上下文（state > 4000 tokens）用 int8 先算 `needs_reasoning`：
   - 若 applied ≠ no_reasoning → 直接 FULL（1 行）；
   - 否则回退 fp32 完整 RDMP；
   - 与 fp32 RDMP 路由 **16/16 一致、0 激进翻转**；
   - shared 6 题 Laya 决策 5.45s→2.56s（**2.1×**）。
3. needle（~2810 tokens）保持 fp32，不引入 light→full 回退。

## 2. 目标

- 新增可选 int8 Laya 资产，由 `jev prepare-laya-int8` 从已 fetch 的 fp32 模型确定性生成。
- `decision_long_context_tokens`（默认 0 关闭）：仅当 state token 数超过阈值时启用
  int8 reasoning-first 门控；命中即可用 1 行完成 FULL 路由。
- 未命中/无 int8 资产 → 完全回退 AR013 RDMP，行为不变。
- 真实数据验证：路由与 RDMP 一致、0 激进翻转、质量/输出不降、端到端有正收益。
- 服务器 7B/14B 配置写明，未实机运行不得宣称收益。

## 3. 功能需求

| ID | 需求 | 验收 |
|----|------|------|
| FR14-1 | `jev prepare-laya-int8` 从 fp32 生成 int8 模型（含外部数据） | 真实文件 + 可加载 |
| FR14-2 | `[cerebellum.laya_onnx] int8_model_path` 可配置；缺失不报错 | 单测/启动 |
| FR14-3 | `decision_long_context_tokens=0` 时行为与 AR013 完全一致 | 回归 |
| FR14-4 | 超过阈值且有 int8：先算 `needs_reasoning`（单行） | 集成测试 |
| FR14-5 | applied≠no_reasoning → FULL，仅记录该决策 | 集成测试 |
| FR14-6 | 否则回退 AR013 RDMP（fp32），不降低安全性 | 集成测试 |
| FR14-7 | 观测 `X-Jev-Decision-Long: int8-reason-full` | 集成测试 |
| FR14-8 | 真实 mixed 16 题路由一致、0 激进翻转 | assets+slow |
| FR14-9 | shared-prefix 端到端 CI 下界 >0 才 adopt | matrix |

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | shared Laya 决策 | ≥1.5× 加速 |
| 安全 | 激进路由翻转 | 0 |
| 资源 | int8 模型 | ≤500MB，CPU 可加载 |
| 兼容 | 无 int8/阈值 0 | 与 AR013 行为一致 |
| 测试 | 后端 | 无 mock |

## 5. 约束

- int8 资产不入仓（`models/` gitignored），由命令生成。
- 量化工作流锁定 onnx/onnxruntime 版本，失败必须给出明确错误。
- 7B/14B 只提供配置，不在本机运行。
