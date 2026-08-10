"""Entry point.

MCP transport:
  stdio (default) — spawned by Claude Code / Pi agent as a child process.
                    The whole bridge lives and dies with the agent session;
                    no standalone service, no daemon.
  sse             — network MCP transport for remote/HTTP-capable clients.

intent-gate 是轻量插件：single 通道（对话框兜底），无常驻监听、无外部凭据。
钉钉群通道与决策闸门在姊妹篇 intent-gate-service（独立 MCP 服务，见 ../intent-gate-service/）。

Usage:
  intent-gate                          # stdio MCP
  intent-gate --mcp-transport sse      # SSE MCP on --mcp-port
"""

from __future__ import annotations

import argparse
import asyncio

from mcp.server.fastmcp import FastMCP

from .alignment.manager import AlignmentManager
from .alignment.tools import register_alignment_tools
from .analysis.tools import register_analysis_tools
from .config import Settings
from .logging import get_logger, setup_logging

log = get_logger("main")

_INSTRUCTIONS = (
    "Requirement intent-alignment engine. Before analyzing any requirement, "
    "read the prompt doc_analysis_playbook in full — it is the law. Resolve "
    "ambiguity through the funnel (code evidence → registered inference → "
    "structured question), never by silent assumption. Every question is "
    "dispatched non-blocking and persisted to files; every settled answer "
    "must land on a precise artifact anchor via resolve_question. Delivery "
    "requires lint_summary with zero CRITICAL."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="intent-gate")
    p.add_argument("--mcp-transport", choices=["stdio", "sse"], default="stdio")
    p.add_argument("--mcp-host", default="127.0.0.1")
    p.add_argument("--mcp-port", type=int, default=8400)
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()  # raises with a clear message on misconfiguration
    setup_logging(settings.log_level)

    # 意图对齐子系统（single 通道，DESIGN.md §5）。钉钉不参与，config 免凭据。
    alignment = AlignmentManager(workspace_root=settings.workspace_root)

    mcp = FastMCP("intent-gate", instructions=_INSTRUCTIONS)
    register_alignment_tools(mcp, alignment)
    # 需求解析引擎（fresh/resume 双路径，不含任何评审内容）
    register_analysis_tools(mcp, settings.workspace_root)
    log.info("channel=single（对话框兜底），钉钉群通道见姊妹篇 intent-gate-service")

    if args.mcp_transport == "stdio":
        await mcp.run_stdio_async()
    else:
        mcp.settings.host = args.mcp_host
        mcp.settings.port = args.mcp_port
        await mcp.run_sse_async()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
