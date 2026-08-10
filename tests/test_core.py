"""Unit tests for the dependency-free core: security & reply parsing.

Runs with stdlib only:  python -m unittest discover -s tests -v
（决策闸门 GateManager 的测试随闸门一起剥离到 intent-gate-service/tests/。）
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent_gate.security import RateLimiter, SenderPolicy, parse_reply  # noqa: E402


class ParseReplyTests(unittest.TestCase):
    def test_token_and_answer(self):
        token, answer = parse_reply("@robot [HG-7F3A] go with option 2")
        self.assertEqual(token, "HG-7F3A")
        self.assertEqual(answer, "go with option 2")

    def test_lowercase_token_normalised(self):
        token, _ = parse_reply("[hg-a1b2] yes")
        self.assertEqual(token, "HG-A1B2")

    def test_no_token(self):
        token, answer = parse_reply("just do it")
        self.assertIsNone(token)
        self.assertEqual(answer, "just do it")


class SenderPolicyTests(unittest.TestCase):
    def test_empty_whitelist_denies_everyone(self):
        self.assertFalse(SenderPolicy(frozenset()).is_allowed("user1"))

    def test_whitelisted_allowed(self):
        self.assertTrue(SenderPolicy(frozenset({"u1"})).is_allowed("u1"))

    def test_unknown_denied(self):
        self.assertFalse(SenderPolicy(frozenset({"u1"})).is_allowed("u2"))


class RateLimiterTests(unittest.TestCase):
    def test_window(self):
        rl = RateLimiter(max_per_minute=2)
        now = time.time()
        self.assertTrue(rl.allow("k", now))
        self.assertTrue(rl.allow("k", now))
        self.assertFalse(rl.allow("k", now))
        self.assertTrue(rl.allow("k", now + 61))


if __name__ == "__main__":
    unittest.main()
