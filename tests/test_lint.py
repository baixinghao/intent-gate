"""summary_lint 机械检查器测试 —— 错题集的代码形态。

每条 lint 规则的抗体 fixture：不是"正确输入通过"，
而是"我故意构造的漏网输入必须被抓"。

收录：
  2026-08-11  L2b —— 仅有自环、无对外出边的状态从 L2（outs 非空失明）
              与 L3（real=∅ 失明）的夹缝漏网。面试复盘中发现，当场抗体化。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.analysis.lint import lint  # noqa: E402


def _rules(findings) -> set[str]:
    return {rule for _, rule, _ in findings}


class LintTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_lint(self, body: str):
        summary = self.root / "summary.md"
        summary.write_text(body, encoding="utf-8")
        return lint(summary)


class StateMachineLeakTests(LintTestBase):
    """状态机检查项的对抗 fixture。"""

    MACHINE_HEAD = (
        "# 需求分析报告\n\n"
        "## 1. 状态机\n\n"
        "```mermaid\nstateDiagram-v2\n"
    )
    MACHINE_TAIL = "```\n"

    def test_l2b_self_loop_only_state_is_caught(self):
        """错题 2026-08-11：PENDING 只有自环（重试），
        L2 不报（outs={PENDING} 非空）、L3 不报（real=∅）——必须被 L2b 抓。"""
        findings, edges, *_ = self.run_lint(
            self.MACHINE_HEAD
            + "    DRAFT --> PENDING: 提交\n"
            + "    PENDING --> PENDING: 重试\n"
            + "    DRAFT --> SUCCESS: 直达\n"
            + self.MACHINE_TAIL
        )
        rules = _rules(findings)
        self.assertIn("L2b", rules)
        l2b = [d for lv, r, d in findings if r == "L2b"]
        self.assertTrue(any("PENDING" in d for d in l2b))
        # 漏网带确认：此形态下 L2/L3 对 PENDING 确实失明（防规则间误覆盖）。
        # 注意 L3 消息会列出出边目标（"→PENDING"），必须按主语"状态 PENDING 有"判。
        self.assertFalse(any(r == "L2" and "PENDING" in d for _, r, d in findings))
        self.assertFalse(
            any(r == "L3" and f"状态 PENDING 有" in d for _, r, d in findings)
        )

    def test_l2b_not_fired_when_self_loop_has_exit(self):
        """自环 + 正常出边 = 合法的重试态，不许误报。"""
        findings, *_ = self.run_lint(
            self.MACHINE_HEAD
            + "    DRAFT --> PENDING: 提交\n"
            + "    PENDING --> PENDING: 重试\n"
            + "    PENDING --> SUCCESS: 通过\n"
            + self.MACHINE_TAIL
        )
        self.assertNotIn("L2b", _rules(findings))

    def test_l2_plain_dead_state_still_fires(self):
        """回归：L2b 不许吞掉 L2 的原辖区——无出边状态仍报 L2。"""
        findings, *_ = self.run_lint(
            self.MACHINE_HEAD
            + "    DRAFT --> STUCK: 提交\n"
            + "    DRAFT --> SUCCESS: 直达\n"
            + self.MACHINE_TAIL
        )
        rules = _rules(findings)
        self.assertIn("L2", rules)
        self.assertNotIn("L2b", rules)

    def test_l2b_terminal_self_loop_via_star_not_fired(self):
        """自环 + 流向 [*]（正常终态出口）不算永驻，不许误报。"""
        findings, *_ = self.run_lint(
            self.MACHINE_HEAD
            + "    DRAFT --> WAITING: 提交\n"
            + "    WAITING --> WAITING: 轮询\n"
            + "    WAITING --> [*]: 完成\n"
            + "    DRAFT --> SUCCESS: 直达\n"
            + self.MACHINE_TAIL
        )
        self.assertNotIn("L2b", _rules(findings))


class L1SuccessTerminalTests(LintTestBase):
    """错题 2026-08-12（红军复盘）：「已完结」是业务终态却被 L1 判死。"""

    HEAD = (
        "# 需求分析报告\n\n## 1. 状态机\n\n```mermaid\nstateDiagram-v2\n"
    )
    TAIL = "```\n"

    def test_yiwanjie_is_success_terminal(self):
        """放款中 --> 已完结 --> [*] 必须过 L1（修复前红）。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    放款中 --> 已完结: 放款\n"
            + "    已完结 --> [*]\n"
            + self.TAIL
        )
        self.assertNotIn("L1", _rules(findings))

    def test_no_success_terminal_still_critical(self):
        """防回归：无成功终态仍 CRITICAL。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    提交中 --> 放款中: 通过\n"
            + "    放款中 --> [*]\n"
            + self.TAIL
        )
        self.assertIn("L1", _rules(findings))

    def test_bare_wancheng_not_success(self):
        """「未完成」含「完成」也不许假通过——只收完整终态词。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    提交中 --> 未完成: 驳回\n"
            + "    未完成 --> [*]\n"
            + self.TAIL
        )
        self.assertIn("L1", _rules(findings))


class L4AnchorSplitTests(LintTestBase):
    """错题 2026-08-12（红军复盘）：§3.2/§5.1 无空格连写，§5.1 被吞并漏检。"""

    def _body(self, spot: str, sections: str) -> str:
        return (
            sections
            + "\n## 意图注入映射表\n\n"
            + "| # | 注入的意图 | 落点 | 复核 |\n"
            + "|---|-----------|------|------|\n"
            + f"| 1 | x | {spot} | ok |\n"
        )

    def test_no_space_multi_anchor_both_checked(self):
        """§3.2 存在、§5.1 不存在：吞并修复后 §5.1 必须独立报 CRITICAL（修复前红）。"""
        sections = "## 3.2 提交流程\n"
        findings, _, _, anchors, _ = self.run_lint(self._body("§3.2/§5.1", sections))
        self.assertTrue(
            any(r == "L4" and "§5.1" in d for _, r, d in findings),
            f"§5.1 被吞并漏检: {findings}",
        )

    def test_slash_not_polluting_kw(self):
        """带空格多锚点：/ 不再进入关键词捕获，两个锚点都判 ✅。"""
        sections = "## 3.2 提交流程\n## 5.1 放款响应\n"
        findings, _, _, anchors, _ = self.run_lint(
            self._body("§3.2 / §5.1", sections)
        )
        self.assertNotIn("L4", _rules(findings))
        refs = [ref for ref, _ in anchors]
        self.assertTrue(any(r.startswith("§3.2") and "/" not in r for r in refs))
        self.assertTrue(any(r.startswith("§5.1") for r in refs))

    def test_kw_mismatch_still_critical(self):
        """回归：§6.2 决策表 关键词错位仍报 CRITICAL（Response 类关键词仍捕获）。"""
        sections = "## 6.2 接口清单\n"
        findings, *_ = self.run_lint(self._body("§6.2 决策表", sections))
        self.assertTrue(any(r == "L4" and "错位" in d for _, r, d in findings))


class L6TableDiscoveryTests(LintTestBase):
    """错题 2026-08-12：小写 SQL 关键词漏判、无反引号建表整张隐形。"""

    def _make_sql(self, ddl: str):
        sql_dir = self.root / "sql"
        sql_dir.mkdir()
        (sql_dir / "t.sql").write_text(ddl, encoding="utf-8")

    def test_lowercase_insert_counts_as_write(self):
        """修复前红：正文小写 insert 不算写入（WRITE_KW 只有大写）。"""
        self._make_sql("CREATE TABLE `pay_order` (id INT);")
        findings, _, matrix, _, _ = self.run_lint(
            "# 报告\n\npay_order 状态变更后 insert 一条流水。\n"
        )
        self.assertEqual([t for t, _, _ in matrix], ["pay_order"])
        self.assertFalse(
            any(r == "L6" and "pay_order" in d and lv == "CRITICAL"
                for lv, r, d in findings)
        )

    def test_unquoted_ddl_table_discovered(self):
        """修复前红：CREATE TABLE refund（无反引号）整张表隐形，CRITICAL 都报不出。"""
        self._make_sql("CREATE TABLE refund (id INT);")
        findings, _, matrix, _, _ = self.run_lint("# 报告\n\n无操作。\n")
        self.assertIn("refund", [t for t, _, _ in matrix])
        self.assertTrue(any(r == "L6" and "refund" in d for _, r, d in findings))

    def test_baocun_counts_as_write(self):
        """「保存」计入写入词表（修复前红）。"""
        self._make_sql("CREATE TABLE `pay_order` (id INT);")
        findings, *_ = self.run_lint("# 报告\n\n审核通过后保存 pay_order。\n")
        self.assertFalse(
            any(r == "L6" and "pay_order" in d and lv == "CRITICAL"
                for lv, r, d in findings)
        )


class L7QPrefixTests(LintTestBase):
    """错题 2026-08-12（红军复盘）：第一列写 Q1 是天然写法，却永远不匹配。"""

    def _body_with_log(self, first_col: str):
        log_dir = self.root / "_review"
        log_dir.mkdir(exist_ok=True)
        (log_dir / "alignment-log.md").write_text("## Q1\n\n注入解读文本\n", encoding="utf-8")
        return (
            "# 报告\n\n## 意图注入映射表\n\n"
            "| # | 注入的意图 | 落点 | 复核 |\n"
            "|---|-----------|------|------|\n"
            f"| {first_col} | 注入解读文本 | §1.1 | ok |\n"
        )

    def test_q_prefix_row_matches(self):
        """第一列 Q1 必须认（修复前红）。"""
        findings, *_ = self.run_lint(self._body_with_log("Q1"))
        self.assertNotIn("L7", _rules(findings))

    def test_bare_number_still_matches(self):
        """回归：draft_mapping 的裸数字第一列照常认。"""
        findings, *_ = self.run_lint(self._body_with_log("1"))
        self.assertNotIn("L7", _rules(findings))

    def test_missing_row_still_major(self):
        """回归：映射行缺失仍报 MAJOR。"""
        findings, *_ = self.run_lint(self._body_with_log("2"))
        self.assertIn("L7", _rules(findings))


class L9PathIsolationTests(LintTestBase):
    """错题 2026-08-12（红军复盘）：SQL 全跑进 summary.md，
    L6 输入源（sql/*.sql）为空而静默失明——路径隔离必须有机械半边。"""

    def test_create_table_embedded_in_summary_critical(self):
        """修复前红：DDL 内嵌 summary.md 必须报 CRITICAL（playbook §3.4/Step 4）。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\nCREATE TABLE `order_flow` (id bigint);\n"
        )
        self.assertTrue(
            any(r == "L9" and lv == "CRITICAL" for lv, r, d in findings),
            f"L9 未抓内嵌 DDL: {findings}",
        )

    def test_declared_sql_path_but_dir_empty_critical(self):
        """修复前红：登记了 sql/order_flow.sql 但 sql/ 无文件必须报 CRITICAL。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\nDDL 见 sql/order_flow.sql\n"
        )
        self.assertTrue(
            any(r == "L9" and "sql" in d for lv, r, d in findings),
            f"L9 未抓 DDL 未落盘: {findings}",
        )

    def test_sql_dir_exists_but_empty_still_critical(self):
        """sql/ 目录存在但没有任何 .sql 文件同样必须报（not list() 分支）。"""
        (self.root / "sql").mkdir()
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\nDDL 见 sql/order_flow.sql\n"
        )
        self.assertTrue(
            any(r == "L9" and lv == "CRITICAL" for lv, r, d in findings)
        )

    def test_embedded_ddl_with_populated_sql_dir_still_critical(self):
        """sql/ 有文件也不能豁免内嵌 DDL——L9a 与 L9b 独立判定。"""
        sql_dir = self.root / "sql"
        sql_dir.mkdir()
        (sql_dir / "order_flow.sql").write_text(
            "CREATE TABLE `order_flow` (id bigint);\n", encoding="utf-8")
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\n"
            "CREATE TABLE `order_flow` (id bigint);\n"
            "写入：提交后 INSERT order_flow (status)\n"
        )
        self.assertTrue(
            any(r == "L9" and lv == "CRITICAL" for lv, r, d in findings),
            f"L9a 未独立触发: {findings}",
        )

    def test_mysql_dir_word_not_mistaken_for_sql_ref(self):
        """\bsql/ 锚定：mysql/、sqlite/ 等普通词不得误报 L9b。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\n数据源走 mysql/ 主从，无 DDL 变更。\n"
        )
        self.assertFalse(
            any(r == "L9" and "sql" in d for lv, r, d in findings),
            f"mysql/ 被误判为 SQL 登记: {findings}",
        )

    def test_proper_sql_dir_passes(self):
        """回归：DDL 在项目根 sql/ 且 summary 只登记路径 → L9 不报。"""
        sql_dir = self.root / "sql"
        sql_dir.mkdir()
        (sql_dir / "order_flow.sql").write_text(
            "-- generated by doc-analysis, pending_review\n"
            "CREATE TABLE `order_flow` (id bigint);\n", encoding="utf-8")
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\nDDL 见 sql/order_flow.sql\n"
            "写入：提交后 INSERT order_flow (status)\n"
        )
        self.assertNotIn("L9", {r for _, r, _ in findings})

    def test_lowercase_create_table_also_caught(self):
        """大小写不敏感：create table 内嵌同样报（re.I）。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\ncreate table order_flow (id int);\n"
        )
        self.assertTrue(
            any(r == "L9" and lv == "CRITICAL" for lv, r, d in findings)
        )

    def test_prose_mention_of_create_table_not_fined(self):
        """防误伤：散文提及「CREATE TABLE 语句」（非建表语句形态）不得报 L9。
        语句形态 = 表名+开括号；正文讨论索引设计是合法写法。"""
        sql_dir = self.root / "sql"
        sql_dir.mkdir()
        (sql_dir / "pay_order.sql").write_text(
            "CREATE TABLE `pay_order` (id int);\n", encoding="utf-8")
        findings, *_ = self.run_lint(
            "# 报告\n\n## 2. 数据模型\n\n"
            "DDL 见 sql/pay_order.sql，CREATE TABLE 语句含唯一索引设计。\n"
            "写入：提交后 INSERT pay_order (status)\n"
        )
        self.assertNotIn(
            "L9", {r for _, r, _ in findings},
            f"散文提及被误伤: {findings}",
        )


class ContractDisclosureTests(LintTestBase):
    """第 0 刀：报告头部必须披露机械判定契约，且全部从常量插值（防手抄漂移）。"""

    def test_report_contains_contract_from_constants(self):
        from intent_gate.analysis import lint as lint_mod

        summary = self.root / "summary.md"
        summary.write_text("# 报告\n", encoding="utf-8")
        result = lint_mod.run_lint(summary)
        self.assertTrue(result["ok"])
        report = Path(result["report"]).read_text(encoding="utf-8")

        self.assertIn("机械判定契约", report)
        for key in lint_mod.KW_MAP:
            self.assertIn(key, report)
        for kw in lint_mod.WRITE_KW + lint_mod.READ_KW:
            self.assertIn(kw, report)
        # 正则原文披露：L1 词表 / L7 映射行 / alignment-log Q 标题
        self.assertIn(lint_mod.L1_SUCCESS_RE, report)
        self.assertIn(lint_mod.L7_MAP_ROW_RE, report)
        self.assertIn(lint_mod.L7_LOG_Q_RE, report)
        # L9 路径隔离正则（防漂移：契约文本必须跟随常量）
        self.assertIn(lint_mod.L9_EMBED_DDL_RE, report)
        self.assertIn(lint_mod.L9_SQL_REF_RE, report)


if __name__ == "__main__":
    unittest.main()
