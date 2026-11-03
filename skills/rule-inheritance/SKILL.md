---
name: rule-inheritance
description: 三层规则继承引擎（RIE）——base→教材→章节的字段级继承/变异/衍生，含 provenance 溯源与三段式差异报告。触发场景：解析或生成章节规则、审核用户定制的规则差异、校验越权覆写。
requires: [python3, pyyaml]
---

# rule-inheritance Skill

规则继承引擎（RIE，B2 批次 · 架构设计 §3.1 方向 1）。把「基础层抽象 / 教材层定制 / 章节层微调」三段式落地为**可执行、可溯源、可审核**的机械流程。

## 1. 遗传变异三态（本引擎的语义核心）

| 态 | 含义 | 落点 | 谁有权 | 溯源标记 |
|---|---|---|---|---|
| **inherit 继承** | 父层值直接落盘 | 字段无本地声明 | 任意层不得改动 | `value_source_layer` |
| **override 变异** | 用户显式设定的新值 | 教材层顶层 / 章节层 `_local_overrides` | 用户（须可审核） | `mutated_by: user` |
| **derive 衍生** | **联网检索衍生**的新规则值 | `_derived` 块 | 用户审核后方可生效 | `derived_from` + `evidence` + `user_review: REQUIRED` |

> derive 是「检索衍生」的唯一合法入口：任何由检索/模型生成而非用户直设的规则值，必须写入 `_derived` 块并附**证据链接**（检索记录 id / URL / DOI），未附证据者校验器直接拦截（V6）。

## 2. 四件套

| 文件 | 职责 | 关键接口 |
|---|---|---|
| `references/rule-engine/field_registry.yaml` | **字段语义注册表**（单一权威）：每个字段叫什么 / 属于哪层 / 怎么继承 / 默认值来源 / 怎么校验 | 只登记语义，不存数据 |
| `scripts/resolve_rules.py` | 三层解析器 + 生成器 | `resolve(chapter_path)` / `--emit` / `--selftest` |
| `scripts/validate_rules.py` | 校验器（V1–V7 七类违规） | `<章节路径>` / `--all` / `--strict` / `--probe-negatives` |
| `references/file-naming.md` | 命名规范单一化源（数字+中文概述 + 编号段规范） | 被 03/06 两处规则文件引用 |

## 3. 继承类型（field_registry 的 inherit_type）

| 类型 | 语义 | 可覆写层 |
|---|---|---|
| `override` | Bootstrap 时从 level_presets 填充，子层可改写 | 教材层 + 章节层（须经 `_local_overrides`） |
| `inherit_only` | 仅从教材层继承 | 章节层**禁止**覆写 |
| `extend` | 可扩展：list 去重合并 / map 深度合并，**不替换** | 教材层 + 章节层 |
| `fixed` | 基础层锁定 | **任何层禁止**覆写 |

## 4. 标准用法

```bash
# 解析某章节的最终规则 + provenance（每字段来自哪层哪文件哪行）
python3 scripts/resolve_rules.py <章节路径>

# 生成三段式差异报告（供用户审核：继承了什么 / 变异了什么 / 哪些待确认）
python3 scripts/resolve_rules.py <章节路径> --emit

# 全库校验（越权覆写 / 循环继承 / 必填 / 类型 / 枚举 / derive 证据 / 白名单）
python3 scripts/validate_rules.py --all

# 校验器自检：构造 7 类违规样例，期望逐项被拦
python3 scripts/validate_rules.py --probe-negatives
```

## 5. 用户审核流程（derive 态专用）

1. 编排者/检索者产出候选规则值 → 写入章节 `_derived` 块（附 `derived_from` + `evidence`）
2. 运行 `--emit` 生成差异报告 → **呈交用户审核**
3. 用户确认 → 迁入 `_local_overrides`（成为 override 态）或删除（弃用）
4. 运行 `validate_rules.py` 确认无遗留 derive 块

> 未确认的 derive 块**不参与** `resolved` 结果，但会出现在差异报告的【待用户确认】段——不静默生效、不静默丢弃。

## 6. 纪律

- **P-2 红线**：本 skill 及其配套脚本属通用层，**不得出现任何教材专用值**（教材名/疾病名/物种名/机构名）——示例一律用 `{{placeholder}}`。自扫：对本 skill 与 `field_registry.yaml` / `resolve_rules.py` / `validate_rules.py` 执行「教材专用词表」扫描应零命中（该词表口径在 B6 批次随领域中立门禁一并落地）
- **字段语义只在注册表登记**：各层规则文件保留自身数据，语义与校验口径以注册表为准
- **类型从严**：`type` 声明即约束（声明 `map` 的字段实得 list → V4 拦截；确需多形态须在注册表显式写 `map | list`）
- **不改教材专用数据**：本引擎只管继承语义，教材内容正确性由各教材实例自持

## 7. 与其他门禁的关系

| 门禁 | 关系 |
|---|---|
| `scripts/test_contract_sync.py`（B1） | 契约层：13 态 SSOT 唯一 / 目录名 / 深度枚举四档；RIE 是其上层扩展 |
| `scripts/validate_rules.py`（B2） | 规则层：继承语义违规（V1–V7），`--strict` 供 B5 门禁接入 |
| 领域中立门禁（B6 落地） | 领域中立层：通用层零教材专用值，与本 skill 的 P-2 红线同源 |
