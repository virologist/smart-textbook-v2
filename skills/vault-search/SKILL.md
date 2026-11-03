---
name: vault-search
description: 在用户 Obsidian vault 中检索知识（含降级路径）
requires_mcp: [vault-brain, mcp-obsidian]
fallback_paths: [direct_file_read]
mcp_semantic_tool: vault.semantic_search
---
# vault-search Skill

在教材撰写中检索用户 Obsidian vault（本地知识库），降低幻觉风险。

## MCP 语义工具名（跨 agent 标准）

- `vault.semantic_search` → Claude Code: `mcp__vault-brain__vault_search`
- `vault.keyword_search` → Claude Code: `mcp__mcp-obsidian__obsidian_simple_search`

详见 `mcp/tools.yaml` SSOT + `mcp/adapters/{agent}.json` 翻译。

## 降级路径（Trae 不支持 MCP 时）

- `direct_file_read`: 直接读 `${TB_VAULT_PATH}/wiki/` 下的 markdown 文件
- 用 grep / ripgrep 做关键词匹配（损失语义检索能力）

## 触发场景

- PRODUCE 前检索已有知识
- VERIFY 发现 [待核实] 时深度检索
- 用户问"我记得..." / "之前..." 时搜 vault

参见 docs/2026-07-30-multi-agent-framework-v3.md §4 防幻觉闭环。
