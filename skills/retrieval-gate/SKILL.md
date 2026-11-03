---
category: workflow
name: tb-retrieval-gate
description: 审核门禁 Skill — Sub-A 资料-审核-循环流程独立 Skill。调用 retrieval_gate_checker.py 双重门禁（门禁1 SPECIFY 前 + 门禁2 RETRIEVE 后），三重规则智能判断（覆盖率/冲突率/时效性）。触发：SPECIFY 完成/ RETRIEVE 完成/ 教师说"审核资料"/"资料够了吗"。
version: v1.0
date: 2026-06-26
trigger: SPECIFY 完成 / RETRIEVE 完成 / 教师说"审核资料"或"资料够了吗"
dependsOn: "retrieval_gate_checker>=v1.0, base_rules.yaml>=v1.9"
passiveLoad: true
type: skill
---

# tb-retrieval-gate — 审核门禁 Skill（TD-70）

## Overview

Sub-A 资料-审核-循环流程的独立入口。教师可主动调用审核当前资料质量。

## 执行流程

1. 读 `retrieval/index.md`（三轨元数据）
2. 运行 `retrieval_gate_checker.py evaluate_gate2`
3. 报告三重规则结果：
   - 覆盖率 → ✓/⚠️ 缺资料
   - 冲突率 → ✓/⚠️ 资料乱
   - 时效性 → ✓/⚠️ 过时
4. 若有信号 → 提供双层按钮：
   - 「🔄 继续循环」→ 自动换关键词重检
   - 「📥 手动指定」→ 打开 manual 轨

## 调用方式

```bash
# 命令行
python3 .claude/scripts/retrieval_gate_checker.py gate2 retrieval/index.json

# Skill 内部（tb-chief-editor 编排）
Skill("tb-retrieval-gate")
```

## 输出

- 门禁 2 判断报告（pass/fail + signals）
- 若 fail → 自动执行 triple_track 重检（自动换关键词 / 手动指定等待）
