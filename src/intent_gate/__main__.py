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
  intent-gate install --target cursor  # 把 session-start 纪律注入接进 agent hooks
  intent-gate hook session-start --format cursor   # 上者的配套 hook 命令
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mcp.server.fastmcp import FastMCP

from .alignment.manager import AlignmentManager
from .alignment.tools import register_alignment_tools
from .analysis.tools import register_analysis_tools
from .config import Settings
from .hook import emit_session_start
from .installer import SUPPORTED_TARGETS, install, uninstall
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
    sub = p.add_subparsers(dest="cmd")

    hook_p = sub.add_parser("hook", help="发射 hook 输出（install --target 的配套命令）")
    hook_p.add_argument("event", choices=["session-start"])
    hook_p.add_argument("--format", choices=["cursor", "claude", "standard"],
                        default="standard")

    for name, helptext in (("install", "把 intent-gate 接进 agent 配置（cursor/codex hooks，或 dsh 的 cordis.patch.yml + skills）"),
                           ("uninstall", "拆除 install --target 接的线（只拆自己的）")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--target", required=True, choices=SUPPORTED_TARGETS)
        sp.add_argument("--dry-run", action="store_true",
                        help="只打印将要写入的内容，不落盘")

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


def _force_utf8_stdio() -> None:
    """Windows GBK 控制台下 emoji 输出会崩（UnicodeEncodeError: 'gbk' codec...）。

    hook 注入文本与 install/uninstall 的 JSON 都可能含 emoji（skill 文本的 📋 等）；
    消费方（Claude Code hook / 终端 / 重定向）按 UTF-8 读 JSON，强制 UTF-8 输出
    同时修掉崩溃与潜在乱码。MCP stdio 走二进制管道，不经过 text 层，不受影响。
    任何失败（流已关闭/不支持 reconfigure）静默降级为原行为，不改变既有功能。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main() -> None:
    _force_utf8_stdio()
    args = parse_args()
    if args.cmd == "hook":
        print(emit_session_start(args.format))
        return
    if args.cmd in ("install", "uninstall"):
        fn = install if args.cmd == "install" else uninstall
        print(json.dumps(fn(args.target, dry_run=args.dry_run),
                         ensure_ascii=False, indent=2))
        return
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
