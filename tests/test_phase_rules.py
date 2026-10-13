"""phase_rules 铁律重注入：工具返回值尾部附「本工具最容易违反的铁律」。

锁住三条契约：
  ① 代表性工具（dispatch_question/resolve_question/lint_summary/analyze_requirement）
     的返回 dict 必含 phase_rules，3-5 条非空字符串、每条长度受控；
     ok:False 的失败返回同样过 helper。
  ② helper 全兜底：非 dict 原样返回、坏路径/坏类型不炸——铁律是提醒，
     绝不能弄炸工具本身。
  ③ lint_summary/draft_mapping 不带 feature 参数，按目录约定反推
     （{root}/.harness/requests/{feature}/summary.md）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from intent_gate.alignment.manager import AlignmentManager
from intent_gate.alignment.tools import register_alignment_tools
from intent_gate.analysis.tools import register_analysis_tools
from intent_gate.phase_rules import feature_from_summary_path, with_phase_rules


def _tool_fn(mcp: FastMCP, name: str) -> Callable[..., Any]:
    """取出 FastMCP 注册的原函数（绕开 stdio，直接断言工具层行为）。"""
    return mcp._tool_manager._tools[name].fn  # type: ignore[attr-defined]


def _mcp(root: Path) -> FastMCP:
    mcp = FastMCP("t")
    register_alignment_tools(mcp, AlignmentManager(root))
    register_analysis_tools(mcp, root)
    return mcp


def _assert_phase_rules(result: dict) -> None:
    rules = result.get("phase_rules")
    assert isinstance(rules, list) and 3 <= len(rules) <= 5, result
    for r in rules:
        assert isinstance(r, str) and r.strip(), result
        assert len(r) <= 80, f"铁律超长（{len(r)} 字符）: {r}"


# ------------------------------------------------------------------ helper 单元


def test_helper_appends_rules_to_dict() -> None:
    got = with_phase_rules({"ok": True}, "lint_summary")
    assert got["ok"] is True  # 现有字段原样保留
    _assert_phase_rules(got)


def test_helper_passthrough_non_dict() -> None:
    """collect_answers 返回 list——非 dict 原样返回，不强塞字段。"""
    assert with_phase_rules([{"a": 1}], "collect_answers") == [{"a": 1}]
    assert with_phase_rules("str", "x") == "str"


def test_helper_unknown_tool_gets_generic_rules() -> None:
    got = with_phase_rules({"ok": True}, "no_such_tool")
    _assert_phase_rules(got)


def test_helper_survives_bad_inputs() -> None:
    """坏路径/坏类型都不炸：相位算不出就降级为工具静态条。"""
    got = with_phase_rules({"ok": True, "mode": "resume"}, "analyze_requirement",
                           workspace_root=object(), feature="f")
    _assert_phase_rules(got)
    missing = with_phase_rules({"ok": True}, "lint_summary",
                               workspace_root=Path("nowhere-xyz"), feature=None)
    _assert_phase_rules(missing)


def test_feature_from_summary_path() -> None:
    root = Path("/p")
    assert feature_from_summary_path(
        root, ".harness/requests/tickets/summary.md") == "tickets"
    assert feature_from_summary_path(root, "/p/sql/t.sql") is None
    assert feature_from_summary_path(object(), "x") is None  # 坏输入不炸


def test_analyze_resume_tail_rule_by_phase(tmp_path: Path) -> None:
    """相位特化：resume 且命中 generate 相位时尾条换成相位当前动作。"""
    (tmp_path / ".harness" / "requests" / "f" / "_review").mkdir(parents=True)
    got = with_phase_rules({"ok": True, "mode": "resume"}, "analyze_requirement",
                           tmp_path, "f")
    _assert_phase_rules(got)
    assert "Phase B" in got["phase_rules"][-1]
    # fresh 模式不触发相位特化（现场还没有，铁律保持静态条）
    fresh = with_phase_rules({"ok": True, "mode": "fresh"}, "analyze_requirement",
                             tmp_path, "f")
    assert "record_judgment" in fresh["phase_rules"][-1]


# ------------------------------------------------------------ MCP 工具面集成


def test_dispatch_question_carries_phase_rules(tmp_path: Path) -> None:
    fn = _tool_fn(_mcp(tmp_path), "dispatch_question")
    got = asyncio.run(fn("f", "核心断层题", "🔧", ["a", "b", "c"], severity="🔴"))
    assert got["ok"] is True
    _assert_phase_rules(got)
    assert any("一题一问" in r for r in got["phase_rules"])


def test_resolve_question_carries_phase_rules(tmp_path: Path) -> None:
    mcp = _mcp(tmp_path)
    dispatch = _tool_fn(mcp, "dispatch_question")
    resolve = _tool_fn(mcp, "resolve_question")
    token = asyncio.run(dispatch("f", "题", "📋", ["a", "b", "c"]))["token"]
    # 核销门禁：先落一份含 mermaid 的绘图层草稿
    d = tmp_path / ".harness" / "requests" / "f" / "_review"
    (d / "analysis-draft.md").write_text(
        "# 草稿\n\n```mermaid\nstateDiagram-v2\n    A --> B: x (DB_INSERT)\n```\n",
        encoding="utf-8")
    got = resolve("f", token, "选1", "张三", "语义", "落点")
    assert got["ok"] is True
    _assert_phase_rules(got)
    assert any("FORBIDDEN" in r for r in got["phase_rules"])


def test_lint_summary_carries_phase_rules_even_on_failure(tmp_path: Path) -> None:
    fn = _tool_fn(_mcp(tmp_path), "lint_summary")
    d = tmp_path / ".harness" / "requests" / "tickets"
    (d / "_review").mkdir(parents=True)
    (d / "summary.md").write_text(
        "# 报告\n\n```mermaid\nstateDiagram-v2\n    DRAFT --> SUCCESS: 直达\n```\n",
        encoding="utf-8")
    got = fn(".harness/requests/tickets/summary.md", project_path=str(tmp_path))
    assert got["ok"] is True
    _assert_phase_rules(got)
    assert any("CRITICAL" in r for r in got["phase_rules"])
    # 失败返回（summary 不存在）同样过 helper
    bad = fn(".harness/requests/ghost/summary.md", project_path=str(tmp_path))
    assert bad["ok"] is False
    _assert_phase_rules(bad)


def test_analyze_requirement_carries_phase_rules(tmp_path: Path) -> None:
    fn = _tool_fn(_mcp(tmp_path), "analyze_requirement")
    prd = tmp_path / "prd.md"
    prd.write_text("# 需求\n做一个工单表。\n", encoding="utf-8")
    got = fn("feat", str(prd), project_path=str(tmp_path))
    assert got["mode"] == "fresh"
    _assert_phase_rules(got)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
