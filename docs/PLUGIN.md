# Claude Code 插件形态

本仓库同时是一个 Claude Code plugin：安装插件即自动获得 MCP server、
SessionStart 纪律注入和三个 skill，agent 零配置接入。

## 骨架

```
.claude-plugin/plugin.json   # 插件清单（名称/版本/许可/关键词）
.mcp.json                    # 插件级 MCP server 声明（装插件即注册 intent-gate）
hooks/
├── hooks.json               # SessionStart（startup|clear|compact）挂载点
├── session-start            # 注入 using-intent-gate 全文的脚本（三平台输出分支）
└── run-hook.cmd             # Windows/Unix 多语言引导包装器
skills/
├── using-intent-gate/        # 每会话开局自动注入：升级纪律 + 两个可选能力位置
├── requirement-alignment/   # 分析需求/画图/DDL/续跑时触发：意图对齐工作流纲要
└── red-blue-review/         # 可选：点名"红蓝对抗/蓝军评审"时触发（不进 MCP 工具面）
```

> 三个 skill 的触发场景与操作面详见
> [../README.md](../README.md)「Skill 触发地图」一节。

> 钉钉交互（群通道 + 阻塞式决策闸门 + dingtalk-escalation skill）已剥离为
> 姊妹篇独立 MCP 服务，见 [../../intent-gate-service/](../../intent-gate-service/)。
> 本插件默认 single 通道（对话框兜底），零凭据零外部依赖。

## 设计要点

- **SessionStart hook 解决纪律到达率**：MCP prompt `doc_analysis_playbook`
  是被动的——agent 不来取，纪律就是空转。hook 在每个会话开局把
  `using-intent-gate` 注入上下文，声明「何时必须走闸门、分析前必须先读
  playbook」。脚本按平台输出三种 JSON（Claude Code 嵌套式 / Cursor
  snake_case / Copilot 顶层），Windows 由 `run-hook.cmd` 引导 Git Bash。
- **SKILL.md 只放触发条件与纲要**：细节单一来源在
  `src/intent_gate/analysis/playbook.md`（经 MCP prompt 全文分发）。
  两边不同步是失真事故的头号来源，skill 里不许复制 playbook 正文。
- **红蓝对抗只做 skill 不做工具**：蓝军有效性依赖信息节食（独立 session、
  信息不对称），做成 MCP 工具同进程执行会把对抗退化成自查
  （DESIGN.md §1.2 分工铁律）。lint 机械基线复用既有 `lint_summary` 工具，
  语义判断全文在 `skills/red-blue-review/SKILL.md`。
- **`.mcp.json` 用 PATH 上的 `intent-gate` 命令**：插件安装前先
  `pip install` 本包（entry point 注册 `intent-gate` 可执行文件）。
  仓库内开发改指虚拟环境解释器，见 README「MCP 接入」。

## 安装（开发期）

```bash
pip install -e .            # 注册 intent-gate 命令（零凭据，无需 .env）
```

然后在 Claude Code 中把本仓库目录作为插件目录加载（或复制/软链到插件
市场目录），重启会话即生效：SessionStart 注入纪律，MCP 工具面可用。

钉钉群通道另装姊妹篇：在姊妹仓 intent-gate-service/ 目录下
`pip install -e ../intent-gate-service -e .`，`.mcp.json` 同时挂 `intent-gate` 与 `intent-gate-service` 两个服务。
