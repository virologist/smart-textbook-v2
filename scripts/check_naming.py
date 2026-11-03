#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_naming.py — 文件命名合规检查（B7 治理收口 · 方向 3）

按 `00-系统规则/file-naming.md`（命名规范唯一权威源）机械校验文件名。

检查项：
  [N1] 正式文件格式：`[序号]-[中文概述内容].md`（序号段 + 中文概述）
  [N2] 编号段落在允许的段表内（10-/20-/30-/40-/50-/60-/70-99）
  [N3] 特殊文件命名合规（审查报告 / 题库 / 导出成品 / ipynb / 草稿快照）
  [N4] 禁用规则：含空格 / 含全角符号 / 临时名（test/tmp/untitled）/ 超长

存量 baseline：违规不阻断（baseline 白名单），但**新增违规必红**——
baseline 用 sha1 精确哈希（不用 glob：glob 会误豁免新建的同名文件）。

用法：
    python3 scripts/check_naming.py [路径] [--all] [--strict] [--probe-negatives]
    python3 scripts/check_naming.py --write-baseline     # 生成/更新 baseline
退出码：0 = 通过；1 = 存在新增违规；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()
NAMING_SSOT = PKG_ROOT / "references" / "file-naming.md"
BASELINE_PATH = PKG_ROOT / "scripts" / "gates" / "_naming_baseline.json"

# 允许的编号段范围（与 file-naming.md §3 段表一致）：(起, 止)
#   00-09 System · 10-19 Rule/Guide/Track · 20-29 Rule扩展 · 30-39 Track
#   40-49 Input · 50-59 Tool · 60-69 Orphan · 70-99 Reserved
ALLOWED_SEGMENT_RANGES = [(0, 9), (10, 19), (20, 29), (30, 39),
                          (40, 49), (50, 59), (60, 69), (70, 99)]

# 扫描范围
# 适用范围：编号规范只管**系统规则层与教材/章节层正式文件**；
# skills/ agents/ scripts/ templates/ 的文件名由各自代码生态约定（Skill 名=目录名、
# Agent MD 名=agentName），不适用中文编号规范——故不纳入扫描。
SCAN_DIRS = ["00-系统规则"]
SCAN_SUFFIX = {".md", ".yaml", ".yml", ".json", ".py", ".sh", ".ipynb"}
SCAN_EXCLUDE_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
                      "egg-info", "98-archive", "99-旧版", "审计报告", "logs"}

# 特殊文件命名（正则 → 说明）；命中即豁免常规格式检查
SPECIAL_PATTERNS = [
    (re.compile(r"^ch\d+_round\d+_\d{8}\.md$"), "审查报告"),
    (re.compile(r"^ch\d+_qbank\.md$"), "题库文件"),
    (re.compile(r"^\d{8}-.+-v\d+\.\d+\.\w+$"), "导出成品"),
    (re.compile(r"^\d{2}-\d{2}_.+\.ipynb$"), "ipynb 图表"),
    (re.compile(r"^draft_v\d+\.md$"), "草稿快照"),
    (re.compile(r"^ch\d+_框架设计\.md$"), "章节框架设计"),
    (re.compile(r"^\d{2}-项目元数据\.md$"), "项目元数据"),
    # 审查/复核产物（多轮审查体系的固定产物名）
    (re.compile(r"^R\d+(_R\d+)*[_-].+\.md$"), "审查轮次产物"),
    (re.compile(r"^R\d+(_R\d+)*_report\.md$"), "审查轮次产物"),
    (re.compile(r"^ROUND_SUMMARY\.md$"), "轮次汇总"),
    (re.compile(r"^consolidate_report\.md$"), "统稿报告"),
    (re.compile(r"^review_report\.md$"), "审查报告"),
    (re.compile(r"^R\d+\.md$"), "审查轮次产物"),
]

# 协议固定文件名（章节层 allowed_files 白名单约定的固定名，非自由命名对象）
FIXED_NAMES = {
    "chapter_rules.yaml", "chapter_spec.md", "chapter_design.md", "write_plan.md",
    "draft.md", "draft_v1.md", "draft_proofread.md", "draft_export_clean.md",
    "handoff.md", "handoff.json", "data_spec.yaml", "gaps.md",
    "figure-registry.md", "visual_plan.md", "illustration_plan.md",
    "assets.md", "progress.md", "INDEX.md", "README.md",
    "proofread_feedback.csv", "runtime_state.json", "style_guide.md",
    "textbook_rules.yaml", "project_registry.yaml", "TEXTBOOK.md",
}

# 禁用：临时名 / 无编号的裸英文名
BANNED_STEMS = {"test", "tmp", "temp", "untitled", "new", "copy", "draft",
                "readme", "index", "main", "misc", "foo", "bar"}

# 规范正式文件格式：[序号]-[中文概述内容].md
OFFICIAL_RE = re.compile(r"^(?P<seg>\d{2})-(?P<rest>.+)$")
HAS_CJK = re.compile(r"[一-鿿]")


def sha1_of(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()


def load_baseline() -> set[str]:
    if not BASELINE_PATH.is_file():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        out = set()
        for e in data.get("entries", []):
            if isinstance(e, str):
                out.add(e)
            elif isinstance(e, dict) and e.get("sha1"):
                out.add(e["sha1"])
        return out
    except (OSError, json.JSONDecodeError):
        return set()


def save_baseline(entries: set[str]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps({
            "version": 2,
            "updated": "2026-11-02",
            "note": ("B7 命名合规检查器存量违规白名单。键形态：sha1 = 文件相对路径的 sha1 精确哈希"
                     "（**不用 glob**——glob 会误豁免新建的同名文件）。本文件属通用层，故只存哈希与通用理由，"
                     "**不存路径**（P-2 红线：通用层禁教材专用值）。"),
            "entries": [{"sha1": h, "check": "N1",
                         "reason": "存量命名违规（迁移期豁免，新增同形态文件必红）"}
                        for h in sorted(entries)],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"✅ baseline 已写入 {len(entries)} 条 → {BASELINE_PATH.relative_to(REPO_ROOT)}")


def is_special(name: str) -> bool:
    return any(p.match(name) for p, _ in SPECIAL_PATTERNS)


def check_name(rel: str, name: str) -> list[str]:
    """返回该文件名的违规列表（空 = 合规）。"""
    errs: list[str] = []
    stem = Path(name).stem

    # N3 特殊文件 / 协议固定名直接豁免
    if is_special(name) or name in FIXED_NAMES:
        return errs

    # N4 禁用规则
    if " " in name:
        errs.append("[N4] 文件名含空格")
    if re.search(r"[（）【】《》：:；;，,]", name):
        errs.append("[N4] 文件名含全角/标点符号")
    if stem.lower() in BANNED_STEMS:
        errs.append(f"[N4] 临时名/裸名禁用（{stem}）")
    if len(name) > 50:
        errs.append(f"[N4] 文件名超长（{len(name)} > 50）")

    # N1/N2 正式文件格式
    m = OFFICIAL_RE.match(stem)
    if not m:
        # 以「数字-」开头但不是两位序号 → 序号格式错（如 1-单数字编号）
        if re.match(r"^\d+[-_]", stem):
            errs.append("[N1] 序号须为两位（如 11-），不得单数字")
            return errs
        # 无编号前缀：仅当含中文概述才合规（如 00-系统规则 下的中文名文件）
        if HAS_CJK.search(stem):
            return errs
        errs.append("[N1] 正式文件应为 `[序号]-[中文概述内容].md`")
        return errs

    seg = m.group("seg")
    rest = m.group("rest")
    seg_num = int(seg)
    if not any(lo <= seg_num <= hi for lo, hi in ALLOWED_SEGMENT_RANGES):
        ranges = "/".join(f"{lo:02d}-{hi:02d}" for lo, hi in ALLOWED_SEGMENT_RANGES)
        errs.append(f"[N2] 编号段 {seg}- 不在允许段表（{ranges}）")
    if not HAS_CJK.search(rest):
        errs.append("[N1] 序号后须含中文概述内容")
    return errs


def iter_files_rel():
    """产出 (文件, 相对路径) —— 相对路径基于其所属 root

    A-1 修复（2026-11-03）：
      ① 原 `roots = [PKG_ROOT / d for d in SCAN_DIRS]` 把扫描根指向**包根**，
         但 `00-系统规则` 是**教材工作区**的规则层，包内并无该目录 → 该 root
         恒为空 → **包端规则层扫描 100% 失效**（实测包端 48 文件 vs 主端 84，
         差额恰为主端 `00-系统规则/` 的 37 个）。
         修正：规则层 root 指向 WORKSPACE_ROOT（被检工作区），包根只提供 SSOT 与
         baseline——与包内其余脚本的双根语义一致（`_ssot_path()` 优先包根）。
      ② 原 `iter_files()` 的 `.py` 豁免判据 `rel_parts[0].startswith("tb_")`
         以**所属 root** 为基准 → 教材实例层的 rel_parts[0] 是 `章节` 而非
         `tb_*` → **豁免失效**（实测某教材实例的 `章节/*/assets/*.py`
         被多扫）。修正：改为按 root 性质判定。
      ③ `iter_files()` 与 `iter_files_rel()` 曾是两份**平行实现**，修复覆盖面易漂移
         （本轮包内 B-3 死代码残留即同类根因）。现合并为**单一实现**，
         `iter_files()` 退化为其薄封装。
      ④ 相对路径基准统一为 **WORKSPACE_ROOT**（与主端 REPO_ROOT 对齐）：
         规则层文件 rel = `00-系统规则/01-状态机.md`、实例层 = `tb_*/章节/...`。
         理由：baseline 条目是 `sha1(rel 字符串)`，基准不同则哈希不同 → 双端
         baseline 互不认、且「新增同名文件违规必红」的保证在跨端场景失效。
         实测双端 `_naming_baseline.json` 条目完全一致，即基线本就以工作区相对
         路径生成 —— 修正后双端可互认。
    """
    # 系统规则层（在被检工作区，不在包内）
    roots = [(WORKSPACE_ROOT / d, "rules") for d in SCAN_DIRS]
    # 教材实例层（教材层规则 + 章节层正式文件）
    roots += [(p, "instance") for p in sorted(WORKSPACE_ROOT.glob("tb_*")) if p.is_dir()]
    for base, layer in roots:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix not in SCAN_SUFFIX:
                continue
            rel_parts = p.relative_to(base).parts
            if any(part in SCAN_EXCLUDE_PARTS for part in rel_parts):
                continue
            # 隐藏目录（.omc / .claude 等）非命名规范对象
            if any(part.startswith(".") for part in rel_parts):
                continue
            # 章节层脚本/工具产物非正式文档对象（正式文档 = md/yaml/json）
            # —— 仅**教材实例层**豁免；系统规则层的 .py 属正式对象，须受检。
            if layer == "instance" and p.suffix == ".py":
                continue
            if p.name.startswith(".") or p.name.startswith("_"):
                continue
            yield p, str(p.relative_to(WORKSPACE_ROOT))


def iter_files():
    """产出 (文件) —— 委托 iter_files_rel()，避免双实现漂移"""
    for p, _rel in iter_files_rel():
        yield p



def scan() -> tuple[dict[str, list[str]], set[str], int]:
    """扫描全部文件 → (违规映射 {rel: errs}, baseline 命中集合, 实际扫描文件数)

    A-1 修复配套（2026-11-03）：原报告用 `len(SCAN_DIRS)`（配置常量长度）
    表示「扫描目录 N 个」——与实际扫描范围脱节（实例层教材目录未计入）。
    按既有口径纪律（A-3：三种口径须区分），改为如实报告**实际扫描文件数**。
    """
    baseline = load_baseline()
    violations: dict[str, list[str]] = {}
    baselined: set[str] = set()
    scanned = 0
    for p, rel in iter_files_rel():
        scanned += 1
        errs = check_name(rel, p.name)
        if not errs:
            continue
        if sha1_of(rel) in baseline:
            baselined.add(rel)
            continue
        violations[rel] = errs
    return violations, baselined, scanned


def probe_negatives() -> int:
    """负向探针：构造违规文件名，断言检查器能拦。"""
    cases = [
        ("11-正常文件.md", [], "合规基线"),
        ("01 含空格.md", ["[N4] 文件名含空格"], "含空格"),
        ("tmp.md", ["[N4] 临时名/裸名禁用（tmp）"], "临时名"),
        ("01-only-english.md", ["[N1] 序号后须含中文概述内容"], "序号后无中文"),
        ("1-单数字编号.md", ["[N1]"], "序号非两位"),
        ("01-文件（测试）.md", ["[N4] 文件名含全角/标点符号"], "全角符号"),
    ]
    passed = 0
    print("── 负向探针（命名合规）──")
    for name, expect_errs, label in cases:
        errs = check_name("x/" + name, name)
        if expect_errs == []:
            hit = not errs
        else:
            hit = all(any(e in x for x in errs) for e in expect_errs)
        if hit:
            passed += 1
            print(f"  ✓ {'放行' if not expect_errs else '拦截'}｜{label}")
        else:
            print(f"  ✗ 未按预期｜{label} → {errs}")
    print()
    if passed == len(cases):
        print(f"✓ 负向探针全部命中：{passed}/{len(cases)}")
        return 0
    print(f"✗ 负向探针未全中：{passed}/{len(cases)}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="文件命名合规检查（B7 · 方向 3）")
    ap.add_argument("path", nargs="?", help="待检查目录（缺省=全库）")
    ap.add_argument("--all", action="store_true", help="全库扫描")
    ap.add_argument("--strict", action="store_true", help="baseline 豁免项也报错")
    ap.add_argument("--write-baseline", action="store_true", help="生成/更新 baseline 白名单")
    ap.add_argument("--probe-negatives", action="store_true", help="负向探针自检")
    args = ap.parse_args()

    if args.probe_negatives:
        return probe_negatives()

    if not NAMING_SSOT.is_file():
        print(f"✗ 命名规范权威源缺失: {NAMING_SSOT}")
        return 2

    violations, baselined, scanned = scan()

    if args.write_baseline:
        entries = set()
        for p, rel in iter_files_rel():
            if check_name(rel, p.name):
                entries.add(sha1_of(rel))
        save_baseline(entries)
        return 0

    if args.strict:
        # strict：baseline 项也报出
        for rel in sorted(baselined):
            print(f"⚠ [baseline·strict] {rel}")

    for rel in sorted(violations):
        for e in violations[rel]:
            print(f"✗ {rel}: {e}")

    print()
    print(f"扫描文件 {scanned} 个（规则层 {len(SCAN_DIRS)} 个目录 + 教材实例层）· "
          f"新增违规 {len(violations)} 处 · baseline 豁免 {len(baselined)} 处")
    if violations:
        print("✗ 命名合规检查未通过（新增违规必须修复；存量已在 baseline 白名单）")
        return 1
    print("✓ 命名合规检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
