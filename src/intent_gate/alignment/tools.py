"""意图对齐的 MCP 工具面（DESIGN.md §5）。

本模块的工具全程非阻塞——发题即返回，答案靠 collect 捡，对账靠会话恢复。
钉钉群分发/催单不在本服务：已剥离为姊妹篇 intent-gate-service 的 group_dispatch /
group_rebroadcast（与这里的 dispatch_question 共用同一落盘契约函数）。

🔴 项目根是**每次调用的参数**（project_path），不是启动时绑定的全局状态：
这 9 个工具全部落在 {项目根}/.harness/requests/ 下，而 MCP server 在宿主里是
全局单例、服务该宿主的所有工作区。省略 project_path 时回落启动时的
workspace_root；宿主服务多工作区（如 DSH 里换了工作区）时必须显式传。
不用"设一次全局生效"的开关——宿主会并发跑 subagent（生成 subagent、workflow
扇出），全局可变状态会让并发 agent 互相污染项目根，且极难复现。

宿主 agent 的标准用法（回合制）：
  分析遇红灯 → dispatch_question（登记+分发，秒回）→ 本轮对话结束
  下轮对话（或用户说"继续"）→ rebroadcast_pending（对账催单）
    → collect_answers（领取群/对话框答案）→ 注入图/规则
    → resolve_question（核销+写 alignment-log）
  AI 推断 → record_inference → 会话末 confirm_inferences（批量点头）
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..logging import get_logger
from ..workspace import resolve_root
from .manager import AlignmentManager

log = get_logger("alignment.tools")


def register_alignment_tools(mcp: FastMCP, align: AlignmentManager) -> None:
    """把意图对齐工具注册到既有 FastMCP 实例上。

    9 个工具统一接受 project_path（本次生效的项目根，省略则回落 align 绑定的根）。
    """
    default_root = align.workspace_root

    def _manager(project_path: str | None) -> AlignmentManager:
        """本次调用生效的管家：project_path 优先，缺省回落启动时的根。

        AlignmentManager 无状态（只持一个 Path），按需现造是零成本。
        """
        return AlignmentManager(resolve_root(project_path, default_root))

    @mcp.tool()
    async def dispatch_question(
        feature: str,
        gap: str,
        category: str = "📋",
        options: list[str] | None = None,
        recommend: str = "",
        targets: list[str] | None = None,
        at_user_ids: list[str] | None = None,
        severity: str = "🟡",
        coordinate: str | None = None,
        reflow: bool = False,
        project_path: str | None = None,
    ) -> dict:
        """登记并分发一道意图断层题（非阻塞，秒回）。

        feature 为 .harness/requests/ 下的需求名；category 用 📋(业务)/🔧(技术)
        标记归属角色；severity 用 🔴(核心逻辑断层，未消则 status=blocked)/🟡(局部歧义，默认)；
        options 至少 3 个互斥选项（有 recommend 推荐项可放宽）。
        coordinate 为图内坐标（如「状态机 X-->Y」「时序图 步骤3」「决策表 BR-01」），
        同一坐标只允许一道在飞题（重复登记被拒并返回 existing_token）。
        reflow=True 标记 Phase B 生成期带回的回流题：轮次按 draft frontmatter
        计数（同轮幂等），超 reflow_budget（缺省 2）拒登并返回 error=ESCALATE。
        本服务 single 通道：题目登记后返回给你，按精准提问格式向用户提问；
        要发钉钉群用姊妹篇 intent-gate-service 的 group_dispatch（同一落盘契约）。
        返回 token，后续凭 token 收答案。
        project_path: 本次调用生效的项目根（.harness 所在）。省略则用进程启动时的
        workspace_root；宿主服务多个工作区时必须显式传，否则账本会落到启动目录去。"""
        return await _manager(project_path).dispatch_question(
            feature, gap, category, options, recommend, targets, at_user_ids,
            severity, coordinate=coordinate, reflow=reflow,
        )

    @mcp.tool()
    def collect_answers(feature: str, project_path: str | None = None) -> list[dict]:
        """领取某需求 inbox 里的新答案（领取即归档，不重复下发）。

        拿到答案后你必须：注入对应图/规则 → 调 resolve_question 核销。
        只领不核销，题会一直挂在待决清单上（这是纪律，不是缺陷）。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传，否则读不到本项目的现场。"""
        return _manager(project_path).collect_answers(feature)

    @mcp.tool()
    def resolve_question(
        feature: str,
        token: str,
        answer: str,
        responder: str,
        interpretation: str,
        landing: str,
        source: str = "group",
        project_path: str | None = None,
    ) -> dict:
        """核销一题：checklist 打勾 + 写 alignment-log 标准流水（蓝军 R1 复核契约）。

        source: group(钉钉群)/dialog(对话框)/code(代码实证)。
        interpretation 是注入图/规则的语义；landing 必须精确到
        状态机边/时序图步骤/决策表规则号/字段——找不到落点就不要核销，先回问。
        成功核销的返回含 phase 块（相位机判定：align/generate/gate/deliverable）。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传，否则流水写错项目。"""
        return _manager(project_path).resolve_question(
            feature, token, answer, responder, interpretation, landing, source
        )

    @mcp.tool()
    def record_inference(
        feature: str, gap: str, conclusion: str, basis: str,
        project_path: str | None = None,
    ) -> dict:
        """登记一条 AI 公示推断（非核心主流程专用）。

        basis 是显式依据链（如"addOrder 对称逻辑"）。资金主流程/红线规则
        禁止纯推断，必须 dispatch_question 让人拍板。登记后进待确认清单，
        会话末 confirm_inferences 批量点头。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传。"""
        return _manager(project_path).record_inference(feature, gap, conclusion, basis)

    @mcp.tool()
    def confirm_inferences(
        feature: str, decisions: list[dict], confirmer: str,
        project_path: str | None = None,
    ) -> dict:
        """批量确认/驳回 AI 推断。decisions 元素：
        {"id": "INF-1", "approved": true, "interpretation": "...", "landing": "..."}
        approved 时 interpretation/landing 必填（确认即注入，注入必须有落点）。
        确认记录会写进 alignment-log，未确认的推断禁止标 intent_aligned: true。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传。"""
        return _manager(project_path).confirm_inferences(feature, decisions, confirmer)

    @mcp.tool()
    async def rebroadcast_pending(feature: str, project_path: str | None = None) -> dict:
        """会话恢复对账：返回未勾题清单（single 通道不发群，由宿主向用户逐题确认）。

        每次开工/用户说"继续"时先调它。钉钉群催单走姊妹篇 intent-gate-service 的
        group_rebroadcast（读同一份清单）。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传，否则对账的是别的项目。"""
        return await _manager(project_path).rebroadcast_pending(feature)

    @mcp.tool()
    def list_pending_questions(feature: str, project_path: str | None = None) -> dict:
        """自检：未勾题数、未确认推断数、已废弃数、是否具备标 intent_aligned 的条件。
        返回含 phase 块（相位机判定：align/generate/gate/deliverable）。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——🔴 省略时若该需求不在缺省根下，会返回"0 题"的空现场而非报错，
        看起来像"意图已齐"，务必配合 describe_workspace 确认根对了再信这个数。"""
        return _manager(project_path).list_pending(feature)

    @mcp.tool()
    def abandon_question(
        feature: str, token: str | None = None, reason: str = "",
        project_path: str | None = None,
    ) -> dict:
        """废弃题目——用户中途不想搞了的正式途径。

        token=None 废弃本需求全部未决题。废弃后不再催单、不阻断
        intent_aligned_ready，也不可再 resolve。比让用户直接删文件体面：
        废弃记录留在清单里，可追溯。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传。"""
        return _manager(project_path).abandon_question(feature, token, reason)

    @mcp.tool()
    def abandon_inference(
        feature: str, inference_id: str, reason: str = "",
        project_path: str | None = None,
    ) -> dict:
        """废弃一条未确认的 AI 推断（推断前提已不成立/用户弃用）。
        project_path: 本次调用生效的项目根（.harness 所在）；省略则用进程启动时的
        workspace_root——宿主服务多个工作区时必须显式传。"""
        return _manager(project_path).abandon_inference(feature, inference_id, reason)

    log.info("alignment tools registered (9 tools)")
