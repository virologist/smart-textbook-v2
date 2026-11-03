---
category: knowledge
name: tb-knowledge-deposit
version: "v1.4"
date: "2026-05-22"
trigger: "tb-researcher Step R5.5 知识分流写入 / 用户说'将检索结果沉淀到Wiki' / H15螺旋链路触发"
dependsOn: "tb-researcher>=v5.7, tb-wiki-search>=v2.2"
errorProtocol: E1|E2|E3
HANDOFF_CONTRACT_version: "v1.0"
description: "将检索摘要中的高置信度知识条目沉淀到 Obsidian Wiki 页面。触发时机：tb-researcher Step R5.5 知识分流写入 / H15螺旋链路 / LEARN阶段三级蒸馏。v1.4：补全四步执行逻辑（输入验证→分类路由→Wiki写入→计数递增）。v1.3：手风琴格式表格补全。v1.2：S1全面合规（trigger/dependsOn/consumed_by/E1E2E3）。v1.1：D6示例去学科特化。v1.0：D6 TOPIC_CLUSTER命名(相似度0.75合并) + D3四维评分字段嵌入"
inputs:
  - digest_source # consumed_by: "tb-knowledge-deposit (阶段1读取摘要)"
  - target_wiki # consumed_by: "tb-knowledge-deposit (阶段2写入Wiki页)"
  - chapter_id # consumed_by: "tb-knowledge-deposit (命名输出文件)"
  - confidence_threshold # consumed_by: "tb-knowledge-deposit (过滤低置信度条目)"
outputs:
  - wiki page追加（append模式，非覆盖） # consumed_by: "tb-wiki-search (检索来源)"
  - wiki/index.md 更新（如新建页面） # consumed_by: "tb-wiki-search (检索索引)"
  - 03-运行状态/run_log.jsonl 知识沉积事件 # consumed_by: "tb-chief-editor (知识管理记录)"
  - retrieval/low_confidence_*.md（如有） # consumed_by: "tb-researcher (低置信度条目复查)"
trigger_events:
  - "retrieval_file_modified"
  - "learn_phase_enter"
  - "r3_review_complete"
passiveLoad: true
---
# tb-knowledge-deposit

## Overview

将tb-researcher输出的检索摘要（`retrieval/summaries/*.md`）中的高置信度知识条目，经四步管线写入Obsidian Wiki知识库。服务于H15 Wiki螺旋链路和LEARN阶段三级蒸馏。

## Step 1 — 输入验证

**输入源**：`retrieval/summaries/` 目录下最新摘要文件，或handoff.md中 `h15_triggered: true` 标记对应的待沉淀条目列表。

**验证项**：
- `confidence ≥ threshold`（默认0.75）：低于阈值条目写入 `retrieval/low_confidence_*.md` 供复查
- `sources_count ≥ 1`：无来源条目拒绝写入，标记 `[待核实]`
- `content_length > 50 chars`：过短条目跳过（可能为噪音）

**错误处理**：
- E1（输入文件不存在）→ 从handoff.md提取待沉淀条目，降级为手动条目模式
- E2（无高置信度条目）→ 退出，标记 `run_log.jsonl` 事件 `knowledge_deposit_skip`

## Step 2 — 分类路由

按条目 `topic_type` 字段路由到目标Wiki子目录：

| topic_type | 目标路径 | 示例 |
|-----------|---------|------|
| `clinical` | `临床知识/` | 诊断标准、治疗方案、鉴别诊断 |
| `literature` | `文献证据/` | PMID锚定的事实陈述、系统综述结论 |
| `methodology` | `方法论/` | 实验设计、统计方法、教学策略 |
| `terminology` | `术语库/` | 专业术语定义、中英文对照 |
| `guideline` | `指南规范/` | 临床指南、行业标准、法规 |

**默认路由**：无法分类的条目 → `08-知识库/通用/`，标记 `topic_type: general`。

## Step 3 — Wiki写入

**写入模式**：append（非覆盖），使用 `mcp-obsidian_append_content` 或 `mcp-obsidian_patch_content`。

**条目格式**：
```markdown
### {条目标题}
- **置信度**: {confidence} | **来源**: {source_summary}
- **摘要**: {content}
- **关联**: [[{related_page}]]
- **写入时间**: {timestamp} | **写入者**: {agent_id}
```

**Wikilinks规范**：
- 已有Wiki页面 → `[[页面名]]`（精确匹配）
- 新概念 → `[[新概念名]]`（后续补充页面）
- 跨领域关联 → 在 `index.md` 追加 `- [[页面名]] — 一句话描述`

**重复检测（三级）**：
1. **页面内去重**：检索目标Wiki页面，若已有≥80%相似度条目 → 更新而非新建（保留原始时间戳）
2. **跨页面去重**：读取 `.claude/logs/deposit_index.jsonl`（SHA256→wiki_page 映射），若同一 content hash 已存在于其他页面 → 追加 `[[已有页面]]` 双向引用，不重复写入正文
3. **同批次去重**：当前批次内，相同 topic_type+source 组合只保留置信度最高的一条

## Step 4 — 计数递增与日志

**操作**：
1. `run_log.jsonl` 追加 `knowledge_deposit` 事件（含 `count`, `topic_types`, `chapter_id`）
2. `.claude/logs/deposit_index.jsonl` 追加 `{content_sha256}|{wiki_page}|{timestamp}` 行（跨页面去重索引）
3. 如 `h15_triggered: true` → 更新handoff.md：`h15_deposited: true` + `h15_deposit_count: N`
3. 输出结构化摘要：

```json
{
  "heartbeat": {
    "step": "DEPOSIT_COMPLETE",
    "action": "知识沉淀完成",
    "deposited": N,
    "low_confidence": M,
    "wiki_pages_updated": [...],
    "timestamp": "ISO8601"
  }
}
```

## 错误处理

| 错误 | 响应 |
|------|------|
| E1 Wiki目录不存在 | 自动创建目录结构，记录到run_log |
| E2 Obsidian MCP不可用 | 降级为本地 `append_content`（直接文件写入） |
| E3 条目格式损坏 | 跳过该条目，继续处理其余 |

## Verifikation

- [ ] 所有高置信度条目已写入Wiki
- [ ] 低置信度条目已归档到 `low_confidence_*.md`
- [ ] `run_log.jsonl` 事件已记录
- [ ] handoff.md状态已更新（如适用）
