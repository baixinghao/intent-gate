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
        # 核销前门禁：落一份含 mermaid 的绘图层草稿
        draft = (self.root / ".harness" / "requests" / "order-refund"
                 / "_review" / "analysis-draft.md")
        draft.write_text(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    A --> B: x (DB_INSERT)\n```\n",
            encoding="utf-8")
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


class RecordAnalysisLayerTagTests(AnalysisTestBase):
    """第二批·层标注 schema（错题集 2026-08-14 假探测案）：层是声明、TBDn 是凭据。
    校验口径：✏️ 无 tbd 拒收（绘图层必须带图内坐标）；📄 带 tbd 软归一
    （忽略+警告，防死门禁）；source 缺省 📄 向后兼容；非法值拒收。"""

    def _rec(self, gaps):
        from intent_gate.analysis.engine import record_analysis
        return record_analysis(
            self.root, "layer-f", ["State-Driven"], "complex", "🔴", gaps=gaps)

    def _gap(self, **kw):
        g = {"gap": "提交后跳转到哪（TBD1）", "severity": "🔴", "category": "📋",
             "options": ["a", "b", "c"]}
        g.update(kw)
        return g

    def test_drawing_layer_without_tbd_rejected(self):
        """✏️绘图层 缺 tbd 凭据 → 拒收（层是声明，TBDn 是图内坐标证据）。"""
        r = self._rec([self._gap(source="✏️")])
        self.assertFalse(r["ok"])
        self.assertIn("tbd", r["reason"])

    def test_drawing_layer_with_tbd_accepted_and_rendered(self):
        """✏️ + tbd → 收下，draft 渲染带层标注与占位凭据。"""
        r = self._rec([self._gap(source="✏️", tbd="TBD1")])
        self.assertTrue(r["ok"])
        draft = (self.root / ".harness" / "requests" / "layer-f"
                 / "_review" / "analysis-draft.md").read_text("utf-8")
        self.assertIn("[✏️绘图层]", draft)
        self.assertIn("占位: TBD1", draft)

    def test_reading_layer_with_tbd_soft_normalized(self):
        """📄 + tbd → 软归一：不拒收、忽略 tbd、warnings 留痕，draft 无占位行。"""
        r = self._rec([self._gap(source="📄", tbd="TBD9")])
        self.assertTrue(r["ok"])
        self.assertTrue(any("已忽略" in w for w in r["warnings"]))
        draft = (self.root / ".harness" / "requests" / "layer-f"
                 / "_review" / "analysis-draft.md").read_text("utf-8")
        self.assertNotIn("占位: TBD9", draft)

    def test_source_defaults_to_reading_layer(self):
        """向后兼容：旧调用无 source → 默认 📄，不炸。"""
        r = self._rec([self._gap()])
        self.assertTrue(r["ok"])
        draft = (self.root / ".harness" / "requests" / "layer-f"
                 / "_review" / "analysis-draft.md").read_text("utf-8")
        self.assertIn("[📄阅读层]", draft)

    def test_illegal_source_rejected(self):
        """source 只收 📄/✏️，其余拒收。"""
        r = self._rec([self._gap(source="🔵")])
        self.assertFalse(r["ok"])
        self.assertIn("source", r["reason"])

    def test_drawing_gap_text_without_tbd_warns(self):
        """✏️ gap 文本未携带 TBDn 编号 → 收下但 warnings 提醒（L20 靠文本对账）。"""
        g = self._gap(source="✏️", tbd="TBD3")
        g["gap"] = "提交后跳转到哪"
        r = self._rec([g])
        self.assertTrue(r["ok"])
        self.assertTrue(any("TBD3" in w for w in r["warnings"]))


class DocxInputTests(AnalysisTestBase):
    """输入契约加固：.docx 支持、相对路径解析、二进制/编码拒绝（全部带指引）。"""

    def _make_docx(self, paragraphs: list[str]) -> Path:
        """构造最小合法 .docx（zip 内放 word/document.xml）。"""
        from zipfile import ZipFile, ZIP_DEFLATED
        import xml.etree.ElementTree as ET

        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        body = ET.Element(f"{{{w}}}document")
        b = ET.SubElement(body, f"{{{w}}}body")
        for para in paragraphs:
            p = ET.SubElement(b, f"{{{w}}}p")
            t = ET.SubElement(p, f"{{{w}}}t")
            t.text = para
        p = self.root / "prd.docx"
        with ZipFile(p, "w", ZIP_DEFLATED) as zf:
            zf.writestr(
                "word/document.xml",
                ET.tostring(body, encoding="utf-8", xml_declaration=True),
            )
        return p

    def test_docx_analyzed(self):
        """docx 提取文本后正常走初筛（引擎自动选 markitdown/mammoth/stdlib）。"""
        p = self._make_docx(["提交按钮需要防重复点击，前端置灰即可。", "看情况跳转。"])
        result = analyze_request(self.root, "docx-feat", str(p))
        self.assertTrue(result["ok"])
        gap_texts = " ".join(g["gap"] for g in result["gaps"])
        self.assertIn("防重", gap_texts)
        self.assertIn("看情况", gap_texts)

    def test_relative_path_resolved_against_workspace_root(self):
        """相对路径按 workspace_root 解析（与 cwd 无关）。"""
        self.prd.write_text(RED_PRD, encoding="utf-8")
        result = analyze_request(self.root, "rel-feat", "prd.md")
        self.assertTrue(result["ok"])

    def test_binary_non_docx_rejected_with_guidance(self):
        """PDF 等二进制拒绝，报错带格式边界与转文本指引。"""
        p = self.root / "prd.pdf"
        p.write_bytes(b"%PDF-1.4\x00\x00fake")
        result = analyze_request(self.root, "pdf-feat", str(p))
        self.assertFalse(result["ok"])
        self.assertIn("二进制", result["reason"])
        self.assertIn(".docx", result["reason"])

    def test_non_utf8_rejected_with_guidance(self):
        """GBK 等非 UTF-8 文本拒绝，报错带转存指引。"""
        p = self.root / "prd-gbk.txt"
        p.write_bytes("中文需求".encode("gbk"))
        result = analyze_request(self.root, "gbk-feat", str(p))
        self.assertFalse(result["ok"])
        self.assertIn("UTF-8", result["reason"])

    def test_docx_engine_detect_and_broken_rejected(self):
        """引擎探测链存在（markitdown|mammoth，无 stdlib 兜底）；损坏 docx 拒绝带指引。"""
        from intent_gate.analysis.docx import _detect_engine

        self.assertIn(_detect_engine(), ("markitdown", "mammoth"))
        p = self.root / "broken.docx"
        # zip 魔数 + NUL：真实损坏 docx 是二进制（触发嗅探），但解不开
        p.write_bytes(b"PK\x03\x04\x00\x00\x00not a real docx")
        result = analyze_request(self.root, "broken-feat", str(p))
        self.assertFalse(result["ok"])
        self.assertIn("docx", result["reason"])

    def test_engine_missing_rejects_with_repair_instruction(self):
        """引擎缺失（环境损坏）→ 拒绝 + 修复指令，不做静默降级。"""
        from unittest import mock

        from intent_gate.analysis.docx import extract_text

        with mock.patch(
            "intent_gate.analysis.docx._detect_engine",
            side_effect=RuntimeError("no engine"),
        ):
            with self.assertRaises(ValueError) as ctx:
                extract_text(self.root / "x.docx")
        self.assertIn("mammoth", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
