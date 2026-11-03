# CHANGELOG

> 本包遵循 [Keep a Changelog](https://keepachangelog.com/) 格式。

## [3.0.2] — 2026-11-03

### 修复（3.0.1 遗留项闭环）

本次为修复补丁版本，无新增功能。三项修复均针对 [3.0.1] 的「已知遗留」逐条闭环，
并新增包内独立自测套件。

- **命名门禁双端覆盖面不等价（A-1）**：`scripts/check_naming.py` 的规则层扫描根由
  **包根**改为**被检工作区**（`WORKSPACE_ROOT`），相对路径基准统一为工作区相对路径。
  - 原缺陷：`SCAN_DIRS = ["00-系统规则"]` 指向包根，但 `00-系统规则` 是**教材工作区**
    的目录，包内并无该目录 → 该 root 恒空 → **包端规则层扫描 100% 失效**
    （实测包端 48 文件 vs 主端 84，差额恰为主端规则层的 37 个）。
  - 连带缺陷：`.py` 豁免判据原以「所属 root」的相对路径首段判定（`rel_parts[0].startswith("tb_")`），
    教材实例层首段实为 `章节` → **豁免失效**（多扫 `章节/*/assets/*.py`）。改为按 root 性质判定。
  - 相对路径基准原两端不同 → baseline 哈希（`sha1(rel 字符串)`）跨端不互认，
    「新增同名文件违规必红」的保证在跨端场景失效。修正后双端 baseline 互认。
  - 附带：`iter_files()` 与 `iter_files_rel()` 曾是两份**平行实现**，覆盖面易漂移；
    现合并为**单一实现**（与 3.0.1 的 B-3 同类根因），`iter_files()` 退化为薄封装。
  - 报告口径修正：原以 `len(SCAN_DIRS)`（配置常量长度）报告「扫描目录 N 个」，
    与实际范围脱节；改为如实报告**实际扫描文件数**。
- **M11 后缀白名单漏放**：`DOMAIN_SCAN_SUFFIX` 补入 `.txt` / `.csv` / `.ipynb`。
  - 原缺陷：这三类后缀文件含教材专用值时 M11 **全部放行**（exit 0），实测确认。
  - 词表分级：单字词「禽」若直接子串匹配会误报「禽类/禽群/禽舍/禽只/畜禽/禽肉」
    等**通用**词（实测误报率高）。改为**上下文排除**——先屏蔽通用组合词，
    若该字仍出现则判定为教材专用值；多字词直接匹配。
  - 词表补充「家禽」「禽病」「疫病防控/防治/流行」；**不收单词「疫病」**
    （会误报「FAO全球疫病监测数据库」这类通用数据源名）。
  - 实测 17/17 策略用例符合预期：真泄漏全部命中，通用词全部不命中。
- **包内 `tests/` 空目录 → 补齐独立自测套件**：`tests/test_package_contract.py`
  （12 个 pytest 用例）。包作为**公开分发的 GitHub 仓库**，使用者拉取后应能
  独立验证其门禁行为，而不必依赖主仓 tb-writer。
  - 覆盖：B-1 探针 fail-closed（含健康态对照项，防止过度收紧）· B-3 死机制不得复活 ·
    包内两份 `_common.py` 不得漂移 · M11 分级词表 · A-1 扫描根与相对路径基准 · 包契约与版本一致性。
- 涉及文件：`scripts/check_naming.py` · `scripts/check_manifest.py` · `tests/test_package_contract.py`

### 验证

- 包内 pytest **12 passed**（exit 0）
- 包内门禁 12 道全 exit 0；探针 11 组（6 脚本级 + 5 gates 级）全 exit 0
- 包契约自检 M1–M12 通过（0 error / 0 warning），真实包体零误报
- 双端命名门禁一致性：主端与包端（`STT_WORKSPACE` 注入主仓）**输出逐字节一致**
  （exit 0 · 扫描 84 · 新增违规 0 · baseline 豁免 16）
- 双端负向验证：注入 `00-系统规则/88-english-only.md`（[N1] 违规）→ 双端
  **均** exit 1，**输出逐字节一致**（扫描 85 · 新增违规 1 · baseline 豁免 16）
- 主仓全量 pytest **201 passed**（exit 0）

## [3.0.1] — 2026-11-03

### 修复（独立第三方复验批次 P4）

本次为修复补丁版本，无新增功能。两项修复均源于独立第三方复验（立项 C）实测发现的问题。

- **探针基础设施前置健康检查（B-1）**：`check_log_schema.py` 与 `check_tool_degradation.py` 现于探针执行**前**调用 `probe_health_check()`。
  - 原缺陷：公共设施（如相位 SSOT）损坏时，`phase_states()` 返回空列表 → 所有合法相位被判「相位非法」→ 探针因**错误原因**而全中 → **假绿**（exit 0）。
  - 修复后：公共设施失效 → 探针整体 exit 1，不给出误导性结论。
- **删除 `is_infra_error()` 子串黑名单机制（B-3）**：以「消息子串黑名单」推断失败成因属**原理性错误**——无法区分「同形异因」消息（实测误判率 7/9，其中 `phase 非法` 恰为真实违规文案）。该机制已**整体删除**，其设计意图（拦截因错误原因而通过的探针）已由前置健康检查完整覆盖。
  - 保留替代：判据函数自身抛异常 → 计入漏放（探针异常 ≠ 拦截成功）。
  - **包内补修说明**：P4 首次修复仅覆盖主仓 + 包内 `scripts/gates/_common.py`，包内 `scripts/_common.py` 的同名副本仍残留死代码（无任何调用点）。发布前复验发现并补修，包内两份 `_common.py` 现与主仓 `scripts/gates/_common.py` **逐字节一致**（sha1 `a5e2fba1a6e3d654dc0e0c9ce58fd7a03c6dc494`）。
- 涉及文件：`scripts/check_log_schema.py` · `scripts/check_tool_degradation.py` · `scripts/_common.py` · `scripts/gates/_common.py` · `scripts/gates/g1_state_phase.py` · `scripts/gates/g2_quality_citation.py` · `scripts/gates/g3_figure_naming.py` · `scripts/gates/g4_rules_files.py` · `scripts/gates/g5_flow_health.py`

### 验证

- 包内门禁 12 道全 exit 0；探针 11 组（6 脚本级 + 5 gates 级）全 exit 0
- 包契约自检 M1–M12 通过（0 error / 0 warning）
- SSOT 故障态探针实测：3 道全 exit 1（不再假绿）——注入 `main_path` 键损坏后，`check_log_schema` / `check_tool_degradation` / `g1_state_phase` 探针均报「公共设施失效，探针结果不可信」
- 发布物自检：zip 解压副本上包契约 + 探针复跑全 exit 0

### 已知遗留

> 以下三条已于 [3.0.2] 全部闭环（保留原文以存历史事实）。

- `tests/` 目录为空（包内不随包分发测试代码）→ [3.0.2] 已补 `tests/test_package_contract.py`
- 命名门禁双端覆盖面不等价：主端扫描 84 文件（含规则层），包端扫描 47 文件（不扫规则层）→ [3.0.2] 已修复，双端现均扫 84
- M11 后缀白名单未覆盖 `.txt` / `.csv` / `.ipynb` → [3.0.2] 已补齐

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
