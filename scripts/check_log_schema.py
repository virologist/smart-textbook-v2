#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_log_schema.py — 统一日志 schema 校验器（B5 · 方向 4 稳健性）

按 `00-系统规则/logging-spec.md`（格式 SSOT）校验运行日志是否符合 12 字段 JSONL schema。

落点（双落点，见 logging-spec.md §2）：
  章节级：{教材目录}/章节/{ch##}/logs/run_log.jsonl
  项目级：{教材目录}/03-运行状态/run_log.jsonl

检查项：
  [S1] 每行必须是合法 JSON 对象
  [S2] 必须且仅须含 12 个字段（多字段 / 少字段均违规）
  [S3] level ∈ {INFO, WARN, ERROR, DEGRADE}
  [S4] scope ∈ {chapter, project}
  [S5] phase ∈ 13 态（读 SSOT state_machine_v2.yaml，不在代码复述枚举）
  [S6] ts 必须可解析为 ISO-8601 时间戳
  [S7] agent / event / task_id 必须非空字符串
  [S8] level=DEGRADE → fallback_from 与 fallback_to 必须非空且不相等
  [S9] level≠DEGRADE → fallback_from 与 fallback_to 必须同时为 null

存量口径（logging-spec.md §6）：
  · `{"consumed": true}` 占位行 → 跳过（既不报违规也不计入统计）
  · 旧格式记录 → 进 baseline 白名单，降级为 warning（新增违规必红）

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/check_log_schema.py
    python3 scripts/check_log_schema.py --baseline      # 存量违规降级为 warning
    python3 scripts/check_log_schema.py --probe-negatives
    python3 scripts/check_log_schema.py --emit-baseline # 输出 baseline 候选
    python3 scripts/check_log_schema.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "gates"))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_VIOLATION, Finding, SEVERITY_ERROR,
    add_common_args, apply_baseline, baseline_load, discover_chapters,
    discover_textbooks, finish, phase_states, probe_health_check, rel_path,
    report, resolve_root,
)

GATE_TITLE = "统一日志 schema 校验"

# 12 字段定义（logging-spec.md §3）—— 字段名是规范的一部分，必须与文档一致
SCHEMA_FIELDS = (
    "ts", "level", "scope", "phase", "agent", "event",
    "task_id", "artifact", "tool", "fallback_from", "fallback_to", "detail",
)

REQUIRED_NON_EMPTY = ("agent", "event", "task_id")
LEVELS = ("INFO", "WARN", "ERROR", "DEGRADE")
SCOPES = ("chapter", "project")

# 占位行（logging-spec.md §6）：既非日志记录，也不计入统计
PLACEHOLDER_RE = re.compile(r'^\{\s*"consumed"\s*:\s*true\s*\}$')


# ── 判据实现 ────────────────────────────────────────────────────────────

def check_record(rec: dict, states: list[str]) -> list[tuple[str, str]]:
    """对单条记录执行 S1~S9 检查，返回 (severity, message) 列表。"""
    issues: list[tuple[str, str]] = []

    # S2：必须且仅须含 12 字段
    keys = set(rec.keys())
    missing = [f for f in SCHEMA_FIELDS if f not in keys]
    extra = sorted(keys - set(SCHEMA_FIELDS))
    if missing:
        issues.append((SEVERITY_ERROR, f"缺字段: {', '.join(missing)}"))
    if extra:
        issues.append((SEVERITY_ERROR, f"多余字段: {', '.join(extra)}"))

    # S3：level 枚举
    level = rec.get("level")
    if level not in LEVELS:
        issues.append((SEVERITY_ERROR, f"level 非法: {level!r}（应为 {'/'.join(LEVELS)}）"))

    # S4：scope 枚举
    scope = rec.get("scope")
    if scope not in SCOPES:
        issues.append((SEVERITY_ERROR, f"scope 非法: {scope!r}（应为 {'/'.join(SCOPES)}）"))

    # S5：phase 枚举（读 SSOT）
    phase = rec.get("phase")
    if phase not in states:
        issues.append((SEVERITY_ERROR, f"phase 非法: {phase!r}（不在 13 态内）"))

    # S6：ts 可解析
    ts = rec.get("ts")
    if not isinstance(ts, str) or not _parse_iso(ts):
        issues.append((SEVERITY_ERROR, f"ts 不可解析为 ISO-8601: {ts!r}"))

    # S7：非空字符串字段
    for field in REQUIRED_NON_EMPTY:
        val = rec.get(field)
        if not isinstance(val, str) or not val.strip():
            issues.append((SEVERITY_ERROR, f"{field} 必须为非空字符串（实得 {val!r}）"))

    # S8/S9：降级链一致性
    f_from = rec.get("fallback_from")
    f_to = rec.get("fallback_to")
    if level == "DEGRADE":
        if not isinstance(f_from, str) or not f_from.strip():
            issues.append((SEVERITY_ERROR, "DEGRADE 事件缺 fallback_from"))
        if not isinstance(f_to, str) or not f_to.strip():
            issues.append((SEVERITY_ERROR, "DEGRADE 事件缺 fallback_to"))
        if isinstance(f_from, str) and isinstance(f_to, str) and f_from == f_to:
            issues.append((SEVERITY_ERROR, "DEGRADE 事件 fallback_from == fallback_to（无效降级）"))
    else:
        if f_from is not None:
            issues.append((SEVERITY_ERROR, f"非 DEGRADE 事件不得有 fallback_from: {f_from!r}"))
        if f_to is not None:
            issues.append((SEVERITY_ERROR, f"非 DEGRADE 事件不得有 fallback_to: {f_to!r}"))

    return issues


def _parse_iso(value: str) -> datetime | None:
    try:
        datetime.fromisoformat(value)
        return datetime.now()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def check_log_file(path: Path, states: list[str]) -> tuple[list[tuple[str, str]], int]:
    """检查单个日志文件，返回 (issues, 有效记录数)。"""
    issues: list[tuple[str, str]] = []
    valid = 0
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [(SEVERITY_ERROR, f"日志文件不可读: {e}")], 0

    for lineno, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if PLACEHOLDER_RE.match(s):
            continue  # 占位行 → 跳过
        try:
            rec = json.loads(s)
        except json.JSONDecodeError as e:
            issues.append((SEVERITY_ERROR, f"L{lineno} 非法 JSON: {e.msg}"))
            continue
        if not isinstance(rec, dict):
            issues.append((SEVERITY_ERROR, f"L{lineno} 不是 JSON 对象"))
            continue
        valid += 1
        for severity, msg in check_record(rec, states):
            issues.append((severity, f"L{lineno}: {msg}"))
    return issues, valid


# ── 落点发现 ────────────────────────────────────────────────────────────

def discover_log_files(root: Path) -> list[Path]:
    """发现章节级 + 项目级日志文件（双落点，见 logging-spec.md §2）。

    章节实例口径与 scripts/gates/_common.py#discover_chapters 一致
    （教材实例 + 插件实例），项目级落点仅教材根有。
    """
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


def run_scan(root: Path) -> list[Finding]:
    states = phase_states(root)
    findings: list[Finding] = []
    for log in discover_log_files(root):
        issues, valid = check_log_file(log, states)
        for severity, msg in issues:
            findings.append(Finding(check_id="LOG-SCHEMA", message=msg,
                                    path=rel_path(root, log), severity=severity))
    return findings


# ── 负向探针 ────────────────────────────────────────────────────────────

def _valid_record() -> dict:
    return {
        "ts": "2026-11-02T15:30:00+08:00", "level": "INFO", "scope": "chapter",
        "phase": "PRODUCE", "agent": "tb-writer", "event": "draft_section_written",
        "task_id": "t-1", "artifact": "a.md", "tool": None,
        "fallback_from": None, "fallback_to": None, "detail": "ok",
    }


def probe_negatives(root: Path) -> int:
    """构造违规样例 → 逐项断言被拦。探针全中 = exit 0。"""
    # P3 · 独立复验 B-1：探针前置健康检查（公共设施失效时探针结果不可信）
    #   SSOT 损坏 → phase_states() 返回 [] → 所有合法 phase 都被判「phase 非法」，
    #   探针会因**错误原因**而全中（假绿）。此处必须前置拦截。
    health = probe_health_check(root)
    if health:
        print("✗ 探针前置健康检查未通过（公共设施失效，探针结果不可信）:")
        for p in health:
            print(f"    {p}")
        return EXIT_VIOLATION

    states = phase_states(root)
    cases: list[tuple[str, object, str]] = []

    def _rec(**over):
        r = _valid_record()
        r.update(over)
        return r

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 探针 1：缺字段
        c1 = tmp / "p1.jsonl"
        _write(c1, json.dumps({"ts": "2026-11-02T15:30:00+08:00",
                                "level": "INFO", "scope": "chapter",
                                "phase": "PRODUCE", "agent": "tb-writer",
                                "event": "e", "task_id": "t"}, ensure_ascii=False))
        cases.append((
            "记录缺字段（少 6 个）",
            lambda: check_log_file(c1, states),
            "LOG-SCHEMA",
        ))

        # 探针 2：多余字段
        c2 = tmp / "p2.jsonl"
        r = _rec()
        r["extra"] = 1
        _write(c2, json.dumps(r, ensure_ascii=False))
        cases.append((
            "记录含多余字段",
            lambda: check_log_file(c2, states),
            "LOG-SCHEMA",
        ))

        # 探针 3：level 非法
        c3 = tmp / "p3.jsonl"
        _write(c3, json.dumps(_rec(level="DEBUG"), ensure_ascii=False))
        cases.append((
            "level 非法值（DEBUG）",
            lambda: check_log_file(c3, states),
            "LOG-SCHEMA",
        ))

        # 探针 4：phase 非法
        c4 = tmp / "p4.jsonl"
        _write(c4, json.dumps(_rec(phase="NOT_A_PHASE"), ensure_ascii=False))
        cases.append((
            "phase 非法值（不在 13 态内）",
            lambda: check_log_file(c4, states),
            "LOG-SCHEMA",
        ))

        # 探针 5：ts 不可解析
        c5 = tmp / "p5.jsonl"
        _write(c5, json.dumps(_rec(ts="昨天"), ensure_ascii=False))
        cases.append((
            "ts 不可解析为 ISO-8601",
            lambda: check_log_file(c5, states),
            "LOG-SCHEMA",
        ))

        # 探针 6：DEGRADE 缺 fallback_to
        c6 = tmp / "p6.jsonl"
        _write(c6, json.dumps(_rec(level="DEGRADE", tool="t",
                                    fallback_from="a", fallback_to=None),
                              ensure_ascii=False))
        cases.append((
            "DEGRADE 事件缺 fallback_to",
            lambda: check_log_file(c6, states),
            "LOG-SCHEMA",
        ))

        # 探针 7：DEGRADE 的 fallback_from == fallback_to
        c7 = tmp / "p7.jsonl"
        _write(c7, json.dumps(_rec(level="DEGRADE", tool="t",
                                    fallback_from="a", fallback_to="a"),
                              ensure_ascii=False))
        cases.append((
            "DEGRADE 事件 fallback_from == fallback_to",
            lambda: check_log_file(c7, states),
            "LOG-SCHEMA",
        ))

        # 探针 8：非 DEGRADE 却带 fallback 链
        c8 = tmp / "p8.jsonl"
        _write(c8, json.dumps(_rec(fallback_from="a", fallback_to="b"),
                              ensure_ascii=False))
        cases.append((
            "非 DEGRADE 事件带 fallback 链",
            lambda: check_log_file(c8, states),
            "LOG-SCHEMA",
        ))

        # 探针 9：非法 JSON 行
        c9 = tmp / "p9.jsonl"
        _write(c9, "{not json}\n")
        cases.append((
            "日志行非法 JSON",
            lambda: check_log_file(c9, states),
            "LOG-SCHEMA",
        ))

        # 探针 10：占位行不得被误报（反向探针）
        c10 = tmp / "p10.jsonl"
        _write(c10, '{"consumed": true}\n')
        cases.append((
            "占位行应被跳过（不得误报）",
            lambda: check_log_file(c10, states),
            "LOG-SCHEMA-NOISE",
        ))

        hit, miss = 0, []
        for name, fn, expect_tag in cases:
            try:
                issues, valid = fn()  # type: ignore[misc]
            except Exception as exc:  # pragma: no cover - 探针自身异常
                print(f"  ✗ 探针异常｜{name} → {exc}")
                miss.append(name)
                continue
            if expect_tag == "LOG-SCHEMA-NOISE":
                # 反向探针：期望「无违规且零有效记录」
                if not issues and valid == 0:
                    hit += 1
                    print(f"  ✓ 已拦截｜{name} → 占位行被正确跳过")
                else:
                    miss.append(name)
                    print(f"  ✗ 漏放｜{name} → 占位行被误报: {issues[:1]}")
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


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
