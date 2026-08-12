---
name: contract-coding
description: Use when implementing or modifying code for a requirement (实现需求/编码/写代码/开工) that has an intent-gate contract (.harness/requests/*/summary.md exists) - an addendum layer ON TOP of your existing coding skills/workflow (superpowers, project CLAUDE.md, etc.): it never replaces them, it only adds "code is generated FROM the mermaid contract, never against it". 中文触发：实现需求/开始编码/按图施工/写代码（当 .harness/requests/ 下存在契约时）。
---

# Contract-Driven Coding（附加层，非编码 skill）

> **本 skill 是附加层，不是编码 skill。** 你的项目自有编码 skill、superpowers、
> CLAUDE.md/AGENTS.md 规约继续拥有"怎么写代码"（架构、风格、模式）的全部决定权。
> 本 skill 只管一件事：**当 intent-gate 契约存在时，代码从契约生成，且绝不违背契约。**
> 代码风格/结构上的任何冲突，以项目自有规则为准；契约保真这件事，本 skill 不让步。

## 契约路由（多需求并存，🔴 禁止猜）

1. 人类点名了需求 → **精确匹配** `.harness/requests/{需求名}/`。没有同名目录 =
   该需求没有契约，走第 3 条——🔴 **禁止模糊匹配到别需求的契约**
   （"授信提交"套"提现确认"的 mermaid 是事故，不是便捷）。文件夹名即唯一标识，
   文件系统保证不重名，无需另造 ID。
2. 人类没点名 → 列出 `.harness/requests/` 下的契约清单让人类选，禁止自己挑。
3. 点名的需求无契约、但项目里已有其他契约（说明本工作流已启用）→
   告知人类"该需求还没有契约"，给两条路：**先跑 `requirement-alignment` 出契约**，
   或人类明确说"不要契约直接写"——后者合法，人类指令优先。
   （仅当整个项目没有任何 `.harness/requests/` 时才完全静默、按原方式编码。）
4. 采用契约前**交叉验证**：任务的领域词汇（实体/动作）应命中契约的术语表；
   明显对不上 → 停，向人类确认是否拿错了契约。

## 开工门禁（机械项，无商量）

1. 路由确认契约后读 frontmatter：`status: approved` 才动工；
   `pending_review` / `blocked` → 停下报回人类。approved 只有两条合法来源：
   蓝军 PASS 或**人类直接拍板**——红蓝评审是可选项，人类说"不用评审，我拍板"
   即合法开工，任何人不得强制走红蓝。
2. 读 `_review/lint-report.md`：CRITICAL > 0 → 契约自身带机械错误，
   禁止对着它编码，报回人类先修契约。

🔴 **门禁拒绝的正确动作是停下报回人类**——契约状态是人类拍板的事，
不是"再分析一遍"的事。禁止因为 status ≠ approved 或 lint 未归零就转入
`requirement-alignment` 重启/续跑意图对齐流程。

## 从契约施工

- mermaid 状态机 / 时序图 / 决策表 + DDL 就是规格。每个实现任务可回溯到锚点：
  哪条边 → 哪个接口/方法；哪条 BR 规则 → 哪个校验分支；哪张表 → 哪个实体/Mapper。
- **只实现契约里有的。** 图里没有的逻辑不许出现在代码里——无契约逻辑就是
  意图对齐要消灭的"静默猜测"在编码期的复活。
- 术语沿用契约词汇表（wiki 对齐版），禁止新造命名。

## 漂移 = 停线

编码中发现契约错了/不够用（现实逼着偏离图）：

1. **停下**，禁止顺手绕过去；
2. 走 `dispatch_question` 升级（对话框通道直接向人类提问）；
3. **先改契约**（重新对齐 + 重跑 lint），再改代码。

mermaid 与代码漂移即 bug——无论哪个方向。

## 完工

- 契约若有修订，重跑 `lint_summary`（CRITICAL 归零）。
- 说明每处改动实现了契约的哪些锚点，让评审能从代码回溯到契约。
