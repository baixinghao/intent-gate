# -*- coding: utf-8 -*-
"""session-start 纪律注入的 Python 发射器（install --target 的配套运行时）。

为什么需要它：bash 版 hooks/session-start 依赖 ${CLAUDE_PLUGIN_ROOT} 等插件
环境变量定位 PLUGIN_ROOT——那是 Claude Code 插件场景。uv/pipx 装完 server 后
走 `intent-gate install --target …` 接线的 agent（Cursor/Codex/…）没有插件目录，
hook 命令必须是纯 Python、零 bash、零插件目录依赖。

输出契约按目标 agent 分叉（与 bash 版三个分支一一对应）：
  cursor   → {"additional_context": "…"}                （snake_case）
  claude   → {"hookSpecificOutput": {"hookEventName": "SessionStart",
             "additionalContext": "…"}}                   （嵌套 camelCase）
  standard → {"additionalContext": "…"}                   （顶层，SDK 标准）
"""
from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path

# 单一事实源：skills/using-intent-gate/SKILL.md，经 hatch force-include
# 打进 wheel（intent_gate/_assets/）。禁止在 Python 里手抄第二份。
_SKILL_RESOURCE = "using-intent-gate.SKILL.md"


def _skill_text() -> str:
    """wheel 内读 _assets（force-include 产物）；源码树（测试/dev）回退读仓库真身。"""
    try:
        return (resources.files("intent_gate._assets") / _SKILL_RESOURCE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        repo_copy = (Path(__file__).resolve().parent.parent.parent
                     / "skills" / "using-intent-gate" / "SKILL.md")
        return repo_copy.read_text(encoding="utf-8")


def build_session_context() -> str:
    """与 bash 版 session-start 完全同构的注入文本。"""
    mcp_warning = ""
    if shutil.which("intent-gate") is None:
        mcp_warning = (
            "⚠️ INTENT-GATE MCP SERVER NOT RUNNING: command 'intent-gate' not found on PATH. "
            "Discipline skills are injected, but ALL mechanical gates are unavailable "
            "(no question persistence, no lint enforcement). Tell the human to run: "
            "pipx install intent-gate-mcp — then restart the session.\n\n"
        )
    return (
        "<EXTREMELY_IMPORTANT>\nYou have a human escalation gate (intent-gate MCP).\n\n"
        f"{mcp_warning}"
        "**Below is the full content of your 'intent-gate:using-intent-gate' skill - "
        "your introduction to when and how to escalate decisions to humans. "
        "Read it before acting on anything it covers:**\n\n"
        f"{_skill_text()}\n</EXTREMELY_IMPORTANT>"
    )


def emit_session_start(fmt: str) -> str:
    """按目标 agent 的输出契约渲染 JSON 字符串。"""
    ctx = build_session_context()
    if fmt == "cursor":
        payload = {"additional_context": ctx}
    elif fmt == "claude":
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": ctx,
            }
        }
    elif fmt == "standard":
        payload = {"additionalContext": ctx}
    else:
        raise ValueError(f"未知输出契约: {fmt}（可选 cursor/claude/standard）")
    return json.dumps(payload, ensure_ascii=False)
