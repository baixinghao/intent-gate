# -*- coding: utf-8 -*-
"""install/uninstall --target：把 intent-gate 接进各家 agent 的配置。

为什么存在：接入动作是「接线」——每家 agent 的配置格式都不一样，README 教不动。

支持面（只收已核实配置格式的目标，未核实的不瞎写）：
  cursor  ~/.cursor/hooks.json   {"version":1,"hooks":{"sessionStart":[…]}}
  codex   ~/.codex/config.toml   [[hooks.SessionStart]] + [[hooks.SessionStart.hooks]]
                                  （Codex 契约官方对齐 Claude：stdin JSON / exit 2 阻断）
  dsh     $DSH_HOME/profiles/*/cordis.patch.yml   mcp-client 实例 + skill-filesystem
                                  覆盖 + skills 复制到 $DSH_HOME/skills/
                                  （DeepSeek Harness：MCP 工具经 dsh-mcp-client 桥接，
                                  skills 经 skill 发现机制热加载；mcp-client 不桥接
                                  MCP prompt，故 playbook 以 doc-analysis-playbook
                                  skill 形式一并安装）
铁律：合并而非覆盖——用户已有的第三方条目一条不动；
幂等——重复 install 不产生重复条目；uninstall 只拆自己接的线。
"""
from __future__ import annotations

import json
import os
import shutil
from importlib import resources
from pathlib import Path

SUPPORTED_TARGETS = ("cursor", "codex", "dsh")

_HOOK_CMD = {
    "cursor": "intent-gate hook session-start --format cursor",
    # Codex hooks 契约官方对齐 Claude Code（stdin JSON，exit 2 阻断），
    # 故复用 claude 输出契约。
    "codex": "intent-gate hook session-start --format claude",
}

_IDENTITY = "intent-gate hook session-start"  # 幂等/卸载的识别标记

# ---------------------------------------------------------------- dsh
_DSH_MARK = "# intent-gate: dsh integration (managed by 'intent-gate install --target dsh')"
_SKILL_NAMES = ("using-intent-gate", "requirement-alignment",
                "red-blue-review", "contract-coding")
_PLAYBOOK_SKILL_NAME = "doc-analysis-playbook"


def _dsh_home(home: Path | None) -> Path:
    env = os.environ.get("DSH_HOME")
    if env:
        return Path(env)
    return (home or Path.home()) / ".dsh"


def _skill_source_text(name: str) -> str:
    """wheel 内 _assets 读（force-include 产物）；源码树（测试/dev）回退读仓库真身。"""
    try:
        return (resources.files("intent_gate._assets") / "skills" / name
                / "SKILL.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        repo = (Path(__file__).resolve().parent.parent.parent
                / "skills" / name / "SKILL.md")
        return repo.read_text(encoding="utf-8")


def _playbook_skill_text() -> str:
    """doc-analysis-playbook skill：MCP prompt 的 skill 分发版（mcp-client 不桥接 prompt）。

    frontmatter 触发词与 MCP prompt doc_analysis_playbook 的用法一致；
    正文即 playbook.md 全文，末尾标注权威来源，禁止手抄第二份。
    """
    try:
        body = (resources.files("intent_gate.analysis")
                .joinpath("playbook.md").read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError):
        body = (Path(__file__).resolve().parent / "analysis" / "playbook.md"
                ).read_text(encoding="utf-8")
    fm = (
        "---\n"
        "name: doc-analysis-playbook\n"
        "description: The requirement-analysis law for the intent-gate MCP tools. "
        "Use at the START of any requirement analysis task "
        "(分析需求/解析PRD/解析需求/画状态机/画时序图/生成决策表/生成DDL/意图对齐/继续分析), "
        "before running analyze_requirement or dispatching questions. Read it in full "
        "before starting Step 0 - judgment lives in your head (full-semantics), "
        "discipline is enforced by the intent-gate MCP tools (ledger/gates/format "
        "validation). This skill is the MCP prompt doc_analysis_playbook delivered "
        "as a skill, because the DeepSeek Harness mcp-client bridges MCP tools but "
        "not MCP prompts.\n"
        "---\n"
        "\n"
    )
    tail = (
        "\n\n---\n"
        "> Distributed copy for the DeepSeek Harness skill catalog; "
        "authoritative source: src/intent_gate/analysis/playbook.md in the "
        "intent-gate repo (also served via MCP prompt doc_analysis_playbook).\n"
    )
    return fm + body + tail


def _dsh_patch_block(dsh_home: Path) -> str:
    """DSH profile patch 块：mcp-client 实例 + skill-filesystem 覆盖。"""
    skills_dir = (dsh_home / "skills").as_posix()
    return (
        f"{_DSH_MARK}\n"
        "- insert:\n"
        "    - id: mcp-intent-gate\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        "        transport: stdio\n"
        "        serverName: intent-gate\n"
        "        command: 'intent-gate'\n"
        "        env:\n"
        "          PYTHONIOENCODING: utf-8\n"
        "        toolCallTimeoutMs: 60000\n"
        "        failOnStartupError: false\n"
        "\n"
        "- id: skill-filesystem\n"
        "  disabled: false\n"
        "  config:\n"
        "    customSkillDirs:\n"
        f"      - '{skills_dir}'\n"
    )


def _dsh_patch_files(dsh_home: Path) -> list[Path]:
    profiles_dir = dsh_home / "profiles"
    if not profiles_dir.is_dir():
        return []
    return sorted(p / "cordis.patch.yml" for p in profiles_dir.iterdir()
                  if p.is_dir() and (p / "cordis.patch.yml").is_file())


def _is_empty_template(text: str) -> bool:
    """DSH 默认 patch 模板（注释 + []）视为空：整体替换，避免 flow 序列残留。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    return all(ln.startswith("#") or ln == "[]" for ln in lines)


def _merge_dsh_patch(existing: str, block: str) -> tuple[str, bool]:
    """合并 patch 块；幂等按管理标记或 serverName 识别（手动添加的旧配置也算已装）。

    🔴 flow 序列（[]）与块序列（- insert:）不能共存于同一 YAML document——
    空模板/空文件必须整体替换为块，残留 [] 会导致 patch 解析失败。
    """
    if _DSH_MARK in existing or "serverName: intent-gate" in existing:
        return existing, False
    stripped = existing.replace("\ufeff", "").strip()  # BOM 兜底
    if stripped in ("", "[]") or _is_empty_template(stripped):
        return block, True
    if stripped.startswith("["):
        raise ValueError(
            "cordis.patch.yml 含非空 flow 序列（[]），无法安全合并——"
            "请手动把 intent-gate 条目追加到该文件")
    return existing.rstrip("\n") + "\n\n" + block, True


def _unmerge_dsh_patch(existing: str, block: str) -> tuple[str, bool]:
    """摘除带管理标记的块；无标记的手动条目不碰（留给用户手工处理）。"""
    if _DSH_MARK not in existing:
        return existing, False
    if block in existing:
        text = existing.replace(block, "")
    else:
        # 块被用户改动过：从标记注释行摘到文件尾（我们的块总是追加在 patch 尾部）
        idx = existing.find(_DSH_MARK)
        text = existing[:idx]
    text = text.replace("\ufeff", "").strip()
    return (text + "\n" if text else "[]\n"), True


def _install_dsh_skills(dsh_home: Path, dry_run: bool) -> dict:
    skills_dir = dsh_home / "skills"
    installed, skipped = [], []
    for name in (*_SKILL_NAMES, _PLAYBOOK_SKILL_NAME):
        dst = skills_dir / name / "SKILL.md"
        if dst.exists():
            skipped.append(name)
            continue
        if dry_run:
            installed.append(name)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = (_skill_source_text(name) if name != _PLAYBOOK_SKILL_NAME
                else _playbook_skill_text())
        dst.write_text(text, encoding="utf-8")
        installed.append(name)
    return {"installed": installed, "skipped": skipped}


def _install_dsh(dsh_home: Path, dry_run: bool) -> dict:
    patch_files = _dsh_patch_files(dsh_home)
    if not patch_files:
        return {
            "ok": False, "target": "dsh",
            "reason": f"{dsh_home / 'profiles'} 下没有 cordis.patch.yml——"
                      "请先运行一次 dsh（生成 profile）再执行 install；"
                      "或检查 DSH_HOME 环境变量",
        }
    block = _dsh_patch_block(dsh_home)
    changed_any = False
    for pf in patch_files:
        existing = pf.read_text(encoding="utf-8") if pf.exists() else ""
        merged, changed = _merge_dsh_patch(existing, block)
        if changed:
            changed_any = True
            if not dry_run:
                pf.write_text(merged, encoding="utf-8")
    skills = _install_dsh_skills(dsh_home, dry_run)
    return {
        "ok": True, "target": "dsh",
        "patches": [str(p) for p in patch_files],
        "patch_changed": changed_any,
        "skills": skills,
        "dry_run": dry_run,
        "note": "重启 DSH 会话后生效（web profile 的 HMR 默认禁用）；"
                "工具以 mcp__intent-gate__<tool> 出现在会话中，"
                "skills 经 $DSH_HOME/skills 热加载",
    }


def _uninstall_dsh(dsh_home: Path, dry_run: bool) -> dict:
    block = _dsh_patch_block(dsh_home)
    removed, notes = [], []
    for pf in _dsh_patch_files(dsh_home):
        existing = pf.read_text(encoding="utf-8")
        if _DSH_MARK not in existing:
            if "serverName: intent-gate" in existing:
                notes.append(f"{pf}: 检测到手动添加的 intent-gate 条目（无管理标记），"
                             "请手动摘除")
            continue
        merged, changed = _unmerge_dsh_patch(existing, block)
        if changed:
            removed.append(str(pf))
            if not dry_run:
                pf.write_text(merged, encoding="utf-8")
    skills_dir = dsh_home / "skills"
    removed_skills = []
    for name in (*_SKILL_NAMES, _PLAYBOOK_SKILL_NAME):
        d = skills_dir / name
        if d.is_dir():
            removed_skills.append(name)
            if not dry_run:
                shutil.rmtree(d)
    return {
        "ok": True, "target": "dsh",
        "patches": removed, "skills": removed_skills,
        "notes": notes, "dry_run": dry_run,
    }


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
def target_path(target: str, home: Path | None = None) -> Path:
    if target == "dsh":
        return _dsh_home(home)
    home = home or Path.home()
    if target == "cursor":
        return home / ".cursor" / "hooks.json"
    if target == "codex":
        return home / ".codex" / "config.toml"
    raise ValueError(f"不支持的目标: {target}（当前支持：{', '.join(SUPPORTED_TARGETS)}）")


def install(target: str, home: Path | None = None, dry_run: bool = False) -> dict:
    if target == "dsh":
        return _install_dsh(_dsh_home(home), dry_run)
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
    if target == "dsh":
        return _uninstall_dsh(_dsh_home(home), dry_run)
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
