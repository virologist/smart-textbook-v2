#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_manifest.py — 专家包契约自检（B6 验收器）

按架构设计 §4.3「铁律自检清单」机械校验包内契约，每项必须可机械验证：
  [M1] agents/*.md 文件名 stem == frontmatter name == plugin.json agents[] 声明
  [M2] plugin.json agentName 有对应 agents/{agentName}.md；且不为通用 'team-lead'
  [M3] teamInfo.leadAgent ∈ memberAgents ∪ {agentName}；memberAgents 不含 lead
  [M4] members[] 至少一条 role='lead'；members[].id 均为 agents 文件名
  [M5] tags 恰好 3 个；quickPrompts 恰好 3 个；第一条 quickPrompt == defaultInitPrompt
  [M6] displayDescription.zh 长度 ∈ [40,50]（中文字符计）
  [M7] skills[] 每项目录含 SKILL.md
  [M8] Team 型 profession.zh == displayName.zh
  [M9] settings.json 存在且 agent == plugin.json agentName
  [M10] 禁止项：hooks/ commands/ .lsp.json 不存在；.codebuddy-plugin/plugin.json 存在
  [M11] 通用层零教材专用值（P-2 红线）：references/ skills/ scripts/ 全量扫描
  [M12] agents frontmatter 禁 tools 字段；必填 name/description 齐备

用法：
    python3 scripts/check_manifest.py            # 全项检查
    python3 scripts/check_manifest.py --probe-negatives  # 负向探针自检
退出码：0 = 通过；1 = 存在违规；2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent

# 教材专用词表（P-2 红线）——通用层出现即违规
DOMAIN_TERMS = [
    "禽", "禽流感", "兽医", "蛋鸡", "肉鸡", "种鸡", "雏鸡",
    "H5N", "H9N", "H7N", "NDV", "法氏囊", "传支", "新城疫",
    "猪病", "奶牛", "水产",
]
# D-6 增强（P3 复验后立项A）：覆盖面扩 agents/ 与 docs/
# （agents/ 的 frontmatter 与正文、docs/ 的说明文档同属通用层，
#   独立 29 词补扫已证零泄漏——此处扩为常驻门禁防止未来渗漏）
DOMAIN_SCAN_DIRS = ["references", "skills", "scripts", "agents", "docs"]
DOMAIN_SCAN_SUFFIX = {".md", ".yaml", ".yml", ".py", ".json", ".sh"}

REQUIRED_FM = ("name", "description")


def fm_parse(md: Path) -> tuple[dict, str]:
    """解析 Agent MD 的 frontmatter（WorkBuddy 格式：单个 --- 块，非 Multi-Document YAML）。"""
    text = md.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}, text
    body = m.group(1)
    fm: dict = {}
    cur_key = None
    for line in body.splitlines():
        if not line.strip():
            continue
        if line.startswith(("  ", "\t")) and cur_key:
            v = line.strip()
            if fm[cur_key] is None:
                fm[cur_key] = {}
            if isinstance(fm[cur_key], dict):
                if ":" in v:
                    k2, _, v2 = v.partition(":")
                    fm[cur_key][k2.strip()] = v2.strip().strip('"')
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            cur_key = k.strip()
            v = v.strip()
            if v == "":
                fm[cur_key] = None
            else:
                fm[cur_key] = v.strip('"').strip("'")
    return fm, text


class F:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def e(self, m: str) -> None:
        self.errors.append(m)

    def w(self, m: str) -> None:
        self.warnings.append(m)


def load_manifest() -> dict:
    p = PKG_ROOT / ".codebuddy-plugin" / "plugin.json"
    if not p.is_file():
        raise FileNotFoundError(f"缺 .codebuddy-plugin/plugin.json：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def check(pj: dict, f: F) -> None:
    # M2 agentName
    agent_name = pj.get("agentName", "")
    if not agent_name:
        f.e("[M2] plugin.json 缺 agentName")
    elif agent_name == "team-lead":
        f.e("[M2] agentName 不得为通用 'team-lead'")
    elif not (PKG_ROOT / "agents" / f"{agent_name}.md").is_file():
        f.e(f"[M2] agentName 无对应文件 agents/{agent_name}.md")

    # M1/M4/M12 agents 声明与文件
    declared = [Path(a).stem for a in pj.get("agents", [])]
    for d in declared:
        p = PKG_ROOT / "agents" / f"{d}.md"
        if not p.is_file():
            f.e(f"[M1] plugin.json 声明的 agent 文件缺失：{d}.md")
            continue
        fm, text = fm_parse(p)
        if fm.get("name") != d:
            f.e(f"[M1] {d}.md frontmatter name={fm.get('name')!r} != 文件名 {d!r}")
        for req in REQUIRED_FM:
            if not fm.get(req):
                f.e(f"[M12] {d}.md frontmatter 缺 {req}")
        fm_block = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if fm_block and re.search(r"^\s*tools\s*:", fm_block.group(1), re.M):
            f.e(f"[M12] {d}.md frontmatter 禁含 tools 字段")
    for md in sorted((PKG_ROOT / "agents").glob("*.md")):
        if md.stem not in declared:
            f.e(f"[M1] agents/{md.name} 未在 plugin.json agents[] 中声明")

    # M3 teamInfo
    ti = pj.get("teamInfo") or {}
    lead = ti.get("leadAgent", "")
    members_agents = ti.get("memberAgents", [])
    if lead and lead in members_agents:
        f.e("[M3] teamInfo.memberAgents 含 lead")
    if lead and lead not in members_agents and lead != agent_name:
        f.e(f"[M3] teamInfo.leadAgent {lead} 既非 agentName 也不在 memberAgents")

    # M4 members
    members = pj.get("members", [])
    if not members:
        f.e("[M4] 缺 members 数组")
    else:
        if not any(m.get("role") == "lead" for m in members if isinstance(m, dict)):
            f.e("[M4] members 数组无 role='lead'")
        ids = {m.get("id") for m in members if isinstance(m, dict)}
        for mid in ids:
            if mid not in declared:
                f.e(f"[M4] members[].id {mid!r} 无对应 agents/{mid}.md")
        if lead and lead not in ids:
            f.e(f"[M4] teamInfo.leadAgent {lead} 未出现在 members[]")

    # M5 tags / quickPrompts
    tags = pj.get("tags", [])
    if len(tags) != 3:
        f.e(f"[M5] tags 须恰好 3 个，实得 {len(tags)}")
    qp = pj.get("quickPrompts", [])
    if len(qp) != 3:
        f.e(f"[M5] quickPrompts 须恰好 3 个，实得 {len(qp)}")
    dip = pj.get("defaultInitPrompt", {})
    if qp and isinstance(dip, dict) and dip.get("zh") and qp[0].get("zh") != dip.get("zh"):
        f.e("[M5] 第一条 quickPrompt 与 defaultInitPrompt 不一致")

    # M6 displayDescription 长度
    dd = (pj.get("displayDescription") or {}).get("zh", "")
    if dd:
        n = len(dd)
        if not (40 <= n <= 50):
            f.e(f"[M6] displayDescription.zh 长度 {n} ∉ [40,50]")
    else:
        f.e("[M6] 缺 displayDescription.zh")

    # M7 skills
    for s in pj.get("skills", []):
        sp = PKG_ROOT / s
        if not (sp / "SKILL.md").is_file():
            f.e(f"[M7] skill 缺 SKILL.md：{s}")

    # M8 profession == displayName
    dn = (pj.get("displayName") or {}).get("zh", "")
    pf = (pj.get("profession") or {}).get("zh", "")
    if dn and pf and dn != pf:
        f.e(f"[M8] Team 型 profession.zh({pf!r}) != displayName.zh({dn!r})")

    # M9 settings.json
    sp = PKG_ROOT / "settings.json"
    if not sp.is_file():
        f.e("[M9] 缺 settings.json（Team 型必需）")
    else:
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
            if st.get("agent") != agent_name:
                f.e(f"[M9] settings.json agent({st.get('agent')!r}) != plugin.json agentName({agent_name!r})")
        except json.JSONDecodeError as e:
            f.e(f"[M9] settings.json 非法 JSON：{e}")

    # M10 禁止项
    for forb in ("hooks", "commands"):
        if (PKG_ROOT / forb).exists():
            f.e(f"[M10] 禁止目录存在：{forb}/")
    if (PKG_ROOT / ".lsp.json").exists():
        f.e("[M10] 禁止文件存在：.lsp.json")
    if not (PKG_ROOT / ".codebuddy-plugin" / "plugin.json").is_file():
        f.e("[M10] 缺 .codebuddy-plugin/plugin.json")

    # M11 P-2 红线
    for d in DOMAIN_SCAN_DIRS:
        base = PKG_ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in DOMAIN_SCAN_SUFFIX:
                continue
            if "__pycache__" in p.parts:
                continue
            if p.name == Path(__file__).name:
                continue  # 词表定义者自身豁免（否则门禁永远红）
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for term in DOMAIN_TERMS:
                if term in t:
                    f.e(f"[M11] 通用层含教材专用值 {term!r}：{p.relative_to(PKG_ROOT)}")
                    break


def probe_negatives() -> int:
    """负向探针：逐项构造违规 manifest，断言检查器能拦下。

    mutator 约定：可返回 cleanup 回调（在检查后复原被临时改动的文件）。
    """
    import copy

    base = load_manifest()
    cases = [
        ("M1 frontmatter name 与文件名不符", "M1", _break_agent_name),
        ("M2 agentName 无对应文件", "M2", lambda m: m.update(agentName="no-such-agent")),
        ("M3 memberAgents 含 lead", "M3", lambda m: m["teamInfo"].update(memberAgents=["smart-textbook-v2-team-lead"])),
        ("M4 members 无 role=lead", "M4", lambda m: [x.update(role="member") for x in m["members"]]),
        ("M5 tags 数量错", "M5", lambda m: m.update(tags=m["tags"][:2])),
        ("M5 quickPrompts 数量错", "M5", lambda m: m.update(quickPrompts=m["quickPrompts"][:2])),
        ("M8 profession != displayName", "M8", lambda m: m["profession"].update(zh="别的名字")),
        ("M11 通用层含教材专用值", "M11", None),  # 特殊：直接写临时文件
    ]
    passed = 0
    print("── 负向探针（包契约自检）──")
    for label, expect, mut in cases:
        m = copy.deepcopy(base)
        cleanup = None
        if label.startswith("M11"):
            probe = PKG_ROOT / "references" / "_probe_domain_term.tmp"
            probe.write_text("示例：禽流感 蛋鸡 H5N\n", encoding="utf-8")
            cleanup = lambda p=probe: p.unlink(missing_ok=True)
            # M11 扫描限定后缀，把临时文件写成 .md 以命中扫描
            probe.rename(PKG_ROOT / "references" / "_probe_domain_term.md")
            cleanup = lambda p=PKG_ROOT / "references" / "_probe_domain_term.md": p.unlink(missing_ok=True)
        elif mut is not None:
            r = mut(m)
            if callable(r):
                cleanup = r

        f = F()
        try:
            check(m, f)
            hit = any(expect in e for e in f.errors)
        finally:
            if cleanup:
                cleanup()
        if hit:
            passed += 1
            print(f"  ✓ 已拦截｜{label}")
        else:
            print(f"  ✗ 未拦截｜{label}（期望 {expect}*）")
    print()
    if passed == len(cases):
        print(f"✓ 负向探针全部命中：{passed}/{len(cases)}")
        return 0
    print(f"✗ 负向探针未全中：{passed}/{len(cases)}")
    return 1


def _break_agent_name(m: dict):
    """破坏某个 agent 的 frontmatter name，返回复原回调。"""
    for a in m.get("agents", []):
        p = PKG_ROOT / a
        if not p.is_file():
            continue
        s = p.read_text(encoding="utf-8")
        fm, _ = fm_parse(p)
        old = fm.get("name")
        if old:
            p.write_text(s.replace(f"name: {old}", f"name: {old}-broken", 1), encoding="utf-8")
            return lambda path=p, content=s: path.write_text(content, encoding="utf-8")
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="专家包契约自检")
    ap.add_argument("--probe-negatives", action="store_true")
    args = ap.parse_args()

    if args.probe_negatives:
        return probe_negatives()

    try:
        pj = load_manifest()
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return 2

    f = F()
    check(pj, f)
    for e in f.errors:
        print(f"✗ {e}")
    for w in f.warnings:
        print(f"⚠ {w}")
    if f.errors:
        print(f"\n✗ 包契约自检未通过（{len(f.errors)} error / {len(f.warnings)} warning）")
        return 1
    print(f"\n✓ 包契约自检通过（0 error / {len(f.warnings)} warning）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
