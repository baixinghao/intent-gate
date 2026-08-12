---
title: STRUCTURE — intent-gate 结构说明与使用说明
status: current
last_updated: 2026-08-09
summary: intent-gate 主插件的目录结构、逐文件说明与使用指引（姊妹篇 intent-gate-service 见其自带 STRUCTURE.md）
---

# intent-gate 结构说明 & 使用说明

> 本仓是 **intent-gate 主插件**（意图对齐引擎，轻量、零凭据、永远非阻塞）。
> 钉钉交互在姊妹仓 **intent-gate-service**（独立 MCP 服务，可选），
> 其结构说明见姊妹仓自带的 `STRUCTURE.md`。
> 两仓通过 `.harness/requests/{需求名}/_review/` 文件契约衔接（见文末附录）。

## 1. 目录树与逐文件说明

```
intent-gate/
├── .claude-plugin/
│   └── plugin.json              # 插件清单：名称/版本/关键词（Claude Code 插件身份证）
├── .mcp.json                    # 插件级 MCP server 声明：装插件即注册 intent-gate 命令
├── pyproject.toml               # 打包定义：依赖仅 mcp/pydantic/pydantic-settings，
│                                #   entry point 注册 intent-gate 可执行文件
├── .env.example                 # 环境变量样例：零配置可用，只有 HG_WORKSPACE_ROOT/HG_LOG_LEVEL
├── .gitattributes               # git 行尾约定
├── LICENSE                      # MIT
├── README.md                    # 门面：为什么需要它 / 认识论地基 / 核心机制 /
│                                #   Skill 触发地图 / 可选红蓝 / 姊妹篇指引 / 快速开始
├── docs/
│   ├── DESIGN.md                # 设计文档：三级漏斗、文件契约、保真分层、姊妹篇分工
│   ├── ARCHITECTURE.md          # 架构决策：分层图、长连接取舍、关联与安全、失败姿态
│   ├── PLUGIN.md                # 插件形态骨架：hook 纪律注入、SKILL.md 单源原则、安装
│   └── STRUCTURE.md             # 本文档
├── hooks/
│   ├── hooks.json               # SessionStart（startup|clear|compact）挂载点声明
│   ├── session-start            # 注入脚本：把 using-intent-gate 全文塞进每个会话开局
│   │                            #   （按 Claude/Cursor/Copilot 三平台输出三种 JSON）
│   └── run-hook.cmd             # Windows/Unix 引导包装器（Windows 下引导 Git Bash 执行）
├── skills/
│   ├── using-intent-gate/SKILL.md       # 【每会话自动注入】入口纪律：何时升级人工、
│   │                                    #   两个可选能力位置、编码前必须读 summary 契约
│   ├── requirement-alignment/SKILL.md   # 【分析需求/画图/DDL/说"继续"时触发】
│   │                                    #   三级对齐漏斗纲要 → 指向 MCP prompt playbook
│   ├── contract-coding/SKILL.md         # 【实现有契约的需求时触发】编码期附加层：
│   │                                    #   叠加在自有编码 skill/superpowers 之上，只立
│   │                                    #   "代码从 mermaid 契约生成、漂移即停线"，不替代
│   └── red-blue-review/SKILL.md         # 【可选：点名"红蓝对抗/蓝军评审"时触发】
│                                        #   蓝军 playbook：独立 session/信息节食/R1-R9/
│                                        #   findings 模板/2 轮熔断 ESCALATE/红军开单模板/
│                                        #   §5.5 红军整改纪律（接单/逐条落账/新造词发题/
│                                        #   冲突不得自行二选一/整改后 lint 重跑归零）
├── src/intent_gate/
│   ├── __init__.py              # 包标识 + 版本号
│   ├── __main__.py              # MCP 入口：装配 AlignmentManager + 注册两个工具面，
│   │                            #   stdio（默认）/ SSE 双传输；single 通道，无钉钉
│   ├── config.py                # Settings（pydantic-settings，HG_ 前缀）：
│   │                            #   只有 workspace_root/log_level/channel；
│   │                            #   channel=group 直接报错并指引迁移到姊妹篇
│   ├── logging.py               # 日志：stderr 单行走格式（stdout 留给 MCP stdio 保持纯净）
│   ├── models.py                # 纯 stdlib 领域模型：HG-XXXX token 生成器、
│   │                            #   Gate/GateEvent/GateStatus（姊妹篇闸门复用）
│   ├── security.py              # 纯 stdlib 安全件：parse_reply（token+答案解析）、
│   │                            #   SenderPolicy（白名单 fail-closed）、RateLimiter（限流）
│   ├── alignment/               # 意图对齐子系统（file-in-the-loop，非阻塞）
│   │   ├── __init__.py          # 包标识
│   │   ├── store.py             # 【契约核心】ReviewStore：_review/ 目录全部落盘读写——
│   │   │                        #   待决清单/alignment-log/推断清单/inbox/原子写；
│   │   │                        #   文件格式唯一事实源，改格式=毁约
│   │   ├── manager.py           # 业务层 AlignmentManager（single 通道发题/收题/核销/
│   │   │                        #   推断/对账/废弃/就绪自检）+ 两个契约函数：
│   │   │                        #   register_question（校验+先落盘，姊妹篇复用）、
│   │   │                        #   file_inbound_reply（群回复认领落盘，姊妹篇复用）
│   │   └── tools.py             # MCP 工具注册：9 个意图对齐工具（dispatch/collect/
│   │                            #   resolve/record_inference/confirm_inferences/
│   │                            #   rebroadcast/list_pending/abandon×2）
│   └── analysis/                # 需求分析子系统
│       ├── __init__.py          # 包标识
│       ├── playbook.md          # 【法律文本】需求分析 playbook 全文（Step 0 灯态 /
│       │                        #   Step 0.5 九类歧义点+精准提问 / Step 1 型态判定 /
│       │                        #   Step 3 mermaid 规范 / Step 4 交付门禁），
│       │                        #   经 MCP prompt doc_analysis_playbook 全文分发
│       ├── engine.py            # analyze_request（fresh 机械初筛 / resume 现场续跑）
│       │                        #   + record_analysis（宿主语义判断校验+落账）
│       ├── lint.py              # 【逻辑冻结】summary 机械检查器 L0-L13 + 三矩阵骨架
│       │                        #   （终态/死状态/多出边/锚点错位/BR 引用/表读写）
│       ├── mapper.py            # 【逻辑冻结】draft_mapping：意图注入映射表的
│       │                        #   章节号/规则号/步骤号锚点真实定位（禁止手写）
│       └── tools.py             # MCP 工具注册：4 工具 + 1 prompt（analyze_requirement /
│                                #   record_judgment / lint_summary / draft_mapping /
│                                #   doc_analysis_playbook）
└── tests/                       # 69 条测试（纯文件驱动，无需任何凭据）
    ├── test_core.py             #   纯核心：parse_reply / SenderPolicy / RateLimiter
    ├── test_alignment.py        #   意图对齐全链路：先落盘/收集去重/核销契约/推断闭环
    ├── test_analysis.py         #   解析引擎：红灯门禁/型态阈值/续跑只读文件现场
    ├── test_fidelity.py         #   保真：playbook 全文/lint/mapper/落账/severity 词表
    └── test_hardening.py        #   审计加固：路径逃逸/推断精确匹配/原子写/废弃途径
```

## 2. 使用说明

**安装**

```bash
cd intent-gate
python -m venv .venv && .venv\Scripts\activate    # Unix: source .venv/bin/activate
pip install -e .                                  # 注册 intent-gate 命令
python -m unittest discover -s tests -v           # 69 条测试，零凭据可跑
```

Claude Code 插件形态：把本目录作为插件目录加载（或软链到插件市场目录），
重启会话即生效——SessionStart 自动注入入口纪律，MCP 工具面可用。
手工挂 MCP（其他客户端）：`.mcp.json` 加 `"intent-gate": { "command": "intent-gate" }`。

**什么场景用什么**

| 场景 | 谁在工作 | 你要做的 |
|---|---|---|
| 让 agent 分析需求/PRD、画状态机/时序图/决策表 | `requirement-alignment` skill + analysis/alignment 工具面 | 说一句"先读 playbook 再分析"；它提问时认真答 |
| 分析中断后继续 | alignment 文件现场（resume 续跑） | 说"继续"，它自动对账 |
| complex 需求交付后要对抗评审 | `red-blue-review` skill（可选） | 说"红蓝对抗/蓝军评审"；**另开新会话跑蓝军**（独立性是命根） |
| 编码开工前 | `using-intent-gate` 注入纪律 | 不用管——它会自动检查 summary 契约与 lint |
| 实现有契约的需求 | `contract-coding` skill（附加层） | 直接说"实现{需求名}"；它与你的编码 skill/superpowers 并存，只加契约保真纪律 |
| 红灯决策/不可逆操作要人拍板 | 对话框兜底（single 通道） | 直接回答它的结构化选项题 |
| 断层要发钉钉群 @业务/技术角色 | 姊妹篇 intent-gate-service（可选） | 见其自带 STRUCTURE.md |

**零配置**：不需要 `.env`。除非要改工作目录（`HG_WORKSPACE_ROOT`）或日志级别。
旧配置 `HG_CHANNEL=group` 会启动报错并指引你装姊妹篇——这是故意的 fail-fast。

## 3. 附录：文件契约（`.harness/requests/{需求名}/_review/`）

> 这是两仓衔接的户口本：intent-gate 拥有全部读写纪律，
> 姊妹篇只做分发与递送（复用本仓 `register_question` / `file_inbound_reply` 落盘）。

| 文件 | 谁写 | 说明 |
|---|---|---|
| `pending-questions.md` | register_question（两仓共用） | 待决问题 checklist（`- [ ]` 未勾 / `- [x]` 核销 / `- [~]` 废弃）；勾不打完禁止 intent_aligned |
| `alignment-log.md` | intent-gate resolve/confirm | 意图对齐流水（蓝军 R1 复核唯一依据）：提问/人类原话/注入解读/落点 四字段契约 |
| `inference-pending.md` | intent-gate | AI 公示推断待确认清单（INF-n 编号永不复用） |
| `analysis-draft.md` | intent-gate record_analysis | 宿主语义判断落账（型态/复杂度/灯态/gaps） |
| `inbox/` | file_inbound_reply（姊妹篇调用） | 群回复落盘区（答案原话一字不改） |
| `inbox/_consumed/` | intent-gate collect_answers | 已领取答案归档（防重复下发） |
| `lint-report.md` | intent-gate lint_summary | 机械检查报告 + 三矩阵骨架（蓝军填判断列） |
| `mapping-draft.md` | intent-gate draft_mapping | 映射表锚点脚本定位草稿 |
| `review-request.md` | 红军（宿主） | 评审开单：产物清单/自评/建议重点（模板见 red-blue-review skill） |
| `review-findings.md` | 蓝军（宿主，可选） | 对抗评审 findings：三态结论 + CRITICAL/MAJOR/MINOR 表 |
| `revision-log.md` | 红军（宿主） | 整改记录（findings 逐条处理结果） |
