#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_gap_flow.py — 缺口闭环卡点检测（B3 · 方向 2）

按架构设计 §3.2「卡点检测点」表机械校验章节缺口闭环状态，不靠模型自觉。

检查项（error 级 → exit 1）：
  [G1] 标签↔台账双向对应：正文中每个 [需补数据·x-gNN] / [已补·x-gNN] /
       [待核实·x-gNN] 标签，必须在 gaps.md 有对应条目；反向亦然（台账有、
       正文无标签 → 台账悬空）
  [G2] 审批范围：approved 数 ∉ [3,10] 且总数 ≥3 → 越界报错
       （总数 <3 时须显式标注「本轮不足 3 个」豁免）
  [G3] 状态流转合法性：禁止 proposed→filled / approved→resolved /
       unresolved→resolved（跳过审批或跳过检索直接关闭）
  [G4] 检索收纳：retrieval/INDEX.md 存在，且索引条目数 == 实际 .md 文件数
       （不含 INDEX.md 自身）；INDEX 每条目含 标题/摘要(≤200字)/文件路径/置信度
       四字段（渐进式加载入口）
  [G5] 终稿清洁（--final 模式）：残留 [需补数据] / [待核实] / [估] 标签数 > 0
       → 阻塞交付

用法：
    python3 scripts/check_gap_flow.py <章节路径>            # 检查 1-4
    python3 scripts/check_gap_flow.py <章节路径> --final    # 追加终稿清洁
    python3 scripts/check_gap_flow.py --all                 # 全库扫描
    python3 scripts/check_gap_flow.py --probe-negatives     # 负向探针自检

退出码：0 = 通过；1 = 存在 error 级违规；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ── 路径语义（B6 双根改造）─────────────────────────────────────────────
# PKG_ROOT：本包根（规则层资产恒定在此）
# WORKSPACE_ROOT：被检工作区（教材实例所在；默认=包根，可用 STT_WORKSPACE 注入）
PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()

# ── 缺口类型枚举（SSOT = references/gap-types.md §2，改枚举须三处同步）────
GAP_TYPES = {
    "data_value", "data_series", "literature_claim",
    "chart_data", "case_example", "terminology", "mechanism",
}

# ── 标签形态（SSOT = references/gap-types.md §7）────────────────────────
# 编号形态：{ch##}-g{NN}
NUM_TAG_RE = re.compile(r"\[(需补数据|已补|待核实)·([A-Za-z0-9_]+-g\d{2})\]")
# 旧形态（兼容，B0-Q6：继续有效，新流程不产出）
LEGACY_TAGS = ("[需补数据]", "[待核实]", "[估]")

# ── gaps.md 台账行格式 ────────────────────────────────────────────────
# | gap_id | gap_type | priority | status | 描述 | 上下文 | 审批 | 检索产物 | 证据锚 |
GAP_ROW_RE = re.compile(
    r"^\|\s*(?P<gap_id>[A-Za-z0-9_]+-g\d{2})\s*\|"
    r"\s*(?P<gap_type>[a-z_]+)\s*\|"
    r"\s*(?P<priority>P[0-2])\s*\|"
    r"\s*(?P<status>[a-z_]+)\s*\|"
    r"\s*(?P<desc>[^|]*)\|"
    r"\s*(?P<ctx>[^|]*)\|"
    r"\s*(?P<approve>[^|]*)\|"
    r"\s*(?P<artifact>[^|]*)\|"
    r"\s*(?P<evidence>[^|]*)\|"
)

# 合法状态流转（SSOT = references/gap-types.md §6）
LEGAL_TRANSITIONS = {
    "proposed": {"approved", "rejected", "deferred"},
    "approved": {"retrieved"},
    "retrieved": {"filled"},
    "filled": {"resolved", "partially_resolved", "unresolved"},
    "rejected": set(),
    "deferred": {"approved", "rejected", "deferred"},
    "resolved": set(),
    "partially_resolved": {"filled", "unresolved"},
    "unresolved": {"filled", "unresolved"},
}

# 终稿前必须归零的标签
FINAL_BLOCKING = ("[需补数据", "[待核实", "[估]")

# INDEX 条目字段（渐进式加载四字段）
INDEX_REQUIRED_FIELDS = ("标题", "摘要", "路径", "置信度")


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def find_chapter_dir(arg: str) -> Path | None:
    p = Path(arg)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / arg
    if p.is_file() and p.name == "chapter_rules.yaml":
        return p.parent
    if p.is_dir():
        return p
    return None


def iter_all_chapters() -> list[Path]:
    out: list[Path] = []
    for pat in ("tb_*/章节/*/", "plugins/*/instances/*/"):
        for p in WORKSPACE_ROOT.glob(pat):
            if p.is_dir() and (p / "chapter_rules.yaml").is_file():
                out.append(p)
    return sorted(set(out), key=lambda p: str(p))


def parse_gaps(gaps_file: Path, f: Findings) -> dict[str, dict]:
    """解析 gaps.md 台账 → {gap_id: row}。台账缺失不报错（无缺口是合法状态）。"""
    rows: dict[str, dict] = {}
    if not gaps_file.is_file():
        return rows
    for i, line in enumerate(_read(gaps_file).splitlines(), 1):
        if "|" not in line or line.strip().startswith("|---") or "gap_id" in line:
            continue
        m = GAP_ROW_RE.match(line.strip())
        if not m:
            continue
        row = m.groupdict()
        gid = row["gap_id"]
        if gid in rows:
            f.error(f"[G1] gaps.md 重复 gap_id: {gid}（第 {i} 行）")
        if row["gap_type"] not in GAP_TYPES:
            f.error(f"[G1] gaps.md {gid}: 未知 gap_type {row['gap_type']!r}（合法枚举见 references/gap-types.md）")
        rows[gid] = row
    return rows


def collect_draft_tags(chapter_dir: Path) -> tuple[set[str], dict[str, list[str]]]:
    """扫描章节正文中的编号标签 → (出现的 gap_id 集合, gap_id→标签形态列表)"""
    ids: set[str] = set()
    forms: dict[str, list[str]] = {}
    for md in sorted(chapter_dir.rglob("*.md")):
        if md.name in {"gaps.md", "chapter_rules.yaml"}:
            continue
        if "98-archive" in md.parts or "exports" in md.parts:
            continue
        text = _read(md)
        for kind, gid in NUM_TAG_RE.findall(text):
            ids.add(gid)
            forms.setdefault(gid, []).append(f"{kind}@{md.relative_to(chapter_dir)}")
    return ids, forms


# ══════════════════════════════════════════════════════════════════════
# [G1] 标签 ↔ 台账双向对应
# ══════════════════════════════════════════════════════════════════════
def check_tag_registry(chapter_dir: Path, rows: dict, f: Findings) -> None:
    ids, forms = collect_draft_tags(chapter_dir)

    for gid in sorted(ids):
        if gid not in rows:
            f.error(f"[G1] 正文标签 {gid} 在 gaps.md 无对应条目（{'; '.join(forms[gid][:3])}）")

    for gid in sorted(rows):
        if gid not in ids:
            f.warn(f"[G1] gaps.md 条目 {gid} 在正文中无标签（台账悬空，不阻塞）")


# ══════════════════════════════════════════════════════════════════════
# [G2] 审批范围 3-10
# ══════════════════════════════════════════════════════════════════════
def check_approval_scope(rows: dict, gaps_file: Path, f: Findings) -> None:
    if not rows:
        return
    approved = [g for g, r in rows.items() if r["status"] == "approved"]
    total = len(rows)
    if total < 3:
        # 不足 3 个：须显式标注豁免，否则报错
        text = _read(gaps_file)
        if "本轮不足 3 个" not in text:
            f.error(f"[G2] 缺口总数 {total} < 3，且 gaps.md 未显式标注「本轮不足 3 个」豁免")
        return
    if not (3 <= len(approved) <= 10):
        f.error(f"[G2] approved 数 {len(approved)} ∉ [3,10]（总数 {total}）——审批越界")


# ══════════════════════════════════════════════════════════════════════
# [G3] 状态流转合法性
# ══════════════════════════════════════════════════════════════════════
def check_transitions(rows: dict, f: Findings) -> None:
    for gid, r in sorted(rows.items()):
        st = r["status"]
        if st not in LEGAL_TRANSITIONS:
            f.error(f"[G3] {gid}: 未知状态 {st!r}（合法状态见 references/gap-types.md §6）")
            continue
        # 「跳过审批 / 跳过检索」的直接证据：proposed 态却已带证据锚
        if st == "proposed" and r["evidence"].strip() not in {"", "-", "—"}:
            f.error(f"[G3] {gid}: 状态 proposed 却已填证据锚（跳过审批直接闭环）")
        if st == "approved" and r["evidence"].strip() not in {"", "-", "—"}:
            f.error(f"[G3] {gid}: 状态 approved 却已填证据锚（跳过检索直接闭环）")
        if st == "unresolved" and r["evidence"].strip() not in {"", "-", "—"}:
            f.error(f"[G3] {gid}: 状态 unresolved 却已填证据锚（未经返修直接关闭）")


# ══════════════════════════════════════════════════════════════════════
# [G4] 检索收纳 + 索引
# ══════════════════════════════════════════════════════════════════════
def check_retrieval(chapter_dir: Path, rows: dict, f: Findings) -> None:
    retr = chapter_dir / "retrieval"
    index = retr / "INDEX.md"

    approved = [g for g, r in rows.items() if r["status"] in {"approved", "retrieved", "filled", "resolved"}]
    if not approved and not retr.exists():
        return  # 无需检索的章节：无 retrieval 目录属正常

    if not index.is_file():
        f.error(f"[G4] 缺 retrieval/INDEX.md（检索产物未建索引）")
        return

    md_files = sorted(p for p in retr.glob("*.md") if p.name != "INDEX.md")
    text = _read(index)
    # 条目 = 表格数据行（排除表头行与分隔行）。表头行以四个字段名之一开头。
    raw_rows = [ln for ln in text.splitlines() if ln.strip().startswith("|") and "---" not in ln]
    entries = []
    for ln in raw_rows:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if not cells:
            continue
        # 表头行：首格命中字段名 → 跳过
        if cells[0] in INDEX_REQUIRED_FIELDS or cells[0].lower() in {"gap_id", "字段", "field"}:
            continue
        entries.append(ln)
    if len(entries) != len(md_files):
        f.error(f"[G4] retrieval/INDEX.md 条目数 {len(entries)} != 实际 .md 文件数 {len(md_files)}")

    for field in INDEX_REQUIRED_FIELDS:
        if field not in text:
            f.error(f"[G4] retrieval/INDEX.md 缺字段「{field}」（渐进式加载四字段：标题/摘要(≤200字)/路径/置信度）")


# ══════════════════════════════════════════════════════════════════════
# [G5] 终稿清洁
# ══════════════════════════════════════════════════════════════════════
def check_final_clean(chapter_dir: Path, f: Findings) -> None:
    residual: list[str] = []
    for md in sorted(chapter_dir.rglob("*.md")):
        if md.name == "gaps.md":
            continue
        if "98-archive" in md.parts or "exports" in md.parts:
            continue
        text = _read(md)
        for tag in FINAL_BLOCKING:
            n = text.count(tag)
            if n:
                residual.append(f"{md.relative_to(chapter_dir)}: {tag}×{n}")
    if residual:
        f.error(f"[G5] 终稿残留未清理标签 {len(residual)} 处（阻塞交付）：{'; '.join(residual[:5])}")


# ══════════════════════════════════════════════════════════════════════
# 负向探针
# ══════════════════════════════════════════════════════════════════════
def probe_negatives() -> int:
    import tempfile
    import shutil

    cases = [
        ("G1 正文标签无台账条目", "G1"),
        ("G2 approved 越界(11个)", "G2"),
        ("G3 proposed 却有证据锚", "G3"),
        ("G4 INDEX 条目数不符", "G4"),
        ("G4 INDEX 缺四字段", "G4"),
        ("G5 终稿残留标签", "G5"),
        ("G1 未知 gap_type", "G1"),
    ]
    passed = 0
    print("── 负向探针（B3 验收硬指标）──")
    for label, expect in cases:
        tmp = Path(tempfile.mkdtemp())
        try:
            ch = tmp / "ch_test"
            (ch / "retrieval").mkdir(parents=True)
            (ch / "draft.md").write_text("正文。\n", encoding="utf-8")
            (ch / "gaps.md").write_text(
                "| gap_id | gap_type | priority | status | 描述 | 上下文 | 审批 | 检索产物 | 证据锚 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| ch_test-g01 | data_value | P0 | proposed | d | §1 | - | - | - |\n",
                encoding="utf-8")
            f = Findings()

            if label.startswith("G1 正文标签"):
                (ch / "draft.md").write_text("数据见 [需补数据·ch_test-g99]\n", encoding="utf-8")
                check_tag_registry(ch, parse_gaps(ch / "gaps.md", f), f)
            elif label.startswith("G2"):
                rows = {f"ch_test-g{i:02d}": {"status": "approved", "gap_type": "data_value",
                                              "priority": "P0", "evidence": "-", "desc": "", "ctx": "",
                                              "approve": "", "artifact": ""} for i in range(11)}
                check_approval_scope(rows, ch / "gaps.md", f)
            elif label.startswith("G3"):
                (ch / "gaps.md").write_text(
                    "| gap_id | gap_type | priority | status | 描述 | 上下文 | 审批 | 检索产物 | 证据锚 |\n"
                    "|---|---|---|---|---|---|---|---|---|\n"
                    "| ch_test-g01 | data_value | P0 | proposed | d | §1 | - | - | PMID:1 |\n",
                    encoding="utf-8")
                check_transitions(parse_gaps(ch / "gaps.md", f), f)
            elif label.startswith("G4 INDEX 条目数"):
                # 真实不匹配场景：2 个检索文件，INDEX 只登记 1 条
                (ch / "retrieval" / "a.md").write_text("x", encoding="utf-8")
                (ch / "retrieval" / "b.md").write_text("y", encoding="utf-8")
                (ch / "retrieval" / "INDEX.md").write_text(
                    "| 标题 | 摘要 | 路径 | 置信度 |\n|---|---|---|---|\n| t | s | a.md | [实] |\n",
                    encoding="utf-8")
                check_retrieval(ch, {}, f)
            elif label.startswith("G4 INDEX 缺四字段"):
                (ch / "retrieval" / "INDEX.md").write_text("| 标题 | 路径 |\n|---|---|\n| t | a.md |\n", encoding="utf-8")
                check_retrieval(ch, {}, f)
            elif label.startswith("G5"):
                (ch / "draft.md").write_text("数据见 [估] 与 [待核实]\n", encoding="utf-8")
                check_final_clean(ch, f)
            elif label.startswith("G1 未知"):
                (ch / "gaps.md").write_text(
                    "| gap_id | gap_type | priority | status | 描述 | 上下文 | 审批 | 检索产物 | 证据锚 |\n"
                    "|---|---|---|---|---|---|---|---|---|\n"
                    "| ch_test-g01 | not_a_type | P0 | proposed | d | §1 | - | - | - |\n",
                    encoding="utf-8")
                parse_gaps(ch / "gaps.md", f)

            # error 消息形态为 "[G1] ..."，用包含匹配（expect 传 "G1"）
            hit = any(expect in e for e in f.errors)
            if hit:
                passed += 1
                print(f"  ✓ 已拦截｜{label}")
            else:
                print(f"  ✗ 未拦截｜{label}（期望 {expect}*）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if passed == len(cases):
        print(f"✓ 负向探针全部命中：{passed}/{len(cases)} 类违规样例均被拦截")
        return 0
    print(f"✗ 负向探针未全中：{passed}/{len(cases)}")
    return 1


def check_chapter(chapter_dir: Path, final: bool) -> Findings:
    f = Findings()
    gaps_file = chapter_dir / "gaps.md"
    rows = parse_gaps(gaps_file, f)
    check_tag_registry(chapter_dir, rows, f)
    check_approval_scope(rows, gaps_file, f)
    check_transitions(rows, f)
    check_retrieval(chapter_dir, rows, f)
    if final:
        check_final_clean(chapter_dir, f)
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description="缺口闭环卡点检测（B3 · 方向 2）")
    ap.add_argument("chapter_path", nargs="?", help="章节目录")
    ap.add_argument("--all", action="store_true", help="全库扫描所有实例章节")
    ap.add_argument("--final", action="store_true", help="终稿模式：追加残留标签清洁检查（阻塞交付）")
    ap.add_argument("--probe-negatives", action="store_true", help="负向探针自检")
    args = ap.parse_args()

    if args.probe_negatives:
        return probe_negatives()

    if args.all:
        chapters = iter_all_chapters()
        if not chapters:
            print("ℹ 未发现教材实例章节（包内无教材实例，属正常）")
            print("  · 校验真实工作区：export STT_WORKSPACE=<工作区根> 后重跑本命令")
            print("  · 实例章节位于 <工作区>/tb_*/章节/*/")
            return 0
        all_err, all_warn = 0, 0
        for ch in chapters:
            rel = ch.relative_to(WORKSPACE_ROOT)
            f = check_chapter(ch, args.final)
            all_err += len(f.errors)
            all_warn += len(f.warnings)
            mark = "✗" if f.errors else ("⚠" if f.warnings else "✓")
            print(f"{mark} {rel}: {len(f.errors)} error / {len(f.warnings)} warning")
            for e in f.errors[:5]:
                print(f"    {e}")
        print()
        if all_err:
            print(f"✗ 缺口流程校验未通过（{all_err} error / {all_warn} warning）")
            return 1
        print(f"✓ 全库缺口流程校验通过（{all_err} error / {all_warn} warning）")
        return 0

    if not args.chapter_path:
        ap.print_usage()
        return 2

    ch = find_chapter_dir(args.chapter_path)
    if ch is None:
        print(f"✗ 章节目录不存在: {args.chapter_path}", file=sys.stderr)
        return 2

    f = check_chapter(ch, args.final)
    for e in f.errors:
        print(f"✗ {e}")
    for w in f.warnings:
        print(f"⚠ {w}")
    if f.errors:
        print(f"\n✗ 缺口流程校验未通过（{len(f.errors)} error / {len(f.warnings)} warning）")
        return 1
    print(f"\n✓ 缺口流程校验通过（0 error / {len(f.warnings)} warning）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
