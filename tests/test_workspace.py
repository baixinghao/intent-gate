"""工作区参数化：project_path 优先 / 缺省回落 / 相对路径按生效项目根解析。

回归对象：MCP server 在宿主里是**全局单例**，服务该宿主的所有工作区。启动时
绑定（cwd / HG_WORKSPACE_ROOT）只能表达一个项目根——换工作区后所有 .harness
落盘会静默落到启动目录去。本模块锁住两条纪律：

  ① 路径基准 = **生效的项目根**，永不按进程 cwd（lint/mapper 此前漏了，cwd 一变
     就跑到别的项目去找 summary）。
  ② 项目根是**每次调用的参数**，不是全局可变状态——宿主会并发跑 subagent，
     全局 set 会让并发 agent 互相污染（故本模块刻意不测"设一次全局生效"，
     因为那个设计被否掉了）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from intent_gate.alignment.manager import AlignmentManager
from intent_gate.alignment.tools import register_alignment_tools
from intent_gate.analysis.lint import run_lint
from intent_gate.analysis.mapper import run_mapper
from intent_gate.analysis.tools import register_analysis_tools
from intent_gate.workspace import resolve_root, resolve_under_root

_REL_SUMMARY = ".harness/requests/tickets/summary.md"

_MINIMAL_SUMMARY = """---
feature: tickets
status: draft
---

# tickets 需求分析报告

```mermaid
stateDiagram-v2
    [*] --> 待处理: 建单
    待处理 --> 已关闭: 关单
    已关闭 --> [*]
```
"""


def _make_summary(root: Path, feature: str = "tickets") -> Path:
    d = root / ".harness" / "requests" / feature
    (d / "_review").mkdir(parents=True, exist_ok=True)
    s = d / "summary.md"
    s.write_text(_MINIMAL_SUMMARY, encoding="utf-8")
    return s


def _tool_fn(mcp: FastMCP, name: str) -> Callable[..., Any]:
    """取出 FastMCP 注册的原函数（绕开 stdio，直接断言工具层行为）。"""
    return mcp._tool_manager._tools[name].fn  # type: ignore[attr-defined]


# ------------------------------------------------------------------ helper 单元


def test_resolve_root_prefers_project_path(tmp_path: Path) -> None:
    assert resolve_root(tmp_path / "explicit", tmp_path / "default") == (
        tmp_path / "explicit"
    ).resolve()


def test_resolve_root_falls_back_on_none(tmp_path: Path) -> None:
    assert resolve_root(None, tmp_path) == tmp_path.resolve()


def test_resolve_root_falls_back_on_blank(tmp_path: Path) -> None:
    """空白串按"没填"处理。

    宿主 LLM 传 "" 表示未指定；若当成相对路径，会被解析成 cwd 下的空目录名，
    比不传更危险。
    """
    assert resolve_root("", tmp_path) == tmp_path.resolve()
    assert resolve_root("   ", tmp_path) == tmp_path.resolve()


def test_resolve_root_returns_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_root(".", tmp_path).is_absolute()


def test_resolve_under_root_keeps_absolute(tmp_path: Path) -> None:
    p = tmp_path / "x" / "y.md"
    assert resolve_under_root(tmp_path, p) == p


def test_resolve_under_root_joins_relative(tmp_path: Path) -> None:
    got = resolve_under_root(tmp_path, Path(_REL_SUMMARY))
    assert got == tmp_path.resolve() / ".harness" / "requests" / "tickets" / "summary.md"


# ---------------------------------------------------- 核心回归：不按 cwd 解析


def test_run_lint_resolves_relative_against_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 回归：相对路径按 workspace_root 解析，不按进程 cwd。

    修复前 run_lint 直接 Path(summary_path) 交给 cwd——多工作区宿主里 cwd 是
    启动目录，于是"检查 A 项目"静默变成"去 B 项目找同名文件"。
    """
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _make_summary(project)
    monkeypatch.chdir(elsewhere)  # cwd 故意指向无关目录

    got = run_lint(_REL_SUMMARY, workspace_root=project)
    assert got["ok"] is True, got

    # 不传 workspace_root 时保持旧行为（按 cwd）→ 此处必须找不到，证明基准确实换了
    legacy = run_lint(_REL_SUMMARY)
    assert legacy["ok"] is False
    assert "不存在" in legacy["reason"]


def test_run_mapper_resolves_relative_against_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """draft_mapping 与 lint_summary 同一基准（此前两者都按 cwd）。"""
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _make_summary(project)
    monkeypatch.chdir(elsewhere)

    # 现场有 summary 但无 alignment-log：报"无 Q 记录"= 文件已找到（基准对了）
    got = run_mapper(_REL_SUMMARY, workspace_root=project)
    assert got["ok"] is False
    assert "alignment-log" in got["reason"]

    # 不传则按 cwd 找 → 连 summary 都不存在
    legacy = run_mapper(_REL_SUMMARY)
    assert "不存在" in legacy["reason"]


def test_run_lint_absolute_path_ignores_workspace_root(tmp_path: Path) -> None:
    """绝对路径不受 workspace_root 影响（显式路径永远优先）。"""
    project = tmp_path / "project"
    _make_summary(project)
    other_root = tmp_path / "other_root"
    other_root.mkdir()
    got = run_lint(str(project / _REL_SUMMARY), workspace_root=other_root)
    assert got["ok"] is True, got


# ------------------------------------------------------------ MCP 工具面契约


def _register_both(mcp: FastMCP, default_root: Path) -> None:
    register_alignment_tools(mcp, AlignmentManager(default_root))
    register_analysis_tools(mcp, default_root)


def test_every_tool_exposes_project_path(tmp_path: Path) -> None:
    """🔴 契约：每个工具都必须把 project_path 暴露给 LLM。

    不暴露 = 宿主 agent 无从指定项目根，多工作区下只能吃启动目录的亏。
    新增工具若漏了这个参数，本测试立刻红。
    """
    mcp = FastMCP("t")
    _register_both(mcp, tmp_path)
    tools = asyncio.run(mcp.list_tools())
    assert tools, "未注册到任何工具"
    for t in tools:
        props = (t.inputSchema or {}).get("properties", {})
        assert "project_path" in props, f"工具 {t.name} 未暴露 project_path"
        assert "project_path" not in (t.inputSchema or {}).get("required", []), (
            f"工具 {t.name} 把 project_path 标成了必填——会破坏既有客户端调用"
        )
    names = {t.name for t in tools}
    assert "describe_workspace" in names


def test_describe_workspace_reports_effective_root(tmp_path: Path) -> None:
    mcp = FastMCP("t")
    _register_both(mcp, tmp_path / "default_root")
    fn = _tool_fn(mcp, "describe_workspace")

    explicit = tmp_path / "explicit_root"
    explicit.mkdir()
    (explicit / ".harness" / "requests" / "tickets").mkdir(parents=True)

    got = fn(project_path=str(explicit))
    assert got["workspace_root"] == str(explicit.resolve())
    assert got["source"] == "project_path"
    assert got["harness_exists"] is True
    assert got["features"] == ["tickets"]

    fallback = fn()
    assert fallback["workspace_root"] == str((tmp_path / "default_root").resolve())
    assert fallback["features"] == []


def test_tools_use_project_path_per_call(tmp_path: Path) -> None:
    """两个项目根交替调用互不污染——这正是"全局 set"方案被否掉的原因。

    宿主会并发跑 subagent；若项目根是全局可变状态，并发调用会串项目。
    参数化后每次调用自带根，顺序无关、并发无关。
    """
    mcp = FastMCP("t")
    _register_both(mcp, tmp_path / "default_root")
    analyze = _tool_fn(mcp, "analyze_requirement")

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    prd = tmp_path / "prd.md"
    prd.write_text("# 需求\n做一个工单表。\n", encoding="utf-8")

    assert analyze("feat", str(prd), project_path=str(a))["mode"] == "fresh"
    assert analyze("feat", str(prd), project_path=str(b))["mode"] == "fresh"

    assert (a / ".harness" / "requests" / "feat").is_dir()
    assert (b / ".harness" / "requests" / "feat").is_dir()
    # 缺省根不得被写过（没有 project_path 的调用从未发生）
    assert not (tmp_path / "default_root" / ".harness").exists()


def test_pending_questions_reads_explicit_root(tmp_path: Path) -> None:
    """list_pending_questions 带 project_path 时读的是那个项目的现场。

    缺省根下没有该需求时它会返回空清单（看起来像"意图已齐"），所以根必须显式。
    """
    mcp = FastMCP("t")
    _register_both(mcp, tmp_path / "default_root")
    fn = _tool_fn(mcp, "list_pending_questions")

    project = tmp_path / "project"
    _make_summary(project)

    got = fn("tickets", project_path=str(project))
    assert got["ok"] is True
    assert got["pending_questions"] == 0

    missing = fn("tickets", project_path=str(tmp_path / "nowhere"))
    assert missing["ok"] is False or missing.get("pending_questions") == 0
