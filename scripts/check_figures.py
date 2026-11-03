#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_figures.py — 图表流水线卡点检测（B4 · 方向 2 轨 B）

按 references/figure-registry.md 规范机械校验章节图表登记与正文引用关系。

检查项（error 级 → exit 1）：
  [F1] 图有源：B 类图存在同名 .ipynb（含 Cell0 溯源 / Cell2 绘图代码）
  [F2] 编号连续：图{ch##}-N 序列无缺号、无重号（存量 fig-NN 形态豁免）
  [F3] 图注完整：每图注含图号 + 标题；C 类必须含 AI 披露
  [F4] 迭代上限：美学迭代 ≤5；超限须已降级为 D 类
  [F5] 文件存在：registry 登记的文件真实存在
  [F6] 正文引用：final 状态的图必须在正文有引用占位符
  [F7] 终稿清洁（--final）：残留 [需补数据]/[待核实]/[估] == 0

辅助模式：
  --reconcile  摸排：列出 assets/ 中未登记的文件 + registry 中文件缺失的条目
  --renumber   重编号：输出 rename_map（dry-run，不改文件；JSON 可反向回滚）
  --all        全库扫描所有实例章节
  --probe-negatives  负向探针自检

用法：
    python3 scripts/check_figures.py <章节路径> [--final]
    python3 scripts/check_figures.py <章节路径> --reconcile
    python3 scripts/check_figures.py <章节路径> --renumber
    python3 scripts/check_figures.py --all
    python3 scripts/check_figures.py --probe-negatives

退出码：0 = 通过；1 = 存在 error 级违规；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import os
import json
import re
import sys
from pathlib import Path

# ── 路径语义（B6 双根改造）─────────────────────────────────────────────
# PKG_ROOT：本包根（规则层资产恒定在此）
# WORKSPACE_ROOT：被检工作区（教材实例所在；默认=包根，可用 STT_WORKSPACE 注入）
PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()

# 规范图号：图{ch##}-N
FIG_NO_RE = re.compile(r"图([A-Za-z0-9_]+)-(\d+)")
# 存量图号：fig-NN（豁免连续性检查）
LEGACY_FIG_RE = re.compile(r"fig-(\d+)")

# registry 表头八列
REG_ROW_RE = re.compile(
    r"^\|\s*(?P<fig_no>[^|]+?)\s*\|"
    r"\s*(?P<ftype>[ABCD])\s*\|"
    r"\s*(?P<file>[^|]*)\|"
    r"\s*(?P<ipynb>[^|]*)\|"
    r"\s*(?P<prov>[^|]*)\|"
    r"\s*(?P<iter>[^|]*)\|"
    r"\s*(?P<caption>[^|]*)\|"
    r"\s*(?P<status>[^|]*)\|"
)

AI_DISCLOSURE_RE = re.compile(r"AI\s*(生成|辅助|绘制|disclosed|generated)", re.I)
MAX_ITER = 5
FINAL_BLOCKING = ("[需补数据", "[待核实", "[估]")


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
    if p.is_file():
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


def parse_registry(reg_file: Path, f: Findings) -> list[dict]:
    """解析 figure-registry.md → 行列表。无 registry 文件返回空（无图章节合法）。"""
    rows: list[dict] = []
    if not reg_file.is_file():
        return rows
    for i, line in enumerate(_read(reg_file).splitlines(), 1):
        if "|" not in line or line.strip().startswith("|---"):
            continue
        m = REG_ROW_RE.match(line.strip())
        if not m:
            continue
        row = m.groupdict()
        if row["fig_no"] in {"图号", "figure"}:
            continue
        row["_line"] = i
        rows.append(row)
    return rows


def chapter_texts(chapter_dir: Path) -> list[tuple[Path, str]]:
    """章节正文文件（排除 registry / 资产 / 归档 / 导出）。"""
    out = []
    for md in sorted(chapter_dir.rglob("*.md")):
        rel = md.relative_to(chapter_dir)
        if md.name == "figure-registry.md":
            continue
        if md.name in {"gaps.md", "chapter_rules.yaml"}:
            continue
        if "assets" in rel.parts or "98-archive" in rel.parts or "exports" in rel.parts:
            continue
        out.append((md, _read(md)))
    return out


# ══════════════════════════════════════════════════════════════════════
# [F1] 图有源 / [F4] 迭代上限 / [F5] 文件存在
# ══════════════════════════════════════════════════════════════════════
def check_rows(chapter_dir: Path, rows: list[dict], f: Findings) -> None:
    for row in rows:
        fno = row["fig_no"].strip()
        ftype = row["ftype"]
        ffile = row["file"].strip()
        ipynb = row["ipynb"].strip()
        prov = row["prov"].strip()
        caption = row["caption"].strip()
        iter_raw = row["iter"].strip()

        # F5 文件存在
        if ffile and ffile != "-":
            if not (chapter_dir / ffile).is_file():
                f.error(f"[F5] {fno}: 登记文件不存在 {ffile}")

        # F1 图有源：B 类必须有 ipynb（且含 Cell0/Cell2）
        if ftype == "B":
            if not ipynb or ipynb == "-":
                f.error(f"[F1] {fno}: B 类数据图缺 ipynb 源（数据实证图须可复现）")
            else:
                nb = chapter_dir / ipynb
                if not nb.is_file():
                    f.error(f"[F1] {fno}: 登记的 ipynb 不存在 {ipynb}")
                else:
                    nbtext = _read(nb)
                    if "Cell 0" not in nbtext and "cell 0" not in nbtext.lower():
                        f.error(f"[F1] {fno}: ipynb 缺 Cell 0 溯源信息（原始数据+来源）")
                    if "Cell 2" not in nbtext and "cell 2" not in nbtext.lower():
                        f.error(f"[F1] {fno}: ipynb 缺 Cell 2 绘图代码")

        # A/B 类须有溯源
        if ftype in {"A", "B"} and (not prov or prov == "-"):
            f.error(f"[F1] {fno}: {ftype} 类图缺溯源（PMID/DOI/本地源锚）")

        # F3 图注完整
        if not caption or caption == "-":
            f.error(f"[F3] {fno}: 缺图注")
        elif fno not in caption:
            f.error(f"[F3] {fno}: 图注未含图号（应形如「图<章节号>-N  标题」）")
        if ftype == "C" and caption not in {"-", ""} and not AI_DISCLOSURE_RE.search(caption):
            f.error(f"[F3] {fno}: C 类叙事插图图注缺 AI 披露（双重披露要求）")
        # F4 迭代上限
        if iter_raw and iter_raw != "-":
            try:
                n = int(iter_raw)
            except ValueError:
                f.error(f"[F4] {fno}: 美学迭代替身非整数 {iter_raw!r}")
                n = 0
            if n > MAX_ITER:
                if ftype != "D":
                    f.error(f"[F4] {fno}: 美学迭代 {n} > {MAX_ITER}，须强制降级为 D 类（当前 {ftype}）")
                else:
                    f.warn(f"[F4] {fno}: 美学迭代 {n} > {MAX_ITER}，已降级 D 类（降级不阻塞）")


# ══════════════════════════════════════════════════════════════════════
# [F2] 编号连续
# ══════════════════════════════════════════════════════════════════════
def check_numbering(rows: list[dict], f: Findings) -> None:
    norm_nums: list[int] = []
    seen: dict[int, str] = {}
    for row in rows:
        fno = row["fig_no"].strip()
        m = FIG_NO_RE.search(fno)
        if not m:
            if LEGACY_FIG_RE.match(fno):
                continue  # 存量形态豁免
            f.warn(f"[F2] 图号 {fno!r} 既非规范形态「图{{ch##}}-N」也非存量形态「fig-NN」（不阻塞）")
            continue
        n = int(m.group(2))
        if n in seen:
            f.error(f"[F2] 图号重复：{seen[n]} 与 {fno} 同为序号 {n}")
        seen[n] = fno
        norm_nums.append(n)

    if not norm_nums:
        return
    lo, hi = min(norm_nums), max(norm_nums)
    missing = [n for n in range(lo, hi + 1) if n not in seen]
    if missing:
        f.error(f"[F2] 图号序列缺号：{missing}（现有 {lo}-{hi}）")


# ══════════════════════════════════════════════════════════════════════
# [F6] 正文引用
# ══════════════════════════════════════════════════════════════════════
def check_body_refs(chapter_dir: Path, rows: list[dict], f: Findings) -> None:
    texts = chapter_texts(chapter_dir)
    body = "\n".join(t for _, t in texts)
    for row in rows:
        if row["status"].strip().lower() != "final":
            continue
        fno = row["fig_no"].strip()
        if not FIG_NO_RE.search(fno):
            continue  # 存量图号不在本检查范围
        if fno not in body:
            f.error(f"[F6] {fno}: 状态 final 但正文无引用占位符（图表须插入正文）")


# ══════════════════════════════════════════════════════════════════════
# [F7] 终稿清洁
# ══════════════════════════════════════════════════════════════════════
def check_final_clean(chapter_dir: Path, f: Findings) -> None:
    residual: list[str] = []
    for md, text in chapter_texts(chapter_dir):
        for tag in FINAL_BLOCKING:
            n = text.count(tag)
            if n:
                residual.append(f"{md.relative_to(chapter_dir)}: {tag}×{n}")
    if residual:
        f.error(f"[F7] 终稿残留未清理标签 {len(residual)} 处（阻塞交付）：{'; '.join(residual[:5])}")


# ══════════════════════════════════════════════════════════════════════
# --reconcile 摸排
# ══════════════════════════════════════════════════════════════════════
def reconcile(chapter_dir: Path, rows: list[dict]) -> int:
    assets = chapter_dir / "assets"
    registered = set()
    for row in rows:
        for key in ("file", "ipynb"):
            v = row[key].strip()
            if v and v != "-":
                registered.add(Path(v).name)

    print(f"── 摸排报告 · {chapter_dir.name} ──")
    if not assets.is_dir():
        print("  · 无 assets/ 目录")
    else:
        on_disk = sorted(p.name for p in assets.iterdir() if p.is_file())
        unreg = [n for n in on_disk if n not in registered]
        print(f"  · assets/ 文件 {len(on_disk)} 个，registry 已登记 {len(registered & set(on_disk))} 个")
        if unreg:
            print(f"  ⚠ 未登记文件 {len(unreg)} 个（须补登 registry 或删除）：")
            for n in unreg[:20]:
                print(f"      {n}")
        else:
            print("  ✓ 无未登记文件")

    missing = []
    for row in rows:
        for key in ("file", "ipynb"):
            v = row[key].strip()
            if v and v != "-" and not (chapter_dir / v).is_file():
                missing.append(f"{row['fig_no'].strip()}: {v}")
    if missing:
        print(f"  ⚠ registry 登记但文件缺失 {len(missing)} 处：")
        for m in missing[:20]:
            print(f"      {m}")
    else:
        print("  ✓ registry 登记文件均存在")
    return 0


# ══════════════════════════════════════════════════════════════════════
# --renumber 重编号（dry-run）
# ══════════════════════════════════════════════════════════════════════
def renumber(chapter_dir: Path, rows: list[dict]) -> int:
    """按正文出现顺序生成规范图号映射。dry-run：不改任何文件。"""
    texts = chapter_texts(chapter_dir)
    order: list[str] = []
    for _, t in texts:
        for m in FIG_NO_RE.finditer(t):
            fno = m.group(0)
            if fno not in order:
                order.append(fno)

    rename_map = {}
    for old in order:
        mm = FIG_NO_RE.search(old)
        if not mm:
            continue
        stem = mm.group(1)
        new = f"图{stem}-{order.index(old) + 1}"
        if new != old:
            rename_map[old] = new

    payload = {
        "chapter": str(chapter_dir.relative_to(WORKSPACE_ROOT)) if chapter_dir.is_relative_to(WORKSPACE_ROOT) else str(chapter_dir),
        "body_order": order,
        "rename_map": rename_map,
        "note": "dry-run 结果，未改动任何文件；确认后由人工执行重命名，并同步正文引用与 assets.md 清单",
    }
    print("── 重编号建议（dry-run · 未改动文件）──")
    if not rename_map:
        print("  ✓ 现有图号已符合正文出现顺序，无需重编号")
    else:
        print(f"  · 需重编号 {len(rename_map)} 张：")
        for old, new in rename_map.items():
            print(f"      {old} → {new}")
    print()
    print("  rename_map JSON（可反向回滚）：")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


# ══════════════════════════════════════════════════════════════════════
# 负向探针
# ══════════════════════════════════════════════════════════════════════
def probe_negatives() -> int:
    import shutil
    import tempfile

    cases = [
        ("F1 B类图缺 ipynb", "F1"),
        ("F1 ipynb 缺 Cell0", "F1"),
        ("F2 图号缺号", "F2"),
        ("F2 图号重复", "F2"),
        ("F3 C类图注缺 AI 披露", "F3"),
        ("F3 图注缺图号", "F3"),
        ("F4 迭代超限未降级", "F4"),
        ("F5 登记文件不存在", "F5"),
        ("F6 final 图正文无引用", "F6"),
        ("F7 终稿残留标签", "F7"),
    ]
    passed = 0
    print("── 负向探针（B4 验收硬指标）──")

    for label, expect in cases:
        tmp = Path(tempfile.mkdtemp())
        try:
            ch = tmp / "ch_test"
            (ch / "assets").mkdir(parents=True)
            (ch / "draft.md").write_text("正文 图ch01-1\n", encoding="utf-8")
            reg = [
                "| 图号 | 类型 | 文件 | ipynb | 溯源 | 美学迭代 | 图注 | 状态 |",
                "|---|---|---|---|---|---|---|---|",
                "| 图ch01-1 | B | assets/图ch01-1.png | assets/图ch01-1.ipynb | PMID:1 | 0 | 图ch01-1 标题 | final |",
            ]

            if label == "F1 B类图缺 ipynb":
                reg[2] = "| 图ch01-1 | B | assets/图ch01-1.png | - | PMID:1 | 0 | 图ch01-1 标题 | final |"
            elif label == "F1 ipynb 缺 Cell0":
                (ch / "assets" / "图ch01-1.png").write_text("x", encoding="utf-8")
                (ch / "assets" / "图ch01-1.ipynb").write_text(
                    '{"cells":[{"cell_type":"code","source":["# Cell 2\\nprint(1)"]}]}', encoding="utf-8")
            elif label == "F2 图号缺号":
                reg.append("| 图ch01-3 | D | assets/图ch01-3.png | - | - | 0 | 图ch01-3 标题 | final |")
            elif label == "F2 图号重复":
                reg.append("| 图ch01-1 | D | assets/图ch01-1.png | - | - | 0 | 图ch01-1 标题 | final |")
            elif label == "F3 C类图注缺 AI 披露":
                reg[2] = "| 图ch01-1 | C | assets/图ch01-1.png | - | - | 0 | 图ch01-1 标题 | final |"
            elif label == "F3 图注缺图号":
                reg[2] = "| 图ch01-1 | D | assets/图ch01-1.png | - | - | 0 | 标题 | final |"
            elif label == "F4 迭代超限未降级":
                reg[2] = "| 图ch01-1 | B | assets/图ch01-1.png | - | PMID:1 | 7 | 图ch01-1 标题 | final |"
            elif label == "F5 登记文件不存在":
                reg[2] = "| 图ch01-1 | D | assets/不存在.png | - | - | 0 | 图ch01-1 标题 | final |"
            elif label == "F6 final 图正文无引用":
                reg[2] = "| 图ch01-9 | D | assets/图ch01-1.png | - | - | 0 | 图ch01-9 标题 | final |"
            elif label == "F7 终稿残留标签":
                (ch / "draft.md").write_text("正文 图ch01-1 [估]\n", encoding="utf-8")

            (ch / "figure-registry.md").write_text("\n".join(reg) + "\n", encoding="utf-8")

            f = Findings()
            rows = parse_registry(ch / "figure-registry.md", f)
            check_rows(ch, rows, f)
            check_numbering(rows, f)
            check_body_refs(ch, rows, f)
            if label.startswith("F7"):
                check_final_clean(ch, f)

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
    reg_file = chapter_dir / "figure-registry.md"
    rows = parse_registry(reg_file, f)
    check_rows(chapter_dir, rows, f)
    check_numbering(rows, f)
    check_body_refs(chapter_dir, rows, f)
    if final:
        check_final_clean(chapter_dir, f)
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description="图表流水线卡点检测（B4 · 方向 2 轨 B）")
    ap.add_argument("chapter_path", nargs="?", help="章节目录")
    ap.add_argument("--all", action="store_true", help="全库扫描所有实例章节")
    ap.add_argument("--final", action="store_true", help="终稿模式：追加残留标签清洁检查")
    ap.add_argument("--reconcile", action="store_true", help="摸排：未登记文件 / 文件缺失条目")
    ap.add_argument("--renumber", action="store_true", help="重编号建议（dry-run，不改文件）")
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
        all_err = all_warn = 0
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
            print(f"✗ 图表校验未通过（{all_err} error / {all_warn} warning）")
            return 1
        print(f"✓ 全库图表校验通过（{all_err} error / {all_warn} warning）")
        return 0

    if not args.chapter_path:
        ap.print_usage()
        return 2

    ch = find_chapter_dir(args.chapter_path)
    if ch is None:
        print(f"✗ 章节目录不存在: {args.chapter_path}", file=sys.stderr)
        return 2

    if args.reconcile:
        return reconcile(ch, parse_registry(ch / "figure-registry.md", Findings()))
    if args.renumber:
        return renumber(ch, parse_registry(ch / "figure-registry.md", Findings()))

    f = check_chapter(ch, args.final)
    for e in f.errors:
        print(f"✗ {e}")
    for w in f.warnings:
        print(f"⚠ {w}")
    if f.errors:
        print(f"\n✗ 图表校验未通过（{len(f.errors)} error / {len(f.warnings)} warning）")
        return 1
    print(f"\n✓ 图表校验通过（0 error / {len(f.warnings)} warning）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
