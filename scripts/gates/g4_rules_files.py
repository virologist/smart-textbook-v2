#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gates/g4_rules_files.py — G4 规则与文件门禁（B5 · 方向 4 稳健性）

把「只存在于 .claude/ 且从不执行」的规则/文件守卫迁为可执行 CLI。

迁移来源（旧脚本 → 新判据）：
  rule-sync-check.sh         → G4-R1 教材层规则禁章节污染 / G4-R2 项目层规则禁硬编码
  file-constraint.sh         → G4-F1 章节文件白名单
  chapter-rules-validate.sh  → G4-C1 chapter_rules 必填字段 / G4-C2 推荐字段
  composite-layer-guard.sh   → G4-L1 三层架构禁建（教材层/章节层禁建 CLAUDE.md 与 .claude/）

【静态化语义（关键）】
原守卫是 Claude Code hook，在「写入某文件的那一刻」触发；静态扫描按
「**文件落盘即须合规**」重建等价语义。

【P-2 红线】
本脚本为通用层，**不得含教材专用值**：
  · 领域词表从教材层规则文件动态读取（pollution_pattern / knowledge_scope），
    不在通用层写死任何疾病名
  · 教材实例路径一律运行时动态发现

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/gates/g4_rules_files.py
    python3 scripts/gates/g4_rules_files.py --baseline
    python3 scripts/gates/g4_rules_files.py --probe-negatives
    python3 scripts/gates/g4_rules_files.py --emit-baseline
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_USAGE, EXIT_VIOLATION, Finding, SEVERITY_ERROR, SEVERITY_WARN,
    add_common_args, apply_baseline, baseline_load, discover_textbooks, finish,
    load_yaml, rel_path, report, resolve_root, select_chapters,
    strip_code_blocks, strip_comments,    probe_health_check, is_infra_error,
)

GATE_TITLE = "G4 规则与文件门禁"

# 教材层规则目录（相对教材根）
TEXTBOOK_RULE_DIRS = ("00-教材规则",)

# 项目层 CLAUDE.md 位置（相对教材根）
PROJECT_CLAUDE = "CLAUDE.md"

# 章节层必填字段（迁移 chapter-rules-validate.sh）
REQUIRED_FIELDS = ("species_focus", "waterfowl", "other_economic", "status", "word_target")
RECOMMENDED_FIELDS = ("depth_level", "chapter_id")

# 教材层规则禁含的章节层专属内容（迁移 rule-sync-check.sh Layer A）
#
# 注意与原脚本的差异：原脚本用全文 grep 检测 `chapter_rules.yaml|chapter_spec.md`，
# 会误伤教材层 allowed_files 白名单（教材层**必须**声明章节层文件名白名单，
# 否则章节无法写入产物）。故本判据先剔除 allowed_files / DISALLOWED 列表块
# 再检测 —— 语义等价（检测的是「正文引用章节层内容」）且无误伤。
CHAPTER_ONLY_PATTERNS = (
    (re.compile(r"(^|\b)ch_[0-9]+-[^\s]+章节"), "章节目录名引用（章节层）"),
    (re.compile(r"(^|\b)chapter_rules\.yaml|chapter_spec\.md"), "章节层文件引用"),
    (re.compile(r"(^|\b)word_distribution:|K[1-9]_[^\s]+:.*[0-9]+"), "章节级字数分配"),
)

# 教材层白名单列表块（检测前剔除）
WHITELIST_BLOCK_RE = re.compile(
    r"^\s*(allowed_files|DISALLOWED|disallowed)\s*:\s*$(?:\n(?:[ \t]*-[^\n]*)?)*",
    re.MULTILINE,
)

# 项目层 CLAUDE.md 禁含的硬编码（迁移 rule-sync-check.sh Layer B）
PROJECT_ONLY_PATTERNS = (
    (re.compile(r"(^|\n)[ \t]*[^#\n]*?/[0-9]{2}-[^\n/]+/"), "具体教材项目路径引用"),
    (re.compile(r"(^|\b)ch_[0-9]+-[^\s/]+/"), "具体章节目录名引用"),
)

# 通用层禁含的教材实例标识（P-2：结构性污染检测；领域词汇检测由既有的
# scripts/audit/domain_leak_lint.py 负责，此处不重复实现，避免误报）
GENERAL_LAYER_INSTANCE_RES = (
    (re.compile(r"tb_[^\s/]*[一-鿿][^\s/]*/"), "教材实例目录名引用"),
    (re.compile(r"章节/ch_[0-9]+"), "章节目录路径引用"),
)


# ── 规则污染判据 ────────────────────────────────────────────────────────

def load_domain_terms(root: Path) -> list[str]:
    """从教材层规则动态读取领域词表（P-2：不在通用层写死学科词）。

    优先 pollution_pattern.diseases；退而取 knowledge_scope 下的标量值。
    """
    terms: list[str] = []
    for tb_root in discover_textbooks(root):
        for rule_dir in TEXTBOOK_RULE_DIRS:
            data = load_yaml(tb_root / rule_dir / "textbook_rules.yaml")
            if not data:
                continue
            pollution = data.get("pollution_pattern") or {}
            if isinstance(pollution, dict):
                for item in pollution.get("diseases") or []:
                    if isinstance(item, str) and item:
                        terms.append(item)
            scope = data.get("knowledge_scope") or {}
            if isinstance(scope, dict):
                terms.extend(_collect_scalars(scope))
    # 去重 + 长度过滤（过短的词易误报）
    uniq = {t.strip() for t in terms if t and len(t.strip()) >= 2}
    return sorted(uniq)


def _collect_scalars(node, depth: int = 0) -> list[str]:
    """递归收集 knowledge_scope 中的字符串叶子（深度受限）。"""
    out: list[str] = []
    if depth > 4:
        return out
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for item in node:
            out.extend(_collect_scalars(item, depth + 1))
    elif isinstance(node, dict):
        for key, val in node.items():
            if key in ("sources", "authority_sources"):
                continue
            out.extend(_collect_scalars(val, depth + 1))
    return out


def check_textbook_rule_purity(path: Path) -> list[tuple[str, str]]:
    """G4-R1：教材层规则文件禁含章节层专属内容。

    实现口径（相对原 rule-sync-check.sh 的两处修正）：
      1. 原脚本 Layer A 的「教材层知识域污染」子项（5c）用疾病名检测教材层
         文件，但教材层**定义** knowledge_scope 正是其本职 → 必然误报。
         该方向的检测移交既有 `scripts/audit/domain_leak_lint.py`（已在 CI 运行），
         本判据不再重复实现。
      2. 原脚本对 `chapter_rules.yaml|chapter_spec.md` 做全文 grep，会误伤
         教材层 allowed_files 白名单（教材层**必须**声明章节层文件名白名单）。
         本判据改用**结构化检测**：只检查 YAML 顶层键与白名单列表**之外**的值，
         避免把字符串值里的说明文字（如 l2_rebuild_note）误判为污染。
    """
    issues: list[tuple[str, str]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues

    # 结构化路径：解析 YAML，只看键名与白名单外的值
    try:
        import yaml
        data = yaml.safe_load(raw) or {}
    except Exception:
        data = None

    if isinstance(data, dict):
        for key, val in data.items():
            if key in ("allowed_files", "DISALLOWED", "disallowed"):
                continue  # 白名单列表本身必须声明章节层文件名
            if key in ("structure",):
                # structure 是章节结构模板；其条目若指向章节层专有产物则违规
                if isinstance(val, list):
                    for item in val:
                        if not isinstance(item, str):
                            continue
                        for pat, label in CHAPTER_ONLY_PATTERNS:
                            if pat.search(item):
                                issues.append((SEVERITY_ERROR, f"教材层规则含{label}"))
                                break
                continue
            if key in ("word_distribution", "knowledge_scope", "species_focus",
                       "waterfowl", "other_economic"):
                continue  # 教材层定义这些属其本职
            if isinstance(val, str):
                for pat, label in CHAPTER_ONLY_PATTERNS:
                    if pat.search(val):
                        issues.append((SEVERITY_ERROR, f"教材层规则含{label}"))
                        break
            elif isinstance(val, list):
                for item in val:
                    if not isinstance(item, str):
                        continue
                    for pat, label in CHAPTER_ONLY_PATTERNS:
                        if pat.search(item):
                            issues.append((SEVERITY_ERROR, f"教材层规则含{label}"))
                            break
        return issues

    # YAML 解析失败 → 退化为文本检测（剔除白名单块与注释）
    text = strip_comments(strip_code_blocks(raw))
    text = WHITELIST_BLOCK_RE.sub("", text)
    for pat, label in CHAPTER_ONLY_PATTERNS:
        if pat.search(text):
            issues.append((SEVERITY_ERROR, f"教材层规则含{label}"))
    return issues


def check_general_layer_purity(path: Path) -> list[tuple[str, str]]:
    """G4-R3：通用层文件不得含教材实例标识（结构性污染）。

    领域**词汇**污染检测由既有的 `scripts/audit/domain_leak_lint.py` 负责
    （已在 CI 中运行）；本判据只做**实例标识**层面的结构性检测，避免与既有
    检测器重复并消除误报。
    """
    issues: list[tuple[str, str]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues
    text = strip_comments(strip_code_blocks(raw))
    for pat, label in GENERAL_LAYER_INSTANCE_RES:
        if pat.search(text):
            issues.append((SEVERITY_ERROR, f"通用层文件含{label}"))
    return issues


def check_project_claude_purity(path: Path) -> list[tuple[str, str]]:
    """G4-R2：项目层 CLAUDE.md 禁硬编码章节路径 / 章节状态。"""
    issues: list[tuple[str, str]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues
    text = strip_comments(strip_code_blocks(raw))
    for pat, label in PROJECT_ONLY_PATTERNS:
        if pat.search(text):
            issues.append((SEVERITY_ERROR, f"项目层 CLAUDE.md 含{label}（H13 泛化原则）"))
    return issues


# ── 文件白名单判据 ──────────────────────────────────────────────────────

def _allowed_patterns(rules: dict) -> list[str]:
    af = rules.get("allowed_files") or []
    return [str(x) for x in af if isinstance(x, (str, int))]


def _disallowed_patterns(rules: dict) -> list[str]:
    dis = rules.get("DISALLOWED") or rules.get("disallowed") or []
    return [str(x) for x in dis if isinstance(x, (str, int))]


def _matches(rel_name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        # 章节层白名单含占位符形态（如 draft_v{N}.md / review/round{N}_*.md）
        p = pat.replace("{N}", "*").replace("{NN}", "*")
        if fnmatch.fnmatch(rel_name, p):
            return True
        if "/" in p and rel_name.startswith(p.rsplit("/", 1)[0] + "/"):
            tail = rel_name.rsplit("/", 1)[1]
            if fnmatch.fnmatch(tail, p.rsplit("/", 1)[1]):
                return True
    return False


def check_allowed_files(chapter_dir: Path, rules: dict) -> list[tuple[str, str]]:
    """G4-F1：章节内文件须在 chapter_rules.allowed_files 白名单内。"""
    issues: list[tuple[str, str]] = []
    allowed = _allowed_patterns(rules)
    if not allowed:
        return issues
    disallowed = _disallowed_patterns(rules)
    for p in sorted(chapter_dir.iterdir()):
        if p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        if _matches(p.name, disallowed):
            issues.append((SEVERITY_ERROR, f"文件命中 DISALLOWED: {p.name}"))
            continue
        if not _matches(p.name, allowed):
            issues.append((SEVERITY_WARN, f"文件不在 allowed_files 白名单: {p.name}"))
    return issues


# ── chapter_rules 字段判据 ──────────────────────────────────────────────

def check_chapter_rules_fields(rules: dict) -> list[tuple[str, str]]:
    """G4-C1/C2：必填字段缺失（阻断）/ 推荐字段缺失（警告）。"""
    issues: list[tuple[str, str]] = []
    for field in REQUIRED_FIELDS:
        if not rules.get(field):
            issues.append((SEVERITY_ERROR, f"缺少必填字段: {field}"))
    for field in RECOMMENDED_FIELDS:
        if not rules.get(field):
            issues.append((SEVERITY_WARN, f"缺少推荐字段: {field}"))
    return issues


# ── 三层架构禁建判据 ────────────────────────────────────────────────────

def check_layer_prohibitions(tb_root: Path, chapter_dirs: list[Path]) -> list[tuple[str, str]]:
    """G4-L1：教材层/章节层禁建 CLAUDE.md 与 .claude/（§10 禁止项）。

    豁免口径（P-2 合规，不硬编码教材名）：原 composite-layer-guard.sh 用
    LEGITIMATE_TEXTBOOKS 硬编码两个教材目录名。本判据改为**声明式豁免** ——
    教材层若声明 `allow_local_claude: true`，则其下的 .claude/ 不算违规
    （等价于原脚本的「合法教材实例」名单，但由教材自己声明而非通用层写死）。
    """
    issues: list[tuple[str, str]] = []
    rules = load_yaml(tb_root / "00-教材规则" / "textbook_rules.yaml")
    allow_local = bool(rules.get("allow_local_claude", False))

    for d in [tb_root] + chapter_dirs:
        if not d.exists():
            continue
        claude = d / PROJECT_CLAUDE
        if claude.exists():
            issues.append((SEVERITY_ERROR, f"禁止在 {d.name}/ 下建 CLAUDE.md（§10）"))
        dotclaude = d / ".claude"
        if dotclaude.is_dir() and not allow_local:
            issues.append((SEVERITY_ERROR, f"禁止在 {d.name}/ 下建 .claude/（§10）"))
    return issues


# ── 编排 ────────────────────────────────────────────────────────────────

def check_textbooks(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for tb_root in discover_textbooks(root):
        rpath = rel_path(root, tb_root)

        def add(check_id: str, issues: list[tuple[str, str]], path: str) -> None:
            for severity, msg in issues:
                findings.append(Finding(check_id=check_id, message=msg, path=path,
                                        severity=severity))

        for rule_dir in TEXTBOOK_RULE_DIRS:
            rdir = tb_root / rule_dir
            if not rdir.is_dir():
                continue
            for f in sorted(list(rdir.glob("*.yaml")) + list(rdir.glob("*.md"))):
                add("G4-R1", check_textbook_rule_purity(f), rel_path(root, f))

        claude = tb_root / PROJECT_CLAUDE
        if claude.exists():
            add("G4-R2", check_project_claude_purity(claude), rel_path(root, claude))

        chapter_dirs = [d for d in (tb_root / "章节").glob("*") if d.is_dir()] \
            if (tb_root / "章节").is_dir() else []
        add("G4-L1", check_layer_prohibitions(tb_root, chapter_dirs), rpath)
    return findings


# 通用层目录（领域污染检测对象；教材层定义 knowledge_scope 属本职，不在此列）
GENERAL_LAYER_DIRS = ("00-系统规则",)


def check_general_layer(root: Path, domain_terms: list[str]) -> list[Finding]:
    """G4-R3：通用层文件不得含教材领域内容。"""
    findings: list[Finding] = []
    for d in GENERAL_LAYER_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(list(base.glob("*.yaml")) + list(base.glob("*.yml"))
                        + list(base.glob("*.md"))):
            for severity, msg in check_general_layer_purity(f):
                findings.append(Finding(check_id="G4-R3", message=msg,
                                        path=rel_path(root, f), severity=severity))
    return findings


def check_chapters(root: Path, chapters: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for ch in chapters:
        rules = load_yaml(ch / "chapter_rules.yaml")
        rpath = rel_path(root, ch)

        def add(check_id: str, issues: list[tuple[str, str]], path: str) -> None:
            for severity, msg in issues:
                findings.append(Finding(check_id=check_id, message=msg, path=path,
                                        severity=severity))

        add("G4-F1", check_allowed_files(ch, rules), rpath)
        add("G4-C1", check_chapter_rules_fields(rules), rpath)
    return findings


def run_scan(root: Path, chapters: list[Path]) -> list[Finding]:
    domain_terms = load_domain_terms(root)
    findings: list[Finding] = []
    findings.extend(check_textbooks(root))
    findings.extend(check_general_layer(root, domain_terms))
    findings.extend(check_chapters(root, chapters))
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

        # 探针 1：教材层规则含章节目录名
        c1 = tmp / "c1.yaml"
        _write(c1, "structure:\n  - 见 ch_02-某某章节\n")
        cases.append((
            "教材层规则含章节目录名引用",
            lambda: check_textbook_rule_purity(c1),
            "G4-R1",
        ))

        # 探针 2：教材层规则含章节层文件引用
        c2 = tmp / "c2.yaml"
        _write(c2, "note: 见 chapter_rules.yaml 说明\n")
        cases.append((
            "教材层规则含章节层文件引用（chapter_rules.yaml）",
            lambda: check_textbook_rule_purity(c2),
            "G4-R1",
        ))

        # 探针 3：通用层文件含教材实例标识
        c3 = tmp / "c3.yaml"
        _write(c3, "note: 见 章节/ch_02 目录\n")
        cases.append((
            "通用层文件含教材实例标识（章节目录路径）",
            lambda: check_general_layer_purity(c3),
            "G4-R3",
        ))

        # 探针 4：项目层 CLAUDE.md 硬编码章节路径
        c4 = tmp / "CLAUDE.md"
        _write(c4, "# 项目\n\n- 路径: 04-章节正文/ch_01-某某/\n")
        cases.append((
            "项目层 CLAUDE.md 硬编码章节目录路径",
            lambda: check_project_claude_purity(c4),
            "G4-R2",
        ))

        # 探针 5：文件不在 allowed_files 白名单
        c5 = tmp / "c5"
        (c5).mkdir(parents=True, exist_ok=True)
        _write(c5 / "random_file.md", "x")
        cases.append((
            "章节文件不在 allowed_files 白名单",
            lambda: check_allowed_files(c5, {"allowed_files": ["draft.md", "handoff.md"]}),
            "G4-F1",
        ))

        # 探针 6：文件命中 DISALLOWED
        c6 = tmp / "c6"
        (c6).mkdir(parents=True, exist_ok=True)
        _write(c6 / "_helper.py", "x")
        cases.append((
            "章节文件命中 DISALLOWED 模式",
            lambda: check_allowed_files(c6, {"allowed_files": ["draft.md"],
                                             "DISALLOWED": ["_*.py"]}),
            "G4-F1",
        ))

        # 探针 7：chapter_rules 必填字段缺失
        c7 = tmp / "c7"
        c7.mkdir(parents=True, exist_ok=True)
        cases.append((
            "chapter_rules.yaml 缺必填字段",
            lambda: check_chapter_rules_fields({"status": "PRODUCE"}),
            "G4-C1",
        ))

        # 探针 8：教材层建 CLAUDE.md / .claude/
        c8 = tmp / "c8"
        (c8 / ".claude").mkdir(parents=True, exist_ok=True)
        _write(c8 / "CLAUDE.md", "x")
        cases.append((
            "教材层/章节层禁建 CLAUDE.md 与 .claude/",
            lambda: check_layer_prohibitions(c8, []),
            "G4-L1",
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
