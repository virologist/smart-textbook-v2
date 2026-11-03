#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gates/g3_figure_naming.py — G3 图表与命名门禁（B5 · 方向 4 稳健性）

把「只存在于 .claude/ 且从不执行」的图表/命名守卫迁为可执行 CLI。

迁移来源（旧脚本 → 新判据）：
  figure-guard.sh           → G3-F1 ipynb A 类字段 / G3-F2 B 类字段 / G3-F3 C 类字段
                              G3-F4 通用必填 / G3-F5 figure_type 双轨 / G3-F6 PNG sidecar
  rename-guard.sh           → G3-N1 正式文件禁止重命名 / G3-N2 版本快照命名放行
  composite-date-guard.sh   → G3-D1 未来日期 / G3-D2 过期日期

【静态化语义（关键）】
原守卫是 Claude Code hook，在「写入某文件的那一刻」触发；静态扫描按
「**产物存在即须达标**」重建等价语义：
  · 章节目录存在 *.ipynb → 触发 F1~F5
  · 章节目录存在 *.png 且存在同名 sidecar (*.md) → 触发 F6
  · 章节目录存在受保护正式文件（draft.md 等）→ 触发 N1（改名即缺失）
  · 章节目录存在版本快照文件 → 触发 N2（快照命名须合规）
  · 章节目录存在 YAML/MD 文件 → 触发 D1/D2（日期一致性）

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/gates/g3_figure_naming.py
    python3 scripts/gates/g3_figure_naming.py --baseline
    python3 scripts/gates/g3_figure_naming.py --chapter 章节/ch_02
    python3 scripts/gates/g3_figure_naming.py --probe-negatives
    python3 scripts/gates/g3_figure_naming.py --emit-baseline
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_USAGE, EXIT_VIOLATION, Finding, SEVERITY_ERROR, SEVERITY_WARN,
    add_common_args, apply_baseline, baseline_load, finish,
    rel_path, report, resolve_root, select_chapters,    probe_health_check,
)

GATE_TITLE = "G3 图表与命名门禁"

# ── 图表判型（迁移 figure-guard.sh / figure_type_schema.md）──────────────
CRITERIA_FIELDS = {
    "A": (("applicable_boundary", "适用边界声明"), ("reference_pmids", "支撑文献PMID")),
    "B": (("data_provenance", "数据溯源"), ("generation_rule", "数据提取规则")),
    "C": (("declaration", "AI披露标签"), ("figure_caption", "图注"),
         ("method_disclosure", "方法部分AI使用说明")),
}

# B 类必须标注数据来源等级（阻断级）
B_PROVENANCE_TAGS = ("[实]", "[估]", "[待核实]")

# C 类必须标注的生成方式
C_DECLARATIONS = ("AI_generated", "AI_assisted", "human_made")

# C 类图注必须含 AI 生成标签
C_CAPTION_TAGS = ("【AI生成】", "[AI_generated]", "[AI_assisted]")

# 通用必填（迁移 figure-guard.sh 段落1 尾部）
COMMON_FIELDS = (("数据来源", "数据来源"), ("生成日期", "生成日期"),
                 ("setup_chinese_font", "中文字体配置"))

# figure_type 双轨（TYPE-E / TYPE-M / TYPE-C）
FIGURE_TYPES = ("TYPE-E", "TYPE-M", "TYPE-C")
TYPE_M_EXTRA = ("applicable_boundary", "generation_rule")

# ── 命名判据（迁移 rename-guard.sh）──────────────────────────────────────
PROTECTED_FILES = ("draft.md", "chapter_spec.md", "write_plan.md",
                   "chapter_rules.yaml", "handoff.md")

# 合法的同族文件（不是改名变体）：handoff.md 与 handoff.json 是两种契约格式，
# 教材层 allowed_files 同时声明了二者（见 chapter_rules.yaml#allowed_files）。
PROTECTED_SIBLINGS = {"handoff.md": ("handoff.json",)}

# 允许的版本快照命名（迁移 rename-guard.sh 的 ALLOWED_PATTERNS）
SNAPSHOT_PATTERNS = (
    re.compile(r"^draft_v[0-9]+\.md$"),
    re.compile(r"^review_ch[0-9]+_round[0-9]+_[0-9]+\.md$"),
    re.compile(r".*_[0-9]{8}\.md$"),
    re.compile(r".*_[0-9]+_[0-9]+\.ipynb$"),
)

# ── 日期判据（迁移 composite-date-guard.sh）──────────────────────────────
DATE_FIELDS = ("date", "created", "updated", "modified", "published",
               "last_modified", "revision_date", "publish_date",
               "last_updated", "release_date")
DATE_VALUE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})|(\d{4}/\d{2}/\d{2})")

FUTURE_BLOCK_DAYS = 7      # frontmatter 未来日期 >7 天阻断
FUTURE_WARN_DAYS = 1       # 全局未来日期 >1 天警告
STALE_WARN_DAYS = 365      # 过期 >365 天警告


# ── 判据实现 ────────────────────────────────────────────────────────────

def _criteria_type(text: str) -> str | None:
    m = re.search(r"criteria_type:\s*([A-D])\b", text)
    return m.group(1) if m else None


def check_ipynb_criteria(text: str, ctype: str) -> list[tuple[str, str]]:
    """G3-F1/F2/F3：按判型检查字段齐备性。"""
    issues: list[tuple[str, str]] = []
    for field, label in CRITERIA_FIELDS.get(ctype, ()):
        if field not in text:
            issues.append((SEVERITY_WARN, f"{ctype} 类缺少 {field}（{label}）"))

    if ctype == "B":
        if not any(tag in text for tag in B_PROVENANCE_TAGS):
            issues.append((
                SEVERITY_ERROR,
                f"B 类 data_provenance 未标注 {'/'.join(B_PROVENANCE_TAGS)}",
            ))
    if ctype == "C":
        if not any(d in text for d in C_DECLARATIONS):
            issues.append((
                SEVERITY_ERROR,
                f"C 类 declaration 未标注 {'/'.join(C_DECLARATIONS)}",
            ))
        if not any(t in text for t in C_CAPTION_TAGS):
            issues.append((
                SEVERITY_WARN,
                f"C 类图注缺少 {'/'.join(C_CAPTION_TAGS)} 标签（双重披露规范）",
            ))
    return issues


def check_ipynb_common(text: str) -> list[tuple[str, str]]:
    """G3-F4：通用必填字段。"""
    issues: list[tuple[str, str]] = []
    for field, label in COMMON_FIELDS:
        if field not in text:
            issues.append((SEVERITY_WARN, f"缺少{label}（{field}）"))
    return issues


def check_figure_type(text: str) -> list[tuple[str, str]]:
    """G3-F5：figure_type 双轨校验（TYPE-E / TYPE-M / TYPE-C）。"""
    issues: list[tuple[str, str]] = []
    m = re.search(r"figure_type:\s*([A-Za-z\-]+)", text)
    if not m:
        return [(SEVERITY_WARN, "figure_type 字段缺失（建议标注 TYPE-E/TYPE-M/TYPE-C）")]
    ftype = m.group(1)
    if ftype not in FIGURE_TYPES:
        return [(SEVERITY_ERROR,
                 f"figure_type 值非法（{ftype}），应为 {'/'.join(FIGURE_TYPES)}")]
    if ftype == "TYPE-M":
        for field in TYPE_M_EXTRA:
            if field not in text:
                issues.append((SEVERITY_WARN, f"TYPE-M 缺少 {field}"))
    return issues


def check_png_sidecar(png_path: Path) -> list[tuple[str, str]]:
    """G3-F6：PNG 同名 sidecar 的 AI 披露与数据溯源标注。"""
    issues: list[tuple[str, str]] = []
    sidecar = None
    for d in (png_path.parent, png_path.parent.parent):
        cand = d / (png_path.stem + ".md")
        if cand.exists():
            sidecar = cand
            break
    if sidecar is None:
        return issues
    text = sidecar.read_text(encoding="utf-8", errors="replace")
    if re.search(r"流程图|模式图|示意图|时间轴", text):
        if not any(t in text for t in C_CAPTION_TAGS):
            issues.append((SEVERITY_WARN, "PNG 图注缺少 AI 生成标签"))
    if re.search(r"数据来源|流行病学|实验数据|统计结果", text):
        if not any(tag in text for tag in B_PROVENANCE_TAGS):
            issues.append((SEVERITY_WARN, "PNG 图注缺少数据溯源标注 [实]/[估]/[待核实]"))
    return issues


def check_protected_rename(chapter_dir: Path) -> list[tuple[str, str]]:
    """G3-N1：受保护正式文件不得被重命名（存在即须为原名）。

    静态等价语义：原脚本拦截「mv 正式文件」这一动作。静态扫描下，
    「正式文件被改名」的表征是**原名缺失而改名后的变体存在**。
    """
    issues: list[tuple[str, str]] = []
    for name in PROTECTED_FILES:
        if (chapter_dir / name).exists():
            continue
        stem = name.rsplit(".", 1)[0]
        siblings = set(PROTECTED_SIBLINGS.get(name, ()))
        variants = [p for p in chapter_dir.glob(f"{stem}*")
                    if p.is_file() and p.name != name and p.name not in siblings]
        if variants:
            issues.append((
                SEVERITY_ERROR,
                f"正式文件 {name} 疑似被重命名（存在变体: "
                f"{', '.join(sorted(p.name for p in variants))}）",
            ))
    return issues


def check_snapshot_naming(chapter_dir: Path) -> list[tuple[str, str]]:
    """G3-N2：版本快照文件命名须命中允许模式。"""
    issues: list[tuple[str, str]] = []
    for p in sorted(chapter_dir.glob("draft_v*.md")):
        if any(pat.match(p.name) for pat in SNAPSHOT_PATTERNS):
            continue
        issues.append((SEVERITY_ERROR, f"版本快照命名不合规: {p.name}"))
    return issues


def check_future_dates(path: Path, today: date) -> list[tuple[str, str]]:
    """G3-D1：未来日期检测（YAML frontmatter >7 天阻断 / 全局 >1 天警告）。"""
    issues: list[tuple[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues
    in_frontmatter = text.startswith("---")
    for line in text.splitlines():
        if in_frontmatter:
            fm = re.match(r"^(date|created|updated|modified|published|last_modified|"
                           r"revision_date|publish_date|last_updated|release_date)\s*:\s*(.+)$",
                           line)
            if fm:
                for dm in DATE_VALUE_RE.finditer(fm.group(2)):
                    ds = dm.group(1) or dm.group(2)
                    try:
                        d = date.fromisoformat(ds.replace("/", "-"))
                    except ValueError:
                        continue
                    diff = (d - today).days
                    if diff > FUTURE_BLOCK_DAYS:
                        issues.append((
                            SEVERITY_ERROR,
                            f"YAML frontmatter {fm.group(1)}={ds} 为未来日期 (+{diff}天，>7天阻断)",
                        ))
                    elif diff > FUTURE_WARN_DAYS:
                        issues.append((
                            SEVERITY_WARN,
                            f"YAML frontmatter {fm.group(1)}={ds} 为未来日期 (+{diff}天)",
                        ))
        # 全局日期扫描
        for dm in DATE_VALUE_RE.finditer(line):
            ds = dm.group(1) or dm.group(2)
            try:
                d = date.fromisoformat(ds.replace("/", "-"))
            except ValueError:
                continue
            diff = (d - today).days
            if diff > FUTURE_WARN_DAYS:
                issues.append((SEVERITY_WARN, f"未来日期 {ds} (+{diff}天)"))
            elif diff < -STALE_WARN_DAYS:
                issues.append((SEVERITY_WARN, f"过期日期 {ds} ({-diff}天前)"))
    return issues


# ── 章节级检查编排 ──────────────────────────────────────────────────────

def check_chapter(root: Path, chapter_dir: Path, today: date) -> list[Finding]:
    findings: list[Finding] = []

    def add(check_id: str, issues: list[tuple[str, str]], path: str) -> None:
        for severity, msg in issues:
            findings.append(Finding(check_id=check_id, message=msg, path=path,
                                    severity=severity))

    for nb in sorted(chapter_dir.glob("*.ipynb")):
        npath = rel_path(root, nb)
        text = nb.read_text(encoding="utf-8", errors="replace")
        ctype = _criteria_type(text)
        if ctype:
            add("G3-F1", check_ipynb_criteria(text, ctype), npath)
        add("G3-F4", check_ipynb_common(text), npath)
        add("G3-F5", check_figure_type(text), npath)

    for png in sorted(chapter_dir.glob("*.png")) + sorted(chapter_dir.glob("assets/*.png")):
        add("G3-F6", check_png_sidecar(png), rel_path(root, png))

    rpath = rel_path(root, chapter_dir)
    add("G3-N1", check_protected_rename(chapter_dir), rpath)
    add("G3-N2", check_snapshot_naming(chapter_dir), rpath)

    for f in sorted(list(chapter_dir.glob("*.yaml")) + list(chapter_dir.glob("*.md"))
                    + list(chapter_dir.glob("*.json"))):
        add("G3-D1", check_future_dates(f, today), rel_path(root, f))

    return findings


def run_scan(root: Path, chapters: list[Path]) -> list[Finding]:
    today = date.today()
    findings: list[Finding] = []
    for ch in chapters:
        findings.extend(check_chapter(root, ch, today))
    return findings


# ── 负向探针 ────────────────────────────────────────────────────────────

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def probe_negatives(root: Path) -> int:
    """构造违规样例 → 逐项断言被拦。探针全中 = exit 0。

    B7 · D-8 加固：**前置基础设施健康检查**——公共设施（状态机 SSOT 等）失效时
    探针结果不可信（会被同一个错误原因顶过），此时探针须整体失败而非「全中」。
    """
    health = probe_health_check(root)
    if health:
        print("✗ 探针前置健康检查未通过（公共设施失效，探针结果不可信）:")
        for h in health:
            print(f"    {h}")
        return EXIT_VIOLATION
    today = date.today()
    cases: list[tuple[str, object, str]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 探针 1：A 类缺 applicable_boundary
        c1 = tmp / "p1"
        _write(c1 / "a.ipynb", "criteria_type: A\nreference_pmids: [1]\n")
        cases.append((
            "A 类判型缺 applicable_boundary",
            lambda: check_ipynb_criteria((c1 / "a.ipynb").read_text(encoding="utf-8"), "A"),
            "G3-F1",
        ))

        # 探针 2：B 类缺数据溯源标注（阻断级）
        c2 = tmp / "p2"
        _write(c2 / "b.ipynb",
               "criteria_type: B\ndata_provenance: 来源\n generation_rule: 规则\n")
        cases.append((
            "B 类判型未标注 [实]/[估]/[待核实]",
            lambda: check_ipynb_criteria((c2 / "b.ipynb").read_text(encoding="utf-8"), "B"),
            "G3-F1",
        ))

        # 探针 3：C 类缺 declaration 标注（阻断级）
        c3 = tmp / "p3"
        _write(c3 / "c.ipynb",
               "criteria_type: C\ndeclaration: 某值\nfigure_caption: 图注\n"
               "method_disclosure: 说明\n")
        cases.append((
            "C 类判型未标注 AI_generated/AI_assisted/human_made",
            lambda: check_ipynb_criteria((c3 / "c.ipynb").read_text(encoding="utf-8"), "C"),
            "G3-F1",
        ))

        # 探针 4：通用必填缺失
        c4 = tmp / "p4"
        _write(c4 / "d.ipynb", "criteria_type: A\n")
        cases.append((
            "ipynb 缺通用必填（数据来源/生成日期/中文字体配置）",
            lambda: check_ipynb_common((c4 / "d.ipynb").read_text(encoding="utf-8")),
            "G3-F4",
        ))

        # 探针 5：figure_type 非法
        c5 = tmp / "p5"
        _write(c5 / "e.ipynb", "figure_type: TYPE-Z\n")
        cases.append((
            "figure_type 值非法（TYPE-Z）",
            lambda: check_figure_type((c5 / "e.ipynb").read_text(encoding="utf-8")),
            "G3-F5",
        ))

        # 探针 6：正式文件被改名
        c6 = tmp / "p6"
        _write(c6 / "draft_final.md", "x")
        cases.append((
            "正式文件 draft.md 疑似被重命名（存在变体 draft_final.md）",
            lambda: check_protected_rename(c6),
            "G3-N1",
        ))

        # 探针 7：快照命名不合规
        c7 = tmp / "p7"
        _write(c7 / "draft_vX.md", "x")
        cases.append((
            "版本快照命名不合规（draft_vX.md）",
            lambda: check_snapshot_naming(c7),
            "G3-N2",
        ))

        # 探针 8：YAML frontmatter 未来日期超 7 天
        c8 = tmp / "p8"
        future = (today + timedelta(days=30)).isoformat()
        _write(c8 / "meta.yaml", f"---\ndate: {future}\n---\n")
        cases.append((
            "YAML frontmatter 未来日期 (+30 天 >7 天阻断)",
            lambda: check_future_dates(c8 / "meta.yaml", today),
            "G3-D1",
        ))

        hit, miss = 0, []
        for name, fn, expect_tag in cases:
            try:
                issues = fn()  # type: ignore[operator]
            except Exception as exc:  # pragma: no cover - 探针自身异常
                print(f"  ✗ 探针异常｜{name} → {exc}")
                miss.append(name)
                continue
            if issues:
                hit += 1
                sev, msg = issues[0]
                print(f"  ✓ 已拦截｜{name} → [{sev}] {msg[:70]}")
            else:
                miss.append(name)
                print(f"  ✗ 漏放｜{name} → (无任何问题——探针漏放!)")

    print()
    if miss:
        print(f"✗ 负向探针 {hit}/{len(cases)} 命中，漏放: {miss}")
        return EXIT_VIOLATION
    print(f"✓ 负向探针全部命中：{hit}/{len(cases)} 类违规样例均被拦截")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(description=GATE_TITLE)
    add_common_args(ap)
    args = ap.parse_args()
    root = resolve_root(args)

    if args.probe_negatives:
        return probe_negatives(root)

    try:
        chapters = select_chapters(root, args)
    except SystemExit as e:
        return int(e.code or EXIT_USAGE)

    findings = run_scan(root, chapters)
    if args.emit_baseline:
        return finish(findings, root, args, GATE_TITLE)
    if args.baseline:
        findings = apply_baseline(findings, baseline_load(root))
    report(findings, root, args.json, GATE_TITLE)
    if any(f.severity == "error" for f in findings):
        return EXIT_VIOLATION
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
