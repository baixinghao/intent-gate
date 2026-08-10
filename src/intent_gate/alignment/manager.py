"""AlignmentManager：意图对齐的业务逻辑层（single 通道版）。

三级（含推断共四级）对齐漏斗里，本层只负责「对话框兜底」通道的分发/收集/对账；
「群里@人」通道（含钉钉出站/入站）已剥离为姊妹篇 intent-gate-service（独立 MCP 服务），
它通过本模块的两个**单源契约函数**与 intent-gate 集成，绝不另起契约实现：

  - register_question   校验 + 先落盘（intent-gate-service 的 group_dispatch 也走这里）
  - file_inbound_reply  群回复认领 + 落盘 inbox/（intent-gate-service 的入站回调用它）

「代码实证」由宿主 agent 自行查 codegraph 后走 resolve_question 落账。

🔴 非阻塞铁律（DESIGN.md §3）：本层没有任何一个方法会等待人类。
   dispatch 发题即返回；答案经 inbox/ 落盘；宿主靠 collect 领取、
   靠 resolve 核销；漏了靠 rebroadcast 在会话恢复时催单。
"""

from __future__ import annotations

import time
from pathlib import Path

from ..logging import get_logger
from ..models import new_gate_token
from ..security import SenderPolicy, parse_reply
from .store import PendingQuestion, ReviewStore

log = get_logger("alignment")


# ---------------------------------------------------------------- 契约函数（intent-gate-service 复用）
def register_question(
    store: ReviewStore,
    gap: str,
    category: str = "📋",
    options: list[str] | None = None,
    recommend: str = "",
    targets: list[str] | None = None,
    severity: str = "🟡",
) -> dict:
    """校验 + 落盘一道意图断层题（🔴 先落盘后发送的唯一入口）。

    intent-gate 的 dispatch_question 与 intent-gate-service 的 group_dispatch 都走这里——
    校验规则与清单格式两边不分叉。成功返回 {"ok", "token", "question"}；
    拒收返回 {"ok": False, "reason"}（不落盘）。
    """
    if category not in ("📋", "🔧"):
        return {"ok": False, "reason": "category 只支持 📋(业务) 或 🔧(技术)"}
    if severity not in ("🔴", "🟡"):
        return {"ok": False, "reason": "severity 只支持 🔴(核心断层) 或 🟡(局部歧义)"}
    opts = list(options or [])
    if len(opts) < 3 and not recommend:
        # DESIGN.md §3：每题至少 3 个互斥选项 + 固定"4.其他"；
        # 有 AI 推荐项的点头题可放宽（推荐项即选项1，其余由宿主补2-3个也行，
        # 但这里给宿主留了带 recommend 免三选项的口子，防止形式主义凑数）
        return {"ok": False, "reason": "无推荐项的题目至少给 3 个候选选项"}
    token = new_gate_token(store.pending_tokens() | store.pending_tokens(checked=True))
    q = PendingQuestion(
        token=token,
        gap=gap,
        category=category,
        severity=severity,
        options=opts,
        recommend=recommend,
        targets=list(targets or []),
    )
    store.add_pending(q)  # 🔴 先落盘，之后任何通道才允许发送
    return {"ok": True, "token": token, "question": q}


def _find_feature_by_token(workspace_root: str | Path, token: str) -> ReviewStore | None:
    """按 token 反查需求目录。token 只有 4 位十六进制，理论上可能跨需求撞号，
    撞上时取先匹配到的（概率极低，且核销后不再匹配）。"""
    requests_dir = Path(workspace_root) / ".harness" / "requests"
    if not requests_dir.exists():
        return None
    for child in sorted(requests_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            store = ReviewStore(workspace_root, child.name)
        except ValueError:
            continue
        if store.has_pending(token):
            return store
    return None


def file_inbound_reply(
    workspace_root: str | Path,
    policy: SenderPolicy,
    raw_text: str,
    sender_staff_id: str | None,
    sender_nick: str | None,
) -> dict:
    """群回复认领落盘（intent-gate-service 入站回调的唯一入口）：
    验白名单 → 解析 token → 找需求 → 答案落盘 inbox/。

    答案落盘后由宿主 agent 调 collect_answers 领取，本函数不做注入——
    注入语义（改图/补分支）是宿主的活，这里只管递送原话。
    """
    if not policy.is_allowed(sender_staff_id):
        log.warning("alignment reply rejected: sender %r not whitelisted", sender_staff_id)
        return {"accepted": False, "reason": "sender not authorised"}
    token, answer = parse_reply(raw_text)
    if token is None:
        return {"accepted": False, "reason": "回复缺少 [HG-XXXX] 关联令牌"}
    store = _find_feature_by_token(workspace_root, token)
    if store is None:
        return {"accepted": False, "token": token,
                "reason": f"token {token} 不在任何需求的待决清单里（可能已核销）"}
    path = store.write_inbox(
        token, answer, str(sender_staff_id), sender_nick or str(sender_staff_id)
    )
    log.info("answer filed feature=%s token=%s by=%r", store.feature, token, sender_nick)
    return {"accepted": True, "token": token, "feature": store.feature,
            "file": path.name}


class AlignmentManager:
    """意图对齐管家（single 通道：题目只回给宿主，由对话框前的人回答）。

    参数：
      workspace_root — 项目根目录（.harness 所在），MCP stdio 通常跑在项目根

    钉钉群通道已剥离：intent-gate-service 的 group_dispatch/group_rebroadcast 与入站
    回调通过本模块的 register_question/file_inbound_reply 复用同一文件契约。
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root)

    def _store(self, feature: str) -> ReviewStore:
        return ReviewStore(self._root, feature)

    # ------------------------------------------------------------- 发题
    async def dispatch_question(
        self,
        feature: str,
        gap: str,
        category: str = "📋",
        options: list[str] | None = None,
        recommend: str = "",
        targets: list[str] | None = None,
        at_user_ids: list[str] | None = None,
        severity: str = "🟡",
    ) -> dict:
        """登记一道意图断层题（先落盘），返回给宿主按精准提问格式向用户提问。

        category: "📋"（业务题）或 "🔧"（技术题）。
        severity: "🔴"（核心逻辑断层，未消则 status=blocked）或 "🟡"（局部歧义）。
        recommend: AI 推荐项+推断依据（核心主流程题用它实现"点头式确认"）。
        钉钉群分发走姊妹篇 intent-gate-service 的 group_dispatch（同一契约函数落盘）。
        """
        store = self._store(feature)
        reg = register_question(store, gap, category, options, recommend, targets, severity)
        if not reg["ok"]:
            return reg
        token = reg["token"]
        log.info("question dispatched feature=%s token=%s gap=%.60r", feature, token, gap)
        return {
            "ok": True,
            "token": token,
            "channel": "single",
            "sent": False,
            "hint": "题目已登记，请宿主 agent 按精准提问格式向用户提问；"
            "拿到答案后调 resolve_question 核销",
        }

    # ------------------------------------------------------------- 收题
    def collect_answers(self, feature: str) -> list[dict]:
        """领取 inbox 里的新答案（领取即归档，防重复）。

        宿主拿到答案后负责：注入图/规则 → 调 resolve_question 核销+写流水。
        只领取不核销 = 题还在清单上挂着，这是有意的——
        答案没被消化就不算数（skill 纪律：禁止只注入不落地/静默丢弃）。
        """
        store = self._store(feature)
        answers = []
        for item in store.read_unconsumed():
            if not store.has_pending(item["token"]):
                # 题已核销或 token 不属于本需求，归档但不下发
                store.mark_consumed(item["file"])
                continue
            store.mark_consumed(item["file"])
            answers.append(
                {
                    "token": item["token"],
                    "answer": item["answer"],
                    "responder": item["nick"],
                    "question": store.question_summary(item["token"]),
                }
            )
        log.info("answers collected feature=%s count=%d", feature, len(answers))
        return answers

    def resolve_question(
        self,
        feature: str,
        token: str,
        answer: str,
        responder: str,
        interpretation: str,
        landing: str,
        source: str = "group",
    ) -> dict:
        """核销一题：checklist 打勾 + alignment-log 追加标准流水条目。

        source: "group"（群回复，经 intent-gate-service 入站）| "dialog"（对话框兜底）
        | "code"（代码实证）。人类原话字段按 DESIGN.md §4.2 三种合法形态拼装。
        """
        store = self._store(feature)
        if not store.has_pending(token):
            return {"ok": False, "reason": f"token {token} 不在待决清单（可能已核销）"}
        # 🔴 skill 硬纪律：注入必须有解读与精确落点（状态机边/时序图步骤/
        # 决策表规则号/字段）。空解读空落点 = 静默丢弃意图，禁止核销，先回问。
        if not interpretation.strip() or not landing.strip():
            return {"ok": False,
                    "reason": "interpretation/landing 不能为空——注入必须有语义解读"
                              "与精确落点；找不到落点请回问人类，禁止静默核销"}
        if source == "code":
            human_quote = f"来源: 代码实证（{responder}）"
        elif source == "dialog":
            human_quote = f"{answer}（{responder}，对话框）"
        else:
            human_quote = f"{answer}（{responder}，钉钉群）"
        gap = store.question_summary(token)
        n = store.append_alignment_log(
            gap=gap,
            question=store.question_detail(token),  # gap + 选项摘要，符合 skill 契约
            human_quote=human_quote,
            interpretation=interpretation,
            landing=landing,
        )
        store.check_off(token, f"{responder}: {answer[:60]}")
        log.info("question resolved feature=%s token=%s source=%s", feature, token, source)
        return {"ok": True, "token": token, "log_entry": f"Q{n}"}

    # ------------------------------------------------------------- 推断
    def record_inference(self, feature: str, gap: str, conclusion: str, basis: str) -> dict:
        """登记 AI 公示推断（漏斗第②级）。非核心主流程才允许走这里——
        资金主流程/红线规则禁止纯推断，必须 dispatch_question 让人拍板。"""
        inf_id = self._store(feature).add_inference(gap, conclusion, basis)
        log.info("inference recorded feature=%s id=%s gap=%.60r", feature, inf_id, gap)
        return {"ok": True, "inference_id": inf_id,
                "hint": "已进待确认清单，会话末调 confirm_inferences 批量确认"}

    def confirm_inferences(
        self,
        feature: str,
        decisions: list[dict],
        confirmer: str,
    ) -> dict:
        """批量确认/驳回推断。decisions 元素: {"id": "INF-1", "approved": true,
        "interpretation": "...", "landing": "..."}（approved 时后两个字段必填——
        确认即注入，注入必须有解读与落点）。"""
        store = self._store(feature)
        settled, failed = [], []
        for d in decisions:
            inf_id = str(d.get("id", ""))
            approved = bool(d.get("approved"))
            info = store.inference_summary(inf_id)
            if not info:
                failed.append({"id": inf_id, "reason": "查无此推断"})
                continue
            # 🔴 确认即注入：approved 必须给语义解读与精确落点。
            # interpretation 缺省时静默回退为推断结论 = 把"待确认的猜测"直接
            # 洗成"注入语义"，与 docstring 的必填约定相悖，必须拒收。
            if approved and not str(d.get("interpretation", "")).strip():
                failed.append({"id": inf_id,
                               "reason": "approved 时 interpretation 必填——确认即注入，"
                                         "注入必须有语义解读，禁止静默回退为推断结论"})
                continue
            # "（宿主补落点）"这类虚词是 skill 明令禁止的
            # （落点必须精确到边/步骤/规则号/字段）
            if approved and not str(d.get("landing", "")).strip():
                failed.append({"id": inf_id,
                               "reason": "approved 时 landing 必填且须精确——"
                                         "找不到落点先回问，禁止虚词落点"})
                continue
            if not store.settle_inference(inf_id, approved, confirmer):
                failed.append({"id": inf_id, "reason": "已确认过，不可重复结算"})
                continue
            if approved:
                # 确认的推断 → 追加 alignment-log（[AI推断] 合法形态 + 确认记录）
                n = store.append_alignment_log(
                    gap=info["gap"],
                    question=f"AI 公示推断：{info['conclusion']}",
                    human_quote=(
                        f"[AI推断·依据: {info['basis']}]"
                        f"（确认人: {confirmer}，{time.strftime('%Y-%m-%d %H:%M')}）"
                    ),
                    interpretation=str(d["interpretation"]).strip(),  # 已在上方强制非空
                    landing=d["landing"],
                )
                settled.append({"id": inf_id, "approved": True, "log_entry": f"Q{n}"})
            else:
                settled.append({"id": inf_id, "approved": False})
        return {"ok": True, "settled": settled, "failed": failed}

    # ------------------------------------------------------------- 对账
    async def rebroadcast_pending(self, feature: str) -> dict:
        """会话恢复对账：返回未勾题清单，宿主向用户逐题确认。

        钉钉群催单走姊妹篇 intent-gate-service 的 group_rebroadcast（读同一份清单）。"""
        store = self._store(feature)
        lines = store.unchecked_lines()
        if not lines:
            return {"ok": True, "pending": 0, "sent": False, "hint": "无未决题"}
        return {"ok": True, "pending": len(lines), "sent": False,
                "questions": lines,
                "hint": "single 通道不发群，请宿主向用户逐题确认；"
                "已装 intent-gate-service 可改用其 group_rebroadcast 发群催单"}

    # ------------------------------------------------------------- 废弃
    def abandon_question(self, feature: str, token: str | None = None, reason: str = "") -> dict:
        """废弃题目（用户中途弃用的正式途径，比删文件体面）。

        token=None = 废弃本需求全部未决题。废弃后该题不再要求回答、
        不阻断 intent_aligned_ready，也不可再 resolve。"""
        count = self._store(feature).abandon_pending(token, reason)
        log.info("question abandoned feature=%s token=%s count=%d", feature, token, count)
        if token is not None and count == 0:
            return {"ok": False, "reason": f"未找到未决题 {token}（可能已核销/已废弃）"}
        return {"ok": True, "abandoned": count}

    def abandon_inference(self, feature: str, inference_id: str, reason: str = "") -> dict:
        """废弃一条未确认的 AI 推断（前提已不成立/用户弃用）。"""
        ok = self._store(feature).abandon_inference(inference_id, reason)
        log.info("inference abandoned feature=%s id=%s ok=%s", feature, inference_id, ok)
        return {"ok": ok, "reason": None if ok else f"未找到未确认推断 {inference_id}"}

    def list_pending(self, feature: str) -> dict:
        """自检/汇报：未勾题、未确认推断各有多少 + skill 词表的 frontmatter 建议。"""
        store = self._store(feature)
        pending = store.unchecked_lines()
        red_pending = len(store.pending_red_lines())
        inf_pending = sum(
            1
            for line in store._read_lines(store.inference_file)
            if line.startswith("- [ ]")
        )
        abandoned = sum(
            1
            for line in store._read_lines(store.pending_file)
            if line.startswith("- [~]")
        )
        # inbox 里躺着未领取的答案时，意图不算齐（答案还没注入图/规则）——
        # 口径与 analyze_request(resume) 一致，两边分叉会让一边报就绪一边报欠账
        inbox_new = (
            len(list(store.inbox_dir.glob("*.md"))) if store.inbox_dir.exists() else 0
        )
        ready = len(pending) == 0 and inf_pending == 0 and inbox_new == 0
        # 状态词表与 doc-analysis playbook §3.1 一致（失真点 Z8 修复）：
        # blocked=🔴未消除；draft=对齐中；pending_review=产物待审；approved=获准开工
        if red_pending:
            status = "blocked"
        elif not ready:
            status = "draft"
        else:
            status = "pending_review"
        return {
            "ok": True,
            "feature": feature,
            "pending_questions": len(pending),
            "pending_red": red_pending,
            "questions": pending,
            "pending_inferences": inf_pending,
            "abandoned_questions": abandoned,
            "inbox_new_answers": inbox_new,
            "intent_aligned_ready": ready,
            "frontmatter_advice": {
                "status": status,
                "intent_aligned": ready,
                "note": "approved 只能由人类/评审授予，MCP 永不自授",
            },
        }
