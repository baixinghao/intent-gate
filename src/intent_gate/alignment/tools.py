"""意图对齐的 MCP 工具面（DESIGN.md §5）。

本模块的工具全程非阻塞——发题即返回，答案靠 collect 捡，对账靠会话恢复。
钉钉群分发/催单不在本服务：已剥离为姊妹篇 intent-gate-service 的 group_dispatch /
group_rebroadcast（与这里的 dispatch_question 共用同一落盘契约函数）。

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
from .manager import AlignmentManager

log = get_logger("alignment.tools")


def register_alignment_tools(mcp: FastMCP, align: AlignmentManager) -> None:
    """把意图对齐工具注册到既有 FastMCP 实例上。"""

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
        返回 token，后续凭 token 收答案。"""
        return await align.dispatch_question(
            feature, gap, category, options, recommend, targets, at_user_ids,
            severity, coordinate=coordinate, reflow=reflow,
        )

    @mcp.tool()
    def collect_answers(feature: str) -> list[dict]:
        """领取某需求 inbox 里的新答案（领取即归档，不重复下发）。

        拿到答案后你必须：注入对应图/规则 → 调 resolve_question 核销。
        只领不核销，题会一直挂在待决清单上（这是纪律，不是缺陷）。"""
        return align.collect_answers(feature)

    @mcp.tool()
    def resolve_question(
        feature: str,
        token: str,
        answer: str,
        responder: str,
        interpretation: str,
        landing: str,
        source: str = "group",
    ) -> dict:
        """核销一题：checklist 打勾 + 写 alignment-log 标准流水（蓝军 R1 复核契约）。

        source: group(钉钉群)/dialog(对话框)/code(代码实证)。
        interpretation 是注入图/规则的语义；landing 必须精确到
        状态机边/时序图步骤/决策表规则号/字段——找不到落点就不要核销，先回问。
        成功核销的返回含 phase 块（相位机判定：align/generate/gate/deliverable）。"""
        return align.resolve_question(
            feature, token, answer, responder, interpretation, landing, source
        )

    @mcp.tool()
    def record_inference(
        feature: str, gap: str, conclusion: str, basis: str
    ) -> dict:
        """登记一条 AI 公示推断（非核心主流程专用）。

        basis 是显式依据链（如"addOrder 对称逻辑"）。资金主流程/红线规则
        禁止纯推断，必须 dispatch_question 让人拍板。登记后进待确认清单，
        会话末 confirm_inferences 批量点头。"""
        return align.record_inference(feature, gap, conclusion, basis)

    @mcp.tool()
    def confirm_inferences(
        feature: str, decisions: list[dict], confirmer: str
    ) -> dict:
        """批量确认/驳回 AI 推断。decisions 元素：
        {"id": "INF-1", "approved": true, "interpretation": "...", "landing": "..."}
        approved 时 interpretation/landing 必填（确认即注入，注入必须有落点）。
        确认记录会写进 alignment-log，未确认的推断禁止标 intent_aligned: true。"""
        return align.confirm_inferences(feature, decisions, confirmer)

    @mcp.tool()
    async def rebroadcast_pending(feature: str) -> dict:
        """会话恢复对账：返回未勾题清单（single 通道不发群，由宿主向用户逐题确认）。

        每次开工/用户说"继续"时先调它。钉钉群催单走姊妹篇 intent-gate-service 的
        group_rebroadcast（读同一份清单）。"""
        return await align.rebroadcast_pending(feature)

    @mcp.tool()
    def list_pending_questions(feature: str) -> dict:
        """自检：未勾题数、未确认推断数、已废弃数、是否具备标 intent_aligned 的条件。
        返回含 phase 块（相位机判定：align/generate/gate/deliverable）。"""
        return align.list_pending(feature)

    @mcp.tool()
    def abandon_question(feature: str, token: str | None = None, reason: str = "") -> dict:
        """废弃题目——用户中途不想搞了的正式途径。

        token=None 废弃本需求全部未决题。废弃后不再催单、不阻断
        intent_aligned_ready，也不可再 resolve。比让用户直接删文件体面：
        废弃记录留在清单里，可追溯。"""
        return align.abandon_question(feature, token, reason)

    @mcp.tool()
    def abandon_inference(feature: str, inference_id: str, reason: str = "") -> dict:
        """废弃一条未确认的 AI 推断（推断前提已不成立/用户弃用）。"""
        return align.abandon_inference(feature, inference_id, reason)

    log.info("alignment tools registered (9 tools)")
