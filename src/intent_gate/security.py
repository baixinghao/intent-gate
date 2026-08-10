"""Reply authentication & correlation parsing. Pure stdlib.

Security model (fail-closed):
- An empty sender whitelist authorises NOBODY.
- A reply must carry the gate correlation token [HG-XXXX]; the implicit
  single-pending fallback is a deliberate usability trade-off and is
  configurable, never assumed.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"\[([Hh][Gg]-[0-9A-Fa-f]{4,8})\]")


def parse_reply(text: str) -> tuple[str | None, str]:
    """Extract the correlation token and the cleaned answer body.

    Returns (token_or_None, answer_text). The answer keeps the original
    wording minus the token marker.
    """
    match = _TOKEN_RE.search(text)
    if not match:
        return None, text.strip()
    token = match.group(1).upper()
    answer = (text[: match.start()] + text[match.end() :]).strip()
    # DingTalk robot messages often lead with "@机器人" — strip leading @mentions.
    answer = re.sub(r"^(@\S+\s*)+", "", answer).strip()
    return token, answer


@dataclass
class SenderPolicy:
    """Whitelist by DingTalk staffId. Empty whitelist = deny everyone."""

    allowed_staff_ids: frozenset[str] = frozenset()

    def is_allowed(self, staff_id: str | None) -> bool:
        if not staff_id:
            return False
        return staff_id in self.allowed_staff_ids


@dataclass
class RateLimiter:
    """Sliding-window limiter per sender to blunt callback floods / replay spam."""

    max_per_minute: int = 30
    _hits: dict[str, deque[float]] = field(default_factory=dict)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        window = self._hits.setdefault(key, deque())
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self.max_per_minute:
            return False
        window.append(now)
        return True
