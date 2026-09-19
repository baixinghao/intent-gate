"""需求解析引擎的 MCP 工具注册（与 alignment 工具面并列）。

🔴 本模块不含任何红蓝对抗/评审内容——评审是应用层的事。

分层（失真修复后的最终形态）：
  判断：宿主 LLM + doc_analysis_playbook（prompt 全文保真分发）
  落账：record_analysis（宿主判断 → MCP 校验+落盘）
  绊线：analyze_requirement（机械初筛，交叉校验用）
  机械：lint_summary / draft_mapping（逻辑冻结的机械检查器，零重写）
  自检：describe_workspace（确认本次调用落在哪个项目根上）

🔴 项目根是**每次调用的参数**（project_path），不是启动时绑定的全局状态：
MCP server 在宿主里是全局单例、服务该宿主的所有工作区，启动时绑定只能表达
一个项目根。省略 project_path 时回落启动时的 workspace_root；宿主服务多工作区
（如 DSH 里换了工作区）时必须显式传。不用"设一次全局生效"的开关——宿主会并发
跑 subagent，全局可变状态会串项目。
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..logging import get_logger
from ..workspace import resolve_root
from .engine import analyze_request, record_analysis
from .lint import run_lint
from .mapper import run_mapper

log = get_logger("analysis.tools")

_PLAYBOOK_PATH = Path(__file__).parent / "playbook.md"


def register_analysis_tools(mcp: FastMCP, workspace_root: str | Path) -> None:
    """把需求解析引擎注册到既有 FastMCP 实例上。

    5 个工具统一接受 project_path（本次生效的项目根，省略则回落 workspace_root）。
    """
    default_root = workspace_root

    def _root(project_path: str | None) -> Path:
        """本次调用生效的项目根：project_path 优先，缺省回落启动时的根。"""
        return resolve_root(project_path, default_root)

    @mcp.prompt()
    def doc_analysis_playbook() -> str:
        """需求分析 playbook 全文（skill + agent 双来源约束，切红蓝版）。

        🔴 开始任何需求分析任务前必须先读本 playbook——它是你的法律文本：
        Step 0 置信度评估 / Step 0.5 意图对齐（九类歧义点 + 精准提问格式）/
        Step 1 型态判定 / Step 3 章节与 mermaid 规范 / Step 4 交付门禁。
        判断在你的脑子里做，纪律由 MCP 工具强制。"""
        return _PLAYBOOK_PATH.read_text(encoding="utf-8")

    @mcp.tool()
    def describe_workspace(project_path: str | None = None) -> dict:
        """自检：本次调用生效的项目根、它的 .harness 现场与已登记需求。

        🔴 每个新会话开工第一件事调它——确认根指向的是你以为的那个项目。
        多工作区宿主（如 DSH）里省略 project_path 会回落**进程启动目录**，未必是
        当前项目；根错了，所有 .harness 落盘都会静默落到别的项目去。
        返回 features 列出 .harness/requests/ 下已有需求名，供后续调用显式传参。"""
        root = _root(project_path)
        requests_dir = root / ".harness" / "requests"
        features: list[str] = []
        if requests_dir.is_dir():
            features = sorted(p.name for p in requests_dir.iterdir() if p.is_dir())
        return {
            "ok": True,
            "workspace_root": str(root),
            "source": "project_path" if project_path else "启动时 workspace_root（未显式指定）",
            "harness_dir": str(root / ".harness"),
            "harness_exists": requests_dir.is_dir(),
            "features": features,
        }

    @mcp.tool()
    def analyze_requirement(
        feature: str, prd_path: str | None = None, project_path: str | None = None
    ) -> dict:
        """需求解析现场勘查（两条路自动区分）+ 机械初筛绊线。

        输入契约：prd_path 指 UTF-8 文本文件或 .docx（相对路径按**生效的项目根**
        解析，不按进程 cwd）。.docx 引擎：markitdown（环境已有则复用增强）→
        mammoth（核心依赖，安装时自动带上）；无 mammoth 拒绝并带修复指令，
        无静默降级。.doc/.pdf 等其他二进制不支持，报错带转文本指引。

        - 从0解析（fresh）：无现场时触发，必须给 prd_path。返回机械初筛信号
          （型态/复杂度/置信度信号/歧义点候选）。🔴 初筛只是交叉校验的绊线，
          正式判断由你按 playbook 语义分析后调 record_analysis 落账。
        - 中断续跑（resume）：现场已存在时自动触发。只读文件现场，汇报
          已答/未决/待确认推断/新答案 + skill 词表的 frontmatter 建议 +
          下一步该调的工具动作。返回含 phase 块（相位机判定：
          align/generate/gate/deliverable）。

        project_path: 本次调用生效的项目根（.harness 所在）。省略则用进程启动时的
        workspace_root；宿主服务多个工作区时必须显式传，否则现场会查/建到启动目录去。"""
        return analyze_request(_root(project_path), feature, prd_path)

    @mcp.tool()
    def record_judgment(
        feature: str,
        logic_pattern: list[str],
        complexity: str,
        confidence: str,
        selected_tools: list[str] | None = None,
        gaps: list[dict] | None = None,
        prd_path: str | None = None,
        notes: str = "",
        project_path: str | None = None,
    ) -> dict:
        """宿主语义判断落账：你按 playbook 分析完的正式结论交给 MCP 校验+落盘。

        logic_pattern: ["State-Driven"/"Process-Driven"/"Rule-Driven"] 子集；
        complexity: simple/medium/complex；confidence: 🟢/🟡/🔴（你的正式灯）；
        gaps: [{"gap","severity"(🔴/🟡),"category"(📋/🔧),"options"(≥3),"recommend"?}]。
        落盘后逐题 dispatch_question 分发（🔴 未消 status=blocked）。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传，否则账本写错项目。"""
        return record_analysis(
            _root(project_path), feature, logic_pattern, complexity, confidence,
            selected_tools, gaps, prd_path, notes,
        )

    @mcp.tool()
    def lint_summary(summary_path: str, project_path: str | None = None) -> dict:
        """summary.md 机械自检（L1-L22 + 三矩阵，逻辑冻结）。

        L1 成功终态 / L2 死状态 / L3 多出边 / L4 映射表锚点 / L5 BR 引用 /
        L6 表读写矩阵 / L7 映射行覆盖 / L8 降级回执 / L9 路径隔离 /
        L10 空矩阵② / L11 complex 打标 / L12 锚点可机检 / L13 占位符残留 /
        L21 图演化校验 / L22 回流熔断兜底 + 三张矩阵骨架。
        🔴 CRITICAL 未归零不得交付（playbook Step 4 门禁）。
        project_path: 本次调用生效的项目根。summary_path 给相对路径时按它解析
        （不再按进程 cwd）；省略则用进程启动时的 workspace_root。"""
        return run_lint(summary_path, workspace_root=_root(project_path))

    @mcp.tool()
    def draft_mapping(summary_path: str, project_path: str | None = None) -> dict:
        """意图注入映射表草稿（锚点脚本定位，逻辑冻结）。

        落点锚点由脚本真实定位（章节号/规则号/步骤号），禁止凭空手写——
        手写锚点是错位事故首要来源。你只需补审语义列后并入 summary.md。
        ⚠️ 未定位条目必须当场回问人类，禁止静默放过。
        project_path: 本次调用生效的项目根。summary_path 给相对路径时按它解析
        （不再按进程 cwd）；省略则用进程启动时的 workspace_root。"""
        return run_mapper(summary_path, workspace_root=_root(project_path))

    log.info("analysis tools registered (5 tools + 1 prompt)")
