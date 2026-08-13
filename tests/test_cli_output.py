"""Regression tests for CLI output encoding on Windows (GBK console).

Bug: `intent-gate hook session-start` crashed with
    UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f4cb'
because the injected skill text contains emoji and print() encoded it with
the ANSI code page. The fix forces UTF-8 on stdout/stderr in main() with a
silent fallback; these tests pin that behaviour without touching MCP paths.

Runs with stdlib only:  python -m unittest discover -s tests -v
"""

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.__main__ import main  # noqa: E402

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


class HookOutputOnGbkStdoutTests(unittest.TestCase):
    """hook/install JSON output must survive a GBK-encoded stdout stream."""

    def _run_main_with_gbk_stdout(self, argv: list[str]) -> bytes:
        buffer = io.BytesIO()
        gbk_stream = io.TextIOWrapper(buffer, encoding="gbk")
        original = sys.stdout
        try:
            sys.stdout = gbk_stream
            with mock.patch.object(sys, "argv", argv):
                main()
            gbk_stream.flush()
        finally:
            sys.stdout = original
        return buffer.getvalue()

    def test_hook_session_start_survives_gbk_stdout(self):
        out = self._run_main_with_gbk_stdout(
            ["intent-gate", "hook", "session-start", "--format", "standard"])
        # The injected skill text contains emoji (📋); must not raise, and the
        # bytes must decode as UTF-8 (the fix reconfigures the stream).
        text = out.decode("utf-8")  # raises if not UTF-8
        self.assertIn("additionalContext", text)
        payload = json.loads(text)
        self.assertIn("additionalContext", payload)

    def test_hook_output_keeps_emoji_verbatim(self):
        out = self._run_main_with_gbk_stdout(
            ["intent-gate", "hook", "session-start", "--format", "standard"])
        text = out.decode("utf-8")
        # 📋 U+1F4CB must survive byte-exact, not get mangled into '?'
        self.assertIn("\U0001f4cb", text)

    def test_install_dry_run_survives_gbk_stdout(self):
        out = self._run_main_with_gbk_stdout(
            ["intent-gate", "install", "--target", "cursor", "--dry-run"])
        text = out.decode("utf-8")
        payload = json.loads(text)
        self.assertIn("dry_run", payload)

    def test_uninstall_dry_run_survives_gbk_stdout(self):
        out = self._run_main_with_gbk_stdout(
            ["intent-gate", "uninstall", "--target", "codex", "--dry-run"])
        out.decode("utf-8")  # must be valid UTF-8, no crash


@unittest.skipUnless(sys.platform == "win32",
                     "GBK code page is Windows-only; pipe encoding is UTF-8 elsewhere")
class HookSubprocessOnWindowsTests(unittest.TestCase):
    """Real subprocess regression: stdout piped (non-tty) on Windows uses the
    ANSI code page by default, which is exactly the crash environment."""

    def test_hook_session_start_subprocess_exit_zero(self):
        env = dict(__import__("os").environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        proc = subprocess.run(
            [sys.executable, "-m", "intent_gate", "hook", "session-start",
             "--format", "standard"],
            capture_output=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr.decode("utf-8", "replace"))
        text = proc.stdout.decode("utf-8")  # must be UTF-8 on the wire
        self.assertIn("additionalContext", text)


if __name__ == "__main__":
    unittest.main()
