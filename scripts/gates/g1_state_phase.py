#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gates/g1_state_phase.py — G1 状态机与相位门禁（B5 · 方向 4 稳健性）

把「只存在于 .claude/ 且从不执行」的相位/状态守卫迁为可执行 CLI。

迁移来源（旧脚本 → 新判据）：
  state-transition-guard.sh   → G1-S1  章节 status 必须在 SSOT 13 态内
                                 G1-S2  handoff 声明的相位必须在 SSOT 13 态内
  composite-phase-gate.sh     → G1-S3  SPECIFY 产物门控（chapter_spec / write_plan）
                                 G1-S4  PRODUCE 入场门控（BOOTSTRAP 不得有 draft）
  gate-visualize.sh           → G1-S5  VISUALIZE 相位门控（未过 VERIFY 不得进）
                                 G1-S6  旧流程阻断（forbid_legacy_illustration_plan）
  gate-deliver.sh             → G1-S7  DELIVER 交付门控（清洁稿/图表清零/待核实清零/DOCX/交付计数）

【静态化语义（关键）】
原守卫是 Claude Code hook，在「写入某文件的那一刻」触发；静态扫描没有时间维度，
因此每条判据按**产物存在性 + 权威状态**重建等价语义：

  · 原 H-SPECIFY（写 write_plan.md 时）→ 「write_plan.md 存在即须达标」
    且「状态已越过 SPECIFY 的章节必须具备 SPECIFY 产物」
  · 原 H-PRODUCE（写 draft.md 时）    → 「draft.md 存在即须状态合法且产物齐备」
  · 原 H-VISUALIZE（写 visual_plan.md 时）→ 「VISUALIZE 产物存在即须状态合法」
  · 原 H-DELIVER（写 draft_export_clean.md 时）→ 「状态已 DELIVER 即须交付物齐备」

【状态权威边界】
按 handoff.md v3.0 声明：状态权威源是 chapter_rules.yaml（status_source 字段），
handoff.md 的相位字段仅作归档。故 G1-S2 只校验 handoff 相位的**枚举合法性**，
不强制其与 chapter_rules.status 相等（那会把归档字段误当权威源）。

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/gates/g1_state_phase.py                 # 全库扫描
    python3 scripts/gates/g1_state_phase.py --baseline      # 存量违规降级为 warning
    python3 scripts/gates/g1_state_phase.py --chapter 章节/ch_02
    python3 scripts/gates/g1_state_phase.py --probe-negatives
    python3 scripts/gates/g1_state_phase.py --emit-baseline # 输出 baseline 候选
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_USAGE, EXIT_VIOLATION, Finding,
    add_common_args, apply_baseline, baseline_load, count_body_sections, finish,
    load_yaml, phase_states, rel_path, report, resolve_root, select_chapters,    probe_health_check,
)

GATE_TITLE = "G1 状态机与相位门禁"

# VISUALIZE 阻断状态（迁移 gate-visualize.sh 的 BLOCKED_STATES）
VISUALIZE_BLOCKED_STATES = {"PRODUCE", "SPECIFY", "RETRIEVE"}

# 已越过 SPECIFY 的状态集合：这些章节的 SPECIFY 产物必须齐备
POST_SPECIFY_STATES = {
    "SPECIFY", "RETRIEVE", "PRODUCE", "VERIFY", "PROOFREAD",
    "CONSOLIDATION", "VISUALIZE", "ASSEMBLE", "DELIVER", "LEARN",
}

# VISUALIZE 产物文件名（原 gate-visualize.sh 的拦截目标集合）
VISUALIZE_ARTIFACTS = ("visual_plan.md", "data_spec.yaml", "illustration_plan.md")

# handoff.md 中承载相位的字段名（原 state-transition-guard.sh 读取 current_phase）
HANDOFF_PHASE_FIELDS = ("current_phase", "last_completed_phase", "task_completed")


# ── 判据实现（正常模式与探针模式共用同一批函数）─────────────────────────

def check_status_enum(rules: dict, states: list[str]) -> list[str]:
    """G1-S1：章节 status 必须在 SSOT 13 态内。"""
    status = str(rules.get("status") or "")
    if not status:
        return ["chapter_rules.yaml 缺少 status 字段"]
    if status not in states:
        return [f"status={status!r} 不在 13 态枚举内"]
    return []


def check_handoff_phase(chapter_dir: Path, states: list[str]) -> list[str]:
    """G1-S2：handoff.md 声明的相位必须在 SSOT 13 态内（枚举合法性）。"""
    problems: list[str] = []
    handoff = chapter_dir / "handoff.md"
    if not handoff.exists():
        return problems
    text = handoff.read_text(encoding="utf-8", errors="replace")
    for field in HANDOFF_PHASE_FIELDS:
        m = re.search(rf"^\s*{field}\s*:\s*\"?([A-Za-z_]+)\"?", text, re.MULTILINE)
        if not m:
            continue
        value = m.group(1)
        if value not in states:
            problems.append(f"handoff.md {field}={value!r} 不在 13 态枚举内")
    return problems


def check_specify_products(rules: dict, chapter_dir: Path) -> list[str]:
    """G1-S3：SPECIFY 产物门控。"""
    problems: list[str] = []
    status = str(rules.get("status") or "")
    spec = chapter_dir / "chapter_spec.md"
    plan = chapter_dir / "write_plan.md"

    # 规则一：write_plan.md 存在即须达标（等价原 H-SPECIFY 写入时检查）
    if plan.exists():
        text = plan.read_text(encoding="utf-8", errors="replace")
        sections = count_body_sections(text)
        has_word = "word_target" in text
        if plan.stat().st_size < 100 or sections < 5 or not has_word:
            problems.append(
                f"write_plan.md 质量不达标 (size={plan.stat().st_size}B, "
                f"sections={sections}(需≥5), word_target={has_word})"
            )

    # 规则二：状态已越过 SPECIFY 的章节必须具备 SPECIFY 产物
    if status in POST_SPECIFY_STATES:
        if not spec.exists():
            problems.append("chapter_spec.md 不存在（状态已越过 SPECIFY）")
        elif spec.stat().st_size < 500:
            problems.append(f"chapter_spec.md 过小: {spec.stat().st_size} bytes（需 ≥500）")
        if not plan.exists():
            problems.append("write_plan.md 不存在（状态已越过 SPECIFY）")
    return problems


def check_produce_entry(rules: dict, chapter_dir: Path) -> list[str]:
    """G1-S4：PRODUCE 入场门控（draft.md 存在即须状态合法且产物齐备）。"""
    problems: list[str] = []
    draft = chapter_dir / "draft.md"
    if not draft.exists():
        return problems
    status = str(rules.get("status") or "")
    if status == "BOOTSTRAP":
        problems.append("章节状态为 BOOTSTRAP，不得进入 PRODUCE")
    if not (chapter_dir / "chapter_spec.md").exists():
        problems.append("SPECIFY 产物不完整（chapter_spec.md 缺失）")
    if not (chapter_dir / "write_plan.md").exists():
        problems.append("SPECIFY 产物不完整（write_plan.md 缺失）")
    return problems


def check_visualize_phase(rules: dict, chapter_dir: Path) -> list[str]:
    """G1-S5：VISUALIZE 相位门控（VISUALIZE 产物存在即须状态合法）。"""
    problems: list[str] = []
    gates = (rules.get("gates") or {}).get("visualize") or {}
    if gates.get("require_verify_complete", True) is not True:
        return problems
    artifacts = [a for a in VISUALIZE_ARTIFACTS if (chapter_dir / a).exists()]
    if not artifacts:
        return problems
    status = str(rules.get("status") or "")
    if status in VISUALIZE_BLOCKED_STATES:
        problems.append(
            f"状态 {status} 不得进入 VISUALIZE（须先完成 VERIFY）；"
            f"已存在产物: {artifacts}"
        )
    return problems


def check_legacy_visualize_block(rules: dict, chapter_dir: Path) -> list[str]:
    """G1-S6：旧流程阻断。

    语义等价说明：原 hook 拦截「写入 illustration_plan.md」这一动作。
    静态等价 = 存在旧流程产物 **且** 尚无新流程产物（visual_plan.md）
    ——即该章节仍停留在旧流程而未迁移；若新流程产物已存在，则旧文件只是
    历史遗留副本，不构成活跃违规（等价于 ch_01 那类已迁移章节）。
    """
    problems: list[str] = []
    gates = (rules.get("gates") or {}).get("visualize") or {}
    if gates.get("forbid_legacy_illustration_plan", False) is not True:
        return problems
    legacy = chapter_dir / "illustration_plan.md"
    if not legacy.exists():
        return problems
    if (chapter_dir / "visual_plan.md").exists():
        return problems
    problems.append(
        "检测到旧流程产物 illustration_plan.md 且无新流程 visual_plan.md"
        "（须使用 visual_plan.md + data_spec.yaml）"
    )
    return problems


def check_deliver_gate(rules: dict, chapter_dir: Path,
                       textbook_root: Path | None) -> list[str]:
    """G1-S7：DELIVER 交付门控。"""
    problems: list[str] = []
    status = str(rules.get("status") or "")
    gates = (rules.get("gates") or {}).get("deliver") or {}
    if status not in {"DELIVER", "LEARN"}:
        return problems

    clean = chapter_dir / "draft_export_clean.md"
    if gates.get("require_draft_export_clean", True) and not clean.exists():
        problems.append("draft_export_clean.md 不存在（C-CLEAN 未执行）")

    target = clean if clean.exists() else (chapter_dir / "draft.md")
    if target.exists():
        text = target.read_text(encoding="utf-8", errors="replace")
        fig = len(re.findall(r"FIG-PLACEHOLDER|\[图表占位符\]|\{\{FIG", text))
        if gates.get("require_fig_zero", True) and fig > 0:
            problems.append(f"FIG 占位符未清零: {fig} 处")
        unverified = len(re.findall(r"\[待核实\]", text))
        if gates.get("require_no_pending_verify", True) and unverified > 0:
            problems.append(f"[待核实] 标注未清零: {unverified} 处")
        est = len(re.findall(r"\[估\]", text))
        if gates.get("require_no_estimate_tags", True) and est > 0:
            problems.append(f"[估] 标注未清零: {est} 处")

    if gates.get("require_docx", True):
        exports = chapter_dir / "exports"
        docx = list(exports.glob("*.docx")) if exports.is_dir() else []
        if not docx:
            problems.append("exports/ 目录无 DOCX 文件")

    if textbook_root is not None:
        registry = textbook_root / "project_registry.yaml"
        if registry.exists():
            data = load_yaml(registry)
            try:
                count = int(data.get("deliver_count", 0))
            except (TypeError, ValueError):
                count = 0
            if count < 1:
                problems.append(f"deliver_count={count}<1（须先完成知识沉淀）")
    return problems


# ── 章节级检查编排 ──────────────────────────────────────────────────────

def find_textbook_root(chapter_dir: Path) -> Path | None:
    """向上找教材根（含 project_registry.yaml 或 00-教材规则/）。"""
    cur = chapter_dir
    for _ in range(4):
        cur = cur.parent
        if not cur.exists():
            break
        if (cur / "project_registry.yaml").exists() or (cur / "00-教材规则").is_dir():
            return cur
    return None


def check_chapter(root: Path, chapter_dir: Path, states: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    rules = load_yaml(chapter_dir / "chapter_rules.yaml")
    rpath = rel_path(root, chapter_dir)

    def add(check_id: str, problems: list[str]) -> None:
        for p in problems:
            findings.append(Finding(check_id=check_id, message=p, path=rpath))

    add("G1-S1", check_status_enum(rules, states))
    add("G1-S2", check_handoff_phase(chapter_dir, states))
    add("G1-S3", check_specify_products(rules, chapter_dir))
    add("G1-S4", check_produce_entry(rules, chapter_dir))
    add("G1-S5", check_visualize_phase(rules, chapter_dir))
    add("G1-S6", check_legacy_visualize_block(rules, chapter_dir))
    add("G1-S7", check_deliver_gate(rules, chapter_dir, find_textbook_root(chapter_dir)))
    return findings


def run_scan(root: Path, chapters: list[Path]) -> list[Finding]:
    states = phase_states(root)
    findings: list[Finding] = []
    for ch in chapters:
        findings.extend(check_chapter(root, ch, states))
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
    states = phase_states(root)
    cases: list[tuple[str, object, str]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 探针 1：非法 status（不在 13 态内）
        c1 = tmp / "p1"
        _write(c1 / "chapter_rules.yaml", "status: NOT_A_PHASE\n")
        cases.append((
            "非法状态枚举（status 不在 13 态内）",
            lambda: check_status_enum(load_yaml(c1 / "chapter_rules.yaml"), states),
            "G1-S1",
        ))

        # 探针 2：handoff 相位非法
        c2 = tmp / "p2"
        _write(c2 / "chapter_rules.yaml", "status: PRODUCE\n")
        _write(c2 / "handoff.md", "runtime_state:\n  last_completed_phase: NOT_A_PHASE\n")
        cases.append((
            "handoff 相位非法（last_completed_phase 不在 13 态内）",
            lambda: check_handoff_phase(c2, states),
            "G1-S2",
        ))

        # 探针 3：write_plan.md 章节数不足
        c3 = tmp / "p3"
        _write(c3 / "chapter_rules.yaml", "status: PRODUCE\n")
        _write(c3 / "write_plan.md", "# plan\n\n## A\n\n正文\n")
        cases.append((
            "write_plan.md 正文章节数不足 5",
            lambda: check_specify_products(load_yaml(c3 / "chapter_rules.yaml"), c3),
            "G1-S3",
        ))

        # 探针 4：状态已越过 SPECIFY 但产物缺失
        c4 = tmp / "p4"
        _write(c4 / "chapter_rules.yaml", "status: VERIFY\n")
        cases.append((
            "SPECIFY 产物缺失（状态已越过 SPECIFY 却没有 chapter_spec.md）",
            lambda: check_specify_products(load_yaml(c4 / "chapter_rules.yaml"), c4),
            "G1-S3",
        ))

        # 探针 5：BOOTSTRAP 状态却有 draft.md
        c5 = tmp / "p5"
        _write(c5 / "chapter_rules.yaml", "status: BOOTSTRAP\n")
        _write(c5 / "draft.md", "草稿")
        cases.append((
            "PRODUCE 入场门控（BOOTSTRAP 状态存在 draft.md）",
            lambda: check_produce_entry(load_yaml(c5 / "chapter_rules.yaml"), c5),
            "G1-S4",
        ))

        # 探针 6：VISUALIZE 相位门控（状态仍在 PRODUCE 却已有 VISUALIZE 产物）
        c6 = tmp / "p6"
        _write(c6 / "chapter_rules.yaml",
               "status: PRODUCE\ngates:\n  visualize:\n    require_verify_complete: true\n")
        _write(c6 / "visual_plan.md", "x")
        cases.append((
            "VISUALIZE 相位门控（状态 PRODUCE 未过 VERIFY 却已有 visual_plan.md）",
            lambda: check_visualize_phase(load_yaml(c6 / "chapter_rules.yaml"), c6),
            "G1-S5",
        ))

        # 探针 7：旧流程 illustration_plan.md 未阻断（且无新流程产物）
        c7 = tmp / "p7"
        _write(c7 / "chapter_rules.yaml",
               "status: DELIVER\ngates:\n  visualize:\n    forbid_legacy_illustration_plan: true\n")
        _write(c7 / "illustration_plan.md", "旧流程")
        cases.append((
            "旧流程阻断（forbid_legacy_illustration_plan=true 且仅存在旧流程产物）",
            lambda: check_legacy_visualize_block(load_yaml(c7 / "chapter_rules.yaml"), c7),
            "G1-S6",
        ))

        # 探针 8：DELIVER 交付门控（清洁稿与 DOCX 皆缺失）
        c8 = tmp / "p8"
        _write(c8 / "chapter_rules.yaml",
               "status: DELIVER\ngates:\n  deliver:\n    require_draft_export_clean: true\n"
               "    require_docx: true\n")
        cases.append((
            "DELIVER 交付门控（清洁稿与 DOCX 皆缺失）",
            lambda: check_deliver_gate(load_yaml(c8 / "chapter_rules.yaml"), c8, None),
            "G1-S7",
        ))

        hit, miss = 0, []
        for name, fn, expect_tag in cases:
            try:
                problems = fn()  # type: ignore[operator]
            except Exception as exc:  # pragma: no cover - 探针自身异常
                print(f"  ✗ 探针异常｜{name} → {exc}")
                miss.append(name)
                continue
            # P3 · B-3：原「基础设施错误子串黑名单」判定已整体删除
            #   （子串黑名单误判率 7/9，见 _common.py 修复记录）。
            #   基础设施失效改由探针**开始前**的 probe_health_check() 统一拦截；
            #   此处「有问题」即计为拦截成功，判据函数自身抛异常 → 计入漏放。
            if problems:
                hit += 1
                print(f"  ✓ 已拦截｜{name} → {problems[0][:80]}")
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
