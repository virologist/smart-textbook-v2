---
name: gap-driven-loop
description: 缺口驱动的量化知识点闭环——起草期标记 [需补数据·x-gNN] → chief-editor 审批筛选 3-10 → 检索收纳+索引 → 回填证据锚 → reviewer 逐条核 → 闭环判定。触发场景：章节起草遇数据缺口、审批缺口清单、核对缺口闭环状态、终稿前残留标签清理。
requires: [python3]
---

# gap-driven-loop Skill

缺口驱动闭环（B3 批次 · 架构设计 §3.2 方向 2）。把「写作时发现缺数据 → 事后补救」的被动模式，改成**起草期登记 → 审批 → 检索 → 回填 → 核验 → 闭环**的量化流水线。

> **与既有 `[待核实]` 的关系**：既有 `[实]`/`[估]`/`[待核实·reason=…]` 是**事实级证据强度标注**，继续有效；本 skill 的 `[需补数据·x-gNN]` 是**缺口级管理标签**，两者并存不冲突（B0-Q6 裁决）。

---

## 1. 为什么要有缺口闭环

旧模式的问题：writer 起草时遇到「这个数字没有出处」，只能写 `[待核实]`，但——**没人知道本章一共缺多少、哪些先补、谁去补、补完没有**。H15 螺旋只能在审查期兜底，且是事后。

新模式：缺口在**起草期**即编号登记进 `gaps.md`，审批后进检索队列，回填后逐条核验，交付前必须归零。

---

## 2. 闭环六步

```
① 拆解        [didactic-planner]  chapter_design.md §B 数据需求 → 候选缺口
② 标记        [subject-writer]    正文写 [需补数据·{{ch##}}-g{{NN}}]，gaps.md 登记
③ 审批筛选    [team-lead]          每轮选 3-10 个 → approved / rejected / deferred
④ 检索        [literature-scout]  4 层并行 subagent + 中英文双轨 → 收纳至 retrieval/ + INDEX
⑤ 回填        [subject-writer]    标签 → [已补·x-gNN] + 正文数据 + 证据锚
⑥ 核验闭环    [quality-examiner]  R3 逐条核对 → resolved / partially_resolved / unresolved
```

---

## 3. 缺口台账（gaps.md）

章节级唯一台账，位置：`章节/{ch##}/gaps.md`。

**表头固定九列**（校验器按此解析，列序不可改）：

| gap_id | gap_type | priority | status | 描述 | 上下文 | 审批 | 检索产物 | 证据锚 |
|---|---|---|---|---|---|---|---|---|

| 列 | 取值 | 约束 |
|---|---|---|
| `gap_id` | `{ch##}-g{NN}` | 章节内唯一，两位补零 |
| `gap_type` | 七类枚举 | 见 `references/gap-types.md` §2（**枚举封闭**） |
| `priority` | P0 / P1 / P2 | 缺省按 gap_type 的审批权重 |
| `status` | 见 §4 状态机 | 非法状态直接报错 |
| `描述` | 缺口是什么 | — |
| `上下文` | 正文位置（如 §1.2） | — |
| `审批` | 审批人 + 轮次 + 结论 | 未审批留 `-` |
| `检索产物` | `retrieval/` 内文件名 | 未检索留 `-` |
| `证据锚` | PMID/DOI/URL/本地源锚 | 未回填留 `-` |

**台账是唯一清单**：正文标签只存 gap_id 引用，双向核对由 `scripts/check_gap_flow.py` 执行。

---

## 4. 状态机与合法流转

```
proposed  ──审批──▶ approved ──检索──▶ retrieved ──回填──▶ filled ──核验──▶ resolved
    │                   │                                      │
    │                   └──▶ rejected                          ├──▶ partially_resolved
    └──▶ deferred                                             └──▶ unresolved（返修重试）
```

**禁止流转**（校验器拦截）：
- `proposed → filled`：跳过审批
- `approved → resolved`：跳过检索
- `unresolved → resolved`：未经返修直接关闭

---

## 5. 审批规则（3-10）

| 情形 | 处置 |
|---|---|
| 待审总数 > 10 | 按 priority 截断至 10（P0 全留） |
| 待审总数 ∈ [3,10] | 全批 |
| 待审总数 < 3 | 扩批至相邻缺口；仍不足则**显式标注「本轮不足 3 个」**（校验器据此豁免，否则报错） |
| 已批 10 且仍有未批 | 其余标 `deferred`，**不得丢弃** |

审批结论三态：`approved` / `rejected`（附理由）/ `deferred`。

---

## 6. 检索收纳与渐进式加载

- 产物落 `章节/{ch##}/retrieval/{ch##}_{slug}.md`
- **索引** `章节/{ch##}/retrieval/INDEX.md` 是渐进式加载入口：每条目四字段（**标题 / 摘要≤200字 / 路径 / 置信度**），缺一即校验失败
- 加载顺序：先读 INDEX → 按需加载具体产物 → **不一次性读入全部检索原文**
- 置信度标注沿用既有三态：`[实]` / `[估]` / `[待核实·reason=…]`

---

## 7. 终止条件（可机检）

| 循环 | 终止条件 | 超限处置 |
|---|---|---|
| L1 缺口检索轮 | `count(status ∈ {unresolved}) == 0` | 超 3 轮 → 升级裁决（deferred 或转人工） |
| L2 章节返修轮 | `handoff.runtime_state.pending_rerun < 3` | 超限 → 人工介入 |
| L3 图美学迭代 | `iteration_count ≤ 5` | 超限 → 强制降级 D 类（B4 落地） |

---

## 8. 卡点检测（机械判据，不靠模型自觉）

`scripts/check_gap_flow.py` 实现五项：

| 检查 | 判据 | 阻塞相位 |
|---|---|---|
| **G1** 标签↔台账双向对应 | 正文 `[需补数据·x-gNN]` 必有台账条目 | 阻塞 VERIFY 出口 |
| **G2** 审批范围 | `approved` 数 ∈ [3,10]（总数≥3 时） | 阻塞 RETRIEVE |
| **G3** 状态流转合法 | 禁跳过审批/检索/返修直接闭环 | 阻塞 VERIFY 出口 |
| **G4** 检索收纳 + 索引 | INDEX 条目数 == 实际 .md 数；四字段齐全 | 阻塞 PRODUCE |
| **G5** 终稿清洁（`--final`） | 残留 `[需补数据]`/`[待核实]`/`[估]` == 0 | **阻塞 DELIVER** |

---

## 9. 用法

```bash
python3 scripts/check_gap_flow.py 章节/{ch##}            # 检查 G1-G4
python3 scripts/check_gap_flow.py 章节/{ch##} --final    # 追加 G5 终稿清洁
python3 scripts/check_gap_flow.py --all                 # 全库扫描
python3 scripts/check_gap_flow.py --probe-negatives     # 校验器负向探针自检
```

---

## 10. 纪律

- **P-2 红线**：本 skill 与 `references/gap-types.md` / `scripts/check_gap_flow.py` 属通用层，禁含教材专用值；示例一律 `{{placeholder}}`
- **枚举封闭**：gap_type 七类不得自造；新增须三处同步（gap-types.md + check_gap_flow.py + 本 skill）
- **旧标签不退役**：既有 `[待核实]`/`[估]`/`[实]` 继续有效，只是不再用于新缺口
- **台账唯一**：正文不写缺口描述细节，只写 gap_id 引用——避免双向同步成本
