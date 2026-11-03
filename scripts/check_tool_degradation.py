#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_tool_degradation.py — 工具降级链完整性校验器（B5 · 方向 4 稳健性）

按 `00-系统规则/logging-spec.md`（格式 SSOT）校验工具降级链是否被真正记录。

核心判据（对应架构设计 §3.4「降级事件强制记录」）：
  [D1] **工具失败必须有 DEGRADE 记录** —— 出现工具失败但无对应降级日志 = 违规
  [D2] DEGRADE 记录的 fallback_from 必须非空（降级必须有来路）
  [D3] DEGRADE 记录的 fallback_to 必须非空（降级必须有去处）
  [D4] fallback_from != fallback_to（无效降级 = 假降级）
  [D5] DEGRADE 记录必须能在故障记录中找到对应条目（降级必留痕）

「工具失败」的识别口径：日志中 level=ERROR 且 tool 非空的记录。
「对应降级」的判定：同一 task_id 下存在 level=DEGRADE 的记录。

验收硬指标：**exit≠0 的负向探针** —— 构造「有工具失败但无 DEGRADE 记录」的样例，
校验器必须 exit 1（证明门禁真能拦住）。

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/check_tool_degradation.py
    python3 scripts/check_tool_degradation.py --baseline      # 存量违规降级为 warning
    python3 scripts/check_tool_degradation.py --probe-negatives
    python3 scripts/check_tool_degradation.py --emit-baseline
    python3 scripts/check_tool_degradation.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "gates"))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_VIOLATION, Finding, SEVERITY_ERROR, SEVERITY_WARN,
    add_common_args, apply_baseline, baseline_load, discover_chapters,
    discover_textbooks, finish, probe_health_check, rel_path, report,
    resolve_root,
)

GATE_TITLE = "工具降级链完整性校验"

# 故障记录文件（降级留痕的落点，见 00-系统规则/07-工具故障记录.md）
FAULT_RECORD_REL = Path("00-系统规则") / "07-工具故障记录.md"

# 故障记录的条目标题形态（迁移 07-工具故障记录.md 的范式：## 故障记录 #NNN — 标题）
FAULT_ENTRY_RE = re.compile(r"^##\s*故障记录\s*#(\d+)\s*[—\-–]\s*(.+)$", re.MULTILINE)

# 故障记录条目内的工具字段（表格行 `| **工具** | \`xxx\` |`）
FAULT_TOOL_RE = re.compile(r"\*\*工具\*\*\s*\|\s*`?([^`|]+)`?")


# ── 判据实现 ────────────────────────────────────────────────────────────

def collect_events(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """从日志记录中分离工具失败事件与降级事件。"""
    failures: list[dict] = []
    degradations: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        level = rec.get("level")
        if level == "ERROR" and rec.get("tool"):
            failures.append(rec)
        elif level == "DEGRADE":
            degradations.append(rec)
    return failures, degradations


def check_degradation_records(degradations: list[dict]) -> list[tuple[str, str]]:
    """D2/D3/D4：DEGRADE 记录的降级链三要素。"""
    issues: list[tuple[str, str]] = []
    for i, rec in enumerate(degradations, 1):
        f_from = rec.get("fallback_from")
        f_to = rec.get("fallback_to")
        if not isinstance(f_from, str) or not f_from.strip():
            issues.append((SEVERITY_ERROR, f"DEGRADE#{i} 缺 fallback_from"))
        if not isinstance(f_to, str) or not f_to.strip():
            issues.append((SEVERITY_ERROR, f"DEGRADE#{i} 缺 fallback_to"))
        if isinstance(f_from, str) and isinstance(f_to, str) and f_from == f_to:
            issues.append((SEVERITY_ERROR, f"DEGRADE#{i} fallback_from == fallback_to（无效降级）"))
    return issues


def check_failures_have_degradation(failures: list[dict],
                                    degradations: list[dict]) -> list[tuple[str, str]]:
    """D1：工具失败必须有对应 DEGRADE 记录（同一 task_id）。"""
    issues: list[tuple[str, str]] = []
    degrade_tasks = {rec.get("task_id") for rec in degradations}
    for rec in failures:
        task_id = rec.get("task_id")
        if task_id in degrade_tasks:
            continue
        issues.append((
            SEVERITY_ERROR,
            f"工具失败未记录降级链: tool={rec.get('tool')!r} task_id={task_id!r} "
            f"（须补 level=DEGRADE 记录并给出 fallback_from/fallback_to）",
        ))
    return issues


def load_fault_entries(path: Path) -> list[dict]:
    """解析故障记录条目（编号 / 标题 / 工具）。"""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[dict] = []
    matches = list(FAULT_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start():end]
        tool_m = FAULT_TOOL_RE.search(body)
        entries.append({
            "id": m.group(1),
            "title": m.group(2).strip(),
            "tool": tool_m.group(1).strip() if tool_m else "",
        })
    return entries


def check_degradation_has_fault_record(degradations: list[dict],
                                        fault_entries: list[dict]) -> list[tuple[str, str]]:
    """D5：DEGRADE 记录必须能在故障记录中找到对应条目（按工具名匹配）。"""
    issues: list[tuple[str, str]] = []
    if not fault_entries:
        return issues
    known_tools = {e["tool"] for e in fault_entries if e["tool"]}
    for rec in degradations:
        tool = rec.get("tool")
        f_from = rec.get("fallback_from")
        candidates = {t for t in (tool, f_from) if isinstance(t, str) and t}
        if not candidates:
            continue
        if candidates & known_tools:
            continue
        issues.append((
            SEVERITY_WARN,
            f"DEGRADE 记录无对应故障条目: tool={tool!r} "
            f"（须在 07-工具故障记录.md 追加条目）",
        ))
    return issues


# ── 编排 ────────────────────────────────────────────────────────────────

def discover_log_files(root: Path) -> list[Path]:
    """发现全部章节级 + 项目级日志文件。"""
    found: list[Path] = []
    for ch_dir in discover_chapters(root):
        log = ch_dir / "logs" / "run_log.jsonl"
        if log.exists():
            found.append(log)
    for tb_root in discover_textbooks(root):
        project_log = tb_root / "03-运行状态" / "run_log.jsonl"
        if project_log.exists():
            found.append(project_log)
    return found


def load_records(path: Path) -> list[dict]:
    """读日志记录（跳过非法行与占位行）。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or re.match(r'^\{\s*"consumed"\s*:\s*true\s*\}$', s):
            continue
        try:
            rec = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def run_scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    fault_entries = load_fault_entries(root / FAULT_RECORD_REL)

    for log in discover_log_files(root):
        records = load_records(log)
        failures, degradations = collect_events(records)
        if not failures and not degradations:
            continue
        lpath = rel_path(root, log)

        for severity, msg in check_degradation_records(degradations):
            findings.append(Finding(check_id="LOG-DEGRADE", message=msg, path=lpath,
                                    severity=severity))
        for severity, msg in check_failures_have_degradation(failures, degradations):
            findings.append(Finding(check_id="LOG-DEGRADE", message=msg, path=lpath,
                                    severity=severity))
        for severity, msg in check_degradation_has_fault_record(degradations, fault_entries):
            findings.append(Finding(check_id="LOG-DEGRADE", message=msg, path=lpath,
                                    severity=severity))
    return findings


# ── 负向探针 ────────────────────────────────────────────────────────────

def probe_negatives(root: Path) -> int:
    """构造违规样例 → 逐项断言被拦。探针全中 = exit 0。

    验收硬指标：探针 1（工具失败无 DEGRADE 记录）必须让校验器判为违规。
    """
    # P3 · 独立复验 B-1：探针前置健康检查（与 gates 口径统一）
    #   公共设施失效时探针结果不可信，必须整体失败而非「探针全中」。
    health = probe_health_check(root)
    if health:
        print("✗ 探针前置健康检查未通过（公共设施失效，探针结果不可信）:")
        for p in health:
            print(f"    {p}")
        return EXIT_VIOLATION

    cases: list[tuple[str, object, str]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 探针 1（核心）：工具失败但无 DEGRADE 记录
        cases.append((
            "工具失败无 DEGRADE 记录（降级链缺失）",
            lambda: check_failures_have_degradation(
                [{"level": "ERROR", "tool": "search_tool", "task_id": "t-1"}], []),
            "LOG-DEGRADE",
        ))

        # 探针 2：DEGRADE 缺 fallback_from
        cases.append((
            "DEGRADE 记录缺 fallback_from",
            lambda: check_degradation_records(
                [{"level": "DEGRADE", "fallback_from": None, "fallback_to": "b"}]),
            "LOG-DEGRADE",
        ))

        # 探针 3：DEGRADE 缺 fallback_to
        cases.append((
            "DEGRADE 记录缺 fallback_to",
            lambda: check_degradation_records(
                [{"level": "DEGRADE", "fallback_from": "a", "fallback_to": None}]),
            "LOG-DEGRADE",
        ))

        # 探针 4：无效降级（from == to）
        cases.append((
            "DEGRADE 记录 fallback_from == fallback_to",
            lambda: check_degradation_records(
                [{"level": "DEGRADE", "fallback_from": "a", "fallback_to": "a"}]),
            "LOG-DEGRADE",
        ))

        # 探针 5：DEGRADE 无对应故障记录条目
        cases.append((
            "DEGRADE 记录无对应故障条目",
            lambda: check_degradation_has_fault_record(
                [{"level": "DEGRADE", "tool": "unknown_tool", "fallback_from": "unknown_tool"}],
                [{"id": "001", "title": "某故障", "tool": "known_tool"}]),
            "LOG-DEGRADE",
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
    add_common_args(ap, chapter_scope=False)
    args = ap.parse_args()
    root = resolve_root(args)

    if args.probe_negatives:
        return probe_negatives(root)

    findings = run_scan(root)
    if args.emit_baseline:
        return finish(findings, root, args, GATE_TITLE)
    # baseline **默认开启**（范式 P5：存量退化不阻塞、新增违规必红）
    # B7 · D-3：原实现 baseline 需显式 --baseline，裸跑把 66 处存量旧格式日志
    # 全报为 error——与「存量已豁免」的设计口径和 CI 实际用法都不一致。
    if not getattr(args, "no_baseline", False):
        findings = apply_baseline(findings, baseline_load(root))
    report(findings, root, args.json, GATE_TITLE)
    if any(f.severity == "error" for f in findings):
        return EXIT_VIOLATION
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
