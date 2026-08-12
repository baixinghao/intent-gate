# -*- coding: utf-8 -*-
"""install/uninstall --target：把 session-start 纪律注入接进各家 agent 的 hooks 配置。

为什么存在：hook 本体（intent-gate hook session-start）是纯 Python、可移植的；
真正的门槛是「接线」——每家 agent 的 hooks 配置格式都不一样，README 教不动。

支持面（只收已核实配置格式的目标，未核实的不瞎写）：
  cursor  ~/.cursor/hooks.json   {"version":1,"hooks":{"sessionStart":[…]}}
  codex   ~/.codex/config.toml   [[hooks.SessionStart]] + [[hooks.SessionStart.hooks]]
                                  （Codex 契约官方对齐 Claude：stdin JSON / exit 2 阻断）
铁律：合并而非覆盖——用户已有的第三方 hooks 条目一条不动；
幂等——重复 install 不产生重复条目；uninstall 只拆自己接的线。
"""
from __future__ import annotations

import json
from pathlib import Path

SUPPORTED_TARGETS = ("cursor", "codex")

_HOOK_CMD = {
    "cursor": "intent-gate hook session-start --format cursor",
    # Codex hooks 契约官方对齐 Claude Code（stdin JSON，exit 2 阻断），
    # 故复用 claude 输出契约。
    "codex": "intent-gate hook session-start --format claude",
}

_IDENTITY = "intent-gate hook session-start"  # 幂等/卸载的识别标记


def target_path(target: str, home: Path | None = None) -> Path:
    home = home or Path.home()
    if target == "cursor":
        return home / ".cursor" / "hooks.json"
    if target == "codex":
        return home / ".codex" / "config.toml"
    raise ValueError(f"不支持的目标: {target}（当前支持：{', '.join(SUPPORTED_TARGETS)}）")


# ---------------------------------------------------------------- cursor
def _merge_cursor(existing: dict, command: str) -> tuple[dict, bool]:
    """合并 sessionStart 条目；返回 (新配置, 是否有变化)。幂等按 command 识别。"""
    cfg = dict(existing)
    cfg.setdefault("version", 1)
    hooks = cfg.setdefault("hooks", {})
    entries = hooks.setdefault("sessionStart", [])
    if any(_IDENTITY in e.get("command", "") for e in entries):
        return cfg, False
    entries.append({"command": command})
    return cfg, True


def _unmerge_cursor(existing: dict) -> tuple[dict, bool]:
    hooks = existing.get("hooks", {})
    entries = hooks.get("sessionStart", [])
    kept = [e for e in entries if _IDENTITY not in e.get("command", "")]
    if len(kept) == len(entries):
        return existing, False
    cfg = dict(existing)
    cfg["hooks"] = {**hooks, "sessionStart": kept}
    return cfg, True


# ---------------------------------------------------------------- codex
_CODEX_BLOCK = (
    "\n[[hooks.SessionStart]]\n"
    "[[hooks.SessionStart.hooks]]\n"
    f'command = "{_HOOK_CMD["codex"]}"\n'
)


def _merge_codex(existing: str) -> tuple[str, bool]:
    if _IDENTITY in existing:
        return existing, False
    return existing.rstrip("\n") + "\n" + _CODEX_BLOCK.lstrip("\n"), True


def _unmerge_codex(existing: str) -> tuple[str, bool]:
    if _IDENTITY not in existing:
        return existing, False
    block = _CODEX_BLOCK.strip("\n")
    text = existing
    while block in text:
        text = text.replace(block, "")
    # 收拢摘除后留下的连续空行
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip("\n") + "\n", True


# ---------------------------------------------------------------- 公共入口
def install(target: str, home: Path | None = None, dry_run: bool = False) -> dict:
    path = target_path(target, home)
    if target == "cursor":
        existing = json.loads(path.read_text("utf-8")) if path.exists() else {}
        merged, changed = _merge_cursor(existing, _HOOK_CMD[target])
        if changed and not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", "utf-8")
    else:
        existing = path.read_text("utf-8") if path.exists() else ""
        merged, changed = _merge_codex(existing)
        if changed and not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(merged, "utf-8")
    return {"ok": True, "target": target, "path": str(path),
            "changed": changed, "dry_run": dry_run}


def uninstall(target: str, home: Path | None = None, dry_run: bool = False) -> dict:
    path = target_path(target, home)
    if not path.exists():
        return {"ok": True, "target": target, "path": str(path),
                "changed": False, "dry_run": dry_run}
    if target == "cursor":
        merged, changed = _unmerge_cursor(json.loads(path.read_text("utf-8")))
        if changed and not dry_run:
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", "utf-8")
    else:
        merged, changed = _unmerge_codex(path.read_text("utf-8"))
        if changed and not dry_run:
            path.write_text(merged, "utf-8")
    return {"ok": True, "target": target, "path": str(path),
            "changed": changed, "dry_run": dry_run}
