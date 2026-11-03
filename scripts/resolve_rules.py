#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resolve_rules.py — 三层规则继承引擎（RIE）· 解析器 + 生成器（B2 · 方向 1）

架构设计 §3.1：把 base_rules.yaml 声明的继承语义（v1.4）落成可执行解析器。

三种模式：
  默认解析    python3 scripts/resolve_rules.py <章节路径>
              输入章节目录（或 chapter_rules.yaml 路径）→ 沿 inherits_from 上溯
              base → textbook → chapter，按 field_registry.yaml 合并 →
              stdout 输出 resolved 规则（--json / --yaml）+ provenance 溯源。
  --emit      python3 scripts/resolve_rules.py <章节路径> --emit
              生成器：产出三段式差异报告（继承了什么 / 变异了什么 /
              需用户确认什么[derive 待审]）→ stdout。
  --selftest  python3 scripts/resolve_rules.py --selftest
              验收模式：对两教材实例（+ demo 最小实例）真实解析，
              幂等断言 resolve(resolve(x)) == resolve(x)。
              exit 0 = 全部通过（B2 验收硬指标）。

字段语义 SSOT = references/rule-engine/field_registry.yaml（本脚本只实现其语义）。
状态机枚举 SSOT = references/rule-engine/state_machine_v2.yaml（B0-Q1，本脚本不校验）。

退出码：0 = 成功；2 = 用法/输入错误；3 = 解析失败（断链/坏 YAML/循环继承）。
"""

from __future__ import annotations

import argparse
import os
import copy
import json
import re
import sys
from pathlib import Path

import yaml

# ── 路径语义（B6 双根改造）─────────────────────────────────────────────
# PKG_ROOT：本包根（规则层资产恒定在此）
# WORKSPACE_ROOT：被检工作区（教材实例所在；默认=包根，可用 STT_WORKSPACE 注入）
PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("STT_WORKSPACE", PKG_ROOT)).resolve()
FIELD_REGISTRY_PATH = PKG_ROOT / "references" / "rule-engine" / "field_registry.yaml"
STATE_MACHINE_SSOT = "references/rule-engine/state_machine_v2.yaml"

# ── 领域值红线（P-2）：selftest 与 emit 报告文案只允许用占位符 ──────────────
PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")

META_KEYS = {"schema_version", "inherits_from", "inherited_from", "comment"}

class ResolveError(Exception):
    """解析失败：断链 / 坏 YAML / 循环继承 / 字段注册表问题。"""


# ══════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════

def load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ResolveError(f"文件不存在: {path}") from None
    except yaml.YAMLError as e:
        raise ResolveError(f"YAML 解析失败: {path}: {e}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ResolveError(f"YAML 顶层必须是映射: {path}")
    return data


def load_registry() -> dict:
    reg = load_yaml(FIELD_REGISTRY_PATH)
    if "fields" not in reg or "inheritance_semantics" not in reg:
        raise ResolveError(f"字段注册表不完整: {FIELD_REGISTRY_PATH}")
    return reg


def find_key_line(path: Path, key: str) -> int:
    """定位 YAML 中 key 所在行号（provenance 用；找不到记 0）。支持缩进形态。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    pattern = rf"^[ \t]*{re.escape(key.strip())}:" if key != key.strip() or True else key
    m = re.search(rf"^[ \t]*{re.escape(key.strip())}:", text, re.M)
    if not m:
        return 0
    return text[: m.start()].count("\n") + 1


def resolve_relative(base_file: Path, rel: str) -> Path:
    """inherits_from 相对路径解析（相对本文件所在目录），拒绝目录逃逸出仓。"""
    p = (base_file.parent / rel).resolve()
    if not p.exists():
        raise ResolveError(f"继承链断裂: {base_file} 的 inherits_from → {rel} 不可达")
    return p


# ══════════════════════════════════════════════════════════════════════
# 合并原语（override / inherit_only / extend / fixed）
# ══════════════════════════════════════════════════════════════════════

def merge_extend(parent_val, child_val):
    """extend 合并：list 去重追加；map 深度合并；标量取子值。"""
    if isinstance(parent_val, list) and isinstance(child_val, list):
        out = list(parent_val)
        for item in child_val:
            if item not in out:
                out.append(item)
        return out
    if isinstance(parent_val, dict) and isinstance(child_val, dict):
        out = dict(parent_val)
        for k, v in child_val.items():
            if k in out and isinstance(out[k], (dict, list)) and isinstance(v, type(out[k])):
                out[k] = merge_extend(out[k], v)
            else:
                out[k] = v
        return out
    return child_val


def is_ssot_ref(value) -> bool:
    """state_machine 的合法指针形态：{ssot_ref: references/rule-engine/state_machine_v2.yaml}。"""
    return (
        isinstance(value, dict)
        and set(value) == {"ssot_ref"}
        and str(value.get("ssot_ref", "")).endswith("state_machine_v2.yaml")
    )


# ══════════════════════════════════════════════════════════════════════
# 继承链构建
# ══════════════════════════════════════════════════════════════════════

class LayerDoc:
    """一层规则文件：路径 + 数据 + 层名 + 对继承语义生效的 field_types 就严声明。"""

    def __init__(self, path: Path, data: dict, layer: str):
        self.path = path
        self.data = data
        self.layer = layer  # base | textbook | chapter

    def strict_field_types(self) -> dict:
        return self.data.get("field_types") or {}


def _is_base_doc(data: dict, path: Path) -> bool:
    """base_rules.yaml 根基因识别：含 universal_genes 段或文件名即 base_rules.yaml。"""
    return "universal_genes" in data or path.name == "base_rules.yaml"


def build_chain(chapter_path: Path) -> list[LayerDoc]:
    """章节 → (教材) → base 继承链；支持无教材层的直达链（demo 最小实例形态）。
    循环继承在此拦截。返回链序：base → textbook → chapter。"""
    if chapter_path.is_dir():
        cr = chapter_path / "chapter_rules.yaml"
    else:
        cr = chapter_path
    if not cr.exists():
        raise ResolveError(f"章节规则文件不存在: {cr}")

    chain: list[LayerDoc] = []
    visited: set[Path] = set()
    cur = cr.resolve()
    while True:
        if cur in visited:
            raise ResolveError(f"循环继承: {cur}")
        visited.add(cur)
        data = load_yaml(cur)
        if _is_base_doc(data, cur) or not chain:
            layer = "base" if _is_base_doc(data, cur) else "chapter"
        else:
            layer = "textbook"
        chain.append(LayerDoc(cur, data, layer))
        if layer == "base":
            break  # 根基因（extends_ref: null）不再上溯
        rel = data.get("inherits_from")
        if not rel:
            raise ResolveError(f"{cur.name} ({layer}) 缺 inherits_from 且非根基因文件")
        cur = resolve_relative(cur, str(rel))
    chain.reverse()  # base → textbook → chapter
    return chain


def chain_layers(chain: list[LayerDoc]) -> dict[str, LayerDoc]:
    return {d.layer: d for d in chain}


# ══════════════════════════════════════════════════════════════════════
# 有效继承类型（field_registry 语义 + 教材层就严声明）
# ══════════════════════════════════════════════════════════════════════

_SEVERITY = {"override": 0, "extend": 1, "inherit_only": 2, "fixed": 3}

def effective_inherit_type(field_name: str, registry: dict, textbook: LayerDoc | None) -> str | None:
    """注册表语义 + 教材 field_types 就严声明 → 生效类型。

    就严规则（registry#inheritance_semantics.note）：
      - 仅 override → inherit_only 方向生效（教材层收紧可变参数）；
      - fixed / extend 的语义由注册表（=base_rules v1.4 语义基准）锁定，
        教材层不得放宽 fixed、也不得把 extend 收为 inherit_only
        （后者与 v1.4『extend=追加合并』语义及既有实例冲突）。
    """
    info = registry["fields"].get(field_name)
    if info is None:
        return None
    eff = info.get("inherit_type")
    if textbook is not None and eff == "override":
        declared = textbook.strict_field_types().get(field_name)
        if declared == "inherit_only":
            eff = "inherit_only"
    return eff


# ══════════════════════════════════════════════════════════════════════
# 解析主流程
# ══════════════════════════════════════════════════════════════════════

def resolve(chapter_path: Path, registry: dict | None = None) -> dict:
    """解析 → {'resolved': {...}, 'provenance': {...}, 'chain': [...]}。

    幂等性：resolve 不写盘、不依赖随机/时间；对同一输入重复解析结果恒等
    （selftest 以 json.dumps(resolve(ch)) == json.dumps(resolve(resolve 输入)) 验证）。
    """
    if registry is None:
        registry = load_registry()
    chain = build_chain(chapter_path)
    docs = chain_layers(chain)
    textbook = docs.get("textbook")
    base, chapter = docs["base"], docs["chapter"]

    resolved: dict = {}
    provenance: dict = {}

    def flat(doc: LayerDoc) -> dict:
        return {k: v for k, v in doc.data.items() if k not in META_KEYS}

    # 章节层专有键（值仅存在于章节文件，无跨层合并语义）
    CHAPTER_LOCAL_KEYS = {
        "chapter_id", "chapter_title", "chapter_number", "status",
        "last_status_change", "status_source", "bootstrap", "gates",
        "lessonslearned", "word_achieved", "status_note",
        "handoff_v3_1_fields", "species_focus", "waterfowl",
        "other_economic", "trigger_patterns", "authority_sources",
        "socratic_gate", "chapter_type", "textbook_id", "display_name",
        "subtitle", "institution", "created", "l2_rebuild_date",
        "l2_rebuild_note", "review_routing", "directory_conventions",
        "DISALLOWED", "special_requirements",
    }

    def record(field: str, value, layer: str, path: Path, mutation: str | None):
        resolved[field] = value
        prov = {
            "value_source_layer": layer,
            "source_file": str(path.relative_to(WORKSPACE_ROOT)) if path.is_relative_to(WORKSPACE_ROOT) else str(path),
            "source_line": find_key_line(path, field),
        }
        if prov["source_line"] == 0 and layer == "base":
            # universal_genes 嵌套基因：定位 genes.<field> 行
            prov["source_line"] = find_key_line(path, f"    {field}:")
        if mutation == "override":
            prov["mutated_by"] = "user"
        provenance[field] = prov

    # 1) base 层基因：universal_genes 展开 + base 顶层段（review_routing 等）
    base_data = flat(base)
    genes = base_data.get("universal_genes", {}).get("genes", {})
    for gname, gdef in genes.items():
        record(gname, copy.deepcopy(gdef.get("value")), "base", base.path, None)

    # 2) 教材层 + 章节层逐字段跨层合并
    #    字段全集 = 教材层键 ∪ 章节层键（排除元/容器/声明键），每字段按生效继承类型合并
    _SKIP_KEYS = {"_local_overrides", "_derived", "field_types"}
    tb_keys = [k for k in flat(textbook) if k not in _SKIP_KEYS] if textbook else []
    ch_keys = [k for k in flat(chapter) if k not in _SKIP_KEYS] if chapter else []
    all_fields = list(dict.fromkeys(tb_keys + ch_keys))  # 保序去重

    local_overrides = (chapter.data.get("_local_overrides") or {}) if chapter else {}

    for field in all_fields:
        eff = effective_inherit_type(field, registry, textbook)
        tb_val = flat(textbook).get(field) if textbook else None
        ch_raw = flat(chapter).get(field) if chapter else None
        # 章节层有效值：_local_overrides 优先（B1 H-01：双写同值去重）
        ch_val = local_overrides.get(field, ch_raw)
        has_tb = textbook is not None and field in flat(textbook)
        has_ch = chapter is not None and (field in flat(chapter) or field in local_overrides)

        # 2a) 章节专有键：直接记录（无父层）
        if field in CHAPTER_LOCAL_KEYS and not has_tb:
            if has_ch:
                record(field, copy.deepcopy(ch_val), "chapter", chapter.path, None)
            continue

        # 2b) 未登记字段：透传记录（最后一层非空值），不套继承语义
        if eff is None:
            if has_ch and ch_val is not None:
                record(field, copy.deepcopy(ch_val), "chapter", chapter.path, None)
            elif has_tb and tb_val is not None:
                record(field, copy.deepcopy(tb_val), "textbook", textbook.path, None)
            continue

        # 2c) fixed：任何层不可覆写；章节 ssot_ref 指针形态视为引用
        if eff == "fixed":
            if field in resolved:  # base 基因已记录
                if has_ch and ch_val is not None and not is_ssot_ref(ch_val):
                    continue  # 越权覆写：保留 base 值（validate_rules 负责报错）
                continue
            if has_tb and tb_val is not None and not is_ssot_ref(tb_val):
                # 教材层覆写 base 无此基因的字段：保留教材值但标注（validate_rules 报错）
                record(field, copy.deepcopy(tb_val), "textbook", textbook.path, None)
            elif has_tb and is_ssot_ref(tb_val):
                record(field, copy.deepcopy(tb_val), "textbook", textbook.path, None)
            elif has_ch:
                if is_ssot_ref(ch_val):
                    record(field, copy.deepcopy(ch_val), "chapter", chapter.path, None)
                elif field == "status" and isinstance(ch_val, str):
                    record(field, ch_val, "chapter", chapter.path, None)  # 章节运行状态字段
            continue

        # 2d) inherit_only：章节禁改——取教材层值（无教材层值时兜底章节值并记警告性 provenance）
        if eff == "inherit_only":
            if has_tb and tb_val is not None:
                record(field, copy.deepcopy(tb_val), "textbook", textbook.path, None)
            elif has_ch and ch_val is not None:
                record(field, copy.deepcopy(ch_val), "chapter", chapter.path, None)
            continue

        # 2e) extend：教材层定基 → 章节层合并（list 去重 / map 深合并）
        if eff == "extend":
            if has_tb and has_ch:
                merged = merge_extend(copy.deepcopy(tb_val), ch_val)
                mutation = None if merged == tb_val else "override"
                record(field, merged, "chapter+textbook", chapter.path, mutation)
            elif has_tb:
                record(field, copy.deepcopy(tb_val), "textbook", textbook.path, None)
            elif has_ch:
                record(field, copy.deepcopy(ch_val), "chapter", chapter.path, None)
            continue

        # 2f) override：教材层定基 → 章节覆盖（值变即变异 mutated_by: user）
        if has_tb and has_ch:
            mutation = None if ch_val == tb_val else "override"
            record(field, copy.deepcopy(ch_val), "chapter", chapter.path, mutation)
        elif has_tb:
            record(field, copy.deepcopy(tb_val), "textbook", textbook.path, None)
        elif has_ch:
            record(field, copy.deepcopy(ch_val), "chapter", chapter.path, None)

    # 3) derive 块（衍生待审）：单独附 provenance（不并入 resolved）
    derived_block = {}
    for doc in (textbook, chapter):
        if doc is not None and isinstance(doc.data.get("_derived"), dict):
            for k, v in doc.data["_derived"].items():
                derived_block[k] = v
    for k, dv in derived_block.items():
        if isinstance(dv, dict):
            provenance[k] = {
                "value_source_layer": "derived",
                "derived_from": dv.get("derived_from", "<missing>"),
                "evidence": dv.get("evidence", "<missing>"),
                "user_review": "REQUIRED",
            }

    return {
        "resolved": resolved,
        "provenance": provenance,
        "chain": [str(d.path.relative_to(WORKSPACE_ROOT)) if d.path.is_relative_to(WORKSPACE_ROOT) else str(d.path) for d in chain],
    }


# ══════════════════════════════════════════════════════════════════════
# --emit：三段式差异报告
# ══════════════════════════════════════════════════════════════════════

def emit_report(result: dict) -> str:
    resolved, prov = result["resolved"], result["provenance"]
    inherited, mutated, derived = [], [], []
    for field, value in resolved.items():
        p = prov.get(field, {})
        if p.get("value_source_layer") == "derived" or p.get("user_review") == "REQUIRED":
            derived.append((field, p))
        elif p.get("mutated_by") == "user":
            mutated.append((field, value, p))
        else:
            inherited.append((field, value, p))

    lines = [
        "# 规则继承差异报告（--emit）",
        f"继承链: {' → '.join(result['chain'])}",
        "",
        "## 一、继承了什么（inherit · 无需确认）",
    ]
    for field, value, p in inherited:
        lines.append(f"- {field}: {_fmt_val(value)}  ← {p.get('value_source_layer')} @ {p.get('source_file')}:{p.get('source_line')}")
    lines += ["", "## 二、变异了什么（override · 用户设定，请复核）"]
    if not mutated:
        lines.append("- （无变异：全部字段按父层继承）")
    for field, value, p in mutated:
        lines.append(f"- {field}: {_fmt_val(value)}  ← {p.get('value_source_layer')} @ {p.get('source_file')}:{p.get('source_line')} [mutated_by: user]")
    lines += ["", "## 三、需用户确认什么（derive · 衍生待审，证据随行）"]
    if not derived:
        lines.append("- （无衍生规则）")
    for field, p in derived:
        lines.append(
            f"- {field}: derived_from={p.get('derived_from')} | evidence={p.get('evidence')}"
            " ⚠ 待用户确认后方可并入 resolved"
        )
    return "\n".join(lines) + "\n"


def _fmt_val(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 72 else s[:69] + "..."


# ══════════════════════════════════════════════════════════════════════
# --selftest：实例真实解析 + 幂等断言（B2 验收硬指标）
# ══════════════════════════════════════════════════════════════════════

# selftest 覆盖的实例章节——**动态发现**，不硬编码教材名（P-2 通用层零教材专用值）
# 覆盖范围：教材实例（tb_*/章节/*/）+ 领域插件实例（plugins/*/instances/*/）
SELFTEST_GLOBS = [
    "tb_*/章节/*/chapter_rules.yaml",
    "plugins/*/instances/*/chapter_rules.yaml",
]


def discover_selftest_chapters() -> list[Path]:
    """扫描全部实例章节（去重、按路径排序）。通用层不得硬编码任何教材名。"""
    found: set[Path] = set()
    for pattern in SELFTEST_GLOBS:
        for p in WORKSPACE_ROOT.glob(pattern):
            if p.is_file():
                found.add(p.resolve())
    return sorted(found, key=lambda p: str(p))


def selftest() -> int:
    failures: list[str] = []
    registry = load_registry()
    passed = 0

    chapters = discover_selftest_chapters()
    if not chapters:
        print("ℹ 未发现教材实例章节（包内无教材实例，属正常）")
        print("  · 校验真实工作区：export STT_WORKSPACE=<工作区根> 后重跑本命令")
        print("  · 实例章节位于 <工作区>/tb_*/章节/*/chapter_rules.yaml")
        return 0

    for ch in chapters:
        rel = str(ch.relative_to(WORKSPACE_ROOT)) if ch.is_relative_to(WORKSPACE_ROOT) else str(ch)
        label = rel
        try:
            r1 = resolve(ch, registry)
        except ResolveError as e:
            failures.append(f"[{label}] 解析失败: {e}")
            continue

        # 幂等断言
        tmp = copy.deepcopy(r1["resolved"])
        r2 = resolve(ch, registry)
        if json.dumps(r1["resolved"], sort_keys=True, ensure_ascii=False) != \
           json.dumps(r2["resolved"], sort_keys=True, ensure_ascii=False):
            failures.append(f"[{label}] 幂等性失败: resolve(resolve(x)) != resolve(x)")
            continue
        del tmp

        # 基本健全断言
        sm = r1["resolved"].get("state_machine")
        checks = {
            "word_target": lambda v: isinstance(v, int) and v > 0,
            "depth_level": lambda v: v in {"vocational", "associate", "bachelor", "graduate"},
            "knowledge_scope": lambda v: isinstance(v, dict) and len(v) > 0,
            "state_machine": lambda v: (isinstance(v, list) and len(v) == 13) or is_ssot_ref(v),
        }
        del sm
        for key, ok in checks.items():
            val = r1["resolved"].get(key)
            if val is None:
                failures.append(f"[{label}] 必填字段缺失: {key}")
            elif not ok(val):
                failures.append(f"[{label}] 字段 {key} 值不健全: {val!r}")

        # provenance 全字段覆盖断言
        missing_prov = [k for k in r1["resolved"] if k not in r1["provenance"]]
        if missing_prov:
            failures.append(f"[{label}] provenance 缺失字段: {missing_prov}")

        # word_target 章节 override 语义断言——**期望值从实例自身读取**，
        # 不硬编码任何教材名或数值（P-2 通用层零教材专用值）。
        # 语义：若实例声明了 _local_overrides.word_target，解析结果必须等于该值；
        #       否则等于实例顶层 word_target（若声明）。
        inst_doc = load_yaml(ch)
        expect_wt = None
        l_override = inst_doc.get("_local_overrides")
        if isinstance(l_override, dict) and "word_target" in l_override:
            expect_wt = l_override["word_target"]
        elif "word_target" in inst_doc:
            expect_wt = inst_doc["word_target"]
        if expect_wt is not None and "word_target" in r1["provenance"]:
            got_wt = r1["resolved"].get("word_target")
            if got_wt != expect_wt:
                failures.append(
                    f"[{label}] word_target 期望 {expect_wt}（取自实例声明），实得 {got_wt}"
                )

        if not any(f.startswith(f"[{label}]") for f in failures):
            passed += 1
            print(f"  ✓ {label}: 解析成功（{len(r1['resolved'])} 字段，链 {' → '.join(r1['chain'])}）")

    print()
    if failures:
        print(f"✗ selftest 失败（{len(failures)} 项）:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"✓ selftest 通过：{passed}/{len(chapters)} 实例真实解析成功（幂等 / 必填 / provenance / override 语义全部断言通过）")
    return 0


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="三层规则继承解析器（RIE）")
    ap.add_argument("chapter_path", nargs="?", help="章节目录或 chapter_rules.yaml 路径")
    ap.add_argument("--json", action="store_true", help="resolved 输出为 JSON（默认 YAML）")
    ap.add_argument("--emit", action="store_true", help="生成三段式差异报告（inherit/override/derive 待审）")
    ap.add_argument("--selftest", action="store_true", help="自检：两教材实例真实解析（B2 验收，exit 0）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.chapter_path:
        ap.print_usage()
        print("错误：需要章节路径，或使用 --selftest", file=sys.stderr)
        return 2

    ch = Path(args.chapter_path)
    if not ch.is_absolute():
        ch = (Path.cwd() / ch)
    try:
        result = resolve(ch)
    except ResolveError as e:
        print(f"✗ 解析失败: {e}", file=sys.stderr)
        return 3

    if args.emit:
        print(emit_report(result), end="")
        return 0

    payload = {"resolved": result["resolved"], "provenance": result["provenance"], "chain": result["chain"]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(yaml.dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
