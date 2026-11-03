#!/usr/bin/env python3
"""test_contract_sync.py — B1 契约归一的机械校验器（SSOT 一致性检查）

对标 STT scripts/check_phase_consistency.py 的「文档与权威源一致」判据：
不一致 exit 1 并列出 `文件:行号:错误值 → 正确值`。

B1 覆盖范围（本批验收 = exit 0）：
  [1] 13 态 SSOT 唯一性：全库（排除 .claude/ 与归档区）仅
      00-系统规则/state_machine_v2.yaml 定义完整 13 态枚举序列；
      schemas/state_machine_v2.yaml 必须是废弃指针（不含枚举复述）。
  [2] 引用一致性：AGENTS.md / README.md / agents/*/SKILL.md /
      schemas/agent-common/io.md 中不得再指向已废弃的
      schemas/state_machine_v2.yaml 作为权威源。
  [3] 深度枚举统一（M-06）：全库 depth_level 取值只能是四档
      vocational / associate / bachelor / graduate（level_presets.yaml SSOT）；
      允许在注释/历史记录（CHANGELOG、审计报告、docs/archive、*.bak、
      日志 jsonl）中出现旧值。
  [4] 教材规则目录名统一（B-05）：活规则文件中教材规则目录统一为
      00-教材规则（教材实例物理目录在 B1 迁移，02-教材规则 不再
      作为目录引用出现在规则契约文件中）。

B2 扩展预留：check_phase_consistency 式逐文档枚举比对（本批只做
SSOT 唯一性 + 死引用检测，行为语义不改变）。

用法：
    python3 scripts/test_contract_sync.py          # 全部检查
    python3 scripts/test_contract_sync.py --quiet  # 只输出错误
退出码：0 = 全部通过；1 = 存在不一致（附 文件:行号 明细）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 双根：PKG_ROOT = 包根（规则层资产恒定在此）· WORKSPACE_ROOT = 被检工作区
PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()
REPO_ROOT = PKG_ROOT

# ── SSOT 权威源 ────────────────────────────────────────────────
PHASE_SSOT = PKG_ROOT / "references" / "rule-engine" / "state_machine_v2.yaml"
DEPRECATED_SSOT = PKG_ROOT / "references" / "rule-engine" / "legacy-state-machine-v2.yaml"

PHASE_SEQUENCE = [
    "BOOTSTRAP", "INTAKE", "CLARIFY", "SPECIFY", "RETRIEVE", "PRODUCE",
    "VERIFY", "PROOFREAD", "CONSOLIDATION", "VISUALIZE", "ASSEMBLE",
    "DELIVER", "LEARN",
]

DEPTH_ENUM = {"vocational", "associate", "bachelor", "graduate"}

# 扫描范围：契约层活文件（排除归档/日志/备份/历史/运行时缓存）
SCAN_EXCLUDE_PARTS = {
    ".claude", ".git", ".venv", ".omc", "__pycache__", "node_modules",
    ".rule_cache", ".workbuddy", "docs", "tb-upgrade-20261102",
    "stt-backlog-20261101", "spec-A",
}
SCAN_EXCLUDE_NAMES = {
    "CHANGELOG.md", "progress.md",
}
SCAN_SUFFIXES = {".md", ".yaml", ".yml", ".py", ".sh"}

# 非深度值的 depth_level 行（field_types 声明：继承类型枚举）
INHERIT_TYPE_WORDS = {"override", "inherit_only", "extend", "fixed"}

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def iter_contract_files():
    for p in WORKSPACE_ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(WORKSPACE_ROOT)
        if any(part in SCAN_EXCLUDE_PARTS for part in rel.parts):
            continue
        if p.name in SCAN_EXCLUDE_NAMES or p.suffix not in SCAN_SUFFIXES:
            continue
        if ".bak" in p.name or p.name.startswith("_"):
            continue
        rel_str = str(rel)
        if "审计报告" in rel_str or "98-archive" in rel_str or "99-旧版" in rel_str:
            continue
        yield p, rel


# ── [1] 13 态 SSOT 唯一性 ─────────────────────────────────────
def check_phase_ssot() -> None:
    if not PHASE_SSOT.exists():
        err(f"[SSOT] 权威源缺失: {PHASE_SSOT.relative_to(PKG_ROOT)}")
        return
    text = PHASE_SSOT.read_text(encoding="utf-8")
    m = re.search(r"^main_path:\s*$\n\s+sequence:\s*\n((?:\s*-\s*\w+\s*\n)+)", text, re.M)
    if not m:
        err("[SSOT] 权威源缺少 main_path.sequence 定义")
        return
    got = [x.strip().lstrip("- ").strip() for x in m.group(1).splitlines() if x.strip()]
    for line in got:
        state = line.lstrip("- ").strip()
        if state not in PHASE_SEQUENCE:
            err(f"00-系统规则/state_machine_v2.yaml:main_path: 未知状态 {state!r}")
    if got != PHASE_SEQUENCE:
        err(f"[SSOT] main_path.sequence 与标准 13 态不一致: {got}")

    # schemas/ 废弃指针不得复述枚举序列
    if DEPRECATED_SSOT.exists():
        dep = DEPRECATED_SSOT.read_text(encoding="utf-8")
        if "main_path" in dep or "state_dependencies" in dep:
            err("[SSOT] schemas/state_machine_v2.yaml 已废弃但仍含枚举复述（main_path/state_dependencies）")
        # 权威源指向判定：按 PHASE_SSOT 的**相对形态**（末两段）而非硬编码路径——
        # 本检查器会被搬进专家包（布局不同：references/rule-engine/…），硬编码会误报。
        ssot_hint = "/".join(PHASE_SSOT.parts[-2:])
        if ssot_hint not in dep:
            err(f"[SSOT] schemas/state_machine_v2.yaml 废弃指针未指向权威源（应含 {ssot_hint}）")
    else:
        err("[SSOT] schemas/state_machine_v2.yaml 指针文件缺失")

    # 其他契约文件不得声明自己是唯一权威机器定义（避免双 SSOT 声明）
    for p, rel in iter_contract_files():
        if p == PHASE_SSOT:
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"唯一的机器可读状态(定义|机)", t):
            err(f"{rel}: 自称『唯一机器可读状态定义』与 B0-Q1 裁决冲突（SSOT=00-系统规则/state_machine_v2.yaml）")
        # 内嵌 13 态序列复述检测：available_states 完整列表 / state_dependencies 表
        if re.search(r"available_states:\s*\[", t):
            err(f"{rel}: 复述 13 态枚举（available_states）——应改为 ssot_ref 引用")


# ── [2] 权威源指向检查 ────────────────────────────────────────
def check_refs() -> None:
    self_name = Path(__file__).name
    for p, rel in iter_contract_files():
        # 豁免按**文件名**而非绝对路径判定——本检查器可能被搬到别处（专家包）后
        # 扫描另一棵树（工作区），绝对路径比较会全部失效 → 跨仓误报。
        # 豁免对象：① 权威源自身 ② 废弃指针自身 ③ 任一同名检查器自身
        if p.name in (PHASE_SSOT.name, DEPRECATED_SSOT.name) or p.name == self_name:
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(t.splitlines(), 1):
            if "schemas/state_machine_v2.yaml" not in line:
                continue
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue  # 注释行里的历史路径提及（迁移说明）——非活跃引用
            if "废弃" in line or "deprecated" in line.lower():
                continue
            err(f"{rel}:{i}: 引用已废弃的 schemas/state_machine_v2.yaml → 00-系统规则/state_machine_v2.yaml")


# ── [3] 深度枚举统一（M-06）───────────────────────────────────
def _strip_comment(line: str) -> str:
    """去掉行内注释（# / //），避免注释里的示例值被当成真实声明。

    B2 修复：原实现直接对原始行做匹配，导致注释中的
    「深度枚举键 + 示例取值」等示例文本被判为非法取值。
    """
    s = line.lstrip()
    if s.startswith("#") or s.startswith("//"):
        return ""
    for marker in ("#", "//"):
        idx = line.find(marker)
        if idx >= 0:
            line = line[:idx]
            break
    return line


def check_depth_enum() -> None:
    pat = re.compile(r"depth_level[:\s=\"']+([A-Za-z_]+)")
    # Python 源码里的取值形态与 YAML 不同：YAML 是 `depth_level: value`，
    # 而代码里常见 `depth_level: lambda v: ...`（校验谓词定义）——后者不是字段取值。
    # B2 修复：原实现对 .py 同样按 YAML 形态扫，把 lambda 定义误判为非法取值
    # （scripts/resolve_rules.py 的 depth_level 校验谓词被报 'lambda' 非法）。
    code_pat = re.compile(r"[\"']depth_level[\"']\s*:\s*lambda\b")
    for p, rel in iter_contract_files():
        t = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(t.splitlines(), 1):
            if p.suffix == ".py" and code_pat.search(line):
                continue  # 代码中的校验谓词定义，非字段取值
            m = pat.search(_strip_comment(line))
            if not m:
                continue
            val = m.group(1)
            if val in INHERIT_TYPE_WORDS:
                continue  # field_types 声明行（depth_level: override）非取值
            if val not in DEPTH_ENUM:
                err(f"{rel}:{i}: depth_level 非法取值 {val!r} → 四档之一 {sorted(DEPTH_ENUM)}")


# ── [4] 教材规则目录名统一（B-05）────────────────────────────
def check_textbook_rules_dir() -> None:
    # 规则契约文件中不应再把 02-教材规则 / 02-编写规则 当作目录引用
    pat = re.compile(r"02-(?:教材规则|编写规则)/")
    for p, rel in iter_contract_files():
        t = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(t.splitlines(), 1):
            if pat.search(line):
                if "废弃" in line or "旧" in line or "历史" in line:
                    continue
                err(f"{rel}:{i}: 目录引用 02-教材规则|02-编写规则 → 统一为 00-教材规则/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="仅输出错误")
    args = ap.parse_args()

    check_phase_ssot()
    check_refs()
    check_depth_enum()
    check_textbook_rules_dir()

    if errors:
        print(f"✗ 契约一致性检查未通过（{len(errors)} 处）：")
        for e in errors:
            print(f"  - {e}")
        return 1
    if not args.quiet:
        print("✓ 契约一致性检查通过：13 态 SSOT 唯一 / 引用无死链 / 深度枚举四档 / 教材规则目录统一")
    return 0


if __name__ == "__main__":
    sys.exit(main())
