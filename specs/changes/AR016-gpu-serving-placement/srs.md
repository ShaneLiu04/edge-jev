# [AR016] 需求设计说明书

| 字段 | 内容 |
|------|------|
| AR 编号 | AR016 |
| AR 主题 | GSP：端侧 GPU 服务放置与可复现接线 |
| 关联 SR | 充分利用本地 GPU；让 GPU/CPU 放置可配置、可复现、可回退 |
| 日期 | 2026-10-30 |
| 状态 | Approved（自动化 harnessX，用户已授权无需逐步确认） |

## 1. 背景

此前结论“brain 保持 CPU”来自长输出/满载场景。当前最优 AGW_CASC_FAST 是
**短输出（5 tokens）+ 长 prefill**，GPU 的 prefill 优势可能重新占优。

真实同配置对照（shared-prefix 12 题，GPU embedding 均开启）：

| 放置 | mean | P50 | prefill | decode tps |
| --- | --- | --- | --- | --- |
| CPU decode（gpu_layers=0） | 3.462s | 2.032s | 726ms | 7.83 |
| **GPU offload（gpu_layers=99）** | **3.054s** | **1.753s** | **357ms** | 7.78 |

GPU 放置 mean −11.8%、P50 −13.7%，decode 不变；B1 也从 48.7s→45.4s。

## 2. 目标

- 新增 `[cerebrum] gpu_layers`（默认 0），生产 `jev serve` 真正传给 llama-server。
- 矩阵/服务器实验显式写明 GPU 放置；保留 CPU 作为无 GPU/显存不足回退。
- 真实对照实验 + 文档；若综合无收益则保留默认 0 并记录负结果。

## 3. 功能需求

| ID | 需求 | 验收 |
|----|------|------|
| FR16-1 | `[cerebrum] gpu_layers` 可配置并传给 `--n-gpu-layers` | 单测 |
| FR16-2 | 默认 0，不改变无 GPU 环境行为 | 回归 |
| FR16-3 | 提供 GPU 最优实验配置与 7B/14B 服务器说明 | 配置/文档 |
| FR16-4 | GPU vs CPU 真实对照矩阵 | assets |
| FR16-5 | 质量/输出/路由一致 | 矩阵 |

## 4. 非功能需求

| 类型 | 指标 | 要求 |
|------|------|------|
| 性能 | 当前最优组合 | GPU 放置有正收益 |
| 可靠性 | 无 GPU/显存不足 | 默认 CPU 可回退 |
| 复现 | 配置 | 显式 gpu_layers，不依赖隐式环境 |
