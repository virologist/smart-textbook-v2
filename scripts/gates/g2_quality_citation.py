#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gates/g2_quality_citation.py — G2 质量与引用门禁（B5 · 方向 4 稳健性）

把「只存在于 .claude/ 且从不执行」的质量/引用守卫迁为可执行 CLI。

迁移来源（旧脚本 → 新判据）：
  quality-gate.sh         → G2-Q1 草稿非空 / G2-Q2 AI 腔禁语 / G2-Q3 段落块粒度
  citation-guard.sh       → G2-C1 引用编号孤儿 / G2-C2 参考文献 PMID·DOI 真实性
  evidence-tag-sync.sh    → G2-E1 待补标记与 handoff 计数一致性
  chart-data-integrity.sh → G2-D1 图表文件魔数与 IHDR 完整性

【静态化语义（关键）】
原守卫是 Claude Code hook，在「写入 draft.md 的那一刻」触发；静态扫描没有
时间维度，因此判据按「**草稿文件存在即须达标**」重建等价语义：
  · draft*.md 存在 → 触发 Q1/Q2/Q3/C1/C2/E1
  · 章节目录存在 assets/*.png|tiff → 触发 D1（产物落盘即须完整）

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/gates/g2_quality_citation.py
    python3 scripts/gates/g2_quality_citation.py --baseline
    python3 scripts/gates/g2_quality_citation.py --chapter 章节/ch_02
    python3 scripts/gates/g2_quality_citation.py --probe-negatives
    python3 scripts/gates/g2_quality_citation.py --emit-baseline
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_USAGE, EXIT_VIOLATION, Finding, SEVERITY_ERROR, SEVERITY_WARN,
    add_common_args, apply_baseline, baseline_load, finish,
    rel_path, report, resolve_root, select_chapters,    probe_health_check,
)

GATE_TITLE = "G2 质量与引用门禁"

# AI 腔禁语（迁移 quality-gate.sh 的回退词表；优先读 neg_constraints_config.yaml）
DEFAULT_AI_PATTERNS = (
    "值得注意的是", "综上所述", "具有重要意义", "不可忽视",
    "有着重要意义", "发挥了重要作用",
)

# 段落块粒度阈值（迁移 quality-gate.sh：>1500 警告 / >2000 阻断）
BLOCK_WARN_CHARS = 1500
BLOCK_FAIL_CHARS = 2000

# 待补标记（迁移 evidence-tag-sync.sh）
PENDING_TAGS = ("[需补证据]", "[待核实]")

# 参考文献节标题（迁移 citation-guard.sh 的匹配口径）
REF_SECTION_RE = re.compile(r"^##\s*参考文献", re.MULTILINE)

# 图表魔数（迁移 chart-data-integrity.sh）
PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")
TIFF_MAGIC_LE = bytes.fromhex("49492a00")
TIFF_MAGIC_BE = bytes.fromhex("4d4d002a")
PNG_IHDR = b"IHDR"
MIN_IMAGE_BYTES = 100


# ── 判据实现 ────────────────────────────────────────────────────────────
#
# 严重级映射（迁移 quality-gate.sh / citation-guard.sh / evidence-tag-sync.sh
# 的 D12 退出码语义：0=OK / 1=WARN(非阻断) / 2=BLOCK(阻断)）：
#   原 WARN → severity=warn（原 exit 1 不阻断会话）
#   原 BLOCK → severity=error（原 exit 2 阻断）
# 统一到本仓 CLI 语义：error → exit 1；warn → 不阻断（仅计入报告）。


def load_ai_patterns(root: Path) -> list[str]:
    """优先从 neg_constraints_config.yaml 读禁语；缺省用回退词表。"""
    for candidate in (root / ".claude" / "neg_constraints_config.yaml",
                      root / "neg_constraints_config.yaml"):
        if not candidate.exists():
            continue
        try:
            import yaml
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        pats: list[str] = []
        gates = data.get("behavioral_gates") or {}
        if isinstance(gates, dict):
            for body in gates.values():
                if not isinstance(body, dict):
                    continue
                for entry in body.get("pattern_check") or []:
                    if isinstance(entry, dict) and entry.get("forbidden") and entry.get("pattern"):
                        pats.append(str(entry["pattern"]))
        if pats:
            return sorted(set(pats))
    return list(DEFAULT_AI_PATTERNS)


def split_blocks(text: str) -> list[tuple[int, int]]:
    """按 Markdown 二/三级标题切块（迁移 quality-gate.sh 的 awk 口径）。"""
    lines = text.split("\n")
    blocks: list[tuple[int, int]] = []
    start = 1
    in_block = False
    for i, line in enumerate(lines, 1):
        if re.match(r"^##\s", line) or re.match(r"^###\s", line):
            if in_block:
                blocks.append((start, i - 1))
                start = i
            else:
                in_block = True
    if len(lines) >= start:
        blocks.append((start, len(lines)))
    return blocks


def check_draft_nonempty(path: Path) -> list[tuple[str, str]]:
    """G2-Q1：草稿文件非空（原脚本 WARN 级）。"""
    if not path.exists():
        return []
    if path.stat().st_size == 0:
        return [(SEVERITY_WARN, f"草稿文件为空: {path.name}")]
    return []


def check_ai_tone(path: Path, patterns: list[str]) -> list[tuple[str, str]]:
    """G2-Q2：AI 腔禁语检测（原脚本 WARN 级）。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[tuple[str, str]] = []
    for pat in patterns:
        n = len(re.findall(re.escape(pat), text))
        if n:
            hits.append((SEVERITY_WARN, f"AI 腔禁语「{pat}」命中 {n} 处"))
    return hits


def check_block_granularity(path: Path) -> list[tuple[str, str]]:
    """G2-Q3：段落块粒度（>1500 警告 / >2000 阻断）。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    problems: list[tuple[str, str]] = []
    for s, e in split_blocks(text):
        size = sum(len(x) for x in lines[s - 1:e])
        if size > BLOCK_FAIL_CHARS:
            problems.append((
                SEVERITY_ERROR,
                f"段落块 L{s}-{e} 字符数={size} 超过 {BLOCK_FAIL_CHARS} 硬限制",
            ))
        elif size > BLOCK_WARN_CHARS:
            problems.append((
                SEVERITY_WARN,
                f"段落块 L{s}-{e} 字符数={size} 超过 {BLOCK_WARN_CHARS} 警告线",
            ))
    return problems


def check_citation_orphans(path: Path) -> list[tuple[str, str]]:
    """G2-C1：正文引用编号必须在参考文献节有对应条目（原脚本 WARN 级）。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    m = REF_SECTION_RE.search(text)
    if not m:
        return []  # 无参考文献节 → 本判据不适用（迁移原脚本放行口径）
    body = text[:m.start()]
    ref = text[m.start():]
    body_nums = set(re.findall(r"\[(\d+)\]", body))
    if not body_nums:
        return []
    ref_nums = set(re.findall(r"^\[(\d+)\]", ref, re.MULTILINE))
    missing = sorted(body_nums - ref_nums, key=int)
    if missing:
        return [(SEVERITY_WARN,
                 f"引用编号在参考文献节缺条目: {', '.join('[' + n + ']' for n in missing)}")]
    return []


def check_reference_identifiers(path: Path) -> list[tuple[str, str]]:
    """G2-C2：参考文献条目须带 PMID 或 DOI（原脚本 WARN 级）。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    m = REF_SECTION_RE.search(text)
    if not m:
        return []
    ref = text[m.start():]
    entries = re.findall(r"^\[(\d+)\].*$", ref, re.MULTILINE)
    if not entries:
        return []
    has_pmid = re.search(r"PMID:\s*[0-9]{1,8}", ref)
    has_doi = re.search(r"(DOI|doi):\s*10\.[0-9]{4,9}/[^\s,;]+", ref)
    if not has_pmid and not has_doi:
        return [(SEVERITY_WARN,
                 f"{len(entries)} 条参考文献无 PMID/DOI 标识（D4-001 真实性校验）")]
    return []


def check_evidence_sync(path: Path, chapter_dir: Path) -> list[tuple[str, str]]:
    """G2-E1：草稿待补标记数与 handoff.md 的 pending_evidence 一致（原脚本 WARN 级）。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    total = sum(text.count(tag) for tag in PENDING_TAGS)
    if total == 0:
        return []
    handoff = chapter_dir / "handoff.md"
    if not handoff.exists():
        return [(SEVERITY_WARN,
                 f"草稿有 {total} 处待补标记，但无 handoff.md 可核对 pending_evidence")]
    htext = handoff.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"pending_evidence:\s*(\d+)", htext)
    if not m:
        return [(SEVERITY_WARN,
                 f"草稿有 {total} 处待补标记，但 handoff.md 无 pending_evidence 字段")]
    declared = int(m.group(1))
    if declared == 0:
        return [(SEVERITY_WARN,
                 f"草稿有 {total} 处待补标记"
                 f"({', '.join(f'{t}={text.count(t)}' for t in PENDING_TAGS)})，"
                 f"但 handoff.md 显示 pending_evidence=0")]
    return []


def check_chart_integrity(path: Path) -> list[tuple[str, str]]:
    """G2-D1：图表文件魔数与 IHDR 完整性（原脚本 exit 2 阻断级）。"""
    if not path.exists():
        return []
    size = path.stat().st_size
    if size <= MIN_IMAGE_BYTES:
        return [(SEVERITY_ERROR, f"图表文件过小 ({size}B) — {path.name}")]
    head = path.read_bytes()[:32]
    if head.startswith(PNG_MAGIC):
        if PNG_IHDR not in head:
            return [(SEVERITY_ERROR, f"PNG 缺少 IHDR chunk — {path.name}")]
        return []
    if head.startswith(TIFF_MAGIC_LE) or head.startswith(TIFF_MAGIC_BE):
        return []
    return [(SEVERITY_ERROR, f"图表文件魔数错误 ({head[:8].hex()}) — {path.name}")]


# ── 章节级检查编排 ──────────────────────────────────────────────────────

def check_chapter(root: Path, chapter_dir: Path, ai_patterns: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    rpath = rel_path(root, chapter_dir)

    def add(check_id: str, issues: list[tuple[str, str]], path: str) -> None:
        for severity, msg in issues:
            findings.append(Finding(check_id=check_id, message=msg, path=path,
                                    severity=severity))

    for draft in sorted(chapter_dir.glob("draft*.md")):
        dpath = rel_path(root, draft)
        add("G2-Q1", check_draft_nonempty(draft), dpath)
        add("G2-Q2", check_ai_tone(draft, ai_patterns), dpath)
        add("G2-Q3", check_block_granularity(draft), dpath)
        add("G2-C1", check_citation_orphans(draft), dpath)
        add("G2-C2", check_reference_identifiers(draft), dpath)
        add("G2-E1", check_evidence_sync(draft, chapter_dir), dpath)

    assets = chapter_dir / "assets"
    if assets.is_dir():
        for img in sorted(list(assets.glob("*.png")) + list(assets.glob("*.tif"))
                          + list(assets.glob("*.tiff"))):
            add("G2-D1", check_chart_integrity(img), rel_path(root, img))
    return findings


def run_scan(root: Path, chapters: list[Path]) -> list[Finding]:
    ai_patterns = load_ai_patterns(root)
    findings: list[Finding] = []
    for ch in chapters:
        findings.extend(check_chapter(root, ch, ai_patterns))
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
    cases: list[tuple[str, object, str]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 探针 1：空草稿
        c1 = tmp / "p1"
        _write(c1 / "draft.md", "")
        cases.append((
            "草稿文件为空",
            lambda: check_draft_nonempty(c1 / "draft.md"),
            "G2-Q1",
        ))

        # 探针 2：AI 腔禁语
        c2 = tmp / "p2"
        _write(c2 / "draft.md", "本节内容综上所述具有重要意义。\n")
        cases.append((
            "AI 腔禁语命中（综上所述 / 具有重要意义）",
            lambda: check_ai_tone(c2 / "draft.md", list(DEFAULT_AI_PATTERNS)),
            "G2-Q2",
        ))

        # 探针 3：段落块超 2000 字符
        c3 = tmp / "p3"
        _write(c3 / "draft.md", "# t\n\n## 第一节\n\n" + "字" * 2100 + "\n")
        cases.append((
            "段落块字符数超过 2000 硬限制",
            lambda: check_block_granularity(c3 / "draft.md"),
            "G2-Q3",
        ))

        # 探针 4：引用编号孤儿
        c4 = tmp / "p4"
        _write(c4 / "draft.md",
               "# t\n\n正文引用 [1] 与 [2]。\n\n## 参考文献\n\n[1] 作者. 标题. PMID: 12345678\n")
        cases.append((
            "正文引用编号在参考文献节缺条目（引用幻觉）",
            lambda: check_citation_orphans(c4 / "draft.md"),
            "G2-C1",
        ))

        # 探针 5：参考文献无 PMID/DOI
        c5 = tmp / "p5"
        _write(c5 / "draft.md",
               "# t\n\n## 参考文献\n\n[1] 作者. 标题. 某出版社, 2020.\n")
        cases.append((
            "参考文献条目无 PMID/DOI 标识",
            lambda: check_reference_identifiers(c5 / "draft.md"),
            "G2-C2",
        ))

        # 探针 6：待补标记与 handoff 不一致
        c6 = tmp / "p6"
        _write(c6 / "draft.md", "正文含 [待核实] 标记。\n")
        _write(c6 / "handoff.md", "handoff_payload:\n  metrics_summary:\n    pending_evidence: 0\n")
        cases.append((
            "草稿有 [待核实] 但 handoff.md pending_evidence=0",
            lambda: check_evidence_sync(c6 / "draft.md", c6),
            "G2-E1",
        ))

        # 探针 7：图表魔数错误
        c7 = tmp / "p7"
        _write(c7 / "bad.png", "NOT-A-PNG" + "x" * 200)
        cases.append((
            "图表文件魔数错误（非 PNG/TIFF）",
            lambda: check_chart_integrity(c7 / "bad.png"),
            "G2-D1",
        ))

        # 探针 8：图表文件过小
        c8 = tmp / "p8"
        _write(c8 / "tiny.png", "x" * 50)
        cases.append((
            "图表文件过小（<100 bytes）",
            lambda: check_chart_integrity(c8 / "tiny.png"),
            "G2-D1",
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
