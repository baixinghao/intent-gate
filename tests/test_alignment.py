"""意图对齐子系统测试（file-in-the-loop 全链路，single 通道，无网络、无钉钉依赖）。

覆盖 DESIGN.md §3-§5 的纪律：
  先落盘后分发 / 非阻塞 / 白名单 fail-closed / token 认领 /
  collect 领取不重复 / resolve 核销写蓝军契约格式流水 /
  AI 推断登记-确认闭环 / rebroadcast 对账

群通道（钉钉出站/入站）的测试随剥离迁至 intent-gate-service/tests/test_bridge.py；
inbox 认领逻辑（file_inbound_reply）单源留在 intent_gate，故在此回归。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.alignment.manager import AlignmentManager, file_inbound_reply  # noqa: E402
from intent_gate.security import SenderPolicy  # noqa: E402


def run(coro):
    return asyncio.run(coro)


WHITELIST = SenderPolicy(frozenset({"zhangsan", "lisi"}))
OPTIONS = ["REFUNDING→成功后REFUNDED", "直接REFUNDED", "独立退款单跟踪"]


class AlignmentTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mgr = AlignmentManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def review_dir(self, feature="order-refund"):
        return self.root / ".harness" / "requests" / feature / "_review"

    def write_draft(self, feature="order-refund", with_mermaid=True):
        d = self.review_dir(feature)
        d.mkdir(parents=True, exist_ok=True)
        body = "# 草稿\n\n```mermaid\nstateDiagram-v2\n    A --> B: x (DB_INSERT)\n```\n" if with_mermaid \
            else "# 草稿\n\n无需画图：纯文案需求，不触发任何图。\n"
        (d / "analysis-draft.md").write_text(body, encoding="utf-8")

    def inbound(self, text, sender, nick):
        """intent-gate-service 入站回调的单源契约函数（single 通道测试直接调它）。"""
        return file_inbound_reply(self.root, WHITELIST, text, sender, nick)


class DispatchTests(AlignmentTestBase):
    def test_dispatch_writes_checklist_and_returns_to_host(self):
        """🔴 先落盘：登记后题目必须已在清单里；single 通道不发送，回给宿主提问。"""
        result = run(self.mgr.dispatch_question(
            "order-refund", "退款后订单状态？", "📋", OPTIONS, targets=["张三"]
        ))
        self.assertTrue(result["ok"])
        token = result["token"]
        checklist = (self.review_dir() / "pending-questions.md").read_text("utf-8")
        self.assertIn(f"[{token}]", checklist)
        self.assertIn("📋 退款后订单状态？", checklist)
        self.assertIn("4.其他", checklist)
        self.assertFalse(result["sent"])
        self.assertEqual(result["channel"], "single")

    def test_dispatch_rejects_bad_category(self):
        result = run(self.mgr.dispatch_question("f", "gap", "❓", OPTIONS))
        self.assertFalse(result["ok"])

    def test_dispatch_requires_three_options_without_recommend(self):
        """无推荐项必须给足 3 个选项（skill 精准提问纪律）。"""
        result = run(self.mgr.dispatch_question("f", "gap", "📋", ["只有", "两个"]))
        self.assertFalse(result["ok"])
        # 有 AI 推荐项的点头题可放宽
        result2 = run(self.mgr.dispatch_question(
            "f", "gap", "📋", recommend="推断走软删（依据: addOrder 对称）"
        ))
        self.assertTrue(result2["ok"])


class InboundAndCollectTests(AlignmentTestBase):
    def dispatch_one(self) -> str:
        return run(self.mgr.dispatch_question(
            "order-refund", "退款后订单状态？", "📋", OPTIONS
        ))["token"]

    def test_unauthorised_sender_rejected(self):
        token = self.dispatch_one()
        outcome = self.inbound(f"[{token}] 选1", "mallory", "马洛里")
        self.assertFalse(outcome["accepted"])

    def test_reply_without_token_rejected(self):
        self.dispatch_one()
        outcome = self.inbound("选1", "zhangsan", "张三")
        self.assertFalse(outcome["accepted"])

    def test_reply_unknown_token_rejected(self):
        self.dispatch_one()
        outcome = self.inbound("[HG-FFFF] 选1", "zhangsan", "张三")
        self.assertFalse(outcome["accepted"])

    def test_full_group_roundtrip(self):
        """发题 → 回复落盘 inbox → collect 领取 → resolve 核销+写流水，全链路。"""
        token = self.dispatch_one()
        outcome = self.inbound(
            f"@机器人 [{token}] 选1，先REFUNDING再REFUNDED", "zhangsan", "张三"
        )
        self.assertTrue(outcome["accepted"])
        # 答案已落盘 inbox
        inbox_files = list((self.review_dir() / "inbox").glob("*.md"))
        self.assertEqual(len(inbox_files), 1)

        answers = self.mgr.collect_answers("order-refund")
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["token"], token)
        self.assertEqual(answers[0]["responder"], "张三")
        self.assertIn("选1", answers[0]["answer"])
        # 领取即归档，第二次 collect 必须为空（防重复注入）
        self.assertEqual(self.mgr.collect_answers("order-refund"), [])

        self.write_draft()  # 核销前须先有绘图层草稿（门禁）
        result = self.mgr.resolve_question(
            "order-refund", token,
            answers[0]["answer"], "张三",
            "退款 → REFUNDING (REDIS_LOCK, PAYMENT_REFUND)，支付回调成功 → REFUNDED",
            "状态机 WITHDRAW_CONFIRM --> REFUNDING 边 / 时序图步骤 6",
            source="group",
        )
        self.assertTrue(result["ok"])
        # checklist 打勾
        checklist = (self.review_dir() / "pending-questions.md").read_text("utf-8")
        self.assertIn(f"- [x] [{token}]", checklist)
        # alignment-log 必须是蓝军 R1 契约格式
        log_text = (self.review_dir() / "alignment-log.md").read_text("utf-8")
        self.assertIn("## Q1", log_text)
        self.assertIn("- 提问：", log_text)
        self.assertIn("（选项:", log_text)  # 提问字段带选项摘要
        self.assertIn("（张三，钉钉群）", log_text)
        self.assertIn("- 注入解读：", log_text)
        self.assertIn("- 落点：", log_text)
        # 已核销的题不能再 resolve
        again = self.mgr.resolve_question(
            "order-refund", token, "x", "y", "z", "w"
        )
        self.assertFalse(again["ok"])

    def test_dialog_and_code_source_quote_formats(self):
        """对话框兜底与代码实证，人类原话字段两种合法形态。"""
        t1 = run(self.mgr.dispatch_question("f", "业务题", "📋", OPTIONS))["token"]
        t2 = run(self.mgr.dispatch_question("f", "技术题", "🔧", OPTIONS))["token"]
        self.write_draft("f")
        self.mgr.resolve_question("f", t1, "就选2", "张三",
                                  "注入语义A", "落点A", source="dialog")
        self.mgr.resolve_question("f", t2, "stock:{skuId}", "StockService.deduct",
                                  "注入语义B", "落点B", source="code")
        log_text = (self.review_dir("f") / "alignment-log.md").read_text("utf-8")
        self.assertIn("（张三，对话框）", log_text)
        self.assertIn("来源: 代码实证（StockService.deduct）", log_text)


class InferenceTests(AlignmentTestBase):
    def test_record_and_confirm_inference(self):
        rec = self.mgr.record_inference(
            "f", "删除订单的业务逻辑", "软删+状态机置CANCELLED",
            "addOrder 对称逻辑 + wiki 术语 cancel 定义"
        )
        self.assertTrue(rec["ok"])
        inf_id = rec["inference_id"]
        self.write_draft("f")
        result = self.mgr.confirm_inferences(
            "f",
            [{"id": inf_id, "approved": True,
              "interpretation": "DELETE 走软删，状态机 PAID-->CANCELLED",
              "landing": "状态机边 / 决策表 BR-03"}],
            confirmer="张三",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["failed"], [])
        log_text = (self.review_dir("f") / "alignment-log.md").read_text("utf-8")
        self.assertIn("[AI推断·依据:", log_text)
        self.assertIn("确认人: 张三", log_text)
        # 已结算的推断不可重复确认
        again = self.mgr.confirm_inferences(
            "f", [{"id": inf_id, "approved": True}], "张三"
        )
        self.assertEqual(len(again["failed"]), 1)

    def test_reject_inference(self):
        rec = self.mgr.record_inference("f", "gap", "结论", "依据")
        self.write_draft("f")
        result = self.mgr.confirm_inferences(
            "f", [{"id": rec["inference_id"], "approved": False}], "张三"
        )
        self.assertTrue(result["settled"][0]["approved"] is False)
        # 驳回不写 alignment-log
        self.assertFalse((self.review_dir("f") / "alignment-log.md").exists())

    def test_pending_inference_blocks_intent_aligned(self):
        self.mgr.record_inference("f", "gap", "结论", "依据")
        status = self.mgr.list_pending("f")
        self.assertFalse(status["intent_aligned_ready"])
        self.assertEqual(status["pending_inferences"], 1)

    def test_confirm_approved_requires_precise_landing(self):
        """🔴 确认即注入：approved 不给精确落点必须驳回（禁虚词落点）。"""
        rec = self.mgr.record_inference("f", "gap", "结论", "依据")
        self.write_draft("f")
        result = self.mgr.confirm_inferences(
            "f", [{"id": rec["inference_id"], "approved": True}], "张三"  # 缺 landing
        )
        self.assertEqual(len(result["failed"]), 1)
        # 落点不给，推断必须还是未结算状态（可补落点后重新确认）
        status = self.mgr.list_pending("f")
        self.assertEqual(status["pending_inferences"], 1)


class ResolveDisciplineTests(AlignmentTestBase):
    def test_resolve_rejects_empty_landing(self):
        """🔴 空解读/空落点禁止核销——防止静默丢弃意图。"""
        token = run(self.mgr.dispatch_question("f", "题", "📋", OPTIONS))["token"]
        self.write_draft("f")
        result = self.mgr.resolve_question("f", token, "选1", "张三", "", "")
        self.assertFalse(result["ok"])
        # 题必须还挂着，没打勾
        checklist = (self.review_dir("f") / "pending-questions.md").read_text("utf-8")
        self.assertIn(f"- [ ] [{token}]", checklist)


class RebroadcastTests(AlignmentTestBase):
    def test_rebroadcast_single_returns_list_without_sending(self):
        run(self.mgr.dispatch_question("f", "题一", "📋", OPTIONS))
        result = run(self.mgr.rebroadcast_pending("f"))
        self.assertEqual(result["pending"], 1)
        self.assertFalse(result["sent"])
        # single 通道把清单回给宿主逐题确认
        self.assertEqual(len(result["questions"]), 1)
        self.assertIn("[HG-", result["questions"][0])

    def test_rebroadcast_nothing_pending(self):
        result = run(self.mgr.rebroadcast_pending("f"))
        self.assertEqual(result["pending"], 0)


class ListPendingTests(AlignmentTestBase):
    def test_intent_aligned_ready_only_when_all_settled(self):
        token = run(self.mgr.dispatch_question("f", "题", "📋", OPTIONS))["token"]
        self.assertFalse(self.mgr.list_pending("f")["intent_aligned_ready"])
        self.inbound(f"[{token}] 选1", "lisi", "李四")
        answers = self.mgr.collect_answers("f")
        self.write_draft("f")
        self.mgr.resolve_question("f", token, answers[0]["answer"], "李四",
                                  "语义", "落点")
        status = self.mgr.list_pending("f")
        self.assertTrue(status["intent_aligned_ready"])


class LedgerHardeningTests(AlignmentTestBase):
    """账本层加固回归（审计修复）。"""

    def test_question_summary_strips_token_prefix(self):
        """"] " 切分法先命中 checkbox 的 "] "，把 [HG-XXXX] 残留进 gap——
        alignment-log 提问字段被污染。修复后 token 必须剥干净。"""
        token = run(self.mgr.dispatch_question(
            "order-refund", "退款后订单状态？", "📋", OPTIONS))["token"]
        store = self.mgr._store("order-refund")
        summary = store.question_summary(token)
        self.assertNotIn("[HG-", summary)
        self.assertNotIn("- [ ]", summary)
        self.assertIn("退款后订单状态？", summary)
        detail = store.question_detail(token)
        self.assertNotIn("[HG-", detail)
        self.assertIn("选项:", detail)

    def test_confirm_inference_requires_interpretation(self):
        """approved 缺 interpretation 时禁止静默回退为推断结论。"""
        inf_id = self.mgr.record_inference("f", "边界 gap", "推断结论", "依据链")["inference_id"]
        self.write_draft("f")
        result = self.mgr.confirm_inferences(
            "f", [{"id": inf_id, "approved": True, "landing": "§4 REFUNDING 边"}], "张三")
        self.assertEqual(result["settled"], [])
        self.assertEqual(result["failed"][0]["id"], inf_id)
        self.assertIn("interpretation", result["failed"][0]["reason"])

    def test_inbox_same_second_replies_do_not_overwrite(self):
        """同秒连发两条同 token 回复，文件名不得互相覆盖。"""
        token = run(self.mgr.dispatch_question("f", "题", "📋", OPTIONS))["token"]
        store = self.mgr._store("f")
        p1 = store.write_inbox(token, "选1", "zhangsan", "张三")
        p2 = store.write_inbox(token, "选2", "zhangsan", "张三")
        self.assertNotEqual(p1.name, p2.name)
        self.assertTrue(p1.exists() and p2.exists())

    def test_inbox_meta_fields_sanitised_against_forgery(self):
        """nick 带换行可在 front-matter 里伪造 sender 行冒充白名单成员。"""
        store = self.mgr._store("f2")
        store.write_inbox("HG-AAAA", "答案原话", "evil", "甲\nsender: admin")
        item = store.read_unconsumed()[0]
        self.assertEqual(item["sender"], "evil")  # 伪造行未生效
        self.assertNotIn("\n", item["nick"])
        self.assertEqual(item["answer"], "答案原话")  # 正文一字不改

    def test_unconsumed_inbox_blocks_ready(self):
        """inbox 躺着未领取答案时 list_pending 不得报就绪（与 resume 同口径）。"""
        token = run(self.mgr.dispatch_question("f", "题", "📋", OPTIONS))["token"]
        self.write_draft("f")
        self.mgr.resolve_question("f", token, "选1", "张三", "语义", "落点")
        self.assertTrue(self.mgr.list_pending("f")["intent_aligned_ready"])
        self.mgr._store("f").write_inbox(token, "迟到的回复", "zhangsan", "张三")
        lp = self.mgr.list_pending("f")
        self.assertFalse(lp["intent_aligned_ready"])
        self.assertEqual(lp["inbox_new_answers"], 1)


class DraftGateTests(AlignmentTestBase):
    """双层意图对齐·核销前草稿门禁：不许带着没动过笔的状态进入核销。"""

    def dispatch_one(self) -> str:
        return run(self.mgr.dispatch_question(
            "order-refund", "退款后订单状态？", "📋", OPTIONS
        ))["token"]

    def test_resolve_rejected_without_draft(self):
        """无草稿直接核销 → 拒收，题保持未决。"""
        token = self.dispatch_one()
        result = self.mgr.resolve_question(
            "order-refund", token, "选1", "张三", "语义", "落点")
        self.assertFalse(result["ok"])
        self.assertIn("草稿", result["reason"])
        checklist = (self.review_dir() / "pending-questions.md").read_text("utf-8")
        self.assertIn(f"- [ ] [{token}]", checklist)

    def test_resolve_passes_with_mermaid_draft(self):
        """草稿含 mermaid 块 → 放行核销。"""
        token = self.dispatch_one()
        self.write_draft()
        result = self.mgr.resolve_question(
            "order-refund", token, "选1", "张三", "语义", "落点")
        self.assertTrue(result["ok"])

    def test_resolve_passes_with_no_diagram_declaration(self):
        """草稿无 mermaid 但显式声明「无需画图」→ 放行（simple 场景豁免通道）。"""
        token = self.dispatch_one()
        self.write_draft(with_mermaid=False)
        result = self.mgr.resolve_question(
            "order-refund", token, "选1", "张三", "语义", "落点")
        self.assertTrue(result["ok"])

    def test_confirm_inferences_gated_the_same(self):
        """confirm_inferences 同样被门禁拦截/放行。"""
        inf_id = self.mgr.record_inference("f", "gap", "结论", "依据")["inference_id"]
        blocked = self.mgr.confirm_inferences(
            "f", [{"id": inf_id, "approved": True,
                   "interpretation": "x", "landing": "落点"}], "张三")
        self.assertFalse(blocked["ok"])
        self.assertIn("草稿", blocked["reason"])
        # 推断必须仍未结算（门禁拒收不产生副作用）
        self.assertEqual(self.mgr.list_pending("f")["pending_inferences"], 1)
        self.write_draft("f")
        passed = self.mgr.confirm_inferences(
            "f", [{"id": inf_id, "approved": True,
                   "interpretation": "x", "landing": "落点"}], "张三")
        self.assertTrue(passed["ok"])
        self.assertEqual(passed["failed"], [])


class CoordinateDedupTests(AlignmentTestBase):
    """同坐标查重：同一图内坐标只允许一道在飞题（防回流期重复开闸）。"""

    def test_same_coordinate_rejected_with_existing_token(self):
        r1 = run(self.mgr.dispatch_question(
            "f", "题一", "📋", OPTIONS, coordinate="状态机 A-->B"))
        self.assertTrue(r1["ok"])
        # 箭头写法不同（A -> B / A→B）规范化后仍视为同坐标
        for alias in ("状态机 A -> B", "状态机 A→B", " 状态机 A-->B "):
            r2 = run(self.mgr.dispatch_question(
                "f", "题二", "📋", OPTIONS, coordinate=alias))
            self.assertFalse(r2["ok"], f"别名 {alias!r} 未被查重拦截")
            self.assertEqual(r2["error"], "同坐标已有在飞题")
            self.assertEqual(r2["existing_token"], r1["token"])
        # 被拒的题不落盘：清单里仍只有一道
        checklist = (self.review_dir("f") / "pending-questions.md").read_text("utf-8")
        self.assertEqual(checklist.count("- [ ]"), 1)
        # 坐标渲染进清单行（追加在尾部，不破坏既有字段）
        self.assertIn("坐标: 状态机 A-->B", checklist)

    def test_different_or_no_coordinate_unaffected(self):
        r1 = run(self.mgr.dispatch_question(
            "f", "题一", "📋", OPTIONS, coordinate="状态机 A-->B"))
        self.assertTrue(r1["ok"])
        r2 = run(self.mgr.dispatch_question(
            "f", "题二", "📋", OPTIONS, coordinate="状态机 B-->C"))
        self.assertTrue(r2["ok"])
        r3 = run(self.mgr.dispatch_question("f", "题三", "📋", OPTIONS))
        self.assertTrue(r3["ok"])
        # 查重只扫未勾行：原题核销后同坐标可重登
        self.write_draft("f")
        self.mgr.resolve_question("f", r1["token"], "选1", "张三",
                                  "语义", "状态机 A-->B 边", source="dialog")
        r4 = run(self.mgr.dispatch_question(
            "f", "题四", "📋", OPTIONS, coordinate="状态机 A-->B"))
        self.assertTrue(r4["ok"])


class ReflowTests(AlignmentTestBase):
    """回流轮次计数（按轮幂等）+ 预算熔断 + 轮次关闭。"""

    def write_meta_draft(self, feature="f"):
        """带 frontmatter 的绘图层草稿（reflow 计数的落点；含 mermaid 过核销门禁）。"""
        d = self.review_dir(feature)
        d.mkdir(parents=True, exist_ok=True)
        (d / "analysis-draft.md").write_text(
            "---\n"
            f"feature: {feature}\n"
            "intent_aligned: false\n"
            "reflow_round: 0\n"
            "---\n\n"
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    A --> B: x (DB_INSERT)\n```\n",
            encoding="utf-8")

    def meta(self, feature="f"):
        return self.mgr._store(feature).read_draft_meta()

    def dispatch_reflow(self, feature="f"):
        return run(self.mgr.dispatch_question(
            feature, "生成期新发现的回流 gap", "📋", OPTIONS, reflow=True))

    def resolve(self, token, feature="f"):
        return self.mgr.resolve_question(
            feature, token, "选1", "张三", "语义", "状态机 A-->B 边", source="dialog")

    def test_reflow_same_round_counts_once(self):
        """③ 同轮两次 reflow dispatch 只计一轮（reflow_active=true 期间不自增）。"""
        self.write_meta_draft()
        r1 = self.dispatch_reflow()
        r2 = self.dispatch_reflow()
        self.assertTrue(r1["ok"] and r2["ok"])
        meta = self.meta()
        self.assertEqual(meta["reflow_round"], "1")
        self.assertEqual(meta["reflow_active"], "true")
        checklist = (self.review_dir("f") / "pending-questions.md").read_text("utf-8")
        self.assertEqual(checklist.count("回流: R1"), 2)

    def test_round_closes_after_resolve_then_second_round(self):
        """④ 全部回流题核销后 reflow_active=false；再次 reflow dispatch 开第二轮。"""
        self.write_meta_draft()
        t1 = self.dispatch_reflow()["token"]
        t2 = self.dispatch_reflow()["token"]
        self.resolve(t1)
        # 还有一道在飞回流题，轮次不关闭
        self.assertEqual(self.meta()["reflow_active"], "true")
        self.resolve(t2)
        self.assertEqual(self.meta()["reflow_active"], "false")
        r = self.dispatch_reflow()
        self.assertTrue(r["ok"])
        self.assertEqual(self.meta()["reflow_round"], "2")
        checklist = (self.review_dir("f") / "pending-questions.md").read_text("utf-8")
        self.assertIn("回流: R2", checklist)

    def test_abandon_also_closes_round(self):
        """轮次关闭对 abandon_question 同样生效（回流题被废弃殆尽）。"""
        self.write_meta_draft()
        self.dispatch_reflow()
        self.mgr.abandon_question("f")
        self.assertEqual(self.meta()["reflow_active"], "false")

    def test_budget_exceeded_escalate(self):
        """⑤ reflow_round 到 budget（缺省 2）后，开新一轮被拒且 error=ESCALATE。"""
        self.write_meta_draft()
        for _ in range(2):  # 两轮各自开闸→核销→关闭
            self.resolve(self.dispatch_reflow()["token"])
        self.assertEqual(self.meta()["reflow_round"], "2")
        r = self.dispatch_reflow()
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "ESCALATE")
        self.assertIn("reflow_budget", r["message"])
        # 熔断拒登不落盘、不改 frontmatter
        self.assertEqual(self.meta()["reflow_round"], "2")

    def test_human_raised_budget_allows_next_round(self):
        """⑥ 人类授权后在 draft frontmatter 手写提高 reflow_budget → 可再回流。"""
        self.write_meta_draft()
        for _ in range(2):
            self.resolve(self.dispatch_reflow()["token"])
        self.assertEqual(self.dispatch_reflow()["error"], "ESCALATE")
        self.mgr._store("f").update_draft_meta(reflow_budget=4)
        r = self.dispatch_reflow()
        self.assertTrue(r["ok"])
        self.assertEqual(self.meta()["reflow_round"], "3")

    def test_phase_block_in_list_pending_and_resolve(self):
        """⑦ list_pending 与 resolve_question 成功返回均含 phase 块。"""
        self.write_meta_draft()
        token = run(self.mgr.dispatch_question("f", "题", "📋", OPTIONS))["token"]
        res = self.resolve(token)
        self.assertTrue(res["ok"])
        self.assertIn("phase", res)
        # 题已核销、无 summary → 相位推进到 generate
        self.assertEqual(res["phase"]["phase"], "generate")
        lp = self.mgr.list_pending("f")
        self.assertIn("phase", lp)
        self.assertEqual(lp["phase"]["phase"], "generate")


if __name__ == "__main__":
    unittest.main()
