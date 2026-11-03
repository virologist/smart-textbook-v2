# figure-registry.md — 图表登记表规范

> 版本：v1.0 ｜ 日期：2026-11-02 ｜ 批次：B4 图表流水线（架构设计 §3.2 轨 B）
> 定位：**图表的登记表规范**。每个章节一份 `章节/{ch##}/figure-registry.md`，登记该章全部图表。
> 校验器：`scripts/check_figures.py`（--reconcile 摸排 / --renumber 重编号）
> 红线 P-2：本文件属通用层，**禁止出现教材专用值**——示例一律 `{{placeholder}}`。

---

## 1. 为什么需要登记表

图表生产链路（判型 → 数据 → ipynb → 美学自检 → 编号 → 图注 → 插入正文）有 6 个环节，任一环节脱节都产生「图在 assets/ 里但正文没有引用」「正文引用了图但文件不存在」这类静默缺陷。

登记表是**唯一对账点**：文件、编号、溯源、迭代次数、图注，五者集中登记，由校验器机械比对正文与目录。

---

## 2. 登记表位置与格式

**位置**：`章节/{ch##}/figure-registry.md`（章节级唯一）

**表头固定八列**（列序不可改）：

| 图号 | 类型 | 文件 | ipynb | 溯源 | 美学迭代 | 图注 | 状态 |
|---|---|---|---|---|---|---|---|

| 列 | 取值 | 约束 |
|---|---|---|
| **图号** | `图{ch##}-N` | 章节内连续、无缺号、无重号；N 从 1 起 |
| **类型** | A / B / C / D | 判型 SSOT = `references/rule-engine/figure_type_schema.md` |
| **文件** | `assets/{文件名}` | 相对章节目录；文件须真实存在 |
| **ipynb** | `assets/{图号}.ipynb` 或 `-` | **B 类必填**（数据实证图须有源）；其他类填 `-` |
| **溯源** | PMID/DOI/本地源锚 或 `-` | A/B 类必填（须可核验）；C 类填 `-`（走 AI 披露） |
| **美学迭代** | 0–5 整数 | ≤5；超限强制降级为 D 类 |
| **图注** | 图注文本 | 含图号 + 标题；**C 类必须含 AI 披露**（AI/AI生成/AI辅助 等字样） |
| **状态** | draft / final | `final` 才允许进入 DELIVER |

---

## 3. 编号规范

### 3.1 新图（规范形态）

```
图{ch##}-{N}      例：图ch01-1、图ch01-2 …
```

- `{ch##}` = 章节号（与章节目录名一致）
- `N` = **按正文出现顺序**编号，章内连续
- 对应文件名：`assets/图ch01-{N}.png` + `assets/图ch01-{N}.ipynb`（B 类）

### 3.2 存量图（历史形态）

存量实例使用 `fig-NN` 形态（如 `fig-01.png`）。**不强制重命名**——重命名会破坏正文引用与 docx 嵌入。

**处置口径**：
- 存量图**不纳入编号连续性检查**（baseline 豁免）
- 新增图一律用 `图{ch##}-N` 规范形态
- 存量图若要规范化，走 `check_figures.py --renumber`（生成 rename_map，人工确认后执行）

---

## 4. 图注规范

```
图{ch##}-{N}  {标题}

{图注正文}
```

**图注正文按类型追加**（与 `figure_type_schema.md` 的字段契约一致）：

| 类型 | 图注必含 |
|---|---|
| A 教学建模图 | 适用边界声明 + 支撑文献（PMID） |
| B 数据实证图 | 数据来源（PMID/DOI）+ 提取规则 |
| C 叙事型插图 | **AI 披露**（方法部分 + 图注双重披露，图注含「AI生成」等字样） |
| D 降级图 | 说明「因复杂度/争议性降级为表格/纯文字」 |

**图注位置**：紧随图位之后，独立段落；正文首次引用处放占位符（如 `图ch01-1`）。

---

## 5. 美学自检（≤5 次迭代）

B 类数据图的产出须经美学自检。**5 条机械判据优先**，模型自评仅在机械判据全过后作参考：

| # | 判据 | 机械可检 |
|---|---|---|
| 1 | 轴标签齐全（X/Y 轴有名称与单位） | 是 |
| 2 | 图例存在且不遮挡数据 | 半（可检图例元素是否存在） |
| 3 | 字号 ≥ 10pt | 是 |
| 4 | 无元素重叠 | 否（模型判） |
| 5 | 色盲友好配色 | 否（模型判） |

**迭代上限 5 次**：
- 第 1 次必做（无论通过与否）
- 每次迭代在登记表「美学迭代」列累加，**并记录 verdict 与 reason**（判定与理由）
- 第 5 次仍不通过 → **强制降级为 D 类**（GFM 表格 / Mermaid / 纯文字），并更新登记表类型列

---

## 6. 摸排与重命名机制（用户新增图表场景）

用户手动往 `assets/` 里新增图文件后，须走**摸排（reconcile）**：

```bash
# 摸排：列出 assets/ 中未登记的文件 + registry 中已登记但文件缺失的条目
python3 scripts/check_figures.py 章节/{ch##} --reconcile

# 重编号：生成 rename_map（dry-run，不动文件）
python3 scripts/check_figures.py 章节/{ch##} --renumber
```

**流程**：
1. 用户新增图 → `--reconcile` 输出待登记清单
2. 编排者判定类型（A/B/C/D）与溯源 → 补登 registry
3. 编号连续性检查（`--renumber` 输出建议映射，含正文引用与 assets.md 的同步更新点）
4. **人工确认后**才执行重命名（脚本默认 dry-run，不自动改文件）
5. 重命名后须同步：正文图位引用 · `assets.md` 清单 · docx 嵌入（若已装配）

> **可回滚**：`--renumber` 输出的 rename_map 是 JSON，可反向执行回滚。

---

## 7. 校验器检查项（`scripts/check_figures.py`）

| 检查 | 判据 | 失败后果 |
|---|---|---|
| **F1 图有源** | B 类图存在同名 `.ipynb`（含 Cell0 溯源 / Cell2 绘图代码） | 阻塞 VISUALIZE 出口 |
| **F2 编号连续** | `图{ch##}-N` 序列无缺号、无重号（存量 fig-NN 豁免） | 阻塞 VISUALIZE 出口 |
| **F3 图注完整** | 每图注含图号 + 标题；**C 类含 AI 披露** | 阻塞 VISUALIZE 出口 |
| **F4 迭代上限** | 美学迭代 ≤5；超限须已降级为 D 类 | 降级不阻塞 |
| **F5 文件存在** | registry 登记的文件真实存在 | 阻塞 VISUALIZE 出口 |
| **F6 正文引用** | final 状态的图必须在正文有引用占位符 | 阻塞 VISUALIZE 出口 |
| **F7 终稿清洁**（`--final`） | 残留 `[需补数据]`/`[待核实]`/`[估]` == 0 | 阻塞 DELIVER（与 gap_flow 的 G5 呼应） |

---

## 8. 与既有体系的关系

| 既有物 | 关系 |
|---|---|
| `references/rule-engine/figure_type_schema.md` | **判型 SSOT**（A/B/C/D 四类 + 字段契约）；本文件是**编号/图注/迭代/登记**规范，不重复定义判型 |
| `章节/{ch##}/assets.md` | 存量图表清单；registry 是**升级形态**（含溯源/迭代/状态），两者并存，迁移走 `--reconcile` |
| `章节/{ch##}/assets/*.ipynb` | B 类图的源文件；Cell0=原始数据+溯源，Cell2=绘图代码（既有 `educatordataprovenance.md` 口径） |
| `scripts/check_gap_flow.py` | 缺口闭环；chart_data 类缺口回填到本 registry |

---

## 9. 自检（实现方必做）

```bash
python3 scripts/check_figures.py 章节/{ch##}            # F1-F6
python3 scripts/check_figures.py 章节/{ch##} --final    # 追加 F7
python3 scripts/check_figures.py 章节/{ch##} --reconcile  # 摸排
python3 scripts/check_figures.py 章节/{ch##} --renumber   # 重编号（dry-run）
python3 scripts/check_figures.py --probe-negatives        # 校验器负向探针
```
