"""install --target 接线器 + hook 发射器测试。

铁律守护：合并而非覆盖（第三方 hooks 一条不动）、幂等（重复 install 无重复条目）、
uninstall 只拆自己接的线、dry-run 不落盘。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.hook import emit_session_start  # noqa: E402
from intent_gate.installer import install, target_path, uninstall  # noqa: E402


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
