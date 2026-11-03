#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_final_clean.py — 终稿前标签清理门禁（B7 治理收口 · 方向 3）

终稿是给读者的最终形态，**不得携带任何未决标记**。本门禁扫描残留标签，
残留 > 0 即阻塞交付（DELIVER）。

覆盖标签族：
  · g 族（数据缺口）  `[需补数据·x-gNN]` / `[已补·x-gNN]` / `[待核实·x-gNN]`
  · u 族（存疑表述）  `[存疑·x-uNN]` / `[已确认·x-uNN]`
  · 事实级（证据强度）`[待核实·reason=…]` / `[估]`

`[实]` 不在清理范围（它是终稿应有的证据标注）。

与 `scripts/check_gap_flow.py --final`（G5）的分工：G5 查**缺口台账闭环**，
本脚本查**终稿文本残留**，覆盖面更全（含 u 族与事实级标签）。

用法：
    python3 scripts/check_final_clean.py <章节路径> [--final] [--all]
    python3 scripts/check_final_clean.py <章节路径> --allow-estimated   # 放行 [估]
    python3 scripts/check_final_clean.py --probe-negatives
退出码：0 = 清洁；1 = 有残留；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 双根：PKG_ROOT = 包根；WORKSPACE_ROOT = 被检工作区（STT_WORKSPACE 注入）
PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()

# 必清标签族（正则）；[实] 不在列
BLOCKING_PATTERNS = [
    (re.compile(r"\[需补数据·[^\]]+\]"), "g族·待补数据"),
    (re.compile(r"\[已补·[^\]]+\]"), "g族·已补（终稿应移除标记）"),
    (re.compile(r"\[待核实·[A-Za-z0-9_]+-g\d{2}[^\]]*\]"), "g族·待核实"),
    (re.compile(r"\[存疑·[^\]]+\]"), "u族·存疑"),
    (re.compile(r"\[已确认·[^\]]+\]"), "u族·已确认（终稿应移除标记）"),
    (re.compile(r"\[待核实·reason=[^\]]*\]"), "事实级·待核实"),
]

# [估] 是**终稿允许的诚实标注**（给读者看「此值为推断」），不是未决标记——
# 设计风险缓解条款明说可白名单放行。故默认不阻塞，`--strict-estimated` 时才阻塞。
ESTIMATED_RE = re.compile(r"\[估\]")

# 终稿不该扫描的位置：
#  · 台账/登记表/规范文件本身记录标签是合法的
#  · 设计产物（chapter_design / chapter_spec / write_plan 等）里的标注是**规范示范文本**
#    （教作者怎么标 [实]/[估]/[待核实]），不是未决标记
EXCLUDE_NAMES = {"gaps.md", "figure-registry.md", "chapter_rules.yaml",
                 "textbook_rules.yaml", "file-naming.md", "gap-types.md",
                 "uncertain-tag-policy.md",
                 "chapter_design.md", "chapter_spec.md", "write_plan.md",
                 "visual_plan.md", "illustration_plan.md", "assets.md",
                 "handoff.md", "write_plan.md", "data_spec.yaml",
                 "ch_01_框架设计.md"}
EXCLUDE_PARTS = {"98-archive", "exports", "__pycache__", ".git", ".omc",
                 ".claude", "logs", "assets", "review", "设计产物"}


def iter_chapter_files(chapter_dir: Path) -> list[Path]:
    out = []
    for p in sorted(chapter_dir.rglob("*.md")):
        rel = p.relative_to(chapter_dir)
        if p.name in EXCLUDE_NAMES:
            continue
        if any(part in EXCLUDE_PARTS for part in rel.parts):
            continue
        out.append(p)
    return out


def scan_dir(chapter_dir: Path, allow_estimated: bool,
             strict_estimated: bool = False) -> list[tuple[str, int, str, str]]:
    """返回 [(文件相对路径, 行号, 标签族, 标签文本)]

    allow_estimated=True  → [估] 放行（默认语义：终稿允许诚实标注估算值）
    strict_estimated=True → [估] 也阻塞（严格终稿口径）
    """
    hits = []
    for p in iter_chapter_files(chapter_dir):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for pat, fam in BLOCKING_PATTERNS:
                for m in pat.finditer(line):
                    hits.append((str(p.relative_to(chapter_dir)), i, fam, m.group(0)))
        if strict_estimated:
            for i, line in enumerate(lines, 1):
                for m in ESTIMATED_RE.finditer(line):
                    hits.append((str(p.relative_to(chapter_dir)), i, "事实级·估算", m.group(0)))
    return hits


def find_chapter_dir(arg: str) -> Path | None:
    p = Path(arg)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / arg
    if p.is_file():
        return p.parent
    if p.is_dir():
        return p
    return None


def iter_all_chapters() -> list[Path]:
    out = []
    for pat in ("tb_*/章节/*/", "plugins/*/instances/*/"):
        for p in WORKSPACE_ROOT.glob(pat):
            if p.is_dir() and (p / "chapter_rules.yaml").is_file():
                out.append(p)
    return sorted(set(out), key=lambda p: str(p))


def probe_negatives() -> int:
    """负向探针：构造残留标签，断言能拦；干净稿断言放行。"""
    import shutil
    import tempfile

    cases = [
        ("g族待补", "[需补数据·ch01-g01]", True),
        ("g族已补", "[已补·ch01-g01]", True),
        ("u族存疑", "[存疑·ch01-u01]", True),
        ("u族已确认", "[已确认·ch01-u01]", True),
        ("事实级待核实", "[待核实·reason=文献来源不一致]", True),
        ("事实级估算(默认放行)", "[估]", False),
        ("干净稿", "本章数据见 PMID:12345678 [实]。", False),
    ]
    passed = 0
    print("── 负向探针（终稿清洁）──")
    for label, content, should_block in cases:
        tmp = Path(tempfile.mkdtemp())
        try:
            ch = tmp / "ch_test"
            ch.mkdir(parents=True)
            (ch / "draft.md").write_text(f"# 标题\n\n{content}\n", encoding="utf-8")
            hits = scan_dir(ch, allow_estimated=False)
            blocked = bool(hits)
            if blocked == should_block:
                passed += 1
                print(f"  ✓ {'拦截' if blocked else '放行'}｜{label}")
            else:
                print(f"  ✗ 未按预期｜{label}（期望{'拦截' if should_block else '放行'}）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # --strict-estimated 时 [估] 阻塞
    tmp0 = Path(tempfile.mkdtemp())
    try:
        ch0 = tmp0 / "ch_test"
        ch0.mkdir(parents=True)
        (ch0 / "draft.md").write_text("数值 [估]\n", encoding="utf-8")
        if scan_dir(ch0, False, True):
            passed += 1
            print("  ✓ 拦截｜--strict-estimated 生效（[估] 阻塞）")
        else:
            print("  ✗ 未按预期｜--strict-estimated 未阻塞")
    finally:
        shutil.rmtree(tmp0, ignore_errors=True)

    # --allow-estimated 放行 [估]
    tmp = Path(tempfile.mkdtemp())
    try:
        ch = tmp / "ch_test"
        ch.mkdir(parents=True)
        (ch / "draft.md").write_text("数值 [估]\n", encoding="utf-8")
        hits = scan_dir(ch, allow_estimated=True)
        if not hits:
            passed += 1
            print("  ✓ 放行｜--allow-estimated 生效（[估] 不阻塞）")
        else:
            print(f"  ✗ 未按预期｜--allow-estimated 仍拦截 {hits}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    total = len(cases) + 2
    if passed == total:
        print(f"✓ 负向探针全部命中：{passed}/{total}")
        return 0
    print(f"✗ 负向探针未全中：{passed}/{total}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="终稿前标签清理门禁（B7 · 方向 3）")
    ap.add_argument("chapter_path", nargs="?", help="章节目录")
    ap.add_argument("--all", action="store_true", help="全库扫描所有实例章节")
    ap.add_argument("--final", action="store_true", help="终稿模式（语义同默认，此处显式化）")
    ap.add_argument("--allow-estimated", action="store_true", help="放行 [估]（默认即放行，此参数显式化）")
    ap.add_argument("--strict-estimated", action="store_true", help="严格终稿口径：[估] 也阻塞交付")
    ap.add_argument("--probe-negatives", action="store_true", help="负向探针自检")
    args = ap.parse_args()

    if args.probe_negatives:
        return probe_negatives()

    chapters: list[Path] = []
    if args.all:
        chapters = iter_all_chapters()
        if not chapters:
            print("ℹ 未发现教材实例章节（包内无教材实例，属正常）")
            print("  · 校验真实工作区：export STT_WORKSPACE=<工作区根> 后重跑本命令")
            print("  · 实例章节位于 <工作区>/tb_*/章节/*/")
            return 0
    elif args.chapter_path:
        ch = find_chapter_dir(args.chapter_path)
        if ch is None:
            print(f"✗ 章节目录不存在: {args.chapter_path}", file=sys.stderr)
            return 2
        chapters = [ch]
    else:
        ap.print_usage()
        return 2

    total_hits = 0
    for ch in chapters:
        rel = ch.relative_to(WORKSPACE_ROOT) if ch.is_relative_to(WORKSPACE_ROOT) else ch
        hits = scan_dir(ch, args.allow_estimated, args.strict_estimated)
        total_hits += len(hits)
        mark = "✗" if hits else "✓"
        print(f"{mark} {rel}: 残留 {len(hits)} 处")
        for f, ln, fam, tag in hits[:10]:
            print(f"    {f}:{ln} [{fam}] {tag}")
        if len(hits) > 10:
            print(f"    … 另有 {len(hits) - 10} 处")

    print()
    if total_hits:
        print(f"✗ 终稿不清洁：残留 {total_hits} 处未决标记（阻塞交付）")
        return 1
    print("✓ 终稿清洁：无残留未决标记")
    return 0


if __name__ == "__main__":
    sys.exit(main())
