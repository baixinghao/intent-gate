"""install --target 接线器 + hook 发射器测试。

铁律守护：合并而非覆盖（第三方条目一条不动）、幂等（重复 install 无重复条目）、
uninstall 只拆自己接的线、dry-run 不落盘。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.hook import emit_session_start  # noqa: E402
from intent_gate.installer import (  # noqa: E402
    _DSH_MARK, _KIMI_MARK_BEGIN, _PLAYBOOK_SKILL_NAME, _SKILL_NAMES,
    install, target_path, uninstall,
)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class CursorInstallTests(Base):
    def test_fresh_install_creates_hooks_json(self):
        r = install("cursor", home=self.home)
        self.assertTrue(r["changed"])
        cfg = json.loads((self.home / ".cursor" / "hooks.json").read_text("utf-8"))
        self.assertEqual(cfg["version"], 1)
        cmds = [e["command"] for e in cfg["hooks"]["sessionStart"]]
        self.assertEqual(cmds, ["intent-gate hook session-start --format cursor"])

    def test_idempotent_second_install(self):
        install("cursor", home=self.home)
        r = install("cursor", home=self.home)
        self.assertFalse(r["changed"])
        cfg = json.loads((self.home / ".cursor" / "hooks.json").read_text("utf-8"))
        self.assertEqual(len(cfg["hooks"]["sessionStart"]), 1)

    def test_preserves_third_party_hooks(self):
        path = self.home / ".cursor" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1,
            "hooks": {"sessionStart": [{"command": "/opt/icm hook start"}],
                      "stop": [{"command": "/opt/other"}]},
        }), "utf-8")
        install("cursor", home=self.home)
        cfg = json.loads(path.read_text("utf-8"))
        cmds = [e["command"] for e in cfg["hooks"]["sessionStart"]]
        self.assertIn("/opt/icm hook start", cmds)
        self.assertEqual(cfg["hooks"]["stop"], [{"command": "/opt/other"}])

    def test_uninstall_removes_only_own_entry(self):
        path = self.home / ".cursor" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1,
            "hooks": {"sessionStart": [{"command": "/opt/icm hook start"}]},
        }), "utf-8")
        install("cursor", home=self.home)
        r = uninstall("cursor", home=self.home)
        self.assertTrue(r["changed"])
        cfg = json.loads(path.read_text("utf-8"))
        cmds = [e["command"] for e in cfg["hooks"]["sessionStart"]]
        self.assertEqual(cmds, ["/opt/icm hook start"])

    def test_dry_run_writes_nothing(self):
        r = install("cursor", home=self.home, dry_run=True)
        self.assertTrue(r["changed"])
        self.assertFalse((self.home / ".cursor" / "hooks.json").exists())


class CodexInstallTests(Base):
    def test_fresh_install_appends_toml_block(self):
        r = install("codex", home=self.home)
        self.assertTrue(r["changed"])
        text = (self.home / ".codex" / "config.toml").read_text("utf-8")
        self.assertIn("[[hooks.SessionStart]]", text)
        self.assertIn("intent-gate hook session-start --format claude", text)

    def test_idempotent_and_preserves_existing_config(self):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('model = "gpt-5"\n', "utf-8")
        install("codex", home=self.home)
        r = install("codex", home=self.home)
        self.assertFalse(r["changed"])
        text = path.read_text("utf-8")
        self.assertIn('model = "gpt-5"', text)
        self.assertEqual(text.count("[[hooks.SessionStart]]"), 1)

    def test_uninstall_removes_block_keeps_rest(self):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('model = "gpt-5"\n', "utf-8")
        install("codex", home=self.home)
        uninstall("codex", home=self.home)
        text = path.read_text("utf-8")
        self.assertNotIn("intent-gate", text)
        self.assertIn('model = "gpt-5"', text)


class DshInstallTests(unittest.TestCase):
    """install/uninstall --target dsh：patch 合并 + skills 复制 + 幂等 + 只拆自己的。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.dsh_home = self.home / "dsh"
        self.env_patch = mock.patch.dict(
            os.environ, {"DSH_HOME": str(self.dsh_home)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        # 模拟一个已存在 profile（dsh 跑过一次）
        self.web_patch = self.dsh_home / "profiles" / "web" / "cordis.patch.yml"
        self.web_patch.parent.mkdir(parents=True)
        self.web_patch.write_text("[]\n", "utf-8")

    def test_fresh_install_writes_patch_and_skills(self):
        r = install("dsh")
        self.assertTrue(r["ok"])
        self.assertTrue(r["patch_changed"])
        text = self.web_patch.read_text("utf-8")
        self.assertIn(_DSH_MARK, text)
        self.assertIn("serverName: intent-gate", text)
        self.assertIn("mcp-client", text)
        self.assertIn("customSkillDirs", text)
        self.assertIn("PYTHONIOENCODING", text)
        # 5 个 skill 全部复制/生成
        for name in (*_SKILL_NAMES, _PLAYBOOK_SKILL_NAME):
            self.assertTrue((self.dsh_home / "skills" / name / "SKILL.md").exists(),
                            f"missing skill {name}")
        playbook = (self.dsh_home / "skills" / _PLAYBOOK_SKILL_NAME
                    / "SKILL.md").read_text("utf-8")
        self.assertTrue(playbook.startswith("---\nname: doc-analysis-playbook"))
        self.assertIn("## Step 0", playbook)  # playbook 正文在

    def test_idempotent_second_install(self):
        install("dsh")
        r = install("dsh")
        self.assertFalse(r["patch_changed"])
        self.assertEqual(r["skills"]["installed"], [])
        text = self.web_patch.read_text("utf-8")
        self.assertEqual(text.count("serverName: intent-gate"), 1)

    def test_preserves_user_patch_entries(self):
        self.web_patch.write_text(
            "- id: my-custom-plugin\n"
            "  name: '@me/plugin'\n", "utf-8")
        install("dsh")
        text = self.web_patch.read_text("utf-8")
        self.assertIn("my-custom-plugin", text)
        self.assertIn("serverName: intent-gate", text)
        # 用户条目在，我们的块在，文件仍是合法 YAML 数组（两个元素）
        self.assertTrue(text.startswith("- id: my-custom-plugin"))

    def test_recognizes_manually_added_legacy_config(self):
        # 手动添加（无管理标记）的旧配置应被幂等识别
        self.web_patch.write_text(
            "- insert:\n"
            "    - id: mcp-intent-gate\n"
            "      name: '@deepseek-ai/dsh-mcp-client'\n"
            "      config:\n"
            "        transport: stdio\n"
            "        serverName: intent-gate\n", "utf-8")
        r = install("dsh")
        self.assertFalse(r["patch_changed"])
        self.assertEqual(self.web_patch.read_text("utf-8").count(
            "serverName: intent-gate"), 1)

    def test_uninstall_removes_only_own_block_and_skills(self):
        self.web_patch.write_text(
            "- id: my-custom-plugin\n"
            "  name: '@me/plugin'\n", "utf-8")
        install("dsh")
        r = uninstall("dsh")
        self.assertIn("web", "".join(r["patches"]))
        text = self.web_patch.read_text("utf-8")
        self.assertNotIn(_DSH_MARK, text)
        self.assertNotIn("serverName: intent-gate", text)
        self.assertIn("my-custom-plugin", text)  # 用户条目保留
        for name in (*_SKILL_NAMES, _PLAYBOOK_SKILL_NAME):
            self.assertFalse((self.dsh_home / "skills" / name).exists())

    def test_uninstall_does_not_touch_manual_legacy_config(self):
        self.web_patch.write_text(
            "- insert:\n"
            "    - id: mcp-intent-gate\n"
            "      name: '@deepseek-ai/dsh-mcp-client'\n"
            "      config:\n"
            "        transport: stdio\n"
            "        serverName: intent-gate\n", "utf-8")
        r = uninstall("dsh")
        self.assertEqual(r["patches"], [])
        self.assertTrue(r["notes"])  # 提示手动摘除
        self.assertIn("serverName: intent-gate",
                      self.web_patch.read_text("utf-8"))

    def test_dry_run_writes_nothing(self):
        r = install("dsh", dry_run=True)
        self.assertTrue(r["patch_changed"])
        self.assertEqual(self.web_patch.read_text("utf-8").strip(), "[]")
        self.assertFalse((self.dsh_home / "skills").exists())

    def test_multiple_profiles_all_patched(self):
        other = self.dsh_home / "profiles" / "tui" / "cordis.patch.yml"
        other.parent.mkdir(parents=True)
        other.write_text("[]\n", "utf-8")
        r = install("dsh")
        self.assertEqual(len(r["patches"]), 2)
        for pf in (self.web_patch, other):
            self.assertIn("serverName: intent-gate", pf.read_text("utf-8"))

    def test_no_profile_returns_ok_false(self):
        for p in self.dsh_home.rglob("cordis.patch.yml"):
            p.unlink()
        r = install("dsh")
        self.assertFalse(r["ok"])
        self.assertIn("cordis.patch.yml", r["reason"])

    def test_target_path_dsh_uses_env(self):
        self.assertEqual(target_path("dsh"), self.dsh_home)

    def test_dsh_default_template_fully_replaced_no_flow_residue(self):
        # DSH 默认 patch 模板：注释 + []。合并后不能残留 []（flow/block 序列互斥）
        self.web_patch.write_text(
            "# Your patch layer for this dsh profile, applied after every bundle layer:\n"
            "# a top-level YAML array of loader patch entries ...\n"
            "[]\n", "utf-8")
        install("dsh")
        text = self.web_patch.read_text("utf-8")
        self.assertNotIn("[]", text)
        self.assertTrue(text.lstrip().startswith("# intent-gate"))
        self.assertIn("serverName: intent-gate", text)

    def test_bom_prefixed_empty_template_replaced(self):
        self.web_patch.write_bytes(b"\xef\xbb\xbf[]\r\n")
        install("dsh")
        text = self.web_patch.read_text("utf-8")
        self.assertNotIn("[]", text)
        self.assertIn("serverName: intent-gate", text)

    def test_uninstall_emptied_patch_restores_empty_array(self):
        install("dsh")
        uninstall("dsh")
        self.assertEqual(self.web_patch.read_text("utf-8").strip(), "[]")

    def test_dsh_home_env_missing_uses_home_dot_dsh(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            from intent_gate.installer import _dsh_home
            self.assertEqual(_dsh_home(self.home), self.home / ".dsh")


class KimiCodeInstallTests(unittest.TestCase):
    """install/uninstall --target kimi-code：mcp.json 合并 + skills 复制 +
    config.toml 纪律注入 hook；幂等；只拆自己接的线。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.kimi_home = self.home / "kimi"
        self.env_patch = mock.patch.dict(
            os.environ, {"KIMI_CODE_HOME": str(self.kimi_home)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _run_hook(self, stdin_text: str) -> subprocess.CompletedProcess:
        script = self.kimi_home / "hooks" / "intent-gate-inject.py"
        return subprocess.run(
            [sys.executable, str(script)], input=stdin_text, check=False,
            capture_output=True, encoding="utf-8", env=dict(os.environ))

    def test_fresh_install_writes_all_three_pieces(self):
        r = install("kimi-code")
        self.assertTrue(r["ok"])
        self.assertTrue(r["mcp_changed"])
        self.assertTrue(r["hook_changed"])
        cfg = json.loads((self.kimi_home / "mcp.json").read_text("utf-8"))
        self.assertEqual(cfg["mcpServers"]["intent-gate"],
                         {"command": "intent-gate"})
        for name in _SKILL_NAMES:
            self.assertTrue((self.kimi_home / "skills" / name / "SKILL.md").exists(),
                            f"missing skill {name}")
        toml = (self.kimi_home / "config.toml").read_text("utf-8")
        self.assertIn(_KIMI_MARK_BEGIN, toml)
        self.assertIn('event = "UserPromptSubmit"', toml)
        self.assertIn("intent-gate-inject.py", toml)
        self.assertIn("timeout = 10", toml)
        script = self.kimi_home / "hooks" / "intent-gate-inject.py"
        self.assertTrue(script.exists())
        self.assertIn("fail-open", script.read_text("utf-8"))

    def test_idempotent_second_install(self):
        install("kimi-code")
        r = install("kimi-code")
        self.assertFalse(r["mcp_changed"])
        self.assertFalse(r["hook_changed"])
        self.assertFalse(r["hook_script_changed"])
        self.assertEqual(r["skills"]["installed"], [])
        toml = (self.kimi_home / "config.toml").read_text("utf-8")
        self.assertEqual(toml.count(_KIMI_MARK_BEGIN), 1)
        self.assertEqual(toml.count("[[hooks]]"), 1)

    def test_preserves_existing_config_and_mcp_entries(self):
        self.kimi_home.mkdir(parents=True)
        (self.kimi_home / "config.toml").write_text('model = "k2"\n', "utf-8")
        (self.kimi_home / "mcp.json").write_text(json.dumps(
            {"mcpServers": {"other": {"command": "other-cmd"}}}), "utf-8")
        install("kimi-code")
        toml = (self.kimi_home / "config.toml").read_text("utf-8")
        self.assertIn('model = "k2"', toml)
        cfg = json.loads((self.kimi_home / "mcp.json").read_text("utf-8"))
        self.assertEqual(cfg["mcpServers"]["other"], {"command": "other-cmd"})
        self.assertIn("intent-gate", cfg["mcpServers"])

    def test_recognizes_manually_added_mcp_entry(self):
        # 用户手动加了同名 mcpServers 条目：视为已装，不覆盖用户配置
        self.kimi_home.mkdir(parents=True)
        (self.kimi_home / "mcp.json").write_text(json.dumps(
            {"mcpServers": {"intent-gate": {"command": "custom-ig"}}}), "utf-8")
        r = install("kimi-code")
        self.assertFalse(r["mcp_changed"])
        cfg = json.loads((self.kimi_home / "mcp.json").read_text("utf-8"))
        self.assertEqual(cfg["mcpServers"]["intent-gate"],
                         {"command": "custom-ig"})

    def test_uninstall_removes_all_keeps_user_content(self):
        self.kimi_home.mkdir(parents=True)
        (self.kimi_home / "config.toml").write_text('model = "k2"\n', "utf-8")
        install("kimi-code")
        marker = self.kimi_home / "hooks" / ".ig-injected-s1"
        marker.write_text("injected\n", "utf-8")
        r = uninstall("kimi-code")
        self.assertTrue(r["ok"])
        self.assertTrue(r["mcp_changed"])
        self.assertTrue(r["hook_changed"])
        toml = (self.kimi_home / "config.toml").read_text("utf-8")
        self.assertNotIn("intent-gate", toml)
        self.assertIn('model = "k2"', toml)
        cfg = json.loads((self.kimi_home / "mcp.json").read_text("utf-8"))
        self.assertNotIn("intent-gate", cfg["mcpServers"])
        self.assertEqual(r["skills"], list(_SKILL_NAMES))
        for name in _SKILL_NAMES:
            self.assertFalse((self.kimi_home / "skills" / name).exists())
        self.assertFalse((self.kimi_home / "hooks" / "intent-gate-inject.py").exists())
        self.assertFalse(marker.exists())

    def test_uninstall_config_created_by_install_is_removed_when_emptied(self):
        install("kimi-code")
        uninstall("kimi-code")
        self.assertFalse((self.kimi_home / "config.toml").exists())

    def test_uninstall_without_marks_leaves_manual_config_untouched(self):
        self.kimi_home.mkdir(parents=True)
        (self.kimi_home / "config.toml").write_text(
            '[[hooks]]\nevent = "UserPromptSubmit"\ncommand = "mine"\n', "utf-8")
        r = uninstall("kimi-code")
        self.assertFalse(r["hook_changed"])
        self.assertIn('command = "mine"',
                      (self.kimi_home / "config.toml").read_text("utf-8"))

    def test_dry_run_writes_nothing(self):
        r = install("kimi-code", dry_run=True)
        self.assertTrue(r["mcp_changed"])
        self.assertTrue(r["hook_changed"])
        self.assertTrue(r["hook_script_changed"])
        self.assertFalse(self.kimi_home.exists())

    def test_hook_script_injects_once_per_session(self):
        install("kimi-code")
        first = self._run_hook('{"session_id": "s1"}')
        self.assertEqual(first.returncode, 0)
        self.assertIn("name: using-intent-gate", first.stdout)
        second = self._run_hook('{"session_id": "s1"}')
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")
        third = self._run_hook('{"session_id": "s2"}')  # 新会话重新注入
        self.assertIn("name: using-intent-gate", third.stdout)

    def test_hook_script_fail_open_on_garbage_stdin(self):
        install("kimi-code")
        r = self._run_hook("not json at all")
        self.assertEqual(r.returncode, 0)  # 取不到 session_id 也照常注入（unknown 去重）
        self.assertIn("name: using-intent-gate", r.stdout)

    def test_home_param_used_when_env_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            r = install("kimi-code", home=self.home)
            self.assertTrue(r["ok"])
            self.assertTrue((self.home / ".kimi-code" / "mcp.json").exists())

    def test_target_path_kimi_code_uses_env(self):
        self.assertEqual(target_path("kimi-code"), self.kimi_home)


class HookEmitTests(Base):
    def test_cursor_contract_snake_case(self):
        payload = json.loads(emit_session_start("cursor"))
        self.assertIn("additional_context", payload)
        self.assertIn("EXTREMELY_IMPORTANT", payload["additional_context"])
        self.assertIn("doc_analysis_playbook", payload["additional_context"])

    def test_claude_contract_nested(self):
        payload = json.loads(emit_session_start("claude"))
        out = payload["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("doc_analysis_playbook", out["additionalContext"])

    def test_standard_contract_top_level(self):
        payload = json.loads(emit_session_start("standard"))
        self.assertIn("additionalContext", payload)

    def test_unknown_format_rejected(self):
        with self.assertRaises(ValueError):
            emit_session_start("gemini")

    def test_unsupported_target_rejected(self):
        with self.assertRaises(ValueError):
            target_path("gemini", home=self.home)


if __name__ == "__main__":
    unittest.main()
