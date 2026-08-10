"""保真测试（失真点 Z4/Z5/Z8 修复验证）：playbook 全文、vendored lint/mapper、
record_analysis 落账、severity → skill 状态词表。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.alignment.manager import AlignmentManager  # noqa: E402
from intent_gate.analysis.engine import analyze_request, record_analysis  # noqa: E402
from intent_gate.analysis.lint import run_lint  # noqa: E402
from intent_gate.analysis.mapper import run_mapper  # noqa: E402


def run(coro):
    return asyncio.run(coro)


PLAYBOOK = Path(__file__).resolve().parent.parent / "src" / "intent_gate" / "analysis" / "playbook.md"


class PlaybookFidelityTests(unittest.TestCase):
    """失真点 Z4：playbook 必须全文保真（skill + agent 双来源），且物理切除红蓝。"""

    def setUp(self):
        self.text = PLAYBOOK.read_text("utf-8")

    def test_skill_core_sections_present(self):
        for marker in (
            "意图置信度评估", "Step 0.5", "歧义点发现规则（九类）", "精准提问格式",
            "降级确认回执", "型态判定标准", "降级修正", "direction LR", "autonumber",
            "条件-动作矩阵表", "意图注入映射表", "技术打标强校验",
        ):
            self.assertIn(marker, self.text, f"playbook 缺失: {marker}")

    def test_nine_gap_categories_present(self):
        for marker in ("字段语义歧义", "第三方接口契约不明", "术语对齐",
                       "缺失的异常/回滚路径", "缺失的条件组合", "资金安全"):
            self.assertIn(marker, self.text)

    def test_agent_constraints_merged(self):
        """agent 约束合并进 playbook 后不许错漏。"""
        for marker in ("主对话层执行", "不生成业务代码", "上下文加载",
                       "不修改 specs/ 和 wiki/"):
            self.assertIn(marker, self.text)

    def test_redblue_physically_cut(self):
        for forbidden in ("蓝军", "红军", "review-findings", "review-request",
                          "评审请求", "开单"):
            self.assertNotIn(forbidden, self.text)

    def test_terminology_degradation_policy(self):
        """无 wiki 时的术语基准降级策略（不做重抽取，纪律不降级）。"""
        self.assertIn("术语基准", self.text)
        self.assertIn("新造词", self.text)
        self.assertIn("代码检索", self.text)


class LintFidelityTests(unittest.TestCase):
    """失真点 Z5：vendored lint 与原始工具同逻辑。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.summary_dir = self.root / ".harness" / "requests" / "f"
        self.summary_dir.mkdir(parents=True)
        (self.summary_dir / "_review").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write_summary(self, text: str) -> str:
        p = self.summary_dir / "summary.md"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_l1_no_success_terminal_critical(self):
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    DRAFT --> PENDING: 提交 (DB_INSERT)\n```\n"
        )
        result = run_lint(path)
        self.assertGreaterEqual(result["critical"], 1)
        self.assertTrue(any(f["rule"] == "L1" for f in result["findings"]))
        self.assertFalse(result["deliverable"])
        # lint-report 落盘
        self.assertTrue((self.summary_dir / "_review" / "lint-report.md").exists())

    def test_clean_state_machine_passes_l1(self):
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    DRAFT --> SUCCESS: 提交 (DB_INSERT)\n```\n"
        )
        result = run_lint(path)
        self.assertFalse(any(f["rule"] == "L1" for f in result["findings"]))

    def test_chinese_state_names_are_parsed(self):
        """中文状态名必须解析出边，L1/L2/L3 不许失明（lint.py:55 回归）。"""
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    待支付 --> 已完成: 支付成功 (DB_UPDATE)\n"
            "    待支付 --> 已取消: 超时关单 (REDIS_UNLOCK)\n```\n"
        )
        result = run_lint(path)
        self.assertEqual(result["edges"], 2)
        self.assertFalse(any(f["rule"] == "L0" for f in result["findings"]))
        self.assertFalse(any(f["rule"] == "L1" for f in result["findings"]))  # 已完成 = 成功终态

    def test_chinese_state_machine_missing_terminal_hits_l1(self):
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    待支付 --> 支付中: 提交 (DB_UPDATE)\n```\n"
        )
        result = run_lint(path)
        self.assertTrue(any(f["rule"] == "L1" for f in result["findings"]))
        self.assertFalse(result["deliverable"])

    def test_unparseable_state_machine_triggers_l0(self):
        """有状态机块却零边 → L0 CRITICAL 报警，绝不静默放行。"""
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    \"待支付\" --> \"已完成\": 支付成功\n```\n"
        )
        result = run_lint(path)
        self.assertEqual(result["edges"], 0)
        self.assertTrue(any(f["rule"] == "L0" and f["level"] == "CRITICAL"
                            for f in result["findings"]))
        self.assertFalse(result["deliverable"])

    def test_indented_br_definition_counts_as_defined(self):
        """缩进的决策表定义行同样算定义（lint L5 与 mapper 口径一致，lint.py:115 回归）。"""
        path = self.write_summary(
            "# 报告\n\n## 6. 业务决策规则\n\n"
            "  | BR-01 | 已支付 | 退款→REFUNDING |\n\n正文引用 BR-01 规则。\n"
        )
        result = run_lint(path)
        self.assertFalse(any(f["rule"] == "L5" for f in result["findings"]))

    def test_l2_dead_state_major(self):
        """有出边的终态不算死状态；只进不出的状态必须命中 L2。"""
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    DRAFT --> SUCCESS: 提交 (DB_INSERT)\n"
            "    SUCCESS --> [*]\n    DRAFT --> DEAD: 撤回 (DB_UPDATE)\n```\n"
        )
        result = run_lint(path)
        l2 = [f for f in result["findings"] if f["rule"] == "L2"]
        self.assertEqual(len(l2), 1)
        self.assertIn("DEAD", l2[0]["detail"])
        self.assertEqual(l2[0]["level"], "MAJOR")

    def test_l3_multi_out_edges_minor(self):
        """同状态多条出边命中 L3（提示语义复核触发条件可区分）。"""
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    DRAFT --> A: x (DB_INSERT)\n"
            "    DRAFT --> B: y (DB_UPDATE)\n    A --> SUCCESS: z (DB)\n"
            "    B --> SUCCESS: z (DB)\n    SUCCESS --> [*]\n```\n"
        )
        result = run_lint(path)
        l3 = [f for f in result["findings"] if f["rule"] == "L3"]
        self.assertEqual(len(l3), 1)
        self.assertEqual(l3[0]["level"], "MINOR")

    def test_l6_table_without_write_critical(self):
        """DDL 里的表在全报告无写入动作 → L6 CRITICAL；无读取 → MINOR。"""
        (self.root / "sql").mkdir()
        (self.root / "sql" / "order_flow.sql").write_text(
            "CREATE TABLE `order_flow` (id bigint);\n", encoding="utf-8")
        path = self.write_summary("# 报告\n\n## 2. 数据模型\n\n见 DDL。\n")
        result = run_lint(path)
        l6 = [f for f in result["findings"] if f["rule"] == "L6"]
        self.assertTrue(any(f["level"] == "CRITICAL" for f in l6))
        self.assertTrue(any(f["level"] == "MINOR" for f in l6))
        self.assertFalse(result["deliverable"])

    def test_l7_log_q_without_mapping_row_major(self):
        """alignment-log 的 Q 编号在映射表无对应行 → L7 MAJOR。"""
        (self.summary_dir / "_review" / "alignment-log.md").write_text(
            "# 流水\n\n## Q1 题一（t）\n- 落点：§4\n\n## Q2 题二（t）\n- 落点：§4\n",
            encoding="utf-8")
        path = self.write_summary(
            "# 报告\n\n## 4. 核心状态机\n\nx\n\n"
            "## 2.1 意图注入映射表\n\n| # | 意图 | 落点 |\n|---|---|---|\n| 1 | x | §4 |\n"
        )
        result = run_lint(path)
        l7 = [f for f in result["findings"] if f["rule"] == "L7"]
        self.assertEqual(len(l7), 1)
        self.assertIn("Q2", l7[0]["detail"])

    def test_l8_downgrade_without_confirmation_minor(self):
        """[🟡待澄清] 降级项附近无人类确认记录 → L8 MINOR。"""
        path = self.write_summary(
            "# 报告\n\n## 5. 边界\n\n- [🟡待澄清] 某边界 case 后续处理\n"
        )
        result = run_lint(path)
        self.assertTrue(any(f["rule"] == "L8" and f["level"] == "MINOR"
                            for f in result["findings"]))
        # 附了确认记录则放行
        path2 = self.write_summary(
            "# 报告\n\n## 5. 边界\n\n- [🟡待澄清] 某边界 case，张三确认同意降级\n"
        )
        result2 = run_lint(path2)
        self.assertFalse(any(f["rule"] == "L8" for f in result2["findings"]))

    def test_l4_anchor_mismatch_critical(self):
        path = self.write_summary(
            "# 报告\n\n## 1. 需求概述\n\n## 2. 数据模型\n\n"
            "## 2.1 意图注入映射表\n\n| # | 意图 | 落点 |\n|---|---|---|\n"
            "| 1 | x | §9 状态机边 |\n"
        )
        result = run_lint(path)
        self.assertTrue(any(f["rule"] == "L4" for f in result["findings"]))
        self.assertFalse(result["deliverable"])


class MapperFidelityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mgr = AlignmentManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mapper_locates_real_anchors(self):
        # 造一题并核销（alignment-log 有 Q1，落点含 BR-01 与状态名）
        token = run(self.mgr.dispatch_question("f", "状态？", "📋", ["a", "b", "c"]))["token"]
        self.mgr.resolve_question(
            "f", token, "选1", "张三", "退款 → REFUNDING", "§4 REFUNDING 边 / BR-01",
        )
        summary = self.root / ".harness" / "requests" / "f" / "summary.md"
        summary.write_text(
            "# 报告\n\n## 4. 核心状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    direction LR\n    PAID --> REFUNDING: 退款 (REDIS_LOCK)\n```\n\n"
            "## 6. 业务决策规则\n\n| BR-01 | 已支付 | 退款→REFUNDING |\n",
            encoding="utf-8",
        )
        result = run_mapper(str(summary))
        self.assertTrue(result["ok"])
        draft = Path(result["draft"]).read_text("utf-8")
        self.assertIn("§6 BR-01", draft)  # BR 真实定位到定义章节
        self.assertIn("状态机", draft)    # 状态机转移被定位

    def test_mapper_requires_log(self):
        summary = self.root / ".harness" / "requests" / "g"
        summary.mkdir(parents=True)
        (summary / "summary.md").write_text("# x", encoding="utf-8")
        result = run_mapper(str(summary / "summary.md"))
        self.assertFalse(result["ok"])


class RecordJudgmentTests(unittest.TestCase):
    """三刀之②：宿主语义判断落账。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_writes_skill_vocab_draft(self):
        result = record_analysis(
            self.root, "f",
            logic_pattern=["State-Driven", "Rule-Driven"],
            complexity="complex",
            confidence="🔴",
            gaps=[{"gap": "防重无服务端机制", "severity": "🔴", "category": "🔧",
                   "options": ["分布式锁", "幂等token", "唯一索引"]}],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "blocked")
        draft = Path(result["draft_file"]).read_text("utf-8")
        self.assertIn("status: blocked", draft)
        self.assertIn("intent_aligned: false", draft)
        self.assertIn("host agent", draft)
        self.assertIn("🔴 🔧 防重无服务端机制", draft)

    def test_record_rejects_invalid_enums(self):
        self.assertFalse(record_analysis(self.root, "f", ["State-Driven"], "complex", "蓝")["ok"])
        self.assertFalse(record_analysis(self.root, "f", ["X-Driven"], "complex", "🔴")["ok"])
        self.assertFalse(record_analysis(
            self.root, "f", ["State-Driven"], "complex", "🔴",
            gaps=[{"gap": "x", "severity": "🔴", "category": "❓"}])["ok"])

    def test_record_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            record_analysis(self.root, "../evil", ["State-Driven"], "complex", "🔴")


class SeverityVocabTests(unittest.TestCase):
    """失真点 Z8：severity → skill 状态词表（blocked/draft/pending_review）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mgr = AlignmentManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_red_pending_means_blocked(self):
        token = run(self.mgr.dispatch_question(
            "f", "核心断层题", "🔧", ["a", "b", "c"], severity="🔴"))["token"]
        checklist = (self.root / ".harness" / "requests" / "f" / "_review"
                     / "pending-questions.md").read_text("utf-8")
        self.assertIn("🔴 🔧 核心断层题", checklist)
        advice = self.mgr.list_pending("f")["frontmatter_advice"]
        self.assertEqual(advice["status"], "blocked")
        self.assertFalse(advice["intent_aligned"])
        # 核销红灯题 → 就绪 → pending_review（approved MCP 永不自授）
        self.mgr.resolve_question("f", token, "选1", "张三", "语义", "落点")
        advice2 = self.mgr.list_pending("f")["frontmatter_advice"]
        self.assertEqual(advice2["status"], "pending_review")

    def test_yellow_pending_means_draft(self):
        run(self.mgr.dispatch_question("f", "边界题", "📋", ["a", "b", "c"], severity="🟡"))
        self.assertEqual(self.mgr.list_pending("f")["frontmatter_advice"]["status"], "draft")

    def test_abandoned_red_unblocks(self):
        token = run(self.mgr.dispatch_question("f", "红题", "📋", ["a", "b", "c"], severity="🔴"))["token"]
        self.mgr.abandon_question("f", token, "用户弃用")
        advice = self.mgr.list_pending("f")["frontmatter_advice"]
        self.assertEqual(advice["status"], "pending_review")

    def test_resume_frontmatter_advice(self):
        run(self.mgr.dispatch_question("f", "红题", "📋", ["a", "b", "c"], severity="🔴"))
        result = analyze_request(self.root, "f")
        self.assertEqual(result["mode"], "resume")
        self.assertEqual(result["frontmatter_advice"]["status"], "blocked")
        self.assertEqual(result["intent_status"]["pending_red"], 1)


if __name__ == "__main__":
    unittest.main()
