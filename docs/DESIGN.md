---
title: DESIGN — 意图对齐子系统（intent-gate）
status: current
last_updated: 2026-08-09
summary: 需求意图对齐引擎：三级对齐漏斗 + file-in-the-loop；钉钉群通道与决策闸门剥离为姊妹篇 intent-gate-service；红蓝对抗评审为插件可选 skill
---

# 意图对齐子系统设计

> 定位：**群体意图对齐引擎**。需求分析的确定性能力下沉为 MCP 工具，
> 意图断层按「代码实证 → AI 公示推断 → 人工拍板」三级漏斗消解。
> 红蓝对抗评审与调度永远留在宿主 agent 层，不进本 MCP；
> 钉钉传输层（群通道 + 阻塞闸门）剥离为姊妹篇 intent-gate-service（独立 MCP 服务）。
>
> 认识论地基（意图置信度为什么可行）见
> [../README.md](../README.md)「意图置信度从哪来」一节——
> **置信度是图的属性，不是模型的属性**：本文档的一切机械门禁都是该命题的工程化。

## 1. 背景与边界

### 1.1 为什么做 MCP 而不是 agent 二开

- agent 是入口，入口抢不过 Claude Code 等宿主；MCP 是插座，谁都能插。
- 意图对齐、置信度门禁这类 harness 能力要推广，唯一出路是可插拔形态。

### 1.2 分工铁律

| 留在本 MCP（轻量、确定性） | 留在宿主/应用层 |
|---|---|
| 型态判定、歧义点扫描、图表规范、lint、DDL 提取 | 多轮问答对话编排 |
| 意图断层的登记/落盘/对账（对话框兜底通道） | 红蓝对抗评审、findings 冲突裁决 |
| 闸门生命周期文件（checklist / alignment-log 供货） | owner 路由与任务分派 |

| 剥离到姊妹篇 intent-gate-service（重、可选） |
|---|
| 钉钉出站/入站（群通道传输层）、阻塞式决策闸门 ask_human |

- 🔴 红蓝对抗不做进本 MCP。烧 token 是次要原因；根本原因是蓝军有效性依赖
  **信息节食**（独立 session、信息不对称），同一进程内做评审会把对抗退化成自查。
  v0.2.0 起以**插件可选 skill**（`skills/red-blue-review/`）形态随插件分发：
  纯 playbook（检查项/findings 模板/熔断规则），调度与裁决永远在宿主层，
  机械基线复用 `lint_summary`，不新增任何 MCP 工具。
- 🔴 钉钉交互剥离的原因：**卡用户**（ask_human 阻塞等人工，客户端超时配置
  不当即炸）+ **太重**（fastapi/uvicorn/cryptography/httpx 全是钉钉依赖）。
  两个服务用 `.harness/requests/{需求名}/_review/` 文件契约衔接：
  intent-gate-service 复用 intent_gate 的 `register_question`/`file_inbound_reply`
  契约函数落盘，绝不另起契约实现。

### 1.3 群聊通道的价值，单聊通道是退路

- 意图对齐的瓶颈不是「怎么问」，是**问谁**：业务断层归业务人员，技术断层归技术人员，
  单对话框对面只坐一个人，答不了全部的题。
- **群里无人反驳 ≈ 共识**。群里的回答带 staffId、带原话、公开可见——谁拍的板有据可查。
  alignment-log 的「人类原话」在群聊模式下具有追责效力。
- 开发在私聊里自己补意图只是「开发的理解」，属于退路级方案。
- 副产品：对齐过程公开化 = 评审前置 + 免费审计流水。

## 2. 通道架构（姊妹篇分工）

intent-gate 只跑 **single 通道**（默认且唯一）：意图断层直接返回给宿主 agent，
由对话框前的人逐题回答。纯 stdio，无常驻进程，零外部依赖。

**group 通道（钉钉群）整体在姊妹篇 intent-gate-service**：断层经 intent-gate-service 的
`group_dispatch` 落盘（复用 intent_gate `register_question` 契约函数）后发群
@对应角色；回复经 intent-gate-service 入站回收，落盘 inbox/（复用 `file_inbound_reply`）。
会话恢复时 intent-gate-service 的 `group_rebroadcast` 对账催单。
旧配置 `HG_CHANNEL=group` 在 intent-gate 侧启动即报错并指引迁移。

群聊双传输（均在 intent-gate-service）：
- 入站：`http`（钉钉回调，需公网地址，推荐）| `stream`（长连接兜底，无需公网）
- 出站：企业内部应用机器人 API（见 ../intent-gate-service/README.md）

## 3. 三级意图对齐漏斗

发现 gap 后按序消解，逐级降本：

```
① 代码实证（自动，零人际成本）
   🔧技术类 gap 先查代码检索工具（codegraph search_graph / trace_path 或 grep）。
   代码有唯一 ground truth → 自动注入，alignment-log 标注「来源: 代码实证」。
   依据：技术类问题的答案多数存在于遗产代码的既有实现中。

② AI 公示推断（自动，但带否决权）
   代码无直接 ground truth，但可类比推断（如从 addOrder 推断 deleteOrder 逻辑）：
   推断置信度高 + 非核心主流程 → 自动注入，标注 [AI推断·依据: {推断链}]，
   进"推断待确认清单"，会话末批量出示，一次点头全确认。
   （公示推断 ≠ 静默假设：带依据、带标注、带确认回执；未经确认同样禁止
   intent_aligned: true，复核时 [AI推断] 条目必须附确认记录）

③ 群里@人（可选，姊妹篇 intent-gate-service）
   推断给不了 / 核心主流程必答题 → 标记 📋业务 or 🔧技术，
   intent-gate-service 的 group_dispatch 落盘后发群并 @白名单中对应成员（atUserIds）。
   核心主流程题带 AI 推断作为推荐选项 1（附推断依据）——人类从"想答案"
   降级为"点头/摇头"；边界 case 题优先走 ② 推断通道，不消耗群注意力。
   串行节奏：一次一题在飞；可按 🔧/📋 分两车道并行（配置项，v2 候选）。

④ 对话框兜底（默认通道）
   全部由宿主 agent 按精准提问格式（3+1 选项）逐题问。
```

- 🔴 禁止把整张问题列表一股脑发群（责任分散 = 无人回答）。
- 🔴 每题至少 3 个互斥选项 + "4. 其他（请输入）"；一次一题。
- 🔴 推断纪律：推断必须有显式依据链（对称逻辑/既有模式/术语约定），
  资金主流程与红线相关规则**禁止纯推断**，必须有人拍板（可点头式确认）。
- 🔴 全程非阻塞：以上四级没有任何一级挂起等待人类——发题即返回，
  答案靠会话恢复时的 collect/对账回收。
- 🔧/📋 分类标签是语义标记，供宿主决定 @谁（dispatch 显式传 at_user_ids）；
  白名单当前为扁平名单，按角色分组属 v2 候选，v1 未实现。

## 4. File-in-the-loop：闸门文件契约

文件是唯一事实源，钉钉只是传输通道。通道可丢消息，事实源不丢。

### 4.1 待决清单 `_review/pending-questions.md`

```markdown
# 待决问题清单 — {需求名}
- [ ] [HG-7F3A] 📋 退款后订单状态？| 选项: 1.REFUNDING→REFUNDED 2.直接REFUNDED 3.独立退款单 4.其他 | @张三 | 发出: 2026-08-08 21:00
- [x] [HG-7F3B] 🔧 库存放哪个Redis key？| 答[李四]: stock:{skuId} Lua扣减 | 回填: 2026-08-08 22:10
```

- 发题前先落清单（先落盘后发送，防进程猝死丢 token）。
- 勾不打完，报告 frontmatter 禁止 `intent_aligned: true`。

### 4.2 alignment-log 供货契约（🔴 硬约束）

群里每条被采纳的回答，必须追加为 `_review/alignment-log.md` 的标准条目：

```markdown
## Q{n} {歧义点一句话}（{时间}）
- 提问：{选项摘要}
- 人类原话：{群成员一字不改的回复}（{昵称/staffId}，钉钉群）
- 注入解读：{AI 注入到图/规则的语义}
- 落点：{状态机边/时序图步骤/决策表规则号/字段}
```

- 代码实证注入的条目，`人类原话` 位置写「来源: 代码实证（{类/方法}）」。
- AI 推断注入的条目，`人类原话` 位置写「[AI推断·依据: {推断链}]」，
  并附批量确认记录（确认人/时间）；未确认前视为意图对齐未完成。
- 降级为 [🟡待澄清] 的条目仍需宿主 agent 逐条向人类出示确认。

### 4.3 无超时闸门 + 会话恢复对账

- 闸门不设超时：没人答说明不急，是用户的选择；砍掉 NO_REPLY 兜底路径。
- **会话恢复即对账**：agent 每次开工先读 checklist；存在未勾项 →
  调 `rebroadcast_pending(需求名)` 拿清单逐题确认（装了 intent-gate-service 则
  `group_rebroadcast` 汇总重发群："还有 N 题未决，@xx 补一下"）。
  答过但丢失的答案，人看到重播自然重发——丢失重发成本 ≈ 零。
- MCP stdio 进程随会话生死无所谓：清单与 inbox 全在文件里，跨 session 天然成立。
- 已知边界：群回复在无任何接收端存活时会被钉钉丢弃（推送模型，无拉取 API——
  `/chat/get` 需企业高级权限且读全群消息，不采用）。v1 接受丢失 + 催单重发；
  v2 可选常驻接收器（inbound 剥出独立跑 + 回复写 inbox/ 文件）。
- 已知边界（子代理自答洞）：子代理物理上问不了人，但理论上能调 `resolve_question`
  伪造 responder 自答。v1 不机械堵：靠 alignment-log「人类原话」可审计（蓝军 R1）
  与 lint L20 占位对账兜底。

## 5. MCP 工具面

意图对齐工具（本设计核心，**已实现**，见 `src/intent_gate/alignment/`）：

| 工具 | 入参 | 出参 | 说明 |
|---|---|---|---|
| `dispatch_question` | 需求名, gap描述, 类别(📋/🔧), 选项[], 推荐项+推断依据(可选), @目标[], coordinate(图内坐标，可选), reflow(回流题标记，可选) | token | 🔴先落盘；single 通道登记后返回宿主（按精准提问格式向用户提问）；秒回不阻塞。要发钉钉群用 intent-gate-service 的 `group_dispatch`（同一契约函数落盘）。coordinate 承载图内坐标（`状态机 X-->Y` / `时序图 步骤N` / `决策表 BR-n`），同坐标已有在飞题机械拒收；reflow 题由工具端自增 reflow_round（同轮多题只计一轮），超 reflow_budget（缺省 2）拒收并返回 ESCALATE |
| `collect_answers` | 需求名 | 新答案列表（token+原话+回答人+原题） | 读 inbox/ 落盘文件，领取即归档防重复；宿主逐条注入后须调 resolve_question 核销 |
| `resolve_question` | 需求名, token, 答案, 回答人, 注入解读, 落点, source(group/dialog/code) | 核销结果+流水号 | checklist 打勾 + 按 §4.2 契约写 alignment-log；找不到落点禁止核销，先回问 |
| `record_inference` | 需求名, gap描述, 推断结论, 推断依据链 | 推断编号 INF-n | ②级推断登记入"推断待确认清单"，供会话末批量确认 |
| `confirm_inferences` | 需求名, 确认/驳回清单(含注入解读+落点), 确认人 | 确认回执 | 批量确认 AI 推断；确认的写 alignment-log（[AI推断] 形态+确认记录） |
| `rebroadcast_pending` | 需求名 | 未决数+清单 | 会话恢复对账；single 通道只返回清单由宿主逐题确认（群催单用 intent-gate-service 的 `group_rebroadcast`） |
| `list_pending_questions` | 需求名 | 未勾题/未决🔴数/未确认推断/已废弃数 + frontmatter_advice | 自检/汇报用；approved 状态 MCP 永不自授 |
| `abandon_question` | 需求名, token(可选), 原因 | 废弃数量 | 用户中途弃用的正式途径；token 缺省=全量废弃；`[~]` 留痕不阻断就绪 |
| `abandon_inference` | 需求名, 推断编号, 原因 | 结果 | 废弃未确认推断 |

> 实现注记：群回复由 intent-gate-service 入站回调 → 白名单校验 → 按 token 反查需求目录
> → 答案落盘 `_review/inbox/`（复用 `file_inbound_reply` 契约函数），全程无内存态，
> 两个 MCP 进程随会话生死均不影响对账。dispatch 支持 severity（🔴/🟡），
> 🔴 未消 → frontmatter_advice.status=blocked。

## 5.1 保真分层

判断归宿主、纪律归代码、机械工具逻辑冻结：

| 层 | 载体 | 状态 |
|---|---|---|
| **playbook 本体**（需求分析工作流全文 + agent 约束，物理切红蓝） | MCP prompt `doc_analysis_playbook`（`analysis/playbook.md`） | **已实现**。九类歧义点/精准提问格式/降级回执/型态门槛/mermaid 规范/上下文加载纪律；术语基准降级策略（无 wiki 用宿主代码检索，新造词必发题） |
| **宿主判断落账** | 工具 `record_analysis` | **已实现**。宿主语义结论（型态/复杂度/灯/gaps）→ MCP 校验+落盘 draft |
| **机械初筛绊线** | 工具 `analyze_requirement` | 已实现，定位为交叉校验信号（语义终判归宿主） |
| **lint 机械自检** | 工具 `lint_summary`（`analysis/lint.py`） | **已实现**。L0-L22 检查 + 三矩阵生成，逻辑冻结，禁止重写（含 L21 图演化校验：summary 状态未出现在 draft 草稿 = MAJOR；L22 回流熔断兜底：reflow_round 超 budget 且 log 无 ESCALATE 留痕 = CRITICAL） |
| **相位机**（phase 计算 + next_action/brief 供货） | `phase.py`（包根，防 engine/manager 循环导入） | **已实现**。相位流转不靠 agent 记忆，由账本状态机械推导透出（align → generate → gate → deliverable），generate 相位附 Phase B subagent 派工 brief |
| **映射表锚点定位** | 工具 `draft_mapping`（`analysis/mapper.py`） | **已实现**。章节号/规则号/步骤号由脚本真实定位，禁止手写锚点 |
| **产物生成（summary.md/mermaid/DDL）** | 宿主按 playbook Step 1-4 生成 | 归宿主——生成是语义活，MCP 用 lint/落点校验守门 |
| 红线/术语基准 | 项目文件（rules/wiki），有则宿主必读，无则走降级策略 | 不进 MCP——项目私有财产不焊死在通用工具里 |

> 产物生成刻意不做成 MCP 工具：语义生成从来不该用 Python 重实现，
> MCP 负责守门（lint CRITICAL 归零 + 落点非虚词 + 锚点脚本定位）。

## 6. 出站：姊妹篇 intent-gate-service 辖区

钉钉出站（企业机器人 API 优先、webhook 仅存量兼容、@人能力）与入站
（http 回调 / stream 长连接）已整体剥离至 intent-gate-service，
凭据配置与接入指南见 [../../intent-gate-service/README.md](../../intent-gate-service/README.md)。
intent-gate 本体零凭据、零钉钉依赖。

## 7. 安全模型（fail-closed）

- 白名单为空 = 任何人无法回复闸门。
- 回复必须携带 [HG-XXXX] token；仅一个待决闸门时允许隐式匹配（可配）。
- HTTP 回调强制验签 + AES 解密；按发送者限流。
- 群回复按 staffId 归属，alignment-log 记录原话与身份（追责效力）。

## 8. Roadmap

- v1（当前）：single 通道 + 三级漏斗 + file-in-the-loop +
  dispatch/collect/rebroadcast。纯 stdio，无常驻进程，零凭据。
  钉钉群通道与决策闸门在姊妹篇 intent-gate-service；红蓝对抗评审为插件可选 skill。
- v2（可选）：编码开工闸门（claim_task 校验 approved + 契约切片注入）；
  🔧/📋 双车道并行。
- intent-gate-service v2（可选）：常驻接收器 daemon（解决无人值守时段丢消息）；
  互动卡片按钮回调（免文本解析）。
- 明确不做：红蓝评审调度/裁决进 MCP、owner 路由、定时拉取群消息（钉钉无此 API）。
