# -*- coding: utf-8 -*-
"""Generate the Jev project cover: docs/assets/cover.png and cover.svg."""
from pathlib import Path

import matplotlib
import matplotlib.font_manager as fm
import matplotlib.patheffects as pe
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path as MplPath

W, H = 12.8, 6.4
OUT_DIR = Path(__file__).resolve().parent / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ZH = fm.FontProperties(family="Microsoft YaHei")
ZH_BOLD = fm.FontProperties(family="Microsoft YaHei", weight="bold")
MONO = fm.FontProperties(family="DejaVu Sans Mono")

BG0 = "#050912"
BG1 = "#0B1730"
TEAL = "#2DD4BF"
TEAL_L = "#5EEAD4"
CYAN = "#38BDF8"
VIOLET = "#A78BFA"
INDIGO = "#818CF8"
TXT = "#F1F5F9"
TXT2 = "#C7D2E8"
MUT = "#8EA3C8"
DIM = "#64748B"
FAINT = "#475569"
LINE = "#1E2B4A"

fig = plt.figure(figsize=(W, H), dpi=200, facecolor=BG0)
ax = fig.add_axes([0, 0, 1, 1], facecolor="none")
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")


def radial_glow(cx, cy, rx, ry, color, peak, z=-15):
    n = 360
    yy, xx = np.mgrid[-1:1:n * 1j, -1:1:n * 1j]
    radius = np.sqrt(xx ** 2 + yy ** 2)
    alpha = np.clip(1 - radius, 0, 1) ** 2.2 * peak
    rgba = np.zeros((n, n, 4))
    rgba[..., :3] = to_rgb(color)
    rgba[..., 3] = alpha
    ax.imshow(
        rgba,
        extent=[cx - rx, cx + rx, cy - ry, cy + ry],
        origin="lower",
        aspect="auto",
        interpolation="bilinear",
        zorder=z,
    )


def grad_round_rect(x, y, w, h, r, c1, c2, z=5, ec=None, lw=0.9):
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={r}",
        fc="none", ec="none", zorder=z,
    )
    ax.add_patch(box)
    n = 256
    yy, xx = np.mgrid[0:1:n * 1j, 0:1:n * 1j]
    t = np.clip((xx + yy) / 2, 0, 1)
    a, b = np.array(to_rgb(c1)), np.array(to_rgb(c2))
    img = np.zeros((n, n, 4))
    for k in range(3):
        img[..., k] = a[k] + (b[k] - a[k]) * t
    img[..., 3] = 1
    im = ax.imshow(
        img,
        extent=[x, x + w, y, y + h],
        origin="lower",
        aspect="auto",
        interpolation="bilinear",
        zorder=z + 0.05,
    )
    im.set_clip_path(box)
    if ec:
        ax.add_patch(
            FancyBboxPatch(
                (x, y), w, h,
                boxstyle=f"round,pad=0,rounding_size={r}",
                fc="none", ec=ec, lw=lw, alpha=0.55, zorder=z + 0.1,
            )
        )


def chip(x, y, w, h, label, accent, size=9.2, z=4):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0,rounding_size=0.11",
            fc="#0C172C", ec="#26375F", lw=0.75, alpha=0.92, zorder=z,
        )
    )
    ax.add_patch(Circle((x + 0.16, y + h / 2), 0.032, color=accent, zorder=z + 1))
    ax.text(
        x + 0.28, y + h / 2 + 0.005, label,
        fontsize=size, color=TXT2, fontproperties=ZH, va="center", zorder=z + 1,
    )


n = 512
t = np.linspace(0, 1, n).reshape(-1, 1)
top, mid, bot = np.array(to_rgb("#0C1934")), np.array(to_rgb(BG1)), np.array(to_rgb(BG0))
base = np.zeros((n, 2, 3))
for i, v in enumerate(t[:, 0]):
    if v < 0.55:
        base[i, :, :] = bot + (mid - bot) * (v / 0.55)
    else:
        base[i, :, :] = mid + (top - mid) * ((v - 0.55) / 0.45)
ax.imshow(base, extent=[0, W, 0, H], origin="lower", aspect="auto", zorder=-30)

radial_glow(11.4, 6.1, 3.6, 2.7, TEAL, 0.20)
radial_glow(7.4, 0.1, 3.8, 2.4, VIOLET, 0.17)
radial_glow(5.0, 3.4, 3.0, 2.2, CYAN, 0.07)

for x in np.arange(0, W + 0.01, 0.32):
    ax.plot([x, x], [0, H], color="#8B9DC9", lw=0.32, alpha=0.042, zorder=-20)
for y in np.arange(0, H + 0.01, 0.32):
    ax.plot([0, W], [y, y], color="#8B9DC9", lw=0.32, alpha=0.042, zorder=-20)

for r in (1.95, 2.30, 2.65):
    ax.add_patch(Circle((10.15, 3.0), r, fill=False, ec=TEAL, lw=0.55, alpha=0.04, zorder=-18))

seg = 220
c1, c2, c3 = np.array(to_rgb(TEAL)), np.array(to_rgb(CYAN)), np.array(to_rgb(VIOLET))
for i in range(seg):
    v = i / (seg - 1)
    c = c1 + (c2 - c1) * (v / 0.5) if v < 0.5 else c2 + (c3 - c2) * ((v - 0.5) / 0.5)
    ax.add_patch(
        Rectangle((i * W / seg, H - 0.032), W / seg + 0.001, 0.032, color=c, lw=0, zorder=8)
    )

ax.text(0.75, 5.93, "E D G E - N A T I V E   ·   D U A L - S Y S T E M   A I   R U N T I M E",
        fontsize=7.5, color=TEAL_L, alpha=0.92, family="DejaVu Sans", zorder=6)

grad_round_rect(0.72, 5.02, 0.60, 0.60, 0.155, TEAL, VIOLET, z=3, ec=TEAL_L, lw=0.8)
mark = "#06101E"
jpath = MplPath(
    [
        (1.095, 5.545), (1.095, 5.230),
        (1.095, 5.110), (0.990, 5.095), (0.915, 5.155),
        (0.893, 5.178), (0.893, 5.230), (0.915, 5.268),
    ],
    [
        MplPath.MOVETO, MplPath.LINETO,
        MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
        MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
    ],
)
ax.add_patch(
    PathPatch(jpath, fc="none", ec=mark, lw=3.4, capstyle="round", joinstyle="round", zorder=6)
)

title_glow = [pe.withStroke(linewidth=10, foreground=(0.18, 0.83, 0.75, 0.14))]
title = ax.text(1.52, 5.28, "Jev", fontsize=64, color=TXT, family="DejaVu Sans",
                fontweight="bold", va="center", zorder=6, path_effects=title_glow)
fig.canvas.draw()
bb = title.get_window_extent(fig.canvas.get_renderer())
x_end = ax.transData.inverted().transform((bb.x1, 0))[0]
ax.text(x_end + 0.13, 5.27, "捷思", fontsize=38, color=TEAL_L, fontproperties=ZH_BOLD,
        va="center", zorder=6)

ax.plot([0.76, 2.35], [4.83, 4.83], color=TEAL, lw=1.4, alpha=0.55, zorder=5)
ax.plot([2.35, 3.55], [4.83, 4.83], color=VIOLET, lw=1.4, alpha=0.35, zorder=5)

ax.text(0.75, 4.47, "端侧快慢双系统 AI 运行时", fontsize=20, color=TXT,
        fontproperties=ZH_BOLD, zorder=6)
ax.text(0.75, 4.10, "System 1 小脑直答  ·  System 2 大脑深思", fontsize=11.5,
        color=TXT2, fontproperties=ZH, zorder=6)
ax.text(0.75, 3.83, "类型化决策  /  级联路由  /  风险可控降级  /  级联门控  /  接地双脑", fontsize=10.3,
        color=MUT, fontproperties=ZH, zorder=6)

chip(0.75, 3.30, 1.45, 0.36, "全本地运行", TEAL)
chip(2.36, 3.30, 1.55, 0.36, "18 决策点", CYAN)
chip(4.07, 3.30, 2.42, 0.36, "direct / light / full", VIOLET, size=8.4)
chip(0.75, 2.84, 2.05, 0.36, "Conformal 风险门", TEAL)
chip(2.96, 2.84, 1.95, 0.36, "JCBA 联合预算", INDIGO)
chip(5.07, 2.84, 1.42, 0.36, "语义缓存", VIOLET)

ax.plot([0.75, 6.55], [2.42, 2.42], color=LINE, lw=0.8, zorder=4)
for sx in (2.04, 3.51, 4.98):
    ax.plot([sx, sx], [1.38, 2.08], color=LINE, lw=0.7, alpha=0.8, zorder=4)

stats = [
    ("0.72s", "P50 延迟", "CGW 1.49s → 0.72s", TEAL_L),
    ("1.01s", "平均延迟", "9 小模型 / 3 升级", TXT),
    ("1079", "CASC tokens", "quality 1.0", VIOLET),
    ("731", "单元测试通过", "67 浅克隆复现", TXT),
]
for i, (num, label, sub, color) in enumerate(stats):
    x = 0.75 + i * 1.47
    ax.text(x, 1.98, num, fontsize=25, color=color, family="DejaVu Sans",
            fontweight="bold", va="center", zorder=6)
    ax.text(x, 1.66, label, fontsize=8.0, color=TXT2, fontproperties=ZH, zorder=6)
    ax.text(x, 1.47, sub, fontsize=7.1, color=DIM, fontproperties=ZH, zorder=6)

ax.text(0.75, 1.11, "AR018 GTB 实测  ·  shared-prefix 12 题  ·  真实后端  ·  零 mock",
        fontsize=7.6, color=DIM, fontproperties=ZH, zorder=6)

ax.plot([0.75, 6.55], [0.66, 0.66], color=LINE, lw=0.7, alpha=0.8, zorder=4)
ax.text(0.75, 0.38, "github.com/ShaneLiu04/edge-jev", fontsize=8.8,
        color=DIM, family="DejaVu Sans", zorder=6)
ax.text(6.55, 0.38, "Python 3.11+  ·  MIT", fontsize=8.8, color=DIM,
        fontproperties=ZH, ha="right", zorder=6)

PX, PY, PW, PH = 7.55, 0.55, 4.75, 5.37
ax.add_patch(
    FancyBboxPatch(
        (PX, PY), PW, PH,
        boxstyle="round,pad=0,rounding_size=0.22",
        fc="#0A1324", ec="#243553", lw=1.0, alpha=0.94, zorder=1,
    )
)
ax.add_patch(
    FancyBboxPatch(
        (PX, PY), PW, PH,
        boxstyle="round,pad=0,rounding_size=0.22",
        fc="none", ec=TEAL, lw=3.5, alpha=0.055, zorder=0,
    )
)

ax.text(7.85, 5.62, "一次请求的快慢协同", fontsize=11.2, color=TXT,
        fontproperties=ZH_BOLD, zorder=6)
ax.text(12.0, 5.62, "AR018 · GTB", fontsize=8.0, color=TEAL_L,
        family="DejaVu Sans", fontweight="bold", ha="right", zorder=6)
ax.text(7.85, 5.38, "FAST / SLOW CASCADE  ·  LOCAL-FIRST", fontsize=7.0,
        color=DIM, family="DejaVu Sans", zorder=6)
ax.plot([7.85, 12.0], [5.22, 5.22], color=LINE, lw=0.8, zorder=4)

ax.add_patch(
    FancyBboxPatch(
        (9.00, 4.70), 1.85, 0.38,
        boxstyle="round,pad=0,rounding_size=0.12",
        fc="#0E1B33", ec="#31446F", lw=0.8, zorder=4,
    )
)
ax.text(9.925, 4.89, "用户 Query", fontsize=9.4, color=TXT2, fontproperties=ZH,
        ha="center", va="center", zorder=6)
ax.text(8.82, 4.89, "IN", fontsize=6.2, color=FAINT, family="DejaVu Sans",
        ha="right", va="center", zorder=6)

ax.add_patch(
    FancyArrowPatch(
        (9.925, 4.70), (9.925, 4.30),
        arrowstyle="-|>", mutation_scale=10, lw=1.1, color="#4B5C82", zorder=5,
    )
)

ax.add_patch(
    FancyBboxPatch(
        (8.00, 3.62), 3.85, 0.64,
        boxstyle="round,pad=0,rounding_size=0.13",
        fc="#101C36", ec="#3B82F6", lw=0.9, alpha=0.98, zorder=4,
    )
)
ax.text(8.20, 3.96, "类型化路由器", fontsize=10.2, color=TXT,
        fontproperties=ZH_BOLD, zorder=6)
ax.text(8.20, 3.77, "预算  /  不确定性  /  前缀指纹  /  熔断", fontsize=7.0,
        color=MUT, fontproperties=ZH, zorder=6)
ax.text(11.62, 3.96, "18 DP", fontsize=8.0, color=CYAN, family="DejaVu Sans",
        fontweight="bold", ha="right", zorder=6)
ax.text(11.62, 3.78, "PRE 9 · MID 4 · POST 5", fontsize=5.8, color=DIM,
        family="DejaVu Sans", ha="right", zorder=6)

ax.add_patch(
    FancyArrowPatch(
        (9.55, 3.62), (8.86, 3.29),
        connectionstyle="arc3,rad=0.14", arrowstyle="-|>", mutation_scale=10,
        lw=1.15, color=TEAL, zorder=5,
    )
)
ax.add_patch(
    FancyArrowPatch(
        (10.30, 3.62), (10.99, 3.29),
        connectionstyle="arc3,rad=-0.14", arrowstyle="-|>", mutation_scale=10,
        lw=1.15, color=VIOLET, linestyle=(0, (3.2, 2.2)), zorder=5,
    )
)

for x, accent, name, l1, l2, badge, badge_col in [
    (7.80, TEAL, "System 1 · 小脑", "Laya ONNX · 进程内", "direct / light", "P50 < 20ms", TEAL_L),
    (9.95, VIOLET, "System 2 · 大脑", "Qwen 1.5B + 0.5B · GTB", "接地验证 · 失败升级", "GPU offload", VIOLET),
]:
    ax.add_patch(
        FancyBboxPatch(
            (x, 2.26), 2.10, 1.00,
            boxstyle="round,pad=0,rounding_size=0.14",
            fc="#0D1830", ec="#2A3B66", lw=0.85, zorder=4,
        )
    )
    ax.add_patch(Rectangle((x + 0.02, 2.38), 0.05, 0.76, color=accent, zorder=5))
    ax.text(x + 0.19, 3.08, name, fontsize=9.2, color=accent,
            fontproperties=ZH_BOLD, zorder=6)
    ax.text(x + 0.19, 2.83, l1, fontsize=6.9, color=TXT2,
            fontproperties=ZH, zorder=6)
    ax.text(x + 0.19, 2.62, l2, fontsize=6.9, color=MUT,
            fontproperties=ZH, zorder=6)
    ax.text(x + 1.92, 2.44, badge, fontsize=6.8, color=badge_col,
            fontproperties=ZH, ha="right", zorder=6)

ax.add_patch(
    FancyArrowPatch(
        (8.85, 2.26), (9.45, 2.08),
        connectionstyle="arc3,rad=-0.10", arrowstyle="-|>", mutation_scale=9,
        lw=1.0, color=TEAL, zorder=5,
    )
)
ax.add_patch(
    FancyArrowPatch(
        (11.00, 2.26), (10.40, 2.08),
        connectionstyle="arc3,rad=0.10", arrowstyle="-|>", mutation_scale=9,
        lw=1.0, color=VIOLET, linestyle=(0, (3.2, 2.2)), zorder=5,
    )
)

ax.add_patch(
    FancyBboxPatch(
        (8.45, 1.68), 2.95, 0.36,
        boxstyle="round,pad=0,rounding_size=0.11",
        fc="#0E1B33", ec="#31446F", lw=0.8, zorder=4,
    )
)
ax.text(9.925, 1.86, "响应  ·  X-Jev-Route  ·  台账", fontsize=7.9, color=TXT2,
        fontproperties=ZH, ha="center", va="center", zorder=6)

ax.plot([7.85, 12.0], [1.50, 1.50], color=LINE, lw=0.8, zorder=4)
ax.text(7.85, 1.34, "SHARED CONTROLS", fontsize=6.4, color=TEAL_L,
        family="DejaVu Sans", fontweight="bold", zorder=6)
ax.text(12.0, 1.34, "ALL LOCAL", fontsize=6.2, color=DIM,
        family="DejaVu Sans", ha="right", zorder=6)

for x, y, label, accent in [
    (7.85, 1.00, "Conformal 风险门", TEAL),
    (9.95, 1.00, "JCBA 联合预算", INDIGO),
    (7.85, 0.68, "KV / Prefix 复用", CYAN),
    (9.95, 0.68, "接地验证 · 回退", VIOLET),
]:
    ax.add_patch(
        FancyBboxPatch(
            (x, y), 1.95, 0.24,
            boxstyle="round,pad=0,rounding_size=0.08",
            fc="#0C172C", ec="#243553", lw=0.7, zorder=4,
        )
    )
    ax.add_patch(Circle((x + 0.12, y + 0.12), 0.022, color=accent, zorder=6))
    ax.text(x + 0.21, y + 0.122, label, fontsize=6.8, color=TXT2,
            fontproperties=ZH, va="center", zorder=6)

fig.savefig(OUT_DIR / "cover.png", dpi=200, facecolor=BG0)
fig.savefig(OUT_DIR / "cover.svg", facecolor=BG0)
print("wrote", OUT_DIR / "cover.png")
print("wrote", OUT_DIR / "cover.svg")
