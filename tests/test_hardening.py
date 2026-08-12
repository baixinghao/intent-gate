"""审计修复加固测试（路径逃逸 + 推断匹配/原子写/废弃途径）。

每条对应审计报告的一项，测试名里留了出处。
（闸门 S2/竞态、回调新鲜度、/events 鉴权、出站重试的加固测试随钉钉剥离
迁至 intent-gate-service/tests/test_hardening.py。）
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.alignment.manager import AlignmentManager  # noqa: E402
from intent_gate.analysis.engine import analyze_request  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ 路径逃逸
class PathEscapeTests(unittest.TestCase):
    def test_engine_rejects_evil_feature(self):
        with tempfile.TemporaryDirectory() as tmp:
            prd = Path(tmp) / "prd.md"
            prd.write_text("需求", encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze_request(tmp, "../../evil", str(prd))


# ------------------------------------------------- 推断匹配/原子写/废弃
class AlignmentHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mgr = AlignmentManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def review(self, feature="f"):
        return self.root / ".harness" / "requests" / feature / "_review"

    def write_draft(self, feature="f"):
        """核销前门禁：落一份含 mermaid 草稿的 analysis-draft.md。"""
        d = self.review(feature)
        d.mkdir(parents=True, exist_ok=True)
        (d / "analysis-draft.md").write_text(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    A --> B: x (DB_INSERT)\n```\n",
            encoding="utf-8")

    def test_settle_exact_match_not_substring(self):
        """审计修复实证：INF-1 不许误中正文提到 'INF-1' 的行，也不误中 INF-10。"""
        store = self.mgr._store("f")
        store.add_inference("删除逻辑", "软删", "addOrder 对称")
        store.add_inference("第二条", "结论2", "依据里提到 INF-1 的结论")  # INF-2
        # 手工补一行 INF-10，模拟编号到两位数
        with store.inference_file.open("a", encoding="utf-8") as fp:
            fp.write("- [ ] INF-10 第十条 | 推断: x | 依据: y | 登记: t\n")
        self.assertTrue(store.settle_inference("INF-1", True, "张三"))
        text = store.inference_file.read_text("utf-8")
        lines = text.splitlines()
        inf1 = next(l for l in lines if " INF-1 " in l and "INF-10" not in l)
        inf2 = next(l for l in lines if l.startswith("- [x] INF-2") or " INF-2 " in l)
        inf10 = next(l for l in lines if " INF-10 " in l)
        self.assertTrue(inf1.startswith("- [x]"))   # INF-1 已结算
        self.assertTrue(inf2.startswith("- [ ]"))   # INF-2（正文含 INF-1）未被误伤
        self.assertTrue(inf10.startswith("- [ ]"))  # INF-10 未被误伤

    def test_atomic_write_leaves_no_tmp(self):
        store = self.mgr._store("f")
        run(self.mgr.dispatch_question("f", "题", "📋", ["a", "b", "c"]))
        token = next(iter(store.pending_tokens()))
        store.check_off(token, "答完")
        self.assertEqual(list(self.review().glob("*.tmp")), [])

    def test_abandon_question_flow(self):
        """废弃途径：单题废弃 → 不再阻断就绪；废弃后不可 resolve；全量废弃。"""
        t1 = run(self.mgr.dispatch_question("f", "题一", "📋", ["a", "b", "c"]))["token"]
        t2 = run(self.mgr.dispatch_question("f", "题二", "📋", ["a", "b", "c"]))["token"]

        result = self.mgr.abandon_question("f", t1, "用户不想搞这题了")
        self.assertTrue(result["ok"])
        checklist = (self.review() / "pending-questions.md").read_text("utf-8")
        self.assertIn(f"- [~] [{t1}]", checklist)
        # 废弃的题不可再 resolve（先有草稿，确保拦它的是"已废弃"而非门禁）
        self.write_draft()
        self.assertFalse(self.mgr.resolve_question("f", t1, "x", "y", "z", "w")["ok"])
        # 还剩一题未决 → 不就绪
        self.assertFalse(self.mgr.list_pending("f")["intent_aligned_ready"])
        # 全量废弃（token=None）
        result2 = self.mgr.abandon_question("f")
        self.assertEqual(result2["abandoned"], 1)  # 只剩 t2 未决
        status = self.mgr.list_pending("f")
        self.assertTrue(status["intent_aligned_ready"])
        self.assertEqual(status["abandoned_questions"], 2)
        # 已废弃的再废弃报错
        self.assertFalse(self.mgr.abandon_question("f", t2)["ok"])

    def test_abandon_inference_and_id_never_reused(self):
        r1 = self.mgr.record_inference("f", "gap1", "c1", "b1")
        r2 = self.mgr.record_inference("f", "gap2", "c2", "b2")
        self.assertTrue(self.mgr.abandon_inference("f", r1["inference_id"], "前提不成立")["ok"])
        self.assertEqual(self.mgr.list_pending("f")["pending_inferences"], 1)
        # 废弃的不可再确认（先有草稿，确保拦它的是"已废弃"而非门禁）
        self.write_draft()
        result = self.mgr.confirm_inferences("f", [{"id": r1["inference_id"], "approved": True, "landing": "x"}], "张三")
        self.assertEqual(len(result["failed"]), 1)
        # 编号不复用：下一个必须是 INF-3
        r3 = self.mgr.record_inference("f", "gap3", "c3", "b3")
        self.assertEqual(r3["inference_id"], "INF-3")


if __name__ == "__main__":
    unittest.main()
