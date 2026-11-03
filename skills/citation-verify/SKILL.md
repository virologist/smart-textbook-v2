---
name: citation-verify
description: 核验 PMID/DOI 真实性（零容忍幻觉引用）
requires_mcp: [pubmed-mcp-server]
fallback_paths: [pubmed_web_lookup]
mcp_semantic_tool: citation.verify
---
# citation-verify Skill

教材撰写零容忍幻觉引用（CLAUDE.md "幻觉=死刑"红线）。每个 PMID/DOI 必须核验真实性。

## MCP 语义工具名

- `citation.verify` → Claude Code: `mcp__pubmed-mcp-server__pubmed_lookup_citation`

详见 `mcp/tools.yaml` SSOT。

## 降级路径（无 MCP 时）

- `pubmed_web_lookup`: 直接访问 https://pubmed.ncbi.nlm.nih.gov/?term=PMID
- CrossRef API: https://api.crossref.org/works/{DOI}

## 核验规则

- 无 PMID 的引用 → 标注 `[⚠️待核实 PMID]`
- PMID 不存在（PubMed 查无）→ 立即删除引用
- DOI 不存在 → 立即删除引用
- 作者/年份不符 → 标注 `[⚠️待核实]`

参见 docs/2026-07-30-multi-agent-framework-v3.md §4 防幻觉闭环。
