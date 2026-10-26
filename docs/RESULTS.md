# AR005 真实实验结果（本机 1.5B / Laya / 真 llama-server）

> 所有数字来自 `jev matrix` 真实后端运行；原始数据在 `benchmarks/results/<run>/results.json`。
> 运行环境：Windows 11，Quadro RTX 5000 16GB，Qwen2.5-1.5B-Instruct-Q4_K_M，CPU/GPU 共用机器（存在外部负载）。

## 实验 1：唯一问题（20 题 ×4 方案）

命令：`jev matrix --config configs/experiments/ar005-local-20.toml`

| arm | quality | completion tok | prompt tok | mean latency | 路由 |
| --- | --- | --- | --- | --- | --- |
| B0（全 full） | **0.70** | 24.9 | 57.6 | 11.7s | full 20 |
| B1（AR002） | 0.60 | 29.2 | 107.8 | 16.4s | light 18 / full 2 |
| B2（AR003） | 0.50 | 26.0 | 86.8 | 17.6s | light 19 / full 1 |
| AR005 | 0.50 | 26.0 | 86.8 | 17.0s | light 19 / full 1 |

选择器：`adopt B0`（效用下界 +0.080），即**自动回退/选择当前最优组合**。
AR005 与 B2 相同：本次运行中 Conformal 风险门找到的可行阈值不足（降级损失高），
缓存准入校准因 embedding 服务在该时段不可用而未启用，系统按设计**失败安全回退**。

## 实验 2：重复/复述（10 题 ×2 轮 ×4 方案）

命令：`jev matrix --config configs/experiments/ar005-repeat-10x2.toml`

| arm | quality | completion tok | prompt tok | mean latency | cache hits |
| --- | --- | --- | --- | --- | --- |
| B0 | **0.60** | 20.6 | 57.8 | 12.2s | 0 |
| B1 | 0.40 | 26.4 | 133.4 | 16.2s | 0 |
| B2 | 0.35 | 13.2 | 66.7 | 9.3s | **10** |
| AR005 | 0.35 | 13.2 | 66.7 | 9.1s | **10** |

结论：语义/精确缓存真实生效——第二轮命中后 completion tokens 较 B0 下降 **36%**，
延迟下降 **25%**；但 light 路径质量低于 full。选择器：`rollback B2`（无正效用下界）。

## 关键发现（诚实记录）

1. **缓存价值已验证**：重复请求命中后 token/延迟显著下降，且不改变答案。
2. **风险门按预期失败安全**：当降级会带来不可接受损失时，CRC 找不到可行阈值 → 禁用，不退化为乱降级。
3. **当前实验的偏差**：为控制 CPU/GPU 时间，`max_tokens=32`，light 路径被截断，
   质量低于 full；这**不利于** light/缓存组合。生产默认 light 为 128 tokens，需在正式实验中放开。
4. **AR005 增益未显现**：本次风险/准入校准未启用（样本/服务条件不满足），AR005≡B2；
   选择器正确地选择了 B0（唯一问题）或回退 B2（重复场景）。
5. **环境风险**：外部 GPU 负载与 llama-server 长时间运行后偶发假死；矩阵已改为**每 arm 重启大脑**
   并写 `progress.json` 检查点，避免整轮丢失。

## 下一步（正式复现）

- 用 `max_tokens=128`（或按 light 设计）重跑 30 题，避免截断偏差；
- 在 **GPU 空闲、embedding 就绪** 时运行，使 `risk.json` / `admission.json` 生成；
- 增加长上下文 needles 40 题，验证 JCBA 的 prompt token ↓≥30% 且保留率下降 ≤2pt；
- 服务器 7B/14B 按 `REPRODUCTION.md` 执行，结果单独补录。

> 未达标即未达标：上述数字不支持「AR005 已显著优于 B2」的结论；选择/回退机制按设计工作。
