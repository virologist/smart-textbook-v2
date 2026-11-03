#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_rules.py — 规则继承引擎（RIE）· 校验器（B2 · 方向 1）

按 references/rule-engine/field_registry.yaml 的字段语义校验三层规则文件。

检查项（severity=error 的违规 → exit 1）：
  [V1] 越权覆写：inherit_only / fixed 字段被章节层覆写（含 _local_overrides）
       fixed 字段被教材层覆写；state_machine 仅放行 ssot_ref 指针形态
  [V2] 循环继承：inherits_from 逐层上溯不得回到已访问节点
  [V3] 必填缺失：required=true 字段在解析结果中无值（含默认值兜底失败）
  [V4] 类型不符：值类型与注册表 type 不符（如 word_target: "34000" 字符串）
  [V5] 枚举越界：depth_level 四档 / status 13 态 / word_tolerance 格式
  [V6] derive 证据：_derived 每项必含 derived_from + evidence
  [V7] 白名单子集：章节 allowed_files 必须是教材层白名单子集
       （历史超集：非 strict 降级 warning + 豁免标记；strict 一律 error）

--strict：把 warning 级问题全部升级为 error（B5 门禁接入用）。
--probe-negatives：构造负向探针样例自检（构造 inherit_only 被覆写等 6 类样例
       → 期望逐项被拦）；验证器自身的验收模式，探针全中 exit 0。

用法：
    python3 scripts/validate_rules.py <章节路径> [--strict]
    python3 scripts/validate_rules.py --probe-negatives
    python3 scripts/validate_rules.py --all          # 全库扫描所有实例章节

退出码：0 = 通过（warning 不阻断）；1 = 存在 error 级违规；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import os
import copy
import re
import sys
import tempfile
from pathlib import Path

import yaml

# ── 路径语义（B6 双根改造）─────────────────────────────────────────────
# PKG_ROOT：本包根（规则层资产恒定在此）
# WORKSPACE_ROOT：被检工作区（教材实例所在；默认=包根，可用 STT_WORKSPACE 注入）
PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()
sys.path.insert(0, str(WORKSPACE_ROOT / "scripts"))
from resolve_rules import (  # noqa: E402
    FIELD_REGISTRY_PATH, ResolveError, build_chain, effective_inherit_type,
    is_ssot_ref, load_registry, load_yaml, resolve,
)

DEPTH_ENUM = {"vocational", "associate", "bachelor", "graduate"}
PHASE_SEQUENCE = [
    "BOOTSTRAP", "INTAKE", "CLARIFY", "SPECIFY", "RETRIEVE", "PRODUCE",
    "VERIFY", "PROOFREAD", "CONSOLIDATION", "VISUALIZE", "ASSEMBLE",
    "DELIVER", "LEARN",
]
TOLERANCE_RE = re.compile(r"^[+-][+-]?[0-9]+%$")  # 兼容 "+-10%"（B1 统一口径写法）与 "+10%" / "-10%"

# 全库扫描的实例章节 glob（两教材 + demo 插件）
ALL_CHAPTER_GLOBS = [
    "tb_*/章节/*/chapter_rules.yaml",
    "plugins/*/instances/*/chapter_rules.yaml",
]


class Findings:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


# ── V2 循环继承（build_chain 已拦截，这里转成 error 记录）──────────────────

def check_circular(chapter_path: Path, f: Findings) -> bool:
    try:
        build_chain(chapter_path)
        return True
    except ResolveError as e:
        f.error(f"[V2 循环继承] {e}")
        return False


# ── V1 越权覆写 ──────────────────────────────────────────────────────────

def check_unauthorized_override(chain: list, registry: dict, f: Findings):
    from resolve_rules import chain_layers
    docs = chain_layers(chain)
    textbook = docs.get("textbook")
    chapter = docs.get("chapter")
    if chapter is None:
        return
    local_over = chapter.data.get("_local_overrides") or {}

    for field, info in registry["fields"].items():
        eff = effective_inherit_type(field, registry, textbook)
        layers_allowed = info.get("layers", [])
        if eff == "fixed":
            # fixed + base 基因：下层任何改写都违规（ssot_ref 指针除外）
            base_data = docs["base"].data if "base" in docs else {}
            genes = base_data.get("universal_genes", {}).get("genes", {})
            is_base_gene = field in genes
            if textbook is not None and field in textbook.data:
                val = textbook.data[field]
                if is_base_gene and not is_ssot_ref(val):
                    f.error(f"[V1 越权覆写·fixed] 教材层覆写 base 锁定字段 {field}（{textbook.path.name}）")
            if field in local_over:
                f.error(f"[V1 越权覆写·fixed] _local_overrides 含锁定字段 {field}")
            elif field in chapter.data:
                val = chapter.data[field]
                if is_base_gene and not is_ssot_ref(val) and field != "status":
                    f.error(f"[V1 越权覆写·fixed] 章节层覆写 base 锁定字段 {field}（{chapter.path.name}）")
        elif eff == "inherit_only":
            if field in local_over:
                f.error(f"[V1 越权覆写·inherit_only] _local_overrides 含章节禁改字段 {field}")
            elif field in chapter.data and textbook is not None and field in textbook.data:
                ch_val, tb_val = chapter.data[field], textbook.data[field]
                if field in ("allowed_files", "DISALLOWED"):
                    check_subset(field, ch_val, tb_val, chapter.path, f)
                elif ch_val != tb_val and field != "status":
                    f.error(f"[V1 越权覆写·inherit_only] 章节层改写禁改字段 {field}（{chapter.path.name}）")


def check_subset(field: str, ch_val, tb_val, path: Path, f: Findings):
    """V7 白名单子集检查（历史超集非 strict 豁免）。"""
    if not (isinstance(ch_val, list) and isinstance(tb_val, list)):
        return
    extra = [x for x in ch_val if x not in tb_val]
    if not extra:
        return
    f.warn(f"[V7 白名单] {path.name}: 章节 {field} 超出教材层白名单 {extra[:4]}"
           f"{'...' if len(extra) > 4 else ''}（历史章节豁免；--strict 升级 error）")


# ── V3/V4/V5 解析结果校验（必填/类型/枚举）───────────────────────────────

def _type_ok(value, expect: str) -> bool:
    if expect == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expect == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expect == "string":
        return isinstance(value, str)
    if expect == "boolean":
        return isinstance(value, bool)
    if expect == "list":
        return isinstance(value, list)
    if expect == "map":
        return isinstance(value, dict)
    if expect.startswith("enum"):
        return isinstance(value, str)
    if "|" in expect:
        return any(_type_ok(value, t.strip()) for t in expect.split("|"))
    if expect.startswith("list["):
        inner = expect[5:-1]
        return isinstance(value, list) and all(_type_ok(x, inner) for x in value)
    return True  # object 等结构占位


def check_resolved(chapter_path: Path, registry: dict, f: Findings):
    try:
        result = resolve(chapter_path, registry)
    except ResolveError as e:
        f.error(f"[V3 解析失败] {e}")
        return
    resolved = result["resolved"]

    # level_presets 兜底（depth_level 缺省 → associate）
    depth = resolved.get("depth_level") or "associate"

    for field, info in registry["fields"].items():
        if field.startswith("_") or field in ("inherits_from", "inherited_from", "schema_version"):
            continue  # 元字段/容器字段不参与 resolved 必填与类型检查
        val = resolved.get(field)
        if val is None:
            if info.get("required"):
                # default_source 兜底检查：能从 level_presets 折算的算已兜底
                ds = info.get("default_source") or ""
                if "level_presets" in ds or "默认" in ds or "backward" in ds:
                    continue
                f.error(f"[V3 必填缺失] {field}（required=true，解析结果无值且无默认兜底）")
            continue
        expect = info.get("type", "string")
        if expect == "enum":
            continue
        # 类型判定一律以注册表 type 声明为准（不再按字段名开特例）：
        #   · 声明 "map" 的字段（如 knowledge_scope）实得 list → V4 error
        #   · 确需兼容多形态的字段，在注册表里显式声明 "map | list"（如 species_focus）
        # B2 修复：原实现对 knowledge_scope 无条件放行 dict|list，导致「注册 type=map
        # 而实得 list」的类型违规被静默吞掉（V4 形同虚设），动检实例即由此漏检。
        # word_tolerance 字符串化豁免：历史 float 由折算逻辑处理（此处已是字符串则过）
        if not _type_ok(val, expect):
            # 基础基因在无教材层实例（demo 直达链）缺失属正常——只报有效层内的
            f.error(f"[V4 类型不符] {field}: 期望 {expect}，实得 {type(val).__name__} = {str(val)[:40]!r}")

    # V5 枚举
    if depth not in DEPTH_ENUM:
        f.error(f"[V5 枚举越界] depth_level: {depth!r} ∉ 四档 {sorted(DEPTH_ENUM)}")
    st = resolved.get("status")
    if st is not None and st not in PHASE_SEQUENCE:
        f.error(f"[V5 枚举越界] status: {st!r} ∉ 13 态（SSOT=references/rule-engine/state_machine_v2.yaml）")
    wt = resolved.get("word_tolerance")
    if wt is not None and not TOLERANCE_RE.match(str(wt)):
        f.error(f"[V5 格式错误] word_tolerance: {wt!r} 不匹配 ^[+-][0-9]+%$（B1 类型统一口径）")


# ── V6 derive 证据 ────────────────────────────────────────────────────────

def check_derived(chain: list, f: Findings):
    for doc in chain:
        dv = doc.data.get("_derived")
        if dv is None:
            continue
        if not isinstance(dv, dict):
            f.error(f"[V6 derive] {doc.path.name}: _derived 必须是映射")
            continue
        for key, item in dv.items():
            if not isinstance(item, dict):
                f.error(f"[V6 derive] {doc.path.name}: _derived.{key} 必须是映射")
                continue
            if not item.get("derived_from"):
                f.error(f"[V6 derive] {doc.path.name}: _derived.{key} 缺 derived_from（检索记录 id）")
            if not item.get("evidence"):
                f.error(f"[V6 derive] {doc.path.name}: _derived.{key} 缺 evidence（证据链接——衍生态硬性要求）")


# ── 单章校验入口 ─────────────────────────────────────────────────────────

def validate_chapter(chapter_path: Path, registry: dict, strict: bool) -> Findings:
    f = Findings()
    if not check_circular(chapter_path, f):
        return f
    try:
        chain = build_chain(chapter_path)
    except ResolveError as e:
        f.error(f"[V2 继承链] {e}")
        return f
    check_unauthorized_override(chain, registry, f)
    check_resolved(chapter_path, registry, f)
    check_derived(chain, f)
    if strict:
        f.errors.extend(f.warnings)
        f.warnings = []
    return f


# ── 负向探针（验收硬指标：探针全被拦截 → exit 0）────────────────────────

PROBE_CASES = [
    # (name, 改写函数, 期望命中的检查 tag)
    ("inherit_only 字段被章节 _local_overrides 覆写（structure）",
     lambda ch: ch["data"].setdefault("_local_overrides", {}).__setitem__("structure", ["篡改的结构"]), "V1"),
    ("fixed 字段被章节覆写（agent_team）",
     lambda ch: ch["data"].__setitem__("agent_team", ["tb-fake-role"]), "V1"),
    ("fixed 字段被教材层覆写（sat_protocol）",
     lambda tb: tb["data"].__setitem__("sat_protocol", {"confidence_threshold": 1}), "V1"),
    ("depth_level 非法枚举（expert）",
     lambda ch: ch["data"].__setitem__("depth_level", "expert"), "V5"),
    ("word_target 类型不符（字符串 '34000'）",
     lambda ch: (ch["data"].__setitem__("word_target", "34000"),
                 ch["data"].setdefault("_local_overrides", {}).__setitem__("word_target", "34000")), "V4"),
    ("_derived 缺证据链接",
     lambda ch: ch["data"].__setitem__("_derived", {"min_figures": {"value": 99}}), "V6"),
    ("word_tolerance 数字型（0.10，B1 前历史写法）",
     lambda ch: ch["data"].__setitem__("word_tolerance", 0.10), "V5"),
]


def probe_negatives() -> int:
    """构造临时探针样例 → 逐项验证被拦截。全中 = exit 0（验收）。

    注意：探针在临时目录构造假章节链（零真实文件改动），用假 textbook/base
    复刻真实三层结构。
    """
    registry = load_registry()
    base = load_yaml(PKG_ROOT / "references" / "rule-engine" / "base_rules.yaml")

    # 教材层与章节层样例：**动态发现**任一实例（不硬编码教材名——P-2 红线）。
    # 优先取工作区真实实例（贴近真实形态）；无实例时用包内合成的最小样例。
    tb = ch = None
    for g in ("tb_*/00-教材规则/textbook_rules.yaml",):
        for p in sorted(WORKSPACE_ROOT.glob(g)):
            tb = load_yaml(p)
            break
        if tb is not None:
            break
    if tb is None:
        tb = {"schema_version": "v1.0", "depth_level": "associate",
              "word_target": 20000, "structure": ["学习目标"],
              "knowledge_scope": {"core": ["占位"]}}
    for g in ("tb_*/章节/*/chapter_rules.yaml",):
        for p in sorted(WORKSPACE_ROOT.glob(g)):
            ch = load_yaml(p)
            break
        if ch is not None:
            break
    if ch is None:
        ch = {"schema_version": "v5.0", "chapter_id": "ch_probe",
              "word_target": 1800, "depth_level": "associate",
              "status": "BOOTSTRAP", "knowledge_scope": {"core": ["占位"]}}

    hit, miss = 0, []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # 复刻三层：root/00-系统规则/base_rules.yaml → root/00-教材规则/... → root/章节/ch_x/...
        (tdp / "00-系统规则").mkdir()
        (tdp / "00-教材规则").mkdir()
        (tdp / "章节" / "ch_x").mkdir(parents=True)
        base_file = tdp / "00-系统规则" / "base_rules.yaml"
        base_file.write_text(yaml.dump(base, allow_unicode=True), encoding="utf-8")
        tb_file = tdp / "00-教材规则" / "textbook_rules.yaml"

        def write_tb(data: dict):
            tb_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")

        ch_file = tdp / "章节" / "ch_x" / "chapter_rules.yaml"

        def write_ch(data: dict):
            ch_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")

        tbm = {"data": copy.deepcopy(tb)}
        chm = {"data": copy.deepcopy(ch)}
        tbm["data"]["inherits_from"] = "../00-系统规则/base_rules.yaml"
        chm["data"]["inherits_from"] = "../../00-教材规则/textbook_rules.yaml"
        write_tb(tbm["data"])
        write_ch(chm["data"])

        # 基线：干净样例必须 exit 0（防探针环境自身坏掉）
        base_f = validate_chapter(ch_file, registry, strict=False)
        if base_f.errors:
            print("✗ 探针基线失败（干净样例不应有 error）:")
            for e in base_f.errors:
                print(f"    {e}")
            return 1

        for name, mutate, expect_tag in PROBE_CASES:
            tbm2, chm2 = copy.deepcopy(tbm), copy.deepcopy(chm)
            target = chm2 if "教材" not in name and "textbook" not in name else tbm2
            # 教材层探针改教材；其余改章节
            if "教材层" in name:
                mutate(tbm2)
            else:
                mutate(chm2)
            write_tb(tbm2["data"])
            write_ch(chm2["data"])
            findings = validate_chapter(ch_file, registry, strict=False)
            got = [x for x in findings.errors if expect_tag in x]
            if got:
                hit += 1
                print(f"  ✓ 已拦截｜{name} → {got[0][:88]}")
            else:
                miss.append(name)
                detail = findings.errors[0][:88] if findings.errors else "(无任何 error——探针漏放!)"
                print(f"  ✗ 漏放｜{name} → {detail}")
        # 恢复干净样例
        write_tb(tbm["data"])
        write_ch(chm["data"])

    print()
    if miss:
        print(f"✗ 负向探针 {hit}/{len(PROBE_CASES)} 命中，漏放: {miss}")
        return 1
    print(f"✓ 负向探针全部命中：{hit}/{len(PROBE_CASES)} 类违规样例均被拦截（exit 1 语义验证通过）")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="三层规则继承校验器（RIE · validate）")
    ap.add_argument("chapter_path", nargs="?", help="章节目录或 chapter_rules.yaml 路径")
    ap.add_argument("--strict", action="store_true", help="warning 全部升级为 error")
    ap.add_argument("--all", action="store_true", help="全库扫描所有实例章节")
    ap.add_argument("--probe-negatives", action="store_true",
                    help="负向探针自检：构造违规样例验证逐项被拦（B2 验收）")
    args = ap.parse_args()

    if args.probe_negatives:
        return probe_negatives()
    if args.all:
        registry = load_registry()
        total_err = 0
        for g in ALL_CHAPTER_GLOBS:
            for ch_file in sorted(WORKSPACE_ROOT.glob(g)):
                # 挂载镜像实例识别（B1 偏离决定 #3）：plugins/ 内 inherits_from 不含
                # "../" 的实例按教材根挂载点解析，在仓库路径树内本就不可达——
                # 跳过并说明（非缺陷；运行时挂载于教材根下可正常解析）。
                if "plugins" in ch_file.parts:
                    head = load_yaml(ch_file).get("inherits_from", "")
                    if isinstance(head, str) and not head.startswith("."):
                        print(f"ℹ {ch_file.parent.relative_to(WORKSPACE_ROOT)}: 挂载镜像实例（解析基准=教材根，仓库树内跳过）")
                        continue
                    if isinstance(head, str) and not head.startswith("../"):
                        print(f"ℹ {ch_file.parent.relative_to(WORKSPACE_ROOT)}: 挂载镜像实例（解析基准=教材根，仓库树内跳过）")
                        continue
                f = validate_chapter(ch_file, registry, args.strict)
                rel = ch_file.parent.relative_to(WORKSPACE_ROOT)
                for e in f.errors:
                    print(f"✗ {rel}: {e}")
                for w in f.warnings:
                    print(f"⚠ {rel}: {w}")
                total_err += len(f.errors)
        if total_err:
            print(f"\n✗ 校验未通过：{total_err} 处 error")
            return 1
        print("✓ 全库章节校验通过（越权覆写 / 循环继承 / 必填 / 类型 / 枚举 / derive 证据 / 白名单）")
        return 0
    if not args.chapter_path:
        ap.print_usage()
        print("错误：需要章节路径，或 --all / --probe-negatives", file=sys.stderr)
        return 2

    ch = Path(args.chapter_path)
    if not ch.is_absolute():
        ch = Path.cwd() / ch
    if not ch.exists():
        print(f"错误：路径不存在 {ch}", file=sys.stderr)
        return 2

    registry = load_registry()
    f = validate_chapter(ch, registry, args.strict)
    for e in f.errors:
        print(f"✗ {e}")
    for w in f.warnings:
        print(f"⚠ {w}")
    if f.errors:
        print(f"\n✗ 校验未通过：{len(f.errors)} error / {len(f.warnings)} warning")
        return 1
    print(f"✓ 校验通过（0 error / {len(f.warnings)} warning）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
