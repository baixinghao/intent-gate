# intent-gate

[English](README.md) | **简体中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python >=3.11](https://img.shields.io/badge/python-%3E%3D3.11-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/protocol-MCP-green)](https://modelcontextprotocol.io/)

**别让编码 agent 猜需求。**

复杂业务需求、质量堪忧的 PRD、没有旧代码可参考——这正是编码 agent 开始编造的地方：
"只有成功没有失败流""防重复无服务端方案"这类断层，会被顺手猜一个合理实现混过去，
等你发现时往往已经晚了。

intent-gate 把意图对齐**前置到需求解析阶段**：PRD 先过意图置信度门禁 → 断层按三级
漏斗消解 → 产出带技术打标的 mermaid 契约（状态机/时序图/决策表）→ lint 机械门禁
CRITICAL 归零 → 才允许编码开工。

以 **MCP server（stdio 子进程）** 形态运行，Claude Code 等即插即用；
**无常驻、无凭据、零配置**，跑通全流程。

## 痛点

编码 agent 会猜——而且专挑最疼的地方猜：

- **复杂业务需求**：密集分支、资金流转、实体生命周期。编码时 agent 的注意力在
  代码生成上，"只有成功没有失败流""防重复无服务端方案"这类断层，被顺手猜一个
  合理实现混过去。
- **需求文档质量不高**：模糊、矛盾、缺数据源。agent 不停下来问，它自行插值。
- **没有旧代码可参考**：全新项目或整体重写，代码库里没有 ground truth 可锚定，
  每个歧义都是一次抛硬币。

宿主自带的意图弹窗（AskUserQuestion 之类）也救不了：判断是临时的、答案是易逝的——
上下文一压缩、会话一结束，对齐现场就没了。

**代码幻觉的重要来源不是模型能力，是意图断层在编码阶段被猜测填补。**

## 方案：意图对齐前置到需求解析阶段

在解析阶段把意图裁决掉，编码阶段就没人需要猜。intent-gate 的立场是**判断归宿主、
纪律归代码**：

- 语义判断（这是什么灯、这是不是断层）由宿主 agent 满血完成，MCP 不越俎代庖；
- 纪律（断层不许静默丢弃、答案不许核销不落点、交付不许带机械错误）由 MCP 工具
  机械强制，**文件是唯一事实源**，进程随会话生死不丢现场，天然抗上下文压缩。

编码 agent 最终拿到的是契约，不是猜谜题。

## 它在 vibe coding 工作流里的位置

intent-gate 是**应用层 Harness Engineering** 的一块——它只占管线的一个阶段：需求解析。

```
PRD ──▶【intent-gate：置信度门禁 → 意图对齐 → mermaid 契约】
         ──▶ summary.md（每条边带技术打标，lint CRITICAL 归零）
         ──▶ 编码 agent（Claude Code / Cursor / 谁都行）──▶ 测试 ──▶ 交付
```

杠杆是不对称的：**契约落得好，编码阶段就是白给的**——每条边、每个步骤、每条规则
都有了着落，换个像样的 agent 都能实现。所以 intent-gate 刻意不碰编码/评审/部署：
下游 agent 是可替换的，输入契约不可替代。

## 核心机制

**意图置信度门禁（Step 0）**：分析任何需求前先评估灯态。🔴 核心逻辑断层未消除前，
报告 `status` 必须 `blocked`，任务拆分首任务强制 `[BLOCKER]`，禁止下游编码开工。

**三级对齐漏斗（Step 0.5）**，逐级降本，全程非阻塞：

```
① 代码实证：技术类断层先查代码，有唯一 ground truth 直接落账，零人际成本
② AI 公示推断：带显式依据链登记，会话末批量请人类点头（资金主流程禁止纯推断）
③ 人工拍板：结构化选项提问（≥3 互斥选项 + "其他"），一次一题
   （装了姊妹篇 intent-gate-service 时，这一级可以发到钉钉群 @对应角色）
```

**机械执法（MCP 工具强制，不靠 prompt 自觉）**：

- 断层一旦登记物理落盘 `pending-questions.md`，勾不打完不给 `intent_aligned_ready`；
- `resolve_question` 空落点机械拒收——每条注入意图必须精确到状态机的边、
  时序图的步骤、决策表的规则号，找不到落点**禁止核销，必须回问**；
- 落点锚点禁止手写，`draft_mapping` 脚本真实定位章节号/规则号/步骤号；
- 交付前 `lint_summary` 机械自检（L0-L8：状态机解析失明兜底/成功终态/死状态/
  锚点错位/BR 引用/表读写矩阵……），**CRITICAL 归零才可交付**。

**mermaid 即编码契约**：状态图每条边强制技术动作打标
（`DRAFT --> PENDING: 提交 (IF校验, DB_INSERT, REDIS_ZSET)`），时序图 `autonumber`
+ 传递变量标注，规则逻辑强制决策表矩阵。编码 agent 拿到的不是示意图，
是每条边对应明确实现动作的规格——猜测空间被结构性压缩。

## 意图置信度为什么从产物上读（认识论地基）

**意图置信度不是模型的自我感觉，是 artifact 的闭合状态。**

让模型自评"有几分把握"是条死通道：口头置信度和真实正确率几乎不相关
（那是事后合理化，不是读数）；token 级 logprobs 够不着语义层的不确定
（"退款后到底该不该有中间态"），且 API 根本不暴露。所以本系统从头到尾
不让模型打分数——**让它生产，从产物上读数**。

画图（状态机/时序图/决策表）是**探测仪器**，不是表达手段：
自然语言能糊弄过去的，形式化糊弄不过去——"退款处理完就结束了"是一句话，
状态机里你必须回答 REFUNDING 之后有没有边、指向谁、触发条件是什么；
每一条边都是一次被迫的离散决策，模糊意图在散文里是隐形的，在边上是空洞。

断层分四种，各有探测器，"画不下去"只是第一层：

| 层 | 机制 | 抓什么 |
|---|---|---|
| ① 形式化强迫 | 画图，画不下去就标记 | **有感知的断层**——模型知道自己不知道 |
| ② 分类学扫雷 | 九类歧义点清单（异常路径/回滚/条件组合/字段语义/防重越权/术语…） | **半沉默断层**——模型不会自发卡住，但拿清单逐元素扫时会暴露 |
| ③ 机械 lint | L1 无成功终态 / L2 死状态 / L6 表无写入… | **全沉默断层**——模型毫无知觉地填上的地方，代码执法，完全不靠自觉 |
| ④ 蓝军独立复核 | 独立 session + 信息节食（可选 skill） | **作者注意力的系统性盲区**——前三层是同一双眼睛，这层换一双 |

所以 🟢 绿灯的含义不是"模型觉得有把握"，而是"状态机每条边有据、九类雷区扫过、
lint CRITICAL 归零、人类对断层逐题拍板"。**置信度是图的属性，不是模型的属性。**
画图是仪器，lint 是校准器，人类拍板是基准源。

## Skill 触发地图（装了之后，什么场景自动用哪个）

插件三个 skill 各有明确触发面，配合 SessionStart 注入的入口纪律工作：

| Skill | 什么时候触发 | 它干什么 |
|---|---|---|
| `using-intent-gate` | **每个会话开局自动注入**（SessionStart hook） | 入口纪律：分析前必须先读 playbook、何时必须升级人工、两个可选能力（红蓝/钉钉）的位置；编码前必须读已落地的 summary 契约 |
| `requirement-alignment` | 你让 agent **分析需求/PRD、画状态机/时序图/决策表、生成 DDL**，或中断后说"继续" | 三级对齐漏斗纲要：代码实证 → AI 公示推断 → 结构化提问；全程非阻塞，答案跨会话对账 |
| `red-blue-review`（可选） | 你**点名**"红蓝对抗 / 蓝军评审 / 需求评审"，或 complex 需求交付后你点头同意 | 蓝军对抗审查：独立 session、信息节食，R1-R9 九项检查产出 findings 驱动整改闭环；`approved` 只有蓝军 PASS 或人类拍板两条路 |

配套 MCP 工具（agent 自动调用，无需你干预）：`doc_analysis_playbook`(prompt)
/ `analyze_requirement` / `record_judgment` / `lint_summary` / `draft_mapping`
/ `dispatch_question` / `collect_answers` / `resolve_question` / `record_inference`
/ `confirm_inferences` / `rebroadcast_pending` / `list_pending_questions` / `abandon_*`。

> 你唯一需要记住的操作面：**分析需求时让它先读 playbook；它提问时认真答；
> complex 交付后想要对抗评审就说一声"红蓝"。**

## 可选：红蓝对抗评审（插件 skill）

红军（需求分析）交付 complex 产物后，可选启动**蓝军对抗评审**：
独立 session、信息不对称（蓝军只读产物不看红军推理），按 R1-R9 九项检查
（注入保真/降级合规/歧义漏判/跨图一致性……）产出 findings，驱动有纪律的整改闭环；
`status` 转 `approved` 只有两条路——蓝军 PASS，或人类直接拍板，红军永远不自授。

- 触发：人类点名（"红蓝对抗 / 蓝军评审"），或 complex 交付后人类点头；
  simple/medium 直接 PASS 放行。
- 形态：纯插件 skill（`skills/red-blue-review/`），**不进 MCP 工具面**——
  蓝军有效性依赖信息节食，同一进程内做评审会把对抗退化成自查。
- 熔断：最多 2 轮，仍 FAIL 标 `ESCALATE` 交人类裁决，禁止无限抛光。
- 红军整改同样有门禁（skill §5.5）：每条 finding 在 `_review/revision-log.md`
  落账（整改动作 + 真实落点），交付第 2 轮前 lint 重跑 CRITICAL 归零；
  整改自身引入的新造词必须发题确认；finding 与已注入的人类意图冲突时，
  红军必须出示冲突请人类裁决——禁止自行二选一。

## 姊妹篇：intent-gate-service（钉钉群共识通道 + 决策闸门）

意图对齐的瓶颈不是"怎么问"，是**问谁**——业务断层归业务人员，技术断层归技术人员。
默认 single 通道下断层由对话框前的人逐题回答，全程不碰钉钉；
装了 [intent-gate-service](https://github.com/baixinghao/intent-gate-service)（独立 MCP 服务）后，漏斗第③级可发钉钉群
@对应角色，回复经回调落盘 inbox，由 intent-gate 的 `collect_answers` 领取。

群通道的真正价值是**留痕**：群里的回答带 staffId、带原话、公开可见，
谁拍的板有据可查，**群里无人反驳 ≈ 共识**。钉钉只是传输层，文件账本不依赖它存活。

编码期的阻塞式紧急人工升级（`ask_human` 决策闸门）同样在 intent-gate-service——
**会卡住等待人工回复的重交互全部在姊妹篇**，主插件永远非阻塞。

自 v0.2.0 起剥离——主插件只保留意图对齐与需求解析，零钉钉依赖。

## 快速开始（Claude Code —— 两步）

```bash
# 1）装 MCP server —— 执法的那一半（工具/账本/lint 门禁）
pipx install git+https://github.com/baixinghao/intent-gate.git
# 或：uv tool install git+https://github.com/baixinghao/intent-gate.git

# 2）装插件 —— skills + hooks，自动注册 MCP server
claude plugin marketplace add baixinghao/intent-gate
claude plugin install intent-gate@baixinghao-plugins
```

重启会话，完事——入口纪律自动注入，MCP 工具面在线。

> ⚠️ **第 1 步不可省。** 插件的 skill 是纪律，MCP server 才是执法。只装插件
> （PATH 里没有 `intent-gate` 命令）= 只剩嘴上劝告，机械门禁全部阵亡——
> 没有问题账本、没有 lint、没有交付拦截。SessionStart hook 每局自检：
> 发现 server 缺席，agent 会主动提醒你去跑第 1 步。

## 其他 MCP client

同样先跑第 1 步装好 server，然后把客户端指向 `intent-gate` 命令：

```json
{
  "mcpServers": {
    "intent-gate": {
      "command": "intent-gate"
    }
  }
}
```

客户端走网络协议的话，用 `intent-gate --mcp-transport sse --mcp-port 8400`
以 **SSE** 暴露 MCP。

**零配置即可使用全部意图对齐能力**（single 通道，对话框兜底）。
钉钉群通道见 [intent-gate-service README](https://github.com/baixinghao/intent-gate-service)。

## 开发（克隆仓库）

```bash
python -m venv .venv && .venv\Scripts\activate   # Unix: source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v   # 核心逻辑测试（无需任何凭据）
```

把 `command` 指向虚拟环境解释器：
`"command": "<repo>\\.venv\\Scripts\\python.exe", "args": ["-m", "intent_gate"]`，
并设 `"env": { "PYTHONPATH": "<repo>\\src" }`。
插件形态骨架见 [docs/PLUGIN.md](docs/PLUGIN.md)。

## 项目结构

```
src/intent_gate/
├── config.py / logging.py        # 配置（HG_* 环境变量，零凭据）、日志
├── models.py / security.py       # 纯 stdlib 核心：token、白名单、回复解析、限流
├── __main__.py                   # MCP 入口（stdio/SSE）
├── alignment/                    # 意图对齐子系统（file-in-the-loop，非阻塞）
│   ├── store.py                  #   落盘层：待决清单/alignment-log/推断清单/inbox
│   ├── manager.py                #   业务层 + 契约函数（register_question /
│   │                             #   file_inbound_reply，姊妹篇 intent-gate-service 复用）
│   └── tools.py                  #   MCP 工具注册（9 个意图对齐工具）
├── analysis/                     # 需求分析子系统
│   ├── playbook.md               #   需求分析 playbook（经 MCP prompt 全文分发）
│   ├── engine.py                 #   现场判定 / 宿主判断落账
│   ├── lint.py                   #   分析报告机械检查器（L0-L8 + 三矩阵）
│   ├── mapper.py                 #   意图注入映射表锚点定位
│   └── tools.py                #   MCP 工具注册（分析工具 + playbook prompt）
skills/
├── using-intent-gate/             # 入口纪律：何时升级人工、两个可选能力的位置
├── requirement-alignment/        # 意图对齐工作流纲要 → 指向 MCP prompt
└── red-blue-review/              # 可选：红蓝对抗评审 playbook（蓝军九项检查 + 红军整改纪律）
（姊妹仓）intent-gate-service      # 姊妹篇：钉钉群通道 + 决策闸门（独立 MCP 服务）
```

## 文档

- [docs/STRUCTURE.md](docs/STRUCTURE.md) — **结构说明 & 使用说明（双仓逐文件，先看这份）**
- [docs/DESIGN.md](docs/DESIGN.md) — 意图对齐子系统设计（三级对齐漏斗、文件契约、姊妹篇分工）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构决策（分层、长连接取舍、失败姿态）
- [intent-gate-service README](https://github.com/baixinghao/intent-gate-service) — 姊妹篇钉钉配置与接入

## Roadmap

- [ ] 编码开工闸门：claim_task 校验 approved + 注入契约切片（意图对齐的最后一公里）
- [ ] intent-gate-service：互动卡片 + 按钮回调（免文本解析）
- [ ] intent-gate-service：闸门审计落盘（SQLite）与回放
- [ ] intent-gate-service：多 agent 实例的网关模式（stream 入站收敛为单连接）

## License

[MIT](LICENSE)
