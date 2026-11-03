# logging-spec.md — 统一日志规范（12 字段 JSONL）

> **版本**：v1.0 | **日期**：2026-11-02 | **维护**：B5 批次（架构设计 §3.4 方向 4 · 稳健性）
> **性质**：本文件是**运行日志格式的唯一权威源（SSOT）**。
> **红线 P-2**：本文件为通用层规范，不含任何教材专用值，示例一律用 `{{placeholder}}` 占位。
> **机制绑定**：格式校验器 `scripts/check_log_schema.py`；降级链校验器 `scripts/check_tool_degradation.py`。

---

## 1. 背景与目标

改造前的问题（G4-3）：四份日志文件职责重叠、格式各异、无 schema 统一：

| 旧文件 | 职责 | 问题 |
|---|---|---|
| `章节/{ch##}/logs/run_log.jsonl` | 章节级事件 | 几乎为空（仅 `{"consumed": true}` 占位） |
| `{教材目录}/03-运行状态/run_log.jsonl` | 项目级事件 | **文件不存在**（目录只有 .md） |
| `references/rule-engine/36-运行日志.jsonl` | 系统级 | 18 行，格式混杂，含陈旧绝对路径 |
| `references/rule-engine/orchestration_run_log.jsonl` | 编排级 | 53 行，与上者重复 |

改造目标：**单一 schema + 双落点 + 机器可校验**。

---

## 2. 落点（双落点）

| 层级 | 路径 | 粒度 | 写什么 |
|---|---|---|---|
| 章节级 | `{教材目录}/章节/{ch##}/logs/run_log.jsonl` | 细粒度 | 章节内事件（写作、审查、图表生成、状态跃迁） |
| 项目级 | `{教材目录}/03-运行状态/run_log.jsonl` | 粗粒度 | 跨章节事件（项目启动、交付、知识沉淀） |

**规则**：
1. 章节级事件**只**写章节级文件；项目级事件**只**写项目级文件。
2. 同一事件不得在两个落点重复写（避免 §1 的"职责重叠"复发）。
3. 项目级文件在教材目录初始化时创建（`03-运行状态/` 目录已存在时）。

---

## 3. Schema：12 字段 JSONL

每行一个 JSON 对象，**必须且仅须**含以下 12 个字段（不多不少）：

| # | 字段 | 类型 | 必填 | 取值 / 说明 |
|---|---|---|---|---|
| 1 | `ts` | string | ✅ | ISO-8601 时间戳，带时区，如 `2026-11-02T15:30:00+08:00` |
| 2 | `level` | string | ✅ | `INFO` / `WARN` / `ERROR` / `DEGRADE`（大写枚举） |
| 3 | `scope` | string | ✅ | `chapter` / `project`（与落点一致） |
| 4 | `phase` | string | ✅ | 13 态之一（SSOT：`references/rule-engine/state_machine_v2.yaml`） |
| 5 | `agent` | string | ✅ | 执行者标识，如 `tb-writer` / `tb-reviewer` |
| 6 | `event` | string | ✅ | 事件名，`snake_case`，语义稳定（便于检索） |
| 7 | `task_id` | string | ✅ | 幂等键；同一次任务的多次重入须复用同一 `task_id` |
| 8 | `artifact` | string | — | 产物路径（相对仓库根）；无产物时置 `null` |
| 9 | `tool` | string | — | 调用的工具名；无工具调用时置 `null` |
| 10 | `fallback_from` | string | — | 降级链起点工具；无降级时置 `null` |
| 11 | `fallback_to` | string | — | 降级链终点工具；无降级时置 `null` |
| 12 | `detail` | string\|object | — | 补充说明；结构化信息用 JSON 对象 |

### 3.1 硬性约束

```
C1  每行必须是合法 JSON 对象（不得为数组 / 标量）
C2  必须且仅须含 12 个字段 —— 多字段、少字段均违规
C3  level 取值 ∈ {INFO, WARN, ERROR, DEGRADE}
C4  scope 取值 ∈ {chapter, project}
C5  phase 取值 ∈ 13 态（读 SSOT，不在文档复述枚举）
C6  ts 必须可解析为 ISO-8601 时间戳
C7  agent / event / task_id 必须非空字符串
C8  level=DEGRADE 时，fallback_from 与 fallback_to 必须同时非空且不相等
C9  level≠DEGRADE 时，fallback_from 与 fallback_to 必须同时为 null
```

> **C8/C9 是降级链可审计的关键**：有降级必有来路与去处；无降级不得伪造链条。

### 3.2 最小示例

```json
{"ts":"2026-11-02T15:30:00+08:00","level":"INFO","scope":"chapter","phase":"PRODUCE","agent":"tb-writer","event":"draft_section_written","task_id":"{{chapter_id}}-produce-{{batch}}","artifact":"{{chapter_dir}}/draft.md","tool":null,"fallback_from":null,"fallback_to":null,"detail":"第 {{n}} 节完成"}
```

```json
{"ts":"2026-11-02T15:31:12+08:00","level":"DEGRADE","scope":"chapter","phase":"RETRIEVE","agent":"tb-researcher","event":"search_tool_degraded","task_id":"{{chapter_id}}-retrieve-01","artifact":null,"tool":"{{primary_tool}}","fallback_from":"{{primary_tool}}","fallback_to":"{{fallback_tool}}","detail":"主检索工具不可用，按回退链降级"}
```

---

## 4. 写入时机

**必须写**（否则视为未执行）：

| 场景 | level | scope | event 示例 |
|---|---|---|---|
| 状态跃迁完成 | `INFO` | chapter | `state_transition` |
| 章节产物落盘 | `INFO` | chapter | `artifact_written` |
| 审查轮次完成 | `INFO` | chapter | `review_round_completed` |
| 工具调用失败 | `ERROR` | chapter/project | `tool_call_failed` |
| **触发降级** | `DEGRADE` | chapter/project | `{{domain}}_degraded` |
| 门禁未通过 | `WARN` | chapter | `gate_warning` |
| 项目启动 / 交付 | `INFO` | project | `project_started` / `project_delivered` |

**不要写**：调试输出、无信息量的心跳、与产物无关的临时状态。

### 4.1 DEGRADE 事件的三要素

任何降级**必须**写 `level=DEGRADE` 且同时给出 `fallback_from` 与 `fallback_to`：

```
fallback_from = 失败的工具
fallback_to   = 实际改用的工具（取自 references/rule-engine/04-工具路由.md §6 回退链）
```

降级链的合法路径见 `references/rule-engine/04-工具路由.md` §6（8 类回退链：文献检索 / 文档转换 /
DOCX导出 / Web提取 / 图表生成 / 内容审查 / Agent委派 / Skill调用）。

> **工具降级 ≠ 步骤降级**：`fallback_from/fallback_to` 描述的是**工具**替换；
> Agent 内部步骤降级（researcher 的 4 层并行 → writer 无对应路径）是 G4-2 缺口，
> 不在本规范范围内。

---

## 5. 轮转

| 触发条件 | 动作 |
|---|---|
| 文件 > 1 MiB | 保留最近 500 行，其余截断 |
| 文件 > 5000 行 | 保留最近 500 行，其余截断 |

轮转在写入前触发（先轮转再追加）。口径与 `.claude/logs/hook-errors.log` 的
CR-007 轮转一致。

---

## 6. 存量迁移口径

**不要求改写存量**。存量日志的合规性按以下口径处理：

| 存量形态 | 处理 |
|---|---|
| `{"consumed": true}` 占位行 | 视为**非日志记录**（既不报违规也不计入统计），跳过 |
| 旧格式记录（字段名不符 12 字段 schema） | 进 baseline 白名单 → 降级为 **warning** |
| 陈旧绝对路径（`/home/user/...`） | 报 warning（不阻断；H-10 清理是独立任务） |
| 新写入的记录 | **一律 error**（不符合 schema 即 exit 1） |

baseline 白名单文件：`scripts/gates/_baseline.json`（键 = 判据 id + 路径 glob）。
新增违规必红 —— baseline 只对已登记的存量路径生效。

---

## 7. 机器校验

| 校验器 | 职责 | 退出码 |
|---|---|---|
| `scripts/check_log_schema.py` | 校验日志符合 12 字段 schema | 0 通过 / 1 违规 / 2 用法错 |
| `scripts/check_tool_degradation.py` | 校验工具降级链被真正记录 | 0 通过 / 1 违规 / 2 用法错 |

两个校验器均支持 `--probe-negatives`（构造违规样例自证能拦；探针全中 = exit 0）。

---

## 8. 落地清单

| 文件 | 角色 |
|---|---|
| `references/logging-spec.md` | 本文件 —— 格式 SSOT |
| `scripts/check_log_schema.py` | schema 一致性检测 |
| `scripts/check_tool_degradation.py` | 降级完整性检测 |
| `scripts/gates/_baseline.json` | 存量违规白名单 |

---

*本文件为零学科占位符，适用于所有教材项目（跨项目通用）。*
