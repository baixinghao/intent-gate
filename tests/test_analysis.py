"""需求解析引擎测试（fresh/resume 双路径，纯文件驱动）。

纪律验证：
  - 红灯门禁：核心逻辑断层必须 blocked + [BLOCKER] 首任务
  - 型态判定阈值与降级修正与 skill 一致
  - 中断续跑只读文件现场，prd_path 被忽略
  - 引擎产物不含任何红蓝对抗/评审内容
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.alignment.manager import AlignmentManager  # noqa: E402
from intent_gate.analysis.engine import analyze_request  # noqa: E402

# 红灯 PRD：前端防重无服务端机制 + "看情况"模糊分支 + 全文无失败路径
RED_PRD = """# 借款确认页需求

用户选完金额和期数后进入确认页，点击确认借款后提交。
提交按钮需要防重复点击，前端置灰即可。
提交成功后，看情况跳转到进度页或首页。
"""

# 复杂 PRD：三型态全达门槛 + 有失败路径（不该报失败流红灯）
COMPLEX_PRD = """# 工单系统

工单生命周期：草稿 → 待审核 → 处理中 → 已完成，另有已取消、已驳回两个终态。
提交时调用决策引擎和风控网关，写 MySQL 和 Redis，通过 MQ 通知第三方短信网关。
规则：如果金额超过一万走人工审批；若用户命中黑名单则拒绝；优先级按 VIP 等级排序；
当库存低于阈值时禁止提交。调用失败时自动重试三次，超时走降级方案，异常回滚。
"""


class AnalysisTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.prd = self.root / "prd.md"

    def tearDown(self):
        self._tmp.cleanup()

    def write_prd(self, text: str) -> str:
        self.prd.write_text(text, encoding="utf-8")
        return str(self.prd)


class FreshPathTests(AnalysisTestBase):
    def test_fresh_requires_prd_path(self):
        result = analyze_request(self.root, "new-feature")
        self.assertEqual(result["mode"], "fresh")
        self.assertFalse(result["ok"])

    def test_fresh_missing_prd_file(self):
        result = analyze_request(self.root, "new-feature", "不存在.md")
        self.assertFalse(result["ok"])

    def test_red_prd_blocked(self):
        """红灯门禁：防重无机制 + 看情况 + 无失败流 → blocked + BLOCKER。"""
        result = analyze_request(self.root, "withdraw", self.write_prd(RED_PRD))
        self.assertTrue(result["ok"])
        self.assertEqual(result["confidence"], "🔴")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["blocker_task"].startswith("[BLOCKER]"))
        gap_texts = [g["gap"] for g in result["gaps"]]
        self.assertTrue(any("防重" in g or "防重复" in g for g in gap_texts))
        self.assertTrue(any("看情况" in g for g in gap_texts))
        self.assertTrue(any("失败" in g for g in gap_texts))
        # 每个歧义点必须带至少 3 个建议选项（精准提问纪律）
        for g in result["gaps"]:
            self.assertGreaterEqual(len(g["suggested_options"]), 3)
        # draft 落盘
        draft = self.root / ".harness" / "requests" / "withdraw" / "_review" / "analysis-draft.md"
        self.assertTrue(draft.exists())
        draft_text = draft.read_text("utf-8")
        self.assertIn("confidence: 🔴", draft_text)
        self.assertIn("status: blocked", draft_text)

    def test_complex_prd_all_patterns_fired(self):
        result = analyze_request(self.root, "workorder", self.write_prd(COMPLEX_PRD))
        self.assertTrue(result["ok"])
        self.assertEqual(result["complexity"], "complex")
        self.assertIn("State-Driven", result["fired_thresholds"])
        self.assertIn("Process-Driven", result["fired_thresholds"])
        self.assertIn("Rule-Driven", result["fired_thresholds"])
        self.assertEqual(
            set(result["selected_tools"]),
            {"stateDiagram-v2", "sequenceDiagram", "decision_table"},
        )
        # 有失败路径，不许误报"只有成功没有失败流"
        self.assertFalse(any("失败路径完全缺失" in g["gap"] for g in result["gaps"]))

    def test_downgrade_low_intensity_multi_pattern(self):
        """降级修正：两种型态都低于触发门槛 → medium 而非 complex。"""
        prd = "订单有草稿和已完成两个状态。调用 Redis 缓存。如果超时则失败重试。"
        result = analyze_request(self.root, "f1", self.write_prd(prd))
        self.assertEqual(result["complexity"], "medium")
        self.assertEqual(result["selected_tools"], [])

    def test_no_redblue_content(self):
        """🔴 引擎产物与返回里不许出现红蓝对抗/评审内容。"""
        result = analyze_request(self.root, "withdraw", self.write_prd(RED_PRD))
        blob = str(result) + (self.root / ".harness" / "requests" / "withdraw"
                              / "_review" / "analysis-draft.md").read_text("utf-8")
        for forbidden in ("蓝军", "红军", "评审", "review-request", "review-findings"):
            self.assertNotIn(forbidden, blob)


class ResumePathTests(AnalysisTestBase):
    def _make_site(self) -> AlignmentManager:
        """用对齐子系统造一个真实工作现场：1 题未决 + 1 推断未确认。"""
        mgr = AlignmentManager(self.root)
        asyncio.run(mgr.dispatch_question(
            "order-refund", "退款后订单状态？", "📋",
            ["REFUNDING→REFUNDED", "直接REFUNDED", "独立退款单"],
        ))
        mgr.record_inference("order-refund", "删除逻辑", "软删", "addOrder 对称")
        return mgr

    def test_resume_reads_file_site_only(self):
        self._make_site()
        # prd_path 故意给个不存在的——resume 模式必须忽略它
        result = analyze_request(self.root, "order-refund", "不存在.md")
        self.assertEqual(result["mode"], "resume")
        status = result["intent_status"]
        self.assertEqual(status["pending_questions"], 1)
        self.assertEqual(status["pending_inferences"], 1)
        self.assertFalse(status["intent_aligned_ready"])
        actions = " ".join(result["next_actions"])
        self.assertIn("rebroadcast_pending", actions)
        self.assertIn("confirm_inferences", actions)

    def test_resume_reports_ready_when_all_settled(self):
        mgr = self._make_site()
        # 再补一题，然后把题答完、推断确认完（对话框兜底路径直接 resolve）
        asyncio.run(mgr.dispatch_question("order-refund", "第二题", "📋", ["a", "b", "c"]))
        store_pending = mgr.list_pending("order-refund")
        tokens = [re.search(r"HG-[0-9A-F]{4}", line).group(0)
                  for line in store_pending["questions"]]
        self.assertEqual(len(tokens), 2)
        for t in tokens:
            mgr.resolve_question("order-refund", t, "选1", "张三", "语义", "落点",
                                 source="dialog")
        mgr.confirm_inferences(
            "order-refund",
            [{"id": "INF-1", "approved": True, "interpretation": "x", "landing": "落点"}],
            "张三",
        )
        result = analyze_request(self.root, "order-refund")
        self.assertEqual(result["mode"], "resume")
        self.assertTrue(result["intent_status"]["intent_aligned_ready"])
        self.assertIn("生成产物", " ".join(result["next_actions"]))


class EngineHardeningTests(AnalysisTestBase):
    """引擎加固回归（审计修复）。"""

    def test_blocker_points_at_first_red_not_first_gap(self):
        """gaps[0] 是规则表扫描序的第一题（可能 🟡）——BLOCKER 必须指第一道 🔴。"""
        # 先命中 🟡 计算口径规则，再命中 🔴"看情况"规则
        prd = self.write_prd(
            "# 展示需求\n按最高优先级展示资方。看情况跳转到详情页。\n失败时重试。"
        )
        result = analyze_request(self.root, "f", prd)
        self.assertEqual(result["confidence"], "🔴")
        self.assertIn("看情况", result["blocker_task"])

    def test_record_rejects_gap_with_insufficient_options(self):
        """选项口径与 dispatch_question 一致：<3 选项且无推荐项必须拒收，
        否则账面上挂一道永远开不了闸的题。"""
        from intent_gate.analysis.engine import record_analysis

        bad = record_analysis(
            self.root, "f", ["State-Driven"], "complex", "🔴",
            gaps=[{"gap": "x", "severity": "🔴", "category": "🔧",
                   "options": ["a", "b"]}],
        )
        self.assertFalse(bad["ok"])
        # 有推荐项的点头题可放宽（与 dispatch 同口径）
        ok = record_analysis(
            self.root, "g", ["State-Driven"], "complex", "🔴",
            gaps=[{"gap": "x", "severity": "🔴", "category": "🔧",
                   "options": [], "recommend": "沿用既有分布式锁"}],
        )
        self.assertTrue(ok["ok"])


if __name__ == "__main__":
    unittest.main()
