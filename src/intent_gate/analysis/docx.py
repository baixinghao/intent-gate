# -*- coding: utf-8 -*-
"""docx 文本提取（主力战场：需求文档多为 Word）。

两级引擎，**无静默降级**：
  Tier 1: markitdown —— 用户环境已有的（如配合其他文档 MCP 装的），自动复用，
     表格/合并单元格增强；日志记录 "engine: markitdown"
  Tier 2: mammoth    —— 硬依赖（intent-gate 核心依赖，pipx/pip 安装时自动带上），
     默认路径；日志记录 "engine: mammoth" + 提示 "pip install markitdown for enhanced tables"

mammoth 缺失（环境损坏）→ 拒绝 + 明确修复指令——宁可拒绝，不产出低质量文本。

.doc（OLE 老格式）不支持：如实报错并给指引，不越界造轮子。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

log = logging.getLogger("intent_gate.docx")

# 引擎探测缓存：None=未探测，探测后固定为 "markitdown" | "mammoth"
_ENGINE: str | None = None


def _detect_engine() -> str:
    """探测可用引擎（惰性：首次转换时才 import，MCP 启动不受影响）。

    优先复用用户环境已有的 markitdown；否则走硬依赖 mammoth。
    两者皆无（环境损坏）→ RuntimeError（带修复指令），拒绝启动。"""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    try:
        import markitdown  # noqa: F401  # 用户环境已有的，复用增强
        _ENGINE = "markitdown"
        log.info("engine: markitdown")
    except ImportError:
        try:
            import mammoth  # noqa: F401  # 核心依赖，正常安装必有
            _ENGINE = "mammoth"
            log.info("engine: mammoth (pip install markitdown for enhanced tables)")
        except ImportError:
            raise RuntimeError(
                "docx 引擎缺失：mammoth 未安装。请重新安装 intent-gate"
                "（pipx install intent-gate-mcp，mammoth 为核心依赖自动带上），"
                "或手动 pip install mammoth。"
            ) from None
    return _ENGINE


def _read_bytes(path: Path) -> io.BytesIO:
    """读文件进内存并立即释放句柄（Windows 下按路径传给引擎会残留句柄，
    导致临时文件无法清理；BytesIO 无系统句柄）。"""
    bio = io.BytesIO(path.read_bytes())
    bio.name = path.name  # markitdown 靠 name 推断格式
    return bio


def _extract_markitdown(path: Path) -> str | None:
    """Tier 1 markitdown：docx → Markdown（表格/合并单元格增强）。失败返回 None 降级。"""
    try:
        from markitdown import MarkItDown
        result = MarkItDown().convert(_read_bytes(path))
        text = (result.text_content or "").strip()
        return text or None
    except Exception as exc:
        log.warning("markitdown 转换失败（%s），降级 mammoth", exc)
        return None


def _extract_mammoth(path: Path) -> str:
    """Tier 2 mammoth（硬依赖）：docx → Markdown（表格保留）。

    转换失败（损坏/老格式）抛 ValueError（带下一步指引）。"""
    try:
        import mammoth
    except ImportError:
        raise ValueError(
            "docx 引擎缺失：mammoth 未安装。请重新安装 intent-gate"
            "（pipx install intent-gate-mcp，mammoth 为核心依赖自动带上），"
            "或手动 pip install mammoth。"
        ) from None
    try:
        result = mammoth.convert_to_markdown(_read_bytes(path))
        text = result.value.strip()
    except Exception as exc:
        raise ValueError(
            f"{path.name} 转换失败（{exc}）。若为 .doc 老格式，请用 Word「另存为」"
            "转成 .docx 或纯文本(.txt)；文档损坏请用 Word 修复后重试。"
        ) from None
    if not text:
        raise ValueError(
            f"{path.name} 提取不到正文文本（空文档或全图片），请检查文档内容。"
        )
    return text


def extract_text(path: Path) -> str:
    """提取 .docx 文本。Tier 1 markitdown（增强）→ Tier 2 mammoth（硬依赖）。

    mammoth 缺失/全部失败 → 拒绝（ValueError/RuntimeError 带修复指令），
    绝不静默产出低质量文本。"""
    try:
        engine = _detect_engine()
    except RuntimeError:
        raise ValueError(
            "docx 引擎缺失：mammoth 未安装。请重新安装 intent-gate"
            "（pipx install intent-gate-mcp，mammoth 为核心依赖自动带上），"
            "或手动 pip install mammoth。"
        ) from None
    if engine == "markitdown":
        text = _extract_markitdown(path)
        if text:
            return text
    return _extract_mammoth(path)
