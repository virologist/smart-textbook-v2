# smart-textbook-v2 · 智能教材编写天团（专家包）

> 规则继承驱动的教材编写专家团。三层规则衍生 · 缺口量化闭环 · 图表流水线 · 32 条可执行门禁。
> 支持任意学科、专科至研究生各层级教材；教材实例与章节规则在**包外工作区**，本包自包含且学科中立。

---

## 这是什么

一个 **WorkBuddy 团队型专家包**：6 名成员 + 7 个技能 + 12 个可执行门禁脚本，把「写一章教材」变成可编排、可验证、可复盘、**可继承定制**的流水线。

**不是**：聊天机器人、提示词合集、一次性写作工具。
**是**：带门禁 / 对账 / 溯源 / 经验沉淀闭环的工程化生产系统。

---

## 三层规则架构（本包的核心创新）

```
包内 references/rule-engine/       ← ① 基因层（抽象·通用·可实例化，零学科专用值）
        │  base_rules.yaml · field_registry.yaml · state_machine_v2.yaml
        ↓ 继承 + 用户审核定制 + 联网检索衍生
工作区 <教材层>/00-教材规则/        ← ② 变异层（教材实例化，学科专用值在此）
        ↓ 继承 + 章节级覆盖（_local_overrides）
工作区 <教材层>/章节/{ch##}/        ← ③ 实例层（章节微调）
```

**遗传变异三态**：

| 态 | 含义 | 落点 | 溯源 |
|---|---|---|---|
| `inherit` 继承 | 父层值直接落盘 | 字段无本地声明 | `value_source_layer` |
| `override` 变异 | 用户显式设定 | 教材层 / 章节层 `_local_overrides` | `mutated_by: user` |
| `derive` 衍生 | **联网检索衍生**，必附证据 | `_derived` 块 | `derived_from` + `evidence` + `user_review: REQUIRED` |

**不变量**：基因层**零教材专用值**——任何学科/教材/物种/机构名都不得进入包内 `references/`、`skills/`、`scripts/`（由 `check_manifest.py` M11 机械拦截）。

---

## 团队成员

| ID | 花名 | 职责 |
|---|---|---|
| `smart-textbook-v2-team-lead` | 糜编澜 | 教材总编辑 · 编排/门禁调度/缺口审批 |
| `didactic-architect` | 陶靖衍 | 教学设计架构师 · Bloom 目标分解 + 数据需求拆分 |
| `literature-scout` | 粟海琼 | 文献检索专员 · 多层降级检索 + 产物收纳索引 |
| `subject-writer` | 祝书白 | 学科内容撰写师 · 正文起草 + 缺口标记回填 |
| `quality-examiner` | 严正词 | 质量控制审查官 · 四轮审查 + 缺口逐条核验 |
| `visual-instructional-designer` | 绘章图 | 可视化教学设计师 · 判型→ipynb→美学自检→编号图注 |

---

## 核心能力

| 能力 | 说明 | 守护机制 |
|---|---|---|
| **规则继承引擎** | 三层字段级继承/变异/衍生 + provenance 溯源 + 三段式差异报告 | `resolve_rules.py` + `validate_rules.py`（V1–V7） |
| **缺口量化闭环** | 标记→审批 3-10→检索收纳索引→回填→逐条核验 | `check_gap_flow.py`（G1–G5） |
| **图表流水线** | 判型 A/B/C/D→ipynb 溯源→美学自检 ≤5→统一编号图注→登记对账 | `check_figures.py`（F1–F7）+ 摸排/重编号 |
| **可执行门禁** | 32 条判据从「只在 hook 里、从不执行」迁为 CLI + CI | `scripts/gates/g1–g5`（35 个判据号） |
| **统一日志** | 12 字段 JSONL，章节级 + 项目级双落点，DEGRADE 必记降级链 | `check_log_schema.py` + `check_tool_degradation.py` |
| **零幻觉引用** | 每个事实声明须有可核验锚点 | `citation-verify` + `retrieval-gate` + reviewer R3 |

---

## 快速开始

```bash
# 1) 指向你的工作区（教材实例所在目录）
export STT_WORKSPACE=/path/to/your/workspace

# 2) 校验规则层
python3 scripts/resolve_rules.py --selftest          # 解析真实实例 + 幂等断言
python3 scripts/validate_rules.py --all              # 越权覆写/循环继承/类型/枚举
python3 scripts/validate_rules.py --probe-negatives  # 校验器自检

# 3) 校验缺口闭环与图表
python3 scripts/check_gap_flow.py --all
python3 scripts/check_figures.py --all
python3 scripts/check_figures.py 章节/ch01 --reconcile   # 摸排未登记图
python3 scripts/check_figures.py 章节/ch01 --renumber    # 重编号建议（dry-run）

# 4) 校验包契约
python3 scripts/check_manifest.py
```

> **双根语义**：包内 `PKG_ROOT` = 规则层资产（恒定在包内）；`WORKSPACE_ROOT` = 被检工作区（默认=包根，用 `STT_WORKSPACE` 注入）。

---

## 目录结构

```
smart-textbook-v2/
├── .codebuddy-plugin/plugin.json    # 专家包契约（validate_expert.py 校验）
├── settings.json                    # Team 型必需
├── agents/                          # 6 名成员（文件名 == agentName）
├── skills/                          # 7 个技能（rule-inheritance / gap-driven-loop / 5 knowledge）
├── references/                      # gap-types · figure-registry · file-naming · logging-spec
│   └── rule-engine/                 # 基因层：base_rules · field_registry · state_machine
├── scripts/                         # 12 个可执行门禁 + gates/ 5 组
├── avatars/                         # 成员头像
├── docs/                            # 架构与使用说明
└── tests/                           # 包内回归
```

---

## 设计文档

| 文档 | 内容 |
|---|---|
| `docs/架构设计.md` | 六层架构（H=模型+Harness）+ 范式 P + 迁移映射 |
| `docs/使用指南.md` | 工作区准备、规则层定制、门禁接入 |

---

## 许可

MIT
