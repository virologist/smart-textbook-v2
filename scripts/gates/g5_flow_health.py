#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gates/g5_flow_health.py — G5 流程健康门禁（B5 · 方向 4 稳健性）

把「只存在于 .claude/ 且从不执行」的流程健康守卫迁为可执行 CLI。

迁移来源（旧脚本 → 新判据）：
  circuit-breaker.sh   → G5-C1 熔断器状态 / G5-C2 熔断器状态文件完整性
  delegation-guard.sh  → G5-D1 循环委派检测
  hook-error-handler.sh→ G5-H1 hook 错误日志健康度

【静态化语义（关键）】
原守卫是 Claude Code hook，在「工具调用那一刻」触发；静态扫描按
「**运行态文件落盘即须健康**」重建等价语义：
  · agent_state.json 存在 → 触发 C1（熔断器不得为 OPEN）+ C2（HMAC 完整性）
  · handoff.md 存在     → 触发 D1（委派链不得成环）
  · hook-errors.log 存在 → 触发 H1（错误日志须可解析 + 不得超阈值未轮转）

退出码：0 = 通过 / 1 = 违规 / 2 = 用法错误

用法：
    python3 scripts/gates/g5_flow_health.py
    python3 scripts/gates/g5_flow_health.py --baseline
    python3 scripts/gates/g5_flow_health.py --probe-negatives
    python3 scripts/gates/g5_flow_health.py --emit-baseline
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_OK, EXIT_USAGE, EXIT_VIOLATION, Finding, SEVERITY_ERROR, SEVERITY_WARN,
    add_common_args, apply_baseline, baseline_load, finish,
    rel_path, report, resolve_root, select_chapters,    probe_health_check, is_infra_error,
)

GATE_TITLE = "G5 流程健康门禁"

# 运行态文件（迁移 circuit-breaker.sh / hook-error-handler.sh 的路径约定）
AGENT_STATE_REL = Path(".claude") / "logs" / "agent_state.json"
HOOK_ERROR_LOG_REL = Path(".claude") / "logs" / "hook-errors.log"

# HMAC 密钥位置（迁移 circuit-breaker.sh 的 _get_secret_key）
HMAC_KEY_ENV = "TB_HMAC_SECRET_KEY"
HMAC_KEY_FILE = Path.home() / ".tb-writer-hmac-secret"

# 熔断器状态取值
CIRCUIT_STATES = ("CLOSED", "HALF_OPEN", "OPEN")

# 委派环检测（迁移 delegation-guard.sh 的正则口径）
DELEGATION_CYCLE_RES = (
    re.compile(r"tb-reviewer.*tb-researcher.*tb-reviewer"),
    re.compile(r"tb-researcher.*tb-reviewer.*tb-researcher"),
)

# hook 错误日志轮转阈值（迁移 hook-error-handler.sh 的 CR-007 口径）
HOOK_LOG_MAX_BYTES = 1048576
HOOK_LOG_MAX_LINES = 5000
HOOK_LOG_KEEP_LINES = 500


# ── 熔断器判据 ──────────────────────────────────────────────────────────

def _sorted_stringify(obj, indent: int = 0) -> str:
    """迁移 circuit-breaker.sh 的 canonical 序列化（键排序 + 缩进）。"""
    sp = " " * indent
    nc2 = " " * (indent + 2)
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        items = ",\n".join(nc2 + _sorted_stringify(v, indent + 2) for v in obj)
        return f"[\n{items}\n{sp}]"
    if not obj:
        return "{}"
    keys = sorted(obj.keys())
    pairs = ",\n".join(
        nc2 + json.dumps(k, ensure_ascii=False) + ": " + _sorted_stringify(obj[k], indent + 2)
        for k in keys
    )
    return f"{{\n{pairs}\n{sp}}}"


def _get_secret_key() -> str:
    env = os.environ.get(HMAC_KEY_ENV, "")
    if env:
        return env
    try:
        if HMAC_KEY_FILE.exists():
            return HMAC_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def check_circuit_state(state: dict) -> list[tuple[str, str]]:
    """G5-C1：熔断器不得处于 OPEN（OPEN = 阻断写操作）。"""
    issues: list[tuple[str, str]] = []
    cb = state.get("circuit_breaker") or {}
    global_cb = cb.get("global") or {}
    g_state = str(global_cb.get("state", "CLOSED"))
    if g_state not in CIRCUIT_STATES:
        issues.append((SEVERITY_ERROR, f"全局熔断器状态非法: {g_state!r}"))
    elif g_state == "OPEN":
        issues.append((SEVERITY_ERROR, "全局熔断器 OPEN（所有写操作应被阻断）"))
    elif g_state == "HALF_OPEN":
        issues.append((SEVERITY_WARN, "全局熔断器 HALF_OPEN（连续失败将触发 OPEN）"))

    agents = state.get("agents") or {}
    if isinstance(agents, dict):
        for name, body in agents.items():
            if not isinstance(body, dict):
                continue
            a_state = str(body.get("circuit", "CLOSED"))
            if a_state not in CIRCUIT_STATES:
                issues.append((SEVERITY_ERROR, f"{name} 熔断器状态非法: {a_state!r}"))
            elif a_state == "OPEN":
                issues.append((SEVERITY_ERROR, f"{name} 熔断器 OPEN（该 Agent 写操作应被阻断）"))
            elif a_state == "HALF_OPEN":
                issues.append((SEVERITY_WARN, f"{name} 熔断器 HALF_OPEN"))
    return issues


def check_state_integrity(state: dict) -> list[tuple[str, str]]:
    """G5-C2：状态文件 HMAC 完整性（有密钥时校验；无密钥则跳过）。"""
    issues: list[tuple[str, str]] = []
    secret = _get_secret_key()
    stored = str(state.get("_hmac_sha256", ""))
    if not secret:
        return issues  # 无密钥 → 跳过校验（迁移原脚本口径）
    if not stored:
        issues.append((SEVERITY_WARN, "状态文件缺 _hmac_sha256（完整性未保护）"))
        return issues
    body = {k: v for k, v in state.items() if k != "_hmac_sha256"}
    canonical = _sorted_stringify(body, 0)
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(stored, expected):
        issues.append((SEVERITY_ERROR, "状态文件 HMAC 验签失败（可能被篡改）"))
    return issues


# ── 委派环判据 ──────────────────────────────────────────────────────────

def check_delegation_cycle(text: str) -> list[tuple[str, str]]:
    """G5-D1：委派链不得成环（researcher↔reviewer 互相委派）。"""
    issues: list[tuple[str, str]] = []
    for pat in DELEGATION_CYCLE_RES:
        if pat.search(text):
            issues.append((
                SEVERITY_ERROR,
                "检测到循环委派链（researcher↔reviewer 互相委派）",
            ))
            break
    return issues


# ── hook 错误日志判据 ───────────────────────────────────────────────────

def check_hook_error_log(path: Path) -> list[tuple[str, str]]:
    """G5-H1：hook 错误日志须可解析 + 不得超阈值未轮转。"""
    issues: list[tuple[str, str]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues
    lines = raw.splitlines()
    size = path.stat().st_size

    bad = 0
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            try:
                json.loads(s)
            except json.JSONDecodeError:
                bad += 1
        elif not re.match(r"^\[[^\]]+\]\s*(ERROR|INFO|WARN|ROTATE)\s*\|", s):
            bad += 1
    if bad:
        issues.append((SEVERITY_WARN, f"hook 错误日志有 {bad} 行无法解析"))

    if size > HOOK_LOG_MAX_BYTES:
        issues.append((
            SEVERITY_WARN,
            f"hook 错误日志 {size}B 超过 {HOOK_LOG_MAX_BYTES}B 轮转阈值"
            f"（应保留最近 {HOOK_LOG_KEEP_LINES} 行）",
        ))
    elif len(lines) > HOOK_LOG_MAX_LINES:
        issues.append((
            SEVERITY_WARN,
            f"hook 错误日志 {len(lines)} 行超过 {HOOK_LOG_MAX_LINES} 行轮转阈值"
            f"（应保留最近 {HOOK_LOG_KEEP_LINES} 行）",
        ))
    return issues


# ── 编排 ────────────────────────────────────────────────────────────────

def check_chapters(root: Path, chapters: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for ch in chapters:
        handoff = ch / "handoff.md"
        if not handoff.exists():
            continue
        text = handoff.read_text(encoding="utf-8", errors="replace")
        for severity, msg in check_delegation_cycle(text):
            findings.append(Finding(check_id="G5-D1", message=msg,
                                    path=rel_path(root, handoff), severity=severity))
    return findings


def run_scan(root: Path, chapters: list[Path]) -> list[Finding]:
    findings: list[Finding] = []

    state_file = root / AGENT_STATE_REL
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            findings.append(Finding(check_id="G5-C2",
                                    message=f"状态文件无法解析: {e}",
                                    path=rel_path(root, state_file)))
            state = {}
        if isinstance(state, dict):
            for severity, msg in check_circuit_state(state):
                findings.append(Finding(check_id="G5-C1", message=msg,
                                        path=rel_path(root, state_file),
                                        severity=severity))
            for severity, msg in check_state_integrity(state):
                findings.append(Finding(check_id="G5-C2", message=msg,
                                        path=rel_path(root, state_file),
                                        severity=severity))

    hook_log = root / HOOK_ERROR_LOG_REL
    if hook_log.exists():
        for severity, msg in check_hook_error_log(hook_log):
            findings.append(Finding(check_id="G5-H1", message=msg,
                                    path=rel_path(root, hook_log), severity=severity))

    findings.extend(check_chapters(root, chapters))
    return findings


# ── 负向探针 ────────────────────────────────────────────────────────────

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

        # 探针 1：全局熔断器 OPEN
        cases.append((
            "全局熔断器 OPEN（应阻断写操作）",
            lambda: check_circuit_state({"circuit_breaker": {"global": {"state": "OPEN"}}}),
            "G5-C1",
        ))

        # 探针 2：单 Agent 熔断器 OPEN
        cases.append((
            "单 Agent 熔断器 OPEN",
            lambda: check_circuit_state({
                "circuit_breaker": {"global": {"state": "CLOSED"}},
                "agents": {"tb-writer": {"circuit": "OPEN"}},
            }),
            "G5-C1",
        ))

        # 探针 3：熔断器状态非法值
        cases.append((
            "熔断器状态取值非法",
            lambda: check_circuit_state({"circuit_breaker": {"global": {"state": "BROKEN"}}}),
            "G5-C1",
        ))

        # 探针 4：HMAC 验签失败
        def _hmac_probe():
            key = "probe-secret-key"
            os.environ[HMAC_KEY_ENV] = key
            try:
                state = {"circuit_breaker": {"global": {"state": "CLOSED"}},
                         "_hmac_sha256": "deadbeef" * 8}
                return check_state_integrity(state)
            finally:
                os.environ.pop(HMAC_KEY_ENV, None)

        cases.append((
            "状态文件 HMAC 验签失败（被篡改）",
            _hmac_probe,
            "G5-C2",
        ))

        # 探针 5：委派环
        cases.append((
            "委派链成环（researcher→reviewer→researcher）",
            lambda: check_delegation_cycle(
                "委派记录: tb-researcher 检索 → tb-reviewer 审查 → tb-researcher 补充"
            ),
            "G5-D1",
        ))

        # 探针 6：hook 错误日志含坏行
        c6 = tmp / "hook-errors.log"
        c6.write_text('{"ts": "x", "hook": "h", "msg": "m"}\nGARBAGE LINE\n',
                      encoding="utf-8")
        cases.append((
            "hook 错误日志含无法解析的行",
            lambda: check_hook_error_log(c6),
            "G5-H1",
        ))

        # 探针 7：hook 错误日志超轮转阈值（内容全部合法，确保命中的是阈值而非坏行）
        c7 = tmp / "big.log"
        c7.write_text('{"ts": "x", "hook": "h", "msg": "m"}\n'
                      * (HOOK_LOG_MAX_LINES + 10), encoding="utf-8")
        cases.append((
            "hook 错误日志超过轮转阈值（行数）",
            lambda: check_hook_error_log(c7),
            "G5-H1",
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
