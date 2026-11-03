---
category: knowledge
name: tb-wiki-search
version: "v2.2"
date: "2026-05-17"
trigger: "tb-researcher RETRIEVE阶段启动 / 用户问'我知道关于XX' / tb-reviewer VERIFY前证据定位"
dependsOn: "tb-researcher>=v5.7, tb-wiki-ingest>=v2.1"
description: >
  在 Vault 的 08-知识库/ 中做本地文本检索。适用于：
  (1) tb-researcher RETRIEVE 阶段 Wiki 优先检索（两阶段协议）
  (2) 用户问"我知道关于XX" / 写作前查知识 / 审查前定位证据
  (3) tb-reviewer VERIFY 前快速定位已沉淀知识
  激活条件：chapter 内容需要外部证据或已知信息需整合时，Wiki 优先于外部检索。
  前置条件：Vault 08-知识库/index.md 存在。
  检索策略：两阶段协议（阶段一匹配 index.md，阶段二精读 top-k）。
---
  检索关键词来源：优雅降级链（详见 L5.2），不硬编码。
  v2.2变更：S1全面合规（trigger/dependsOn/consumed_by/E1E2E3）
allowed_tools:
  - Read              # 读取 index.md 和精读命中的 wiki 页面
  - Grep              # 关键词匹配（index.md 标题/aliases/tags 字段）
  - Bash              # find 查找 wiki 页面文件
  - Glob              # 按类型/目录查找 wiki 页面
  - mcp__vault-brain__vault_search   # 语义搜索（语义维度）
  - mcp__obsidian__obsidian_simple_search  # 模糊关键词搜索（精确维度）
inputs:
  - query（检索主题关键词，来自 knowledge_scope 优雅降级链）
    consumed_by: tb-wiki-search (阶段一关键词匹配)
  - knowledgescope（来自 chapter_rules.yaml 的知识域定义）
    consumed_by: tb-wiki-search (降级链构建)
  - chapter_id（章节标识，用于命名输出文件）
    consumed_by: tb-wiki-search (输出文件名)
  - query_lang（zh|en|both，默认 both）
    consumed_by: tb-wiki-search (中英文并行检索)
  - max_pages（精读页数上限，默认 5）
    consumed_by: tb-wiki-search (精读数量控制)
trigger_events:
  - "chapter_produce_start"
  - "retrieval_file_modified"
passive_load: false
outputs:
  - 命中文件路径列表
    consumed_by: tb-researcher (检索结果复用)
  - 核心段落摘要（每页 300-500 字）
    consumed_by: tb-writer (写作素材引用)
  - wiki_digest_{{chapter_id}}_{{YYYYMMDD}}.md（写入 retrieval/）
    consumed_by: tb-researcher (检索摘要归档)
  - pending_wiki_refs.md（超出 max_pages 的相关页）
    consumed_by: tb-writer (handoff时检查)
completion:
  - 目标文件（wiki_digest_chXX_YYYYMMDD.md）已写入且非空
  - index.md 检索命中率 ≥ 1 页（若 = 0，则降级到 raw 全文搜索，标注 source: raw）
  - pending_wiki_refs.md 已更新（超出 max_pages 的相关页路径）
disable: false
passiveLoad: true
type: skill
---

## 错误协议

### E1 · 输入缺失降级
- **触发**: 08-知识库/index.md 不存在，或 knowledge_scope 为空
- **降级**: index.md 不存在 → 降级到 raw 全文扫描，标注 `source: raw`；knowledge_scope 为空 → 使用 discipline 字段，若也空则标注 `[WARN: empty_knowledge_scope]`
- **上报**: 无（静默降级）

### E2 · 工具失败降级
- **触发**: vault-brain / obsidian MCP 工具调用失败，或 wiki_digest 写入失败
- **降级**: MCP 失败 → 降级到 Grep+Glob 本地搜索；写入失败 → 降级到输出到控制台，标注 `[OUTPUT_FALLBACK: stdout]`
- **上报**: 连续3次同类失败 → 标注 `E2_FAIL: vault_mcp` 并上报 tb-chief-editor

### E3 · 质量不达标上报
- **触发**: 命中率 = 0（降级到 raw 后仍无结果），或生成的 wiki_digest 内容少于100字
- **上报**: 在 wiki_digest 中标注 `source: raw_empty`，路由至 tb-researcher 外部检索补充
- **上报**: 标注 `E3_ESCALATE: zero_hits=<true>`

# tb-wiki-search — Vault Wiki 优先检索工具

## 何时使用

| 场景 | 调用方 | 说明 |
|------|-------|------|
| RETRIEVE 阶段启动 | tb-researcher | Wiki 优先检索，两阶段协议 |
| "我知道关于XX" | 用户/tb-writer | 快速定位已沉淀知识 |
| VERIFY 前证据定位 | tb-reviewer | 复用已有检索摘要（72h 有效） |
| 写作前知识查卡 | tb-writer | handoff 时检查 pending_wiki_refs |

**优先级**：Wiki 命中 → raw 全文降级 → 外部检索（PubMed/Tavily）

## 两阶段检索协议

### L5.2 检索关键词优雅降级链（v2.1新增）

> **设计原则**：检索关键词永远从 `knowledge_scope` 动态读取，不硬编码。
> 缺失数据不是错误，是信息量少——系统按降级链逐级缩小范围。

```
优先级顺序（从最具体到最通用）：
1. diseases[0]     → 模板："{disease}" + "{disease_en}"
2. pathogens[0]    → 模板："{pathogen}" + "{pathogen_en}"
3. topics[0]      → 模板："{topic}" + "{topic_en}"
4. discipline      → 模板："{discipline}" + "{discipline_en}"
5. SKIP           → 警告："无法构建检索词，请检查 knowledge_scope"
```

**中英文并行检索**：每个优先级同步检索中英文（如 `"{disease}"` + `"{disease_en}"`）。

**参数获取来源**：
```
knowledge_scope.diseases[0]       ← chapter_rules.yaml 或 bootstrap_knowledge_config
knowledge_scope.pathogens[0]      ← 同上
knowledge_scope.topics[0]        ← 同上
textbook_rules.yaml.discipline    ← 教材级学科名
```

**通用示例**（某学科教材）：
```
输入：diseases=["{{disease}}"], pathogens=["{{pathogen}}"], discipline="{{discipline}}"
检索词构建：
  P1: "{{disease}}" + "{{disease_en}}"         ← 命中学科 wiki
  P2: "{{pathogen}}"                            ← 精确检索病原
```

**通用示例**（另一学科教材）：
```
输入：diseases=[], pathogens=["{{pathogen}}"], discipline="{{discipline}}"
检索词构建：
  P1(空) → P2: "{{pathogen}}" + "{{pathogen_en}}"  ← 命中学科 wiki
```

### 阶段一：index.md 关键词匹配

```
1. 读取 Vault/08-知识库/index.md
2. 按上述降级链提取关键词（中英文并行）
3. grep 匹配 Wiki 页路径（title/aliases/tags 字段）
4. 按匹配次数排序，取 top N（默认 max_pages=5）
5. 命中数 = 0 → 降级到 discipline 检索；若 discipline 也空 → SKIP
```

**输出中间产物**：
```python
matched_pages = [
    {"path": "08-知识库/entities/{disease_entity}.md", "score": 5, "match_terms": ["{keyword}", "{keyword_en}"]},
    {"path": "08-知识库/concepts/{topic_concept}.md", "score": 3, "match_terms": ["{keyword}", "{keyword_en}"]},
]
```

### 阶段二：精读 top-k，生成 wiki_digest

```
1. 按 score 排序精读前 max_pages 页
2. 每页提取核心段落（300-500 字），标注来源路径
3. 超出 max_pages 的页写 pending_wiki_refs.md
4. 合并为 wiki_digest_chXX_YYYYMMDD.md
```

## wiki_digest 文件格式

```markdown
---
doc_type: wiki_digest
chapter_id: ch_03
query_date: 2026-04-17
source: wiki          # wiki | raw | mixed
pages_read: 3
pages_pending: 2
query_lang: both
---

## wiki_digest 文件格式（通用模板）

```markdown
---
doc_type: wiki_digest
chapter_id: {{chapter_id}}
query_date: {{YYYY-MM-DD}}
source: wiki          # wiki | raw | mixed
query_strategy: {{"P1:diseases[0]"}}  # 使用的降级优先级
pages_read: {{N}}
pages_pending: {{M}}
query_lang: both
fallback_chain_used: {{["P1→P2→P3→P4"] or []}}  # 若使用了降级
---

## 检索元数据

| 字段 | 值 |
|------|-----|
| 查询时间 | {{YYYY-MM-DDTHH:MM}} |
| 知识域 | {{knowledge_scope字段内容}} |
| 检索优先级 | {{P1/P2/P3/P4}} |
| 命中 Wiki 页数 | {{N}} |
| pending 页数 | {{M}} |
| 降级链使用 | {{fallback_chain_used}} |

### 命中页列表

| # | Wiki 页 | 匹配分 | 匹配词 |
|---|---------|--------|--------|
| 1 | {{path/to/wiki_page.md}} | {{score}} | {{match_terms}} |
| 2 | {{path/to/wiki_page.md}} | {{score}} | {{match_terms}} |
| 3 | {{path/to/wiki_page.md}} | {{score}} | {{match_terms}} |

---

## 知识摘要

### 1. {{path/to/wiki_page.md}}

**核心内容**：
{{从命中页提取的核心段落，300-500字}}

**关键数据**：
- {{字段名}}：{{[实/估/待核实]}}
- {{字段名}}：{{数据}}

**置信度**：{{高/中/低}} | **来源**：{{08-知识库/path}}

---

## 待补充

以下相关页超出 max_pages 限制，未在本摘要中精读：

| Wiki 页 | 匹配词 |
|---------|--------|
| {{path/to/wiki_page.md}} | {{match_terms}} |

> 来源文件：retrieval/pending_wiki_refs.md

> **⚠️ 降级警告**：若 fallback_chain_used 非空，说明前序优先级无命中，检索范围已扩大，可能影响置信度
> **置信度说明**：
> - 高：命中 ≥2 页且与检索词精确匹配
> - 中：命中 1 页或匹配度一般
> - 低：降级链生效，匹配度低，建议外部检索补充
```
```

## 72h 过期机制

- 检索摘要有效期：72 小时（与项目健康检查 PENDING 超时对齐）
- tb-reviewer/tb-writer 读取前先检查文件日期
- 过期 → 标记 `stale`，tb-researcher 下次 RETRIEVE 时自动刷新
- stale 标注格式：`<!-- stale: 2026-04-14 (72h expired) -->`

## 与 tb-researcher 的集成

tb-researcher RETRIEVE 阶段在 Research 步骤前插入 Wiki 优先子步骤：
```
1. tb-wiki-search(query, knowledgescope, chapter_id)
2. 若命中 ≥ 1 页且置信度 ≥ 80% → 直接进入 Reflect 步骤
3. 若置信度 < 80% → 继续外部检索（PubMed/Tavily）
4. 新证据 → tb-wiki-ingest 增量编译回 Wiki
```

## 与 tb-writer/tb-reviewer 的复用协议

1. 进入 PRODUCE/VERIFY 阶段前先检查 `retrieval/wiki_digest_chXX_*.md`
2. 取最新日期版本（非 stale）
3. 存在 → 直接读取复用，不重复调用 tb-wiki-search
4. 不存在或 stale → tb-wiki-search 重新检索
