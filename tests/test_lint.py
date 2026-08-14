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


class L10EmptyTableMatrixTests(LintTestBase):
    """错题 2026-08-12（红军复盘·提现确认）：§7 写成 Markdown 字段表、
    sql/ 零产出、全文不登记 sql/ 路径——L9 一致性检查无从触发，
    矩阵② 空表这面红旗必须自己报警。"""

    def test_data_model_section_with_empty_matrix_critical(self):
        """有「数据模型」章节但矩阵②为空 → CRITICAL（修复前红）。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 7. 数据模型（DDL 建议）\n\n"
            "| 字段 | 类型 |\n|------|------|\n| id | BIGINT |\n"
        )
        self.assertTrue(
            any(r == "L10" and lv == "CRITICAL" for lv, r, d in findings),
            f"L10 未抓空矩阵②: {findings}",
        )

    def test_data_model_section_with_populated_sql_dir_passes(self):
        """有数据模型章节且 sql/ 有表 → 不报。"""
        sql_dir = self.root / "sql"
        sql_dir.mkdir()
        (sql_dir / "t.sql").write_text("CREATE TABLE `pay_order` (id int);\n", "utf-8")
        findings, *_ = self.run_lint(
            "# 报告\n\n## 7. 数据模型\n\nDDL 见 sql/pay_order.sql\n"
            "写入：提交后 INSERT pay_order (status)\n"
        )
        self.assertNotIn("L10", _rules(findings))

    def test_no_data_model_section_not_fined(self):
        """防误伤：无数据模型章节（simple 需求）矩阵②为空不报。"""
        findings, *_ = self.run_lint("# 报告\n\n## 1. 概述\n\n纯文案需求。\n")
        self.assertNotIn("L10", _rules(findings))

    def test_exempt_declaration_downgrades_to_minor(self):
        """豁免通道：显式声明「无新增表，复用旧表」→ 降 MINOR 留蓝军复核，不 CRITICAL。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 7. 数据模型\n\n无新增表，复用旧表 t_order（只读）。\n"
        )
        l10 = [(lv, d) for lv, r, d in findings if r == "L10"]
        self.assertEqual(len(l10), 1)
        self.assertEqual(l10[0][0], "MINOR")

    def test_exempt_with_populated_sql_dir_silent(self):
        """豁免词 + sql/ 有表 → L10 完全不报（矩阵非空，豁免无需触发）。"""
        sql_dir = self.root / "sql"
        sql_dir.mkdir()
        (sql_dir / "t.sql").write_text("CREATE TABLE `t_order` (id int);\n", "utf-8")
        findings, *_ = self.run_lint(
            "# 报告\n\n## 7. 数据模型\n\n无新增表，复用旧表 t_order。\n"
            "读取：SELECT t_order 状态\n"
        )
        self.assertNotIn("L10", _rules(findings))


class L11ComplexEdgeTagTests(LintTestBase):
    """错题 2026-08-12（红军复盘·提现确认）：frontmatter 自称 complex，
    17 条边全裸触发词——约束第 3 条（complex 技术动作强制打标）需要机械半边。"""

    FM = "---\nfeature: t\ncomplexity: complex\n---\n\n"
    HEAD = "# 报告\n\n## 3. 状态机\n\n```mermaid\nstateDiagram-v2\n"
    TAIL = "```\n"

    def test_complex_with_bare_edge_critical(self):
        """complex + 边无 (技术动作) → CRITICAL（修复前红）。"""
        findings, *_ = self.run_lint(
            self.FM + self.HEAD
            + "    DRAFT --> PROCESSING: 提交成功\n"
            + "    PROCESSING --> FINISHED: 放款成功 (DB_UPDATE)\n"
            + self.TAIL
        )
        self.assertTrue(
            any(r == "L11" and lv == "CRITICAL" and "DRAFT" in d
                for lv, r, d in findings),
            f"L11 未抓裸边: {findings}",
        )

    def test_complex_all_tagged_passes(self):
        """complex + 全边打标 → 不报。"""
        findings, *_ = self.run_lint(
            self.FM + self.HEAD
            + "    DRAFT --> PROCESSING: 提交 (IF校验, DB_INSERT)\n"
            + "    PROCESSING --> FINISHED: 放款 (DB_UPDATE)\n"
            + self.TAIL
        )
        self.assertNotIn("L11", _rules(findings))

    def test_non_complex_bare_edges_not_fined(self):
        """防误伤：无 complex 声明（simple/medium）裸边不报。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    DRAFT --> PROCESSING: 提交成功\n"
            + "    PROCESSING --> FINISHED: 放款\n"
            + self.TAIL
        )
        self.assertNotIn("L11", _rules(findings))


class L12ProseAnchorTests(LintTestBase):
    """错题 2026-08-12（红军复盘·提现确认）：映射表锚点全是
    「决策表 R1 / 时序图 4.3 步骤 2」散文——无 § 无 BR-，L4 无从校验。"""

    def _body(self, anchor: str) -> str:
        return (
            "# 报告\n\n## 3.2 提交流程\n\n## 意图注入映射表\n\n"
            "| # | 注入的意图 | 落点 | 复核 |\n"
            "|---|-----------|------|------|\n"
            f"| 1 | x | {anchor} | ok |\n"
        )

    def test_pure_prose_anchors_major(self):
        """映射表有数据行但无一行含 §/BR- → MAJOR（修复前红）。"""
        findings, *_ = self.run_lint(self._body("决策表 R1 / 时序图 4.3 步骤 2"))
        self.assertTrue(any(r == "L12" for _, r, _ in findings))

    def test_section_anchor_passes(self):
        """含 §x.y 锚点 → 不报。"""
        findings, *_ = self.run_lint(self._body("§3.2"))
        self.assertNotIn("L12", _rules(findings))

    def test_br_anchor_passes(self):
        """含 BR-n 锚点 → 不报。"""
        findings, *_ = self.run_lint(self._body("决策表 BR-01"))
        self.assertNotIn("L12", _rules(findings))

    def test_no_mapping_table_not_fined(self):
        """防误伤：无映射表不报。"""
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L12", _rules(findings))


class L13PlaceholderTests(LintTestBase):
    """双层意图对齐·绘图层探测器：图内占位符 ???/TBDn 残留 = CRITICAL；
    占位只许代码实证/人类拍板消除，禁止猜测填空后交付。"""

    def test_tbd_edge_placeholder_critical(self):
        """summary 状态机含 --> TBD1 占位边 → L13 CRITICAL。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n## 1. 状态机\n\n```mermaid\nstateDiagram-v2\n"
            "    DRAFT --> TBD1: 退款去向未知\n"
            "    DRAFT --> SUCCESS: 直达\n```\n"
        )
        self.assertTrue(
            any(r == "L13" and lv == "CRITICAL" and "TBD1" in d
                for lv, r, d in findings),
            f"L13 未抓 TBD1 占位: {findings}",
        )

    def test_bare_question_marks_critical(self):
        """裸 ??? 兼容扫描：mermaid 块内出现即 L13 CRITICAL。"""
        findings, *_ = self.run_lint(
            "# 报告\n\n```mermaid\nstateDiagram-v2\n"
            "    state \"???待确认\" as TBD2\n```\n"
        )
        self.assertTrue(
            any(r == "L13" and lv == "CRITICAL" for lv, r, d in findings),
            f"L13 未抓裸 ???: {findings}",
        )

    def test_prose_tbd_reference_not_fined(self):
        """防误伤：散文里的 TBD 引用（PRD 原文「§8 TBD」/断层清单「TBD2」）
        不算占位——L13 只扫 mermaid 块。"""
        findings, *_ = self.run_lint(
            "# 报告\n\nPRD 原文「§8 TBD」，TBD项较多。\n"
            "断层清单：✏️绘图层 TBD2 待决。\n\n"
            "```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n"
        )
        self.assertNotIn("L13", _rules(findings))


class L13bDraftPlaceholderTests(LintTestBase):
    """L13b（MINOR，单方向）：草稿图占位仍在但待决清单无在飞题，
    疑似猜测填空或漏发题。"""

    def write_draft(self, mermaid_body: str):
        d = self.root / "_review"
        d.mkdir(exist_ok=True)
        (d / "analysis-draft.md").write_text(
            f"# 草稿\n\n```mermaid\n{mermaid_body}\n```\n", encoding="utf-8")

    def write_pending(self, text: str):
        (self.root / "_review" / "pending-questions.md").write_text(text, encoding="utf-8")

    def test_draft_placeholder_without_open_question_minor(self):
        """草稿图含占位 + 待决清单无 `- [ ]` 在飞题 → L13b MINOR。"""
        self.write_draft("stateDiagram-v2\n    A --> TBD1")
        self.write_pending("# 待决\n\n- [x] [HG-AAAA] 已核销题\n")
        findings, *_ = self.run_lint("# 报告\n")
        l13b = [(lv, d) for lv, r, d in findings if r == "L13b"]
        self.assertEqual(len(l13b), 1)
        self.assertEqual(l13b[0][0], "MINOR")

    def test_draft_placeholder_with_open_question_silent(self):
        """草稿图含占位但有在飞题 → 不报（占位正在等答案，合法中间态）。"""
        self.write_draft("stateDiagram-v2\n    A --> TBD1")
        self.write_pending("# 待决\n\n- [ ] [HG-AAAA] 在飞题\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L13b", _rules(findings))

    def test_draft_prose_tbd_reference_not_fined(self):
        """防误伤：草稿散文引用「✏️绘图层：TBD2」不算占位——只扫 mermaid 块。"""
        d = self.root / "_review"
        d.mkdir(exist_ok=True)
        (d / "analysis-draft.md").write_text(
            "# 草稿\n\n✏️绘图层：TBD2 待决（散文引用）\n", encoding="utf-8")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L13b", _rules(findings))


class L14EntryFailEdgeTests(LintTestBase):
    """L14（MAJOR，错题集 2026-08-14 LOADING 案）：入口态（[*] --> X 的 X）
    出边集合无一条失败语义边 → MAJOR；图内显式统一兜底注释 → 降 MINOR。
    门禁逼表态，不逼画边——误伤面必须压零。"""

    HEAD = "# 报告\n\n## 1. 状态机\n\n```mermaid\nstateDiagram-v2\n"
    TAIL = "```\n"

    def test_entry_without_fail_edge_major(self):
        """阳性·LOADING 案：入口态只有成功边（资方匹配失败去向漏画）→ L14 MAJOR。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    [*] --> LOADING: 发起 (IF校验)\n"
            + "    LOADING --> WAIT_CONFIRM: 路由成功 (DB_INSERT)\n"
            + "    WAIT_CONFIRM --> 已完成: 提交 (DB_UPDATE)\n"
            + "    已完成 --> [*]: 完结\n"
            + self.TAIL
        )
        l14 = [(lv, d) for lv, r, d in findings if r == "L14"]
        self.assertEqual(len(l14), 1, f"L14 未抓入口态画漏: {findings}")
        self.assertEqual(l14[0][0], "MAJOR")
        self.assertIn("LOADING", l14[0][1])

    def test_entry_with_fail_edge_silent(self):
        """阴性：入口态有 --> FAILED 失败边，不报。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    [*] --> LOADING: 发起 (IF校验)\n"
            + "    LOADING --> WAIT_CONFIRM: 路由成功 (DB_INSERT)\n"
            + "    LOADING --> FAILED: 路由失败 (DB_UPDATE)\n"
            + "    WAIT_CONFIRM --> 已完成: 提交 (DB_UPDATE)\n"
            + "    已完成 --> [*]: 完结\n"
            + "    FAILED --> [*]: 终止\n"
            + self.TAIL
        )
        self.assertNotIn("L14", _rules(findings))

    def test_fail_edge_via_prose_alias_silent(self):
        """阴性·别名通道：LOADING --> 已失效 + 散文「已失效=INVALID」
        → 目标态别名命中失败词表，不报。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    [*] --> LOADING: 发起 (IF校验)\n"
            + "    LOADING --> WAIT_CONFIRM: 路由成功 (DB_INSERT)\n"
            + "    LOADING --> 已失效: 路由失败 (DB_UPDATE)\n"
            + "    WAIT_CONFIRM --> 已完成: 提交 (DB_UPDATE)\n"
            + "    已完成 --> [*]: 完结\n"
            + "    已失效 --> [*]: 终止\n"
            + self.TAIL
            + "\n> 状态语义：已失效=INVALID。\n"
        )
        self.assertNotIn("L14", _rules(findings))

    def test_global_fallback_note_downgrades_minor(self):
        """豁免：无失败边但图内注释「失败统一兜底」→ MINOR 而非 MAJOR。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    [*] --> DRAFT: 发起 (IF校验)\n"
            + "    DRAFT --> SUCCESS: 提交 (DB_INSERT)\n"
            + "    SUCCESS --> [*]: 完结\n"
            + "    note right of DRAFT: 失败统一兜底由全局异常处理器承担\n"
            + self.TAIL
        )
        l14 = [(lv, d) for lv, r, d in findings if r == "L14"]
        self.assertEqual(len(l14), 1)
        self.assertEqual(l14[0][0], "MINOR")

    def test_no_entry_edge_not_fired(self):
        """防误伤：无 [*] --> 入口边的图（片段/纯内部流转）不启用本规则。"""
        findings, *_ = self.run_lint(
            self.HEAD
            + "    DRAFT --> SUCCESS: 直达\n"
            + self.TAIL
        )
        self.assertNotIn("L14", _rules(findings))


class L15DdlEnumConsistencyTests(LintTestBase):
    """L15（MAJOR，错题集 2026-08-14 routing_status 案）：DDL 列注释中的
    状态枚举值必须在状态机状态集/别名中出现——实现层想到、图里漏画
    = 产物自相矛盾。含别名解析；显式声明非生命周期字段 → 降 MINOR。"""

    MACHINE = (
        "# 报告\n\n## 1. 状态机\n\n```mermaid\nstateDiagram-v2\n"
        "    [*] --> LOADING: 发起 (IF校验)\n"
        "    LOADING --> WAIT_CONFIRM: 路由成功 (DB_INSERT)\n"
        "    LOADING --> INVALID: 路由失败 (DB_UPDATE)\n"
        "    WAIT_CONFIRM --> PROCESSING: 提交 (DB_UPDATE)\n"
        "    PROCESSING --> 已完成: 渠道完成 (DB_UPDATE)\n"
        "    PROCESSING --> TERMINATE: 推进失败 (DB_UPDATE)\n"
        "    已完成 --> [*]: 完结\n"
        "    TERMINATE --> [*]: 终止\n"
        "    INVALID --> [*]: 失效\n"
        "```\n"
    )

    def write_sql(self, body: str, name: str = "t_sign_order.sql"):
        d = self.root / "sql"
        d.mkdir(exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")

    ROUTING_SQL = (
        "CREATE TABLE t_sign_order (\n"
        "    id BIGINT NOT NULL AUTO_INCREMENT,\n"
        "    routing_status VARCHAR(32) DEFAULT NULL COMMENT '路由状态 PROCESSING/FAILED',\n"
        "    PRIMARY KEY (id)\n"
        ") ENGINE=InnoDB;\n"
    )

    def test_enum_missing_from_machine_major(self):
        """阳性·routing_status 案：枚举 FAILED 不在状态机状态集中 → L15 MAJOR。"""
        self.write_sql(self.ROUTING_SQL)
        findings, *_ = self.run_lint(self.MACHINE)
        l15 = [(lv, d) for lv, r, d in findings if r == "L15"]
        self.assertEqual(len(l15), 1, f"L15 未抓枚举漏画: {findings}")
        self.assertEqual(l15[0][0], "MAJOR")
        self.assertIn("routing_status", l15[0][1])
        self.assertIn("FAILED", l15[0][1])

    def test_enum_covered_via_prose_alias_silent(self):
        """阴性·今日真实案：withdraw_status 四值中 FINISHED 由散文别名
        「已完成=FINISHED」解析覆盖 → 全枚举有机落点，不报。"""
        self.write_sql(
            "CREATE TABLE t_sign_order (\n"
            "    withdraw_status VARCHAR(32) NOT NULL COMMENT '提现状态 PROCESSING/FINISHED/TERMINATE/INVALID',\n"
            "    PRIMARY KEY (id)\n"
            ") ENGINE=InnoDB;\n"
        )
        findings, *_ = self.run_lint(
            self.MACHINE + "\n> 状态语义：已完成=FINISHED。\n")
        self.assertNotIn("L15", _rules(findings))

    def test_non_lifecycle_declaration_downgrades_minor(self):
        """豁免：枚举缺失但报告显式声明「过程字段，不入状态机」→ MINOR。"""
        self.write_sql(self.ROUTING_SQL)
        findings, *_ = self.run_lint(
            self.MACHINE + "\n> routing_status 为过程字段，不入状态机。\n")
        l15 = [(lv, d) for lv, r, d in findings if r == "L15"]
        self.assertEqual(len(l15), 1)
        self.assertEqual(l15[0][0], "MINOR")

    def test_no_state_machine_not_fired(self):
        """防误伤：无状态机（没图谈何一致）不启用本规则。"""
        self.write_sql(self.ROUTING_SQL)
        findings, *_ = self.run_lint("# 报告\n\n纯文本报告，无图。\n")
        self.assertNotIn("L15", _rules(findings))

    def test_non_status_comment_not_scanned(self):
        """防误伤：注释无「状态」字样的枚举列（费用类型等）不扫。"""
        self.write_sql(
            "CREATE TABLE t_pre_cost (\n"
            "    fee_type VARCHAR(32) DEFAULT NULL COMMENT '费用类型 COMMON/OTHER',\n"
            "    PRIMARY KEY (id)\n"
            ") ENGINE=InnoDB;\n", name="t_pre_cost.sql")
        findings, *_ = self.run_lint(self.MACHINE)
        self.assertNotIn("L15", _rules(findings))


class L16DrawingZeroOutputTests(LintTestBase):
    """L16（MINOR，错题集 2026-08-14 假探测案）：draft 有图/有清单但 ✏️ 标注为零
    → 绘图探测疑似未开机。显式声明无需画图 → 豁免（复用 alignment 词表单源）。"""

    def write_draft(self, text: str):
        d = self.root / "_review"
        d.mkdir(exist_ok=True)
        (d / "analysis-draft.md").write_text(text, encoding="utf-8")

    def test_tagged_gaps_all_reading_layer_minor(self):
        """阳性：有 mermaid 图但断层全标 📄 → L16 MINOR。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [📄阅读层] 费率口径未说明\n  1. a\n  2. b\n  3. c\n")
        findings, *_ = self.run_lint("# 报告\n")
        l16 = [(lv, d) for lv, r, d in findings if r == "L16"]
        self.assertEqual(len(l16), 1)
        self.assertEqual(l16[0][0], "MINOR")

    def test_legacy_untagged_gap_list_minor(self):
        """逼升级：旧格式清单（无层标注）也报 L16 MINOR。"""
        self.write_draft("# 草稿\n\n### G1 🔴 📋 费率口径未说明\n  1. a\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertIn("L16", _rules(findings))

    def test_drawing_gap_present_silent(self):
        """阴性：✏️ ≥1 → 探测开过机，不报。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [✏️绘图层] 提交后跳转到哪（TBD1）\n  占位: TBD1\n  1. a\n  2. b\n  3. c\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L16", _rules(findings))

    def test_no_diagram_declaration_exempt(self):
        """豁免：显式声明「无需画图」→ 静默。"""
        self.write_draft("# 草稿\n\n无需画图：纯文案变更，不触发任何图。\n\n"
                         "### G1 🟡 📋 [📄阅读层] 文案口径未定\n  1. a\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L16", _rules(findings))

    def test_no_draft_not_fired(self):
        """防误伤：无 draft 不启用。"""
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L16", _rules(findings))

    def test_mermaid_but_zero_gap_list_silent(self):
        """防误伤（评审 2026-08-14 ①收紧）：有图但真零断层清单的干净需求
        不报——playbook 原文「断层全部来自阅读层」才是红旗，「零断层」不是。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "（宿主判定无歧义点）\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L16", _rules(findings))


class L18LayerTypeMatchTests(LintTestBase):
    """L18（MAJOR）：📄 断层命中图结构词表且无 落图: 声明 → 报。
    🔴 来源层不罚（从 wiki/旧代码找到答案是加分项），罚的是图结构断层无图内落点。"""

    def write_draft(self, text: str):
        d = self.root / "_review"
        d.mkdir(exist_ok=True)
        (d / "analysis-draft.md").write_text(text, encoding="utf-8")

    def test_reading_gap_graph_word_no_landing_major(self):
        """阳性：📄「提交成功后的跳转分支未说明」无落图声明 → L18 MAJOR。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [✏️绘图层] 签署超时进什么态（TBD1）\n  占位: TBD1\n  1. a\n  2. b\n  3. c\n\n"
            "### G2 🔴 📋 [📄阅读层] 提交成功后的跳转分支未说明\n  1. a\n  2. b\n  3. c\n")
        findings, *_ = self.run_lint("# 报告\n")
        l18 = [(lv, d) for lv, r, d in findings if r == "L18"]
        self.assertEqual(len(l18), 1, f"L18 未抓伪装: {findings}")
        self.assertEqual(l18[0][0], "MAJOR")
        self.assertIn("G2", l18[0][1])

    def test_reading_gap_with_landing_silent(self):
        """阴性：📄 命中图结构词但有 落图: 声明（含锚点形态）→ 不报。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [✏️绘图层] 签署超时进什么态（TBD1）\n  占位: TBD1\n  1. a\n  2. b\n  3. c\n\n"
            "### G2 🔴 📋 [📄阅读层] 提交成功后的跳转分支未说明\n"
            "  落图: 状态机 SUBMITTED-->WAITING 边（wiki 代码实证）\n  1. a\n  2. b\n  3. c\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L18", _rules(findings))

    def test_drawing_gap_not_checked(self):
        """✏️ 断层自带占位坐标，L18 不查。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [✏️绘图层] 提交后的跳转分支（TBD1）\n  占位: TBD1\n  1. a\n  2. b\n  3. c\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L18", _rules(findings))

    def test_pure_text_gap_not_fined(self):
        """防误伤：📄「埋点事件命名规范未定」不命中图结构词 → 静默。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [✏️绘图层] 签署超时进什么态（TBD1）\n  占位: TBD1\n  1. a\n  2. b\n  3. c\n\n"
            "### G2 🟡 📋 [📄阅读层] 埋点事件命名规范未定\n  1. a\n  2. b\n  3. c\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L18", _rules(findings))

    def test_legacy_untagged_not_fired(self):
        """防误伤：旧格式（无层标注）L18 不启用——逼升级由 L16 承担。"""
        self.write_draft("# 草稿\n\n### G1 🔴 📋 提交成功后的跳转分支未说明\n  1. a\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L18", _rules(findings))


class L20TbdResolutionTests(LintTestBase):
    """L20（MAJOR）：每个 ✏️ 占位的 TBDn 必须在 alignment-log 有核销留痕——
    编造占位 = 伪造探测。只扫 tbd 凭据字段，不扫散文正文。"""

    def write_draft(self, text: str):
        d = self.root / "_review"
        d.mkdir(exist_ok=True)
        (d / "analysis-draft.md").write_text(text, encoding="utf-8")

    def write_log(self, text: str):
        (self.root / "_review" / "alignment-log.md").write_text(text, encoding="utf-8")

    DRAFT = (
        "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
        "### G1 🔴 📋 [✏️绘图层] 提交后跳转到哪（TBD1）\n  占位: TBD1\n  1. a\n  2. b\n  3. c\n")

    def test_tbd_without_resolution_major(self):
        """阳性：✏️ TBD1 在 alignment-log 无任何核销记录 → L20 MAJOR。"""
        self.write_draft(self.DRAFT)
        self.write_log("# 流水\n\n## Q1 其他题\n- 落点：§3.5\n")
        findings, *_ = self.run_lint("# 报告\n")
        l20 = [(lv, d) for lv, r, d in findings if r == "L20"]
        self.assertEqual(len(l20), 1, f"L20 未抓编造占位: {findings}")
        self.assertEqual(l20[0][0], "MAJOR")
        self.assertIn("TBD1", l20[0][1])

    def test_tbd_with_resolution_silent(self):
        """阴性：alignment-log 的核销流水含 TBD1 编号 → 不报。"""
        self.write_draft(self.DRAFT)
        self.write_log("# 流水\n\n## Q1 提交后跳转到哪（TBD1）\n- 人类原话：按步骤语义\n- 落点：§3.5\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L20", _rules(findings))

    def test_reading_layer_only_not_scanned(self):
        """防误伤：只有 📄 断层（无 tbd 凭据）→ L20 不扫。"""
        self.write_draft(
            "# 草稿\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n\n"
            "### G1 🔴 📋 [📄阅读层] 费率口径未说明\n  1. a\n  2. b\n  3. c\n")
        self.write_log("# 流水\n\n## Q1 费率口径\n- 落点：§3.7\n")
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L20", _rules(findings))

    def test_no_log_not_fired(self):
        """防误伤：alignment-log 不存在时无从对账，不报（未进核销阶段）。"""
        self.write_draft(self.DRAFT)
        findings, *_ = self.run_lint("# 报告\n")
        self.assertNotIn("L20", _rules(findings))


if __name__ == "__main__":
    unittest.main()
