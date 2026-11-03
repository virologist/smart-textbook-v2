# CHANGELOG

> 本包遵循 [Keep a Changelog](https://keepachangelog.com/) 格式。

## [3.0.0] — 2026-11-02

### 架构重构（自 content-forge v5 / tb-writer）

- **规则继承引擎（RIE）**：三层规则（基因层 → 教材层 → 章节层）的字段级继承/变异/衍生
  - `references/rule-engine/field_registry.yaml` —— 字段语义注册表（单一权威）
  - `references/rule-engine/base_rules.yaml` —— 基础层基因（零教材专用值）
  - `references/rule-engine/state_machine_v2.yaml` —— 相位枚举唯一权威
  - `scripts/resolve_rules.py` —— 三层解析器 + provenance 溯源 + 三段式差异报告（`--emit`）
  - `scripts/validate_rules.py` —— 校验器 V1–V7（越权覆写/循环继承/必填/类型/枚举/derive 证据/白名单）
  - `skills/rule-inheritance/` —— RIE 用法与三态语义
- **缺口量化闭环**：起草期登记 → 审批筛选 3-10 → 检索收纳索引 → 回填证据锚 → 逐条核验
  - `references/gap-types.md` —— 七类 gap_type 枚举 SSOT + 状态机
  - `scripts/check_gap_flow.py` —— G1–G5 卡点检测
  - `skills/gap-driven-loop/` —— 缺口闭环协议
- **图表流水线**：判型 → ipynb 溯源 → 美学自检 ≤5 → 统一编号图注 → 登记对账
  - `references/figure-registry.md` —— 登记表规范（八列）+ 摸排/重命名机制
  - `scripts/check_figures.py` —— F1–F7 + `--reconcile` + `--renumber`（dry-run）
- **门禁可执行化**：32 条守卫从「只在 hook 里、从不执行」迁为确定性 CLI + CI
  - `scripts/gates/g1_state_phase.py` · `g2_quality_citation.py` · `g3_figure_naming.py` · `g4_rules_files.py` · `g5_flow_health.py`
  - `scripts/gates/_common.py` —— 退出码语义（0 通过 / 1 违规 / 2 用法错）+ Finding 模型 + 探针框架
  - `scripts/gates/_baseline.json` —— 存量白名单（sha1 精确哈希，保证新增违规必红）
- **统一日志**：12 字段 JSONL schema，章节级 + 项目级双落点，DEGRADE 必记 `fallback_from`/`fallback_to`
  - `references/logging-spec.md` —— 日志 schema SSOT
  - `scripts/check_log_schema.py` · `scripts/check_tool_degradation.py`
- **命名规范单一化**：`references/file-naming.md`（数字+中文概述 + 编号段规范）
- **包契约自检**：`scripts/check_manifest.py` —— M1–M12（文件名/agentName/teamInfo/members/tags/quickPrompts/描述长度/skills/profession/settings/禁止项/P-2 红线/frontmatter）

### 团队

- 6 名成员：主理人（教材总编辑）+ 教学设计架构师 + 文献检索专员 + 学科内容撰写师 + 质量控制审查官 + 可视化教学设计师
- 7 个技能：rule-inheritance · gap-driven-loop · citation-verify · retrieval-gate · wiki-query · knowledge-deposit · vault-search

### 不变量

- **基因层零教材专用值**：包内 `references/` `skills/` `scripts/` 不得出现任何学科/教材/物种/机构名（`check_manifest.py` M11 机械拦截）
- **相位枚举唯一权威**：其余文档只引用不复述
- **每条声明都有实现**：`check_declaration` 式反查纪律贯穿

---

## [2.5.0] — 上游 content-forge v5

见 `smart-textbook-team` v2.5.0 与 tb-writer 仓历史记录。
