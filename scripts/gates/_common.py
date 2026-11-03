#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gates/_common.py — 门禁脚本公共库（B5 · 方向 4 稳健性）

统一约定（所有 scripts/gates/*.py 必须遵守）：

  退出码语义
    0 = 通过（无 error 级违规）
    1 = 违规（存在 error 级违规）
    2 = 用法错误（参数错误 / 文件缺失等）

  baseline 白名单
    存量违规进 scripts/gates/_baseline.json，命中项降级为 warning。
    键为 glob 模式（相对仓库根），不含教材专用值（P-2 红线）。
    `--emit-baseline` 输出当前 error 清单，供人工审阅后落盘。

  负向探针
    `--probe-negatives` 在临时目录构造违规样例，调用与正常模式**同一批**
    判据函数，逐项断言「必须被拦」。探针全中 = exit 0；有漏放 = exit 1。

  P-2 红线
    本文件属通用层，禁止含教材专用值（教材名 / 学科词 / 实例路径）。
    教材与章节一律运行时动态发现。
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Iterable

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_USAGE = 2

# ── 路径语义（双根）────────────────────────────────────────────────────
# PKG_ROOT：包/仓根，**规则层资产恒定在此**（状态机 SSOT、baseline 等）
# WORKSPACE_ROOT：被检工作区（教材实例所在）；默认=PKG_ROOT，可用 STT_WORKSPACE 注入
# B7 修复（D-1/D-4）：原实现只有单一 REPO_ROOT（恒=脚本所在仓根），
#   导致本脚本迁入专家包后对工作区恒「真空绿」（扫不到任何章节却报 0 error）。
PKG_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()
REPO_ROOT = PKG_ROOT  # 向后兼容旧引用（= 规则层根）
GATES_DIR = Path(__file__).resolve().parent
BASELINE_PATH = GATES_DIR / "_baseline.json"

# 状态机 SSOT（B0-Q1 裁决：唯一机器可读权威源）
# **锚定规则层根**（PKG_ROOT）——SSOT 是规则资产，不随工作区变化
STATE_MACHINE_SSOT = Path("references") / "rule-engine" / "state_machine_v2.yaml"
# 工作区内的同名 SSOT（教材实例仓中可能有一份，路径不同）
WORKSPACE_STATE_MACHINE_SSOT = Path("00-系统规则") / "state_machine_v2.yaml"

# 章节实例与教材实例的发现 glob（与 B2 validate_rules 同口径）
CHAPTER_GLOBS = ("tb_*/章节/*/chapter_rules.yaml", "plugins/*/instances/*/chapter_rules.yaml")
TEXTBOOK_GLOBS = ("tb_*/00-教材规则/textbook_rules.yaml", "plugins/*/instances/*/00-教材规则/textbook_rules.yaml")

# 元数据标题（不计入正文章节数）——沿用 composite-phase-gate.sh 的排除口径
META_SECTION_WORDS = (
    "目录", "前言", "附录", "参考", "索引", "contents", "preface",
    "appendix", "references", "index", "元数据", "概述",
)

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"


@dataclass
class Finding:
    """单条门禁判据结论。"""

    check_id: str
    message: str
    path: str = ""          # 相对仓库根的路径（baseline 匹配用）
    severity: str = SEVERITY_ERROR
    detail: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.check_id}|{self.path}"


# ── 基础设施 ────────────────────────────────────────────────────────────

def load_yaml(path: Path) -> dict:
    """读 YAML；解析失败返回空 dict（调用方自行判空）。"""
    try:
        import yaml
    except ImportError:  # pragma: no cover - 环境缺 PyYAML
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, Exception):
        return {}
    return data if isinstance(data, dict) else {}


def read_jsonl(path: Path) -> list[dict]:
    """读 JSONL；坏行跳过（由调用方统计）。"""
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def rel_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def discover_chapters(root: Path) -> list[Path]:
    """动态发现章节实例目录（含 chapter_rules.yaml 的目录）。"""
    found: list[Path] = []
    for pattern in CHAPTER_GLOBS:
        for rules_file in sorted(root.glob(pattern)):
            found.append(rules_file.parent)
    # 去重（同一目录可能命中多个 glob）
    seen: set[str] = set()
    uniq: list[Path] = []
    for d in found:
        key = str(d)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def discover_textbooks(root: Path) -> list[Path]:
    """动态发现教材实例根目录（含 00-教材规则/textbook_rules.yaml 的目录）。"""
    found: list[Path] = []
    for pattern in TEXTBOOK_GLOBS:
        for rules_file in sorted(root.glob(pattern)):
            found.append(rules_file.parent.parent)
    seen: set[str] = set()
    uniq: list[Path] = []
    for d in found:
        key = str(d)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def _ssot_path(root: Path) -> Path:
    """SSOT 路径：优先规则层根（PKG_ROOT），回退工作区同路径。"""
    for base in (PKG_ROOT, root):
        p = base / STATE_MACHINE_SSOT
        if p.is_file():
            return p
    p2 = root / WORKSPACE_STATE_MACHINE_SSOT
    return p2 if p2.is_file() else (PKG_ROOT / STATE_MACHINE_SSOT)


def phase_states(root: Path) -> list[str]:
    """从 SSOT 读取 13 态序列（不在通用层复述枚举）。"""
    data = load_yaml(_ssot_path(root))
    seq = (data.get("main_path") or {}).get("sequence") or []
    return [str(s) for s in seq]


def next_valid_map(root: Path) -> dict[str, list[str]]:
    """从 SSOT 读取状态跃迁表。"""
    data = load_yaml(_ssot_path(root))
    out: dict[str, list[str]] = {}
    for name, body in (data.get("states") or {}).items():
        if isinstance(body, dict):
            out[str(name)] = [str(x) for x in (body.get("next_valid") or [])]
    return out


def status_index(states: list[str], status: str) -> int:
    try:
        return states.index(status)
    except ValueError:
        return -1


# ── baseline ────────────────────────────────────────────────────────────

def baseline_load(root: Path) -> list[dict]:
    path = root / "scripts" / "gates" / "_baseline.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def baseline_hit(entries: Iterable[dict], check_id: str, path: str) -> bool:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("check") != check_id:
            continue
        pattern = str(entry.get("pattern", ""))
        if pattern and path_match(pattern, path):
            return True
    return False


def path_match(pattern: str, path: str) -> bool:
    """按路径段匹配（`*` 只匹配单个路径段，不跨 `/`）。

    比 fnmatch 更精确：fnmatch 的 `*` 会跨目录分隔符，可能把
    `tb_*/章节/ch_02` 误配到 `tb_a/其他/章节/ch_02`。

    另支持 `sha1:<8位十六进制>` 精确匹配形态（见 generic_pattern）。
    """
    if pattern.startswith("sha1:"):
        return path_hash(path) == pattern[5:]
    pat_parts = pattern.split("/")
    path_parts = path.split("/")
    if len(pat_parts) != len(path_parts):
        return False
    for p, q in zip(pat_parts, path_parts):
        if p == "*":
            continue
        if not fnmatch.fnmatchcase(q, p):
            return False
    return True


# ── P-2 通用化：把含教材专用值的具体路径转成不含教材专用值的模式 ────────

# 结构性中文目录名：保留（它们是通用目录契约，不是教材专用值）
STRUCTURAL_CJK = {
    "章节", "章节正文", "教材规则", "系统规则", "运行状态",
    "审计报告", "设计产物", "日志", "运行", "状态", "规则",
}


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def path_hash(path: str) -> str:
    """路径的稳定短哈希（用于 baseline 的精确匹配形态）。"""
    import hashlib
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]


def generic_pattern(path: str) -> str:
    """把具体路径转成 P-2 合规的 baseline 匹配模式。

    规则：
      · 路径含**教材专用值**（教材实例名 / 学科词等非结构性 CJK）→ 精确哈希
        形态 `sha1:<8hex>`。这是 baseline 的**精确**豁免：只豁免该条具体
        存量路径，新增同类违规（哪怕在别的教材实例下）仍必红。
      · 路径只含 ASCII 或结构性 CJK → glob 形态（`*` 单段通配），
        如 `plugins/*/instances/ch_test_smoke-*/logs/run_log.jsonl`。

    目的：baseline 白名单是通用层文件，不得写死教材名/学科词（P-2 红线）；
    同时不得因过宽的通配而放过新增违规。
    """
    segs = path.split("/")
    for i, seg in enumerate(segs):
        prev = segs[i - 1] if i > 0 else ""
        if prev == "plugins":
            # 插件 slug 是领域标识 → 该路径按精确哈希处理
            return f"sha1:{path_hash(path)}"
        if not _has_cjk(seg):
            continue
        if seg in STRUCTURAL_CJK:
            continue
        # 非结构性 CJK = 教材专用值 → 走精确哈希
        return f"sha1:{path_hash(path)}"
    return path


def apply_baseline(findings: list[Finding], entries: Iterable[dict]) -> list[Finding]:
    """命中 baseline 的 error 降级为 warn。"""
    out: list[Finding] = []
    for f in findings:
        if f.severity == SEVERITY_ERROR and baseline_hit(entries, f.check_id, f.path):
            f.severity = SEVERITY_WARN
            f.detail = {**f.detail, "baseline": True}
        out.append(f)
    return out



# ── 探针基础设施健康检查（B7 · D-8 加固）─────────────────────────────
# 问题：探针框架只断言「有问题」，不判「违规类型正确」。于是当**公共设施失效**
# （如 SSOT 路径错 → 相位枚举返回空）时，多个探针会因**同一个错误原因**通过——
#   例：日志门禁的「DEGRADE 缺 fallback_to」探针，实际被「phase 非法」顶过。
# 对策：**探针开始前做基础设施健康前置检查，坏则整体失败**。
#
# P3 · 独立复验 B-3 修复记录（2026-11-03）：
#   原实现另有「② 探针命中时断言问题不是基础设施类错误」，用**消息子串黑名单**
#   （INFRA_ERROR_MARKERS）推断成因。独立复验实测：黑名单含 `非法`/`不可用`/
#   `未找到`/`无法解析` 等泛化中文词，与真实违规文案直接重叠——**误判率 7/9**，
#   其中 `phase 非法` 恰是 check_log_schema S5 的**真违规文案**。子串黑名单在原理上
#   无法区分「同形异因」消息，故**整体删除该机制**；其设计意图（拦截因错误原因而
#   通过的探针）已由前置健康检查完整覆盖——基础设施失效在探针开始前即被拦下。
#   保留替代：判据函数自身抛异常 → 计入漏放（探针异常 ≠ 拦截成功）。
def probe_health_check(root: Path) -> list[str]:
    """探针前置健康检查：公共设施不可用时返回问题列表（探针须整体失败）。"""
    problems: list[str] = []
    try:
        states = phase_states(root)
    except Exception as exc:  # pragma: no cover
        return [f"状态机 SSOT 读取异常: {exc}"]
    if not states:
        problems.append(
            f"状态机 SSOT 未读到相位枚举（SSOT 路径={_ssot_path(root)}）——"
            "公共设施失效，探针结果不可信")
    else:
        if len(states) != 13:
            problems.append(f"相位枚举长度 {len(states)} ≠ 13（SSOT 异常）")
        for required in ("BOOTSTRAP", "PRODUCE", "VERIFY", "DELIVER", "LEARN"):
            if required not in states:
                problems.append(f"相位枚举缺 {required}（SSOT 异常）")
    if not next_valid_map(root):
        problems.append("状态机跃迁表为空（SSOT 异常）")
    return problems



# ── CLI 装配 ────────────────────────────────────────────────────────────

def add_common_args(parser, *, chapter_scope: bool = True) -> None:
    parser.add_argument("--root", default=str(WORKSPACE_ROOT),
                        help="被检工作区根目录（默认：STT_WORKSPACE 或脚本所在仓根）")
    parser.add_argument("--probe-negatives", action="store_true",
                        help="负向探针：构造违规样例自证能拦（探针全中 = exit 0）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结论")
    parser.add_argument("--baseline", action="store_true",
                        help="启用 baseline 白名单（存量违规降级为 warning）——**默认已开启**，此参数显式化")
    parser.add_argument("--no-baseline", action="store_true",
                        help="关闭 baseline 豁免（存量违规也按 error 拦）")
    parser.add_argument("--emit-baseline", action="store_true",
                        help="输出当前 error 清单（baseline 条目候选），不落盘")
    if chapter_scope:
        parser.add_argument("--chapter", action="append", default=[],
                            help="只检查指定章节目录（可重复；缺省=全库）")


def resolve_root(args) -> Path:
    return Path(args.root).resolve()


def select_chapters(root: Path, args) -> list[Path]:
    if getattr(args, "chapter", None):
        out = []
        for raw in args.chapter:
            p = Path(raw)
            if not p.is_absolute():
                p = root / p
            if not p.exists():
                print(f"[用法错误] 章节目录不存在: {raw}", file=sys.stderr)
                raise SystemExit(EXIT_USAGE)
            out.append(p.resolve())
        return out
    return discover_chapters(root)


def report(findings: list[Finding], root: Path, as_json: bool, title: str) -> None:
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    warns = [f for f in findings if f.severity == SEVERITY_WARN]
    if as_json:
        print(json.dumps({
            "gate": title,
            "error": len(errors),
            "warning": len(warns),
            "findings": [
                {"check_id": f.check_id, "severity": f.severity, "path": f.path,
                 "message": f.message, "detail": f.detail}
                for f in findings
            ],
        }, ensure_ascii=False, indent=2))
        return
    print("=" * 68)
    print(f"{title}")
    print("=" * 68)
    for f in errors:
        print(f"✗ [{f.check_id}] {f.path}\n    {f.message}")
    for f in warns:
        tag = "（baseline 豁免）" if f.detail.get("baseline") else ""
        print(f"⚠ [{f.check_id}] {f.path}{tag}\n    {f.message}")
    print(f"—— 结论: {len(errors)} error / {len(warns)} warning")


def finish(findings: list[Finding], root: Path, args, title: str) -> int:
    """统一收尾：baseline → 报告 → 退出码。"""
    if getattr(args, "emit_baseline", False):
        entries = baseline_candidates(findings)
        print(json.dumps({"entries": entries}, ensure_ascii=False, indent=2))
        return EXIT_OK
    if getattr(args, "baseline", False):
        findings = apply_baseline(findings, baseline_load(root))
    report(findings, root, getattr(args, "json", False), title)
    if any(f.severity == SEVERITY_ERROR for f in findings):
        return EXIT_VIOLATION
    return EXIT_OK


def baseline_candidates(findings: list[Finding]) -> list[dict]:
    """把 error 结论转成 baseline 条目候选（路径 → P-2 合规 glob）。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for f in findings:
        if f.severity != SEVERITY_ERROR:
            continue
        pattern = generic_pattern(f.path)
        sig = (f.check_id, pattern)
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"check": f.check_id, "pattern": pattern, "reason": f.message[:80]})
    return out


# ── 通用文本工具 ────────────────────────────────────────────────────────

def strip_comments(text: str) -> str:
    """去注释行与行内注释（迁移 rule-sync-check.sh 的预处理思路）。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def strip_code_blocks(text: str) -> str:
    """去围栏代码块与行内代码（迁移 rule-sync-check.sh 的预处理思路）。"""
    text = re.sub(r"```[\w]*\n[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    return text


def count_body_sections(text: str) -> int:
    """统计正文章节数（排除元数据标题）——迁移 composite-phase-gate.sh 口径。"""
    n = 0
    for line in text.splitlines():
        if not re.match(r"^##\s", line):
            continue
        title = line.lstrip("#").strip().lower()
        if any(w in title for w in META_SECTION_WORDS):
            continue
        n += 1
    return n


def extract_yaml_field(text: str, field_name: str) -> str | None:
    """从文本中提取形如 `field: value` 的值（支持引号包裹）。"""
    m = re.search(rf"^\s*{re.escape(field_name)}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    val = m.group(1).strip().strip('"').strip("'")
    return val or None
