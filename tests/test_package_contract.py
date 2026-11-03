"""test_package_contract.py — 专家包独立自测（pytest）

本文件补齐包内 `tests/` 空目录（遗留项登记于 P4 修复记录 §5）：
包作为**公开分发的 GitHub 仓库**，使用者拉取后应能独立验证其门禁行为，
而不必依赖主仓 tb-writer。

覆盖本批次修复的关键行为（回归防护，非重复门禁本身）：
  [1] 探针 fail-closed（B-1）：公共设施（相位 SSOT）失效时，探针必须整体
      exit 1 并报出失效原因，**不得**因错误原因而「全中」假绿。
  [2] `is_infra_error` 死机制彻底移除（B-3）：子串黑名单在原理上无法区分
      「同形异因」消息，已整体删除；本测试断言其**不再存在**，防止复活。
  [3] M11 学科中立分级词表（A-1 遗留项）：单字词按上下文排除通用组合，
      既不漏放真泄漏，也不误报通用词。
  [4] 命名门禁双端扫描基准一致（A-1）：规则层 root 必须指向**被检工作区**
      而非包根；相对路径基准统一为工作区相对路径。

运行：
    cd <包根> && python3 -m pytest tests/ -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PKG_ROOT / "scripts"


# ── 模块加载工具 ────────────────────────────────────────────────────────

def _load(rel: str, name: str):
    """按路径加载包内脚本模块（避免依赖 sys.path 注入顺序）。"""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / rel)
    assert spec and spec.loader, f"无法加载 {rel}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(rel: str, *args: str, cwd: Path | None = None,
         scripts_root: Path | None = None,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """裸跑包内脚本，返回 CompletedProcess（**不吞退出码**）。

    `scripts_root` 允许指定**脚本所在根**：包内脚本用 `PKG_ROOT = __file__.parent.parent`
    推导包根，因此若要检验「某个被复制的包副本」，必须跑**副本里的脚本**，
    只改 `cwd` 不改脚本路径 → 实际检验的仍是原包（假绿/假红均可能）。
    """
    full_env = {**os.environ, **(env or {})}
    root = scripts_root or SCRIPTS
    return subprocess.run(
        [sys.executable, str(root / rel), *args],
        cwd=str(cwd or PKG_ROOT), env=full_env,
        capture_output=True, text=True,
    )


# ── [2] is_infra_error 死机制不得复活 ──────────────────────────────────

def test_no_is_infra_error_mechanism():
    """B-3：`INFRA_ERROR_MARKERS` / `is_infra_error` 已整体删除，不得复活。"""
    offenders = []
    for py in (SCRIPTS / "gates").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "def is_infra_error" in text or "INFRA_ERROR_MARKERS = (" in text:
            offenders.append(str(py.relative_to(PKG_ROOT)))
    for py in SCRIPTS.glob("_common.py"):
        text = py.read_text(encoding="utf-8")
        if "def is_infra_error" in text or "INFRA_ERROR_MARKERS = (" in text:
            offenders.append(str(py.relative_to(PKG_ROOT)))
    assert not offenders, f"死机制已复活：{offenders}"


def test_gates_common_files_are_identical():
    """包内两份 `_common.py` 必须逐字节一致（同名副本漂移是已发生过的缺陷）。"""
    a = (SCRIPTS / "_common.py").read_bytes()
    b = (SCRIPTS / "gates" / "_common.py").read_bytes()
    assert a == b, "包内 scripts/_common.py 与 scripts/gates/_common.py 已漂移"


# ── [1] 探针 fail-closed ───────────────────────────────────────────────

def test_probe_fails_closed_when_ssot_broken(tmp_path):
    """B-1：相位 SSOT 损坏 → 探针必须 exit 1（不得假绿 exit 0）。

    复现路径：复制包体 → 破坏 `references/rule-engine/state_machine_v2.yaml`
    的 `main_path` 键 → 跑探针。健康检查应报「公共设施失效」并以 exit 1 收场。
    """
    pkg = tmp_path / "pkg"
    shutil.copytree(PKG_ROOT, pkg, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "tests"))

    ssot = pkg / "references" / "rule-engine" / "state_machine_v2.yaml"
    text = ssot.read_text(encoding="utf-8")
    assert "main_path:" in text, "SSOT 结构与预期不符，无法注入故障"
    ssot.write_text(text.replace("main_path:", "main_path_BROKEN:", 1),
                    encoding="utf-8")

    for rel in ("check_log_schema.py", "check_tool_degradation.py",
                "gates/g1_state_phase.py"):
        r = _run(rel, "--probe-negatives", cwd=pkg, scripts_root=pkg / "scripts")
        assert r.returncode == 1, (
            f"{rel} 在公共设施失效时应 exit 1，实际 {r.returncode}"
            f"（0 = 假绿）\n{r.stdout}"
        )
        assert "公共设施失效" in r.stdout or "未读到相位枚举" in r.stdout, (
            f"{rel} 未报出失效原因：\n{r.stdout}"
        )


def test_probe_passes_on_healthy_package(tmp_path):
    """对照项：包体健康时探针必须 exit 0（防止 fail-closed 过度收紧）。"""
    pkg = tmp_path / "pkg_healthy"
    shutil.copytree(PKG_ROOT, pkg, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "tests"))
    for rel in ("check_log_schema.py", "check_tool_degradation.py",
                "gates/g1_state_phase.py"):
        r = _run(rel, "--probe-negatives", cwd=pkg, scripts_root=pkg / "scripts")
        assert r.returncode == 0, f"{rel} 健康态探针应 exit 0：\n{r.stdout}"


# ── [3] M11 学科中立分级词表 ───────────────────────────────────────────

def test_m11_word_table_context_exclusion():
    """M11：单字词须按上下文排除通用组合；多字词直接命中。"""
    mod = _load("check_manifest.py", "pkg_check_manifest")
    hits = mod.find_domain_terms

    # 真泄漏 → 必须命中
    for text in ("禽流感防控", "蛋鸡养殖", "兽医专业", "家禽学科",
                 "禽群疫病防控", "禽病诊治", "猪病防控", "奶牛泌乳", "水产养殖"):
        assert hits(text), f"真泄漏未检出：{text}"

    # 通用词 → 必须不命中
    for text in ("禽类数据", "禽群管理", "禽舍温度", "禽只数量", "畜禽养殖",
                 "禽肉产量", "FAO全球疫病监测数据库"):
        assert not hits(text), f"通用词误报：{text}"


def test_m11_covers_txt_csv_ipynb_suffixes(tmp_path):
    """M11：`.txt`/`.csv`/`.ipynb` 曾被漏放（实测 exit 0），现必须拦下。"""
    pkg = tmp_path / "pkg_m11"
    shutil.copytree(PKG_ROOT, pkg, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "tests"))

    leaks = {
        "references/_t_leak.txt": "本文件讨论禽流感防控要点。\n",
        "references/_t_leak.csv": "章节,主题\nch01,禽流感\n",
        "references/_t_leak.ipynb": json.dumps(
            {"cells": [{"cell_type": "markdown",
                        "source": ["# 禽流感案例分析"]}],
             "metadata": {}, "nbformat": 4, "nbformat_minor": 5},
            ensure_ascii=False),
    }
    for rel, content in leaks.items():
        (pkg / rel).write_text(content, encoding="utf-8")

    r = _run("check_manifest.py", cwd=pkg, scripts_root=pkg / "scripts")
    assert r.returncode == 1, f"含学科专用值的未扫描后缀应被拦下：\n{r.stdout}"
    for rel in leaks:
        assert rel in r.stdout, f"{rel} 未被报出：\n{r.stdout}"


def test_m11_no_false_positive_on_real_package():
    """M11：真实包体零误报（扩扫后通用层不得因新后缀被误判）。"""
    r = _run("check_manifest.py")
    assert r.returncode == 0, f"真实包体 M11 误报：\n{r.stdout}"


# ── [4] 命名门禁双端扫描基准 ───────────────────────────────────────────

def test_naming_scan_root_points_to_workspace(tmp_path):
    """A-1：规则层扫描根必须指向被检工作区，而非包根。

    原缺陷：`roots = [PKG_ROOT / d for d in SCAN_DIRS]` 指向包根，而
    `00-系统规则` 是**教材工作区**的目录 → 包内不存在 → 该 root 恒空 →
    规则层扫描 100% 失效。
    """
    ws = tmp_path / "ws"
    (ws / "00-系统规则").mkdir(parents=True)
    (ws / "00-系统规则" / "01-状态机.md").write_text("# x\n", encoding="utf-8")
    (ws / "00-系统规则" / "02-编写标准.md").write_text("# x\n", encoding="utf-8")

    mod = _load("check_naming.py", "pkg_check_naming")
    mod.WORKSPACE_ROOT = ws.resolve()
    rels = {rel for _p, rel in mod.iter_files_rel()}
    assert "00-系统规则/01-状态机.md" in rels, (
        f"规则层文件未被扫描（实际扫到 {sorted(rels)}）"
    )


def test_naming_rel_base_is_workspace(tmp_path):
    """A-1：相对路径基准须为工作区相对路径（否则 baseline 跨端不互认）。"""
    ws = tmp_path / "ws"
    (ws / "00-系统规则").mkdir(parents=True)
    (ws / "00-系统规则" / "01-状态机.md").write_text("# x\n", encoding="utf-8")
    book = ws / "tb_demo"
    (book / "章节" / "ch_01").mkdir(parents=True)
    (book / "章节" / "ch_01" / "01-概述.md").write_text("# x\n", encoding="utf-8")

    mod = _load("check_naming.py", "pkg_check_naming")
    mod.WORKSPACE_ROOT = ws.resolve()
    rels = {rel for _p, rel in mod.iter_files_rel()}
    assert rels == {
        "00-系统规则/01-状态机.md",
        "tb_demo/章节/ch_01/01-概述.md",
    }, f"相对路径基准不符：{sorted(rels)}"


def test_naming_instance_py_is_exempt(tmp_path):
    """A-1：教材实例层的 `.py` 为工具产物，须豁免（原判据因基准错位而失效）。"""
    ws = tmp_path / "ws"
    book = ws / "tb_demo"
    (book / "章节" / "ch_01" / "assets").mkdir(parents=True)
    (book / "章节" / "ch_01" / "assets" / "gen_charts.py").write_text(
        "print(1)\n", encoding="utf-8")

    mod = _load("check_naming.py", "pkg_check_naming")
    mod.WORKSPACE_ROOT = ws.resolve()
    rels = {rel for _p, rel in mod.iter_files_rel()}
    assert not any(rel.endswith(".py") for rel in rels), (
        f"实例层 .py 未豁免：{sorted(rels)}"
    )


# ── 包契约基础门禁 ─────────────────────────────────────────────────────

def test_manifest_contract_passes():
    """M1–M12 包契约自检必须通过（0 error / 0 warning）。"""
    r = _run("check_manifest.py")
    assert r.returncode == 0, f"包契约自检未通过：\n{r.stdout}\n{r.stderr}"


def test_plugin_manifest_version_matches_changelog():
    """版本号与 CHANGELOG 首条目须一致（避免发布物与文档漂移）。"""
    manifest = json.loads(
        (PKG_ROOT / ".codebuddy-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    changelog = (PKG_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{version}]" in changelog, (
        f"CHANGELOG 缺少 [{version}] 条目（plugin.json version={version}）"
    )
