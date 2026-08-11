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


if __name__ == "__main__":
    unittest.main()
