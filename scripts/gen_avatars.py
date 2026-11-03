#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_avatars.py — 生成专家包成员头像（几何扁平风格，确定性输出）

风格约束（团队统一）：圆形构图 + 同色系配色 + 极简几何符号 + 无文字
（无文字可规避中文字体渲染问题，且与 WorkBuddy 头像规范一致）

用法：python3 scripts/gen_avatars.py [--out <avatars目录>]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
BG = (247, 249, 252)

# 6 名成员：统一蓝色主调 + 各自辅助色（保持团队感）
MEMBERS = [
    ("smart-textbook-v2-team-lead", (52, 96, 165), (32, 68, 132), "orchestrate"),
    ("didactic-architect", (45, 112, 122), (24, 80, 90), "structure"),
    ("literature-scout", (62, 104, 148), (34, 68, 108), "search"),
    ("subject-writer", (72, 98, 136), (42, 62, 96), "write"),
    ("visual-instructional-designer", (116, 92, 142), (78, 58, 100), "chart"),
    ("quality-examiner", (138, 96, 84), (96, 58, 48), "shield"),
]


def draw_symbol(d: ImageDraw.ImageDraw, kind: str, primary: tuple, accent: tuple) -> None:
    """在 (140,140)-(372,372) 区域绘制角色符号。"""
    x0, y0, x1, y1 = 150, 150, 362, 362
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

    if kind == "orchestrate":
        # 三条流程线 + 节点（编排）
        for i, dy in enumerate((-52, 0, 52)):
            d.rounded_rectangle([cx - 78, cy + dy - 14, cx + 78, cy + dy + 14],
                                radius=12, outline=primary, width=9)
            d.ellipse([cx - 96, cy + dy - 18, cx - 60, cy + dy + 18], fill=accent)
        d.line([cx - 48, cy - 52, cx - 48, cy + 52], fill=primary, width=7)
        d.line([cx + 48, cy - 52, cx + 48, cy + 52], fill=primary, width=7)

    elif kind == "structure":
        # 三层金字塔（教学设计）
        d.polygon([(cx, y0 + 14), (x1 - 6, y1 - 6), (x0 + 6, y1 - 6)], outline=primary, width=9)
        d.line([cx - 74, cy + 36, cx + 74, cy + 36], fill=accent, width=9)
        d.line([cx - 42, cy + 78, cx + 42, cy + 78], fill=accent, width=9)

    elif kind == "search":
        # 放大镜 + 文档（检索）
        d.ellipse([x0 + 8, y0 + 8, cx + 20, cy + 20], outline=primary, width=12)
        d.line([cx + 26, cy + 26, x1 - 12, y1 - 12], fill=primary, width=14)
        d.rectangle([x0 + 2, y0 + 2, x0 + 46, y0 + 62], fill=accent)

    elif kind == "write":
        # 笔 + 纸（撰写）
        d.rounded_rectangle([x0 + 4, y0 + 24, x1 - 40, y1 - 4], radius=10,
                            outline=primary, width=9)
        for i in range(4):
            yy = y0 + 60 + i * 34
            d.line([x0 + 24, yy, x1 - 62, yy], fill=accent, width=8)
        d.polygon([(x1 - 66, y1 - 24), (x1 - 14, y1 - 74), (x1 - 6, y1 - 16)], fill=primary)

    elif kind == "chart":
        # 柱状图 + 折线（可视化）
        base = y1 - 12
        for i, h in enumerate((66, 116, 92)):
            bx = x0 + 26 + i * 56
            d.rectangle([bx, base - h, bx + 38, base], fill=accent if i != 1 else primary)
        d.line([x0 + 30, y0 + 34, x0 + 86, y0 + 60, x0 + 142, y0 + 24], fill=primary, width=9)

    elif kind == "shield":
        # 盾牌 + 对勾（审查）：上宽下尖的经典盾形 + 高对比对勾
        d.polygon([(x0 + 26, y0 + 10), (x1 - 26, y0 + 10),
                   (x1 - 26, cy + 6), (cx, y1 - 6), (x0 + 26, cy + 6)],
                  outline=primary, width=11)
        d.line([cx - 46, cy - 6, cx - 12, cy + 30], fill=accent, width=17)
        d.line([cx - 12, cy + 30, cx + 48, cy - 36], fill=accent, width=17)


def make_avatar(primary: tuple, accent: tuple, kind: str) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    # 外圈
    d.ellipse([16, 16, SIZE - 16, SIZE - 16], fill=(255, 255, 255), outline=primary, width=10)
    # 内圈淡色底
    d.ellipse([42, 42, SIZE - 42, SIZE - 42], fill=tuple(min(255, c + 42) for c in primary))
    draw_symbol(d, kind, primary, accent)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description="生成成员头像")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "avatars"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for name, primary, accent, kind in MEMBERS:
        p = out / f"{name}.png"
        img = make_avatar(primary, accent, kind)
        img.save(p, "PNG", optimize=True)
        kb = p.stat().st_size / 1024
        print(f"✅ {p.name}（{img.size[0]}×{img.size[1]} · {kb:.1f}KB · {kind}）")
    print(f"\n共 {len(MEMBERS)} 张 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
