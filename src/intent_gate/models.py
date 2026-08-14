"""Core domain models. Pure stdlib dataclasses — no third-party imports by design,
so this module and everything built only on it stays unit-testable without the
project dependencies installed."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

TOKEN_PREFIX = "HG-"


def new_gate_token(existing: set[str]) -> str:
    """Short human-typeable correlation token, e.g. HG-7F3A."""
    while True:
        token = TOKEN_PREFIX + uuid.uuid4().hex[:4].upper()
        if token not in existing:
            return token


class GateStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class EventType(str, Enum):
    OPENED = "gate.opened"
    RESOLVED = "gate.resolved"
    TIMEOUT = "gate.timeout"
    CANCELLED = "gate.cancelled"
    REPLY_REJECTED = "gate.reply_rejected"


@dataclass
class Gate:
    token: str
    question: str
    context: str
    options: list[str]
    timeout_sec: int
    created_at: float = field(default_factory=time.time)
    status: GateStatus = GateStatus.PENDING
    responder: str | None = None
    answer: str | None = None

    def to_public_dict(self) -> dict:
        return {
            "token": self.token,
            "question": self.question,
            "options": self.options,
            "status": self.status.value,
            "created_at": self.created_at,
            "timeout_sec": self.timeout_sec,
        }


@dataclass
class GateEvent:
    type: EventType
    gate_token: str
    detail: dict
    at: float = field(default_factory=time.time)


# 草稿「无需画图」豁免词表（单源定义，alignment.manager 与 analysis.lint 共用——
# 错题集 2026-08-14 评审：私有常量跨模块导入是设计债，提升到本层共用）
DRAFT_NO_DIAGRAM_RE = r"无需画图|无需绘图|不需要画图|无图可画|不触发任何图"
