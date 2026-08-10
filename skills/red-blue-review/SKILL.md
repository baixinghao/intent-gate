---
name: red-blue-review
description: Use when the user asks for 红蓝对抗 / 蓝军评审 / 对抗评审 / 需求评审 (adversarial review of a requirement analysis), when the user asks to 整改/按 findings 整改 (red-team revision of the analysis per review findings, §5.5), or after a complex requirement's summary.md is delivered and the human opts in - the blue army reviews red army artifacts in an independent session with information asymmetry, producing actionable findings that drive the revision loop until PASS
---

# 红蓝对抗评审（蓝军 playbook）

> 对需求分析产物做**对抗性审查**：红军（需求分析方）产出 summary.md/DDL/对齐流水，
> 蓝军（你）以对立视角找茬，产出结构化 findings，驱动红军整改闭环。
>
> **可选项**：本评审是插件自带的可选质量门，不是必经流程——
> 人类点名启动，或 complex 需求交付时由你建议、人类同意后启动。
> 没启动意图时不要把评审内容塞进正常分析流程。
>
> **适用门槛：仅 complexity=complex 的需求值得评审；simple/medium 直接 PASS 放行并说明。**

---

## 1. 独立性与信息节食（🔴 对抗有效性的根基）

- 🔴 蓝军必须在**独立 session** 执行（新开对话，或派遣与红军无共享上下文的子代理），
  禁止与红军同 session——同 session 看到红军推理过程即继承其假设，对抗退化成自查。
- 🔴 **信息不对称**：蓝军只读"产物 + 过程文件 + rubric 文件"，
  **禁止**向人类索要红军的思维链、对话记录或"当时是怎么想的"。
- 🔴 蓝军**不修改**红军的任何产物，唯一输出是 `_review/review-findings.md`。
- 🟡 同厂商异 session 即可满足独立性；异厂商模型为加分项，非准入门槛。

## 2. 输入（全部来自 `.harness/requests/{需求名}/`）

| 文件 | 用途 |
|------|------|
| `summary.md` | 被审主产物（报告） |
| `_review/alignment-log.md` | 意图对齐流水（R1 注入保真复核的唯一依据） |
| `_review/review-request.md` | 红军开单（产物清单 + 自评 + 建议评审重点） |
| `_review/review-findings.md`（前轮，如存在） | 🔴 **并集复核**：逐条判定"仍有效→并入本轮 / 已失效→注明原因"，禁止覆盖丢弃前轮成果 |
| `sql/*.sql`（项目根目录） | 被审 DDL |
| 原始 PRD（`doc/request/` 或用户提供） | 蓝军独立扫雷用（R3 歧义漏判检查） |

**rubric 参照**：项目红线文件（有则读）、术语 wiki（有则读）、
MCP prompt `doc_analysis_playbook` 的产出规范（§3.5-3.8 图表规范）。

> 🟡 **进场前置检查**：发现红军产物目录缺 `_review/alignment-log.md` →
> 不必开审，直接结论 **FAIL-可整改**（过程证据缺失：没有对齐流水，
> R1 注入保真无从复核），要求红军补齐流水后重新开单。

## 2.5 执行流程（lint 先行 + 两遍制）

### 第 0 步：lint 先行（🔴 不可跳过）

调 MCP 工具 `lint_summary(summary_path)`，机械检查（状态机终态/死状态/锚点引用/
规则引用/表读写矩阵）由工具完成。蓝军**必须**将其作为 findings 基线：
复核真实性后并入 findings（标注来源 `[lint]`），**禁止忽略或重复劳动**。
lint 覆盖了 R4/R5/R9 的机械半边，蓝军把精力留给语义判断。

### 第 1 遍：单产物检查（R1-R8）

逐表逐节过 R1-R8，产出单产物 findings。

### 第 2 遍：跨图 + 独立扫雷（R9 + R3）——lint 出矩阵，蓝军填判断

🔴 **禁止手工建矩阵**。lint 已在 `_review/lint-report.md` 生成三张矩阵骨架
（① 转移清单含事件/动作、② 表读写矩阵、③ 引用核对清单），蓝军的工作是
**填判断列**：矩阵中的空格、"待核"、❌ 格就是 findings 候选。

🔴 **多出边状态的语义跟进（禁止踢回人类）**：lint L3 或矩阵①标出的"同一状态多条出边"，
蓝军**必须**逐边对照 alignment-log 注入意图与决策表规则，自行判定触发条件是否互斥、
是否与注入意图矛盾（典型案件：注入意图说"超时自动刷新"，状态机却画了"超时 → 踢出回首页"）。
确实无法判定的，标 MAJOR 并写明缺什么信息，
**禁止原样写"需人工确认"——语义分析是蓝军的活，不是人类的**。

🔴 **前轮 findings 并集（限 CRITICAL/MAJOR）**：进场发现前轮 `review-findings.md` 时，
仅对前轮 **CRITICAL/MAJOR** 逐条复核——仍有效的并入本轮（标注"前轮遗留"），
已失效的注明失效原因；MINOR 免检（不复核、不并入，防止低价值项滚动膨胀）。禁止静默丢弃。

最后执行 R3 独立扫雷：通读原始 PRD，列出红军未标记的歧义。

## 3. 评审检查项（9 项，逐项过）

> 🔴 **R9 跨图一致性为最高优先级**：单图规范合格不代表产物合格，
> 需求报告的核心产物是 Mermaid 图，图与图打架就是实现事故。

| # | 检查项 | 通过标准 |
|---|--------|---------|
| R1 | **注入保真** | 对照 alignment-log：每条注入意图**忠于人类原话**，无添油加醋、无缩水、无静默丢弃 |
| R2 | **降级合规** | 每个 `[🟡待澄清]` 项附人类确认记录；无人类确认的降级 = CRITICAL |
| R3 | **歧义漏判** | 蓝军**独立**通读原始 PRD 扫一遍歧义，红军漏掉的每个语义断层 = MAJOR 起 |
| R4 | **报告规范** | 状态图技术打标、时序图 autonumber/participant/数据变量、决策表矩阵格式（对照 playbook §3.5-3.8） |
| R5 | **数据模型** | 三范式合理性（快照反范式需有注释说明）、唯一约束、索引覆盖查询场景 |
| R6 | **术语对齐** | 实体/字段/错误码/状态名命名符合项目术语基准（wiki 或既有代码命名），新造词 = MAJOR。**演练实证**：整改动作自身引入的新造词最易漏确认（前轮 findings 修完、新词又生），蓝军对整改 diff 必须再扫一遍新造词 |
| R7 | **红线** | 对照项目红线文件逐条（有红线文件时）；防重/脱敏在方案中必须有明确机制 |
| R8 | **状态一致性** | frontmatter `status` 与红灯消除/降级确认的实际状态匹配；`intent_aligned` 真实性 |
| R9 | **跨图一致性**（最高优先级） | 状态机↔时序图↔决策表↔注入意图四方互查，逐项核对：<br>① 每个状态机转移在时序图中有对应步骤，反之亦然<br>② 每张 DDL 表至少有一张图写入、一张图读取（无人写入的表 = CRITICAL）<br>③ 决策表规则触发条件与状态机转移条件不互斥（同一事件两条互斥转移 = CRITICAL）<br>④ 注入意图的落点引用（状态名/转移名/步骤号/规则号）**真实存在**，引用不存在的锚点 = MAJOR<br>⑤ 同一数据只有一个权威数据源；DB/Redis 双写必须声明以谁为准<br>⑥ 接口职责单一：查询接口不得夹带写库动作；与遗产/PRD 不同的 redesign 必须有文字说明 |

## 4. 输出：`review-findings.md` 模板

```markdown
---
feature: {需求名}
review_round: 1
verdict: PASS | FAIL-可整改 | FAIL-重做
reviewer: red-blue-review
date: YYYY-MM-DD
---

# 蓝军评审 Findings — {需求名}（第 N 轮）

## 结论
{一句话结论 + 统计：CRITICAL x / MAJOR x / MINOR x}

## Findings

| # | 级别 | 位置（文件/章节/图/字段） | 问题 | 整改要求 |
|---|------|--------------------------|------|---------|
| F-1 | CRITICAL | summary.md §4 状态机 | 无成功终态，SUBMIT_ORDER 后断裂 | 补 FINISHED 终态及 WAITING_PUSH→FINISHED 转移 |

## 通过项（抽查确认无问题的检查项）
- R2 降级合规：4 项均附人类确认原话 ✅
```

**结论三态**：
- `PASS`：无 CRITICAL/MAJOR → 红军将 status 转 `approved`，对抗闭环
- `FAIL-可整改`：有 CRITICAL/MAJOR 但不推翻主干 → 红军按 findings 整改，写 `_review/revision-log.md`，进入第 2 轮
- `FAIL-重做`：主干逻辑错误/注入大面积失真 → 报告作废，红军重走 Step 0

## 5. 熔断与升级

- 最多 **2 轮**评审；第 2 轮仍 FAIL → findings 标注 `ESCALATE`，由人类裁决，禁止无限抛光
- MINOR 项不阻断 PASS，登记即可（可随编码阶段顺手修）

## 5.5 红军整改纪律（接单方遵守，🔴 为硬性）

> 评审闭环的另一半。蓝军出 findings，红军按本节接单整改——
> 整改不是自由发挥，是有门禁的正式流程。

**接单入口**：发现 `_review/review-findings.md` 且 verdict ≠ PASS 时进入整改，
**禁止重走分析主流程**（不重做 Step 0-4），工作现场 = summary.md + alignment-log.md
+ review-findings.md（+ 前轮 revision-log.md）。

1. 🔴 **逐条整改**：CRITICAL/MAJOR 必须处理；MINOR 可登记随编码阶段顺手修，但必须在
   revision-log 中显式标注"登记待修"，禁止静默跳过
2. 🔴 **整改逐条落账**：追加 `_review/revision-log.md`，每条 findings 一行——
   `| # | 级别 | 整改动作 | 落点（章节/规则号/边） | 结果 |`，无落点的整改视为未整改
3. 🔴 **整改引入的新意图/新造词必须发题确认**：整改动作自身产生的错误码、状态名、
   术语等新造词，走 `dispatch_question` 让人类拍板（记入 alignment-log），
   禁止静默注入——前轮修完、新词又生是演练实证的高发事故（蓝军 R6 会复查整改 diff）
4. 🔴 **冲突不得自行二选一**：蓝军 findings 与 alignment-log 中已注入的人类意图冲突时，
   **必须向人类出示冲突点请其裁决**（出示：finding 原文 + 被冲突的注入原话 + 候选方案），
   禁止红军自己挑一个改——已注入意图的修改权在人类手里
5. 🔴 **整改后重跑 `lint_summary`**：CRITICAL 归零才可交付第 2 轮评审，
   `_review/lint-report.md` 同步更新
6. 结果流转：PASS → `status` 转 `approved`；FAIL-可整改 → 整改后等第 2 轮；
   FAIL-重做 → 报告作废，重走 playbook Step 0

## 6. 红军侧：开单模板（评审发起方填写 `_review/review-request.md`）

红军交付后由红军（或代行的人类）开单，蓝军才有评审入口：

```markdown
---
feature: {需求名}
complexity: complex
status: pending_review
review_round: 1
created: YYYY-MM-DD
---

# 蓝军评审请求 — {需求名}（第 N 轮）

## 产物清单
- `summary.md`（需求分析报告）
- `sql/*.sql`（DDL，或"无新增 DDL"说明）
- `_review/alignment-log.md`（意图对齐流水，共 N 轮问答）
- `_review/lint-report.md`（机械自检报告，CRITICAL=0）
- `_review/review-findings.md`（前轮 findings，第 2 轮起必填）
- `_review/revision-log.md`（整改记录，第 2 轮起必填）

## 红军自评
- **型态判定**：（complexity / 命中的型态 / 生成了哪些图）
- **红灯消除**：（🔴 断层逐条如何消除，或"无红灯"）
- **降级项**：（🟡待澄清清单 + 人类确认状态）
- **机械自检**：（lint 结果摘要）
- **第 N-1 轮 findings 整改**：（第 2 轮起：逐条说明处理结果）

## 建议第 N 轮评审重点
1. （红军认为最容易翻车的地方，蓝军可参考但不受限）
```

## 7. 禁止

- 🔴 禁止修改红军产物（报告/DDL/日志）
- 🔴 禁止评审 simple/medium 需求（看到 complexity ≠ complex 直接输出 PASS 并说明）
- 🔴 禁止把 findings 写成建议清单散文——必须逐条可执行（位置 + 问题 + 整改要求）
- 🔴 禁止与红军同 session 执行
- 🔴 **findings 的位置引用必须真实可复核**——行号/规则号/状态名/转移名必须指向
  真实存在的锚点，禁止凭印象编造引用；引用前必须打开文件核对
- 🔴 `approved` 的授予只有两条路：蓝军 PASS，或人类直接拍板。红军永远不自授
