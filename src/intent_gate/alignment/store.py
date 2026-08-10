"""ReviewStore：意图对齐的文件落盘层（纯 stdlib，可脱离第三方依赖单测）。

管理的文件（全部位于 {workspace_root}/.harness/requests/{需求名}/_review/ 下）：

    pending-questions.md   待决问题清单（checkbox 对账表，发题前先落盘）
    alignment-log.md       意图对齐流水（🔴 蓝军 R1「注入保真」复核的唯一依据，
                           格式与 doc-analysis skill 的既有约定一字不差）
    inference-pending.md   AI 推断待确认清单（批量点头用）
    inbox/                 群回复落盘区（钉钉回调写进来的答案原话）
        _consumed/         已被 collect_answers 领取的答案（归档，防重复领取）

纪律（对应 DESIGN.md §4）：
  - 先落盘后发送：任何群消息发出前，题目必须已经躺在 pending-questions.md 里。
  - 勾不打完，宿主 agent 禁止给报告标 intent_aligned: true。
  - 内容里的 "|" 一律替换为 "/"——它是我们的字段分隔符，不能出现在正文里。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

# checklist 行格式（与 docs/DESIGN.md §4.1 一致，改动必须同步设计稿）：
# - [ ] [HG-7F3A] 📋 退款后订单状态？| 选项: 1.xxx 2.xxx 3.xxx 4.其他 | @张三 | 发出: 2026-08-08 21:00
_UNCHECKED = "- [ ]"
_CHECKED = "- [x]"
_ABANDONED = "- [~]"  # 废弃态：用户中途弃用，不再要求回答，也不阻断就绪判定

# 推断编号精确匹配（审计修复：子串匹配会结算错行）：
# 只认行首 checkbox 后的第一个字段，如 "- [ ] INF-12 xxx" 里的 INF-12
_INF_LINE_RE = re.compile(r"^- \[.\] (INF-\d+)\s")

# checklist 行首：checkbox + [HG-XXXX] token。取 gap 段前必须先剥掉这两个
# 前缀（"] " 切分法会先命中 "- [ ]" 里的 "] "，把 token 残留在结果里）
_QUESTION_HEAD_RE = re.compile(r"^- \[.\] \[HG-[0-9A-Fa-f]{4}\]\s*(.*)$")


def _question_head(line: str) -> str:
    """剥掉 checkbox 与 [HG-XXXX] token，返回「severity 类别 gap」段。
    正则不匹配（老格式/异常行）时保底返回去 checkbox 后的整段，不留 token 垃圾。"""
    head = line.split(" | ", 1)[0]
    m = _QUESTION_HEAD_RE.match(head)
    if m:
        return m.group(1).strip()
    for mark in (_UNCHECKED, _CHECKED, _ABANDONED):
        if head.startswith(mark):
            return head[len(mark):].strip()
    return head.strip()


def _now() -> str:
    """本地时间，人读格式。"""
    return time.strftime("%Y-%m-%d %H:%M")


def _clean(text: str) -> str:
    """字段分隔符 | 不允许出现在正文里，统一降级为斜杠。"""
    return text.replace("|", "/").replace("\n", " ").strip()


def _atomic_write(path: Path, text: str) -> None:
    """原子整写（审计修复：进程猝死会丢掉写到一半的清单）。
    同目录先写临时文件再 os.replace——replace 在同一文件系统内是原子操作。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class PendingQuestion:
    """一条待决问题（checklist 里的一行）。"""

    token: str  # 关联令牌，如 HG-7F3A，群回复靠它认领题目
    gap: str  # 意图断层描述（业务/技术缺口）
    category: str = "📋"  # 📋业务题 / 🔧技术题
    severity: str = "🟡"  # 🔴核心逻辑断层（未消→status blocked）/ 🟡局部歧义
    options: list[str] = field(default_factory=list)  # 候选选项（不含"4.其他"，那是固定尾巴）
    recommend: str = ""  # AI 推荐项 + 推断依据（点头式确认用），可空
    targets: list[str] = field(default_factory=list)  # 期望回答人（昵称展示用）
    issued_at: str = field(default_factory=_now)


class ReviewStore:
    """单个需求的 _review 目录读写器。feature 即 .harness/requests/ 下的需求目录名。"""

    def __init__(self, workspace_root: str | Path, feature: str) -> None:
        # feature 不允许带路径分隔符，防目录逃逸
        if "/" in feature or "\\" in feature or feature in ("", ".", ".."):
            raise ValueError(f"非法需求名: {feature!r}")
        self.feature = feature
        self.review_dir = (
            Path(workspace_root) / ".harness" / "requests" / feature / "_review"
        )

    # ------------------------------------------------------------------ 路径
    @property
    def pending_file(self) -> Path:
        return self.review_dir / "pending-questions.md"

    @property
    def log_file(self) -> Path:
        return self.review_dir / "alignment-log.md"

    @property
    def inference_file(self) -> Path:
        return self.review_dir / "inference-pending.md"

    @property
    def inbox_dir(self) -> Path:
        return self.review_dir / "inbox"

    @property
    def consumed_dir(self) -> Path:
        return self.inbox_dir / "_consumed"

    def _ensure(self) -> None:
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------- pending 清单
    def add_pending(self, q: PendingQuestion) -> None:
        """发题登记：🔴 必须先落盘，再允许任何发送动作。"""
        self._ensure()
        if not self.pending_file.exists():
            self.pending_file.write_text(
                f"# 待决问题清单 — {self.feature}\n\n", encoding="utf-8"
            )
        opts = " ".join(f"{i + 1}.{_clean(o)}" for i, o in enumerate(q.options))
        if opts:
            opts += " 4.其他"
        # 行首：checkbox [token] 严重级 类别 gap —— severity 在类别前，
        # resume/对账时从行首解析红灯题（skill 词表：🔴未消→status blocked）
        sev = q.severity if q.severity in ("🔴", "🟡") else "🟡"
        parts = [f"{_UNCHECKED} [{q.token}] {sev} {q.category} {_clean(q.gap)}"]
        if opts:
            parts.append(f"选项: {opts}")
        if q.recommend:
            parts.append(f"推荐: {_clean(q.recommend)}")
        if q.targets:
            parts.append("@" + " @".join(_clean(t) for t in q.targets))
        parts.append(f"发出: {q.issued_at}")
        with self.pending_file.open("a", encoding="utf-8") as f:
            f.write(" | ".join(parts) + "\n")

    def pending_red_lines(self) -> list[str]:
        """未决的 🔴 题（skill 词表：有 🔴 未消 → status 必须 blocked）。"""
        return [
            line
            for line in self.unchecked_lines()
            if "🔴" in line.split(" | ", 1)[0]
        ]

    def _read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def pending_tokens(self, checked: bool = False) -> set[str]:
        """列出清单中未勾（或已勾）的 token 集合。"""
        mark = _CHECKED if checked else _UNCHECKED
        tokens = set()
        for line in self._read_lines(self.pending_file):
            if line.startswith(mark) and "[HG-" in line:
                # 行内令牌形如 [HG-7F3A]，取 "HG-" 前缀 + 4 位十六进制
                hex4 = line.split("[HG-", 1)[1][:4]
                tokens.add(f"HG-{hex4}")
        return tokens

    def has_pending(self, token: str) -> bool:
        return token.upper() in self.pending_tokens()

    def unchecked_lines(self) -> list[str]:
        """未勾题的原始行（rebroadcast 汇总重发用）。"""
        return [
            line
            for line in self._read_lines(self.pending_file)
            if line.startswith(_UNCHECKED) and "[HG-" in line
        ]

    def question_summary(self, token: str) -> str:
        """取某题的 gap 描述（alignment-log「提问」字段要用）。"""
        token = token.upper()
        for line in self._read_lines(self.pending_file):
            if f"[{token}]" in line:
                # 行格式: - [ ] [HG-XXXX] 🔴 📋 gap | 选项: ... → 剥前缀取 gap 段
                return _question_head(line)
        return ""

    def question_detail(self, token: str) -> str:
        """取某题的「提问摘要」：gap + 候选项（alignment-log 的提问字段）。

        skill 契约要求提问字段是给人类的选项摘要，不能只写 gap 一句话。"""
        token = token.upper()
        for line in self._read_lines(self.pending_file):
            if f"[{token}]" in line:
                head = _question_head(line)
                opts = ""
                for seg in line.split(" | "):
                    if seg.strip().startswith("选项:"):
                        opts = seg.strip().removeprefix("选项:").strip()
                        break
                return f"{head}（选项: {opts}）" if opts else head
        return ""

    def check_off(self, token: str, resolution: str) -> bool:
        """核销一题：checkbox 打勾并追加核销摘要。返回是否找到未勾的该题。"""
        token = token.upper()
        lines = self._read_lines(self.pending_file)
        for i, line in enumerate(lines):
            if line.startswith(_UNCHECKED) and f"[{token}]" in line:
                lines[i] = (
                    f"{_CHECKED}{line[len(_UNCHECKED):]}"
                    f" | 核销: {_clean(resolution)} | 回填: {_now()}"
                )
                _atomic_write(self.pending_file, "\n".join(lines) + "\n")
                return True
        return False

    def abandon_pending(self, token: str | None = None, reason: str = "") -> int:
        """废弃题目（用户中途弃用）：checkbox 置 [~]，不再要求回答、不阻断就绪。

        token=None 表示废弃本需求全部未决题。返回废弃数量。"""
        lines = self._read_lines(self.pending_file)
        count = 0
        for i, line in enumerate(lines):
            if not (line.startswith(_UNCHECKED) and "[HG-" in line):
                continue
            if token is not None and f"[{token.upper()}]" not in line:
                continue
            suffix = f" | 废弃: {_clean(reason) or '用户弃用'} | {_now()}"
            lines[i] = f"{_ABANDONED}{line[len(_UNCHECKED):]}{suffix}"
            count += 1
        if count:
            _atomic_write(self.pending_file, "\n".join(lines) + "\n")
        return count

    # ------------------------------------------------------- alignment-log
    def append_alignment_log(
        self,
        gap: str,
        question: str,
        human_quote: str,
        interpretation: str,
        landing: str,
    ) -> int:
        """追加一条标准对齐流水。🔴 格式是蓝军 R1 复核契约，改格式 = 毁约。

        human_quote 的三种合法形态（DESIGN.md §4.2）：
          - 群成员一字不改的回复（{昵称/staffId}，钉钉群）
          - 来源: 代码实证（{类/方法}）
          - [AI推断·依据: {推断链}]（确认人: {谁}，{时间}）
        本层只做落盘，形态正确性由调用方（manager/宿主）负责。
        """
        self._ensure()
        if not self.log_file.exists():
            self.log_file.write_text(
                f"# 意图对齐流水 — {self.feature}\n", encoding="utf-8"
            )
        n = sum(
            1
            for line in self._read_lines(self.log_file)
            if line.startswith("## Q")
        ) + 1
        block = (
            f"\n## Q{n} {gap}（{_now()}）\n"
            f"- 提问：{question}\n"
            f"- 人类原话：{human_quote}\n"
            f"- 注入解读：{interpretation}\n"
            f"- 落点：{landing}\n"
        )
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(block)
        return n

    # ---------------------------------------------------------------- inbox
    @staticmethod
    def _meta(text: str) -> str:
        """front-matter 字段消毒：换行是注入向量（nick 里塞换行可伪造 meta 行，
        比如伪造 sender 行冒充白名单成员），一律压成空格。正文 answer 不在此列，
        答案原话一字不改。"""
        return text.replace("\r", " ").replace("\n", " ").strip()

    def write_inbox(self, token: str, answer: str, sender: str, nick: str) -> Path:
        """群回复落盘。答案原话一字不改；文件名带时间戳+随机段，同秒连发不覆盖。"""
        self._ensure()
        ts = time.strftime("%Y%m%d-%H%M%S")
        rand = uuid4().hex[:6]
        path = self.inbox_dir / f"{token.upper()}-{ts}-{rand}.md"
        # 原子写：回调进程猝死不留半个 front-matter 文件给 collect 误解析
        _atomic_write(
            path,
            f"---\ntoken: {token.upper()}\nsender: {self._meta(sender)}\n"
            f"nick: {self._meta(nick)}\nat: {_now()}\n---\n\n{answer}\n",
        )
        return path

    def read_unconsumed(self) -> list[dict]:
        """领取 inbox 里还没被 collect 过的答案（按文件名时间序）。"""
        if not self.inbox_dir.exists():
            return []
        out = []
        for path in sorted(self.inbox_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            meta, _, answer = text.partition("---\n\n")
            fields = {}
            for line in meta.splitlines():
                if ": " in line:
                    k, v = line.split(": ", 1)
                    fields[k.strip()] = v.strip()
            out.append(
                {
                    "token": fields.get("token", ""),
                    "sender": fields.get("sender", ""),
                    "nick": fields.get("nick", ""),
                    "answer": answer.strip(),
                    "file": path.name,
                }
            )
        return out

    def mark_consumed(self, filename: str) -> None:
        """答案归档进 _consumed/，防止下次 collect 重复领取。"""
        if "/" in filename or "\\" in filename:
            raise ValueError(f"非法文件名: {filename!r}")
        self.consumed_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(
            str(self.inbox_dir / filename), str(self.consumed_dir / filename)
        )

    # ------------------------------------------------------------- 推断清单
    def add_inference(self, gap: str, conclusion: str, basis: str) -> str:
        """登记一条 AI 公示推断，返回推断编号 INF-{n}。"""
        self._ensure()
        if not self.inference_file.exists():
            self.inference_file.write_text(
                f"# AI 推断待确认清单 — {self.feature}\n"
                f"> 未经确认的推断禁止标 intent_aligned: true（蓝军 R1 复核）\n\n",
                encoding="utf-8",
            )
        n = sum(
            1
            for line in self._read_lines(self.inference_file)
            if _INF_LINE_RE.match(line)  # 含已废弃行——编号永不复用
        ) + 1
        inf_id = f"INF-{n}"
        with self.inference_file.open("a", encoding="utf-8") as f:
            f.write(
                f"{_UNCHECKED} {inf_id} {_clean(gap)} | 推断: {_clean(conclusion)}"
                f" | 依据: {_clean(basis)} | 登记: {_now()}\n"
            )
        return inf_id

    def settle_inference(self, inf_id: str, approved: bool, confirmer: str) -> bool:
        """确认/驳回一条推断。approved=True 打勾并记确认人，False 记驳回。

        🔴 编号用行首字段精确匹配（审计修复：子串匹配 INF-1 会误中
        正文里提到 'INF-1' 的其他行，结算错行）。"""
        lines = self._read_lines(self.inference_file)
        verdict = f"确认[{_clean(confirmer)}]" if approved else f"驳回[{_clean(confirmer)}]"
        for i, line in enumerate(lines):
            if not line.startswith(_UNCHECKED):
                continue
            m = _INF_LINE_RE.match(line)
            if m and m.group(1) == inf_id:
                lines[i] = (
                    f"{_CHECKED}{line[len(_UNCHECKED):]} | {verdict} | {_now()}"
                )
                _atomic_write(self.inference_file, "\n".join(lines) + "\n")
                return True
        return False

    def abandon_inference(self, inf_id: str, reason: str = "") -> bool:
        """废弃一条未确认推断（推断的前提已不成立/用户弃用）。"""
        lines = self._read_lines(self.inference_file)
        for i, line in enumerate(lines):
            if not line.startswith(_UNCHECKED):
                continue
            m = _INF_LINE_RE.match(line)
            if m and m.group(1) == inf_id:
                lines[i] = (
                    f"{_ABANDONED}{line[len(_UNCHECKED):]}"
                    f" | 废弃: {_clean(reason) or '用户弃用'} | {_now()}"
                )
                _atomic_write(self.inference_file, "\n".join(lines) + "\n")
                return True
        return False

    def inference_summary(self, inf_id: str) -> dict:
        """取一条推断的登记内容（确认时写 alignment-log 要用原文）。
        行首字段精确匹配，避免正文提及编号造成误读。"""
        for line in self._read_lines(self.inference_file):
            m = _INF_LINE_RE.match(line)
            if not (m and m.group(1) == inf_id):
                continue
            # - [ ] INF-1 {gap} | 推断: {x} | 依据: {y} | 登记: {t}
            body = line.split(f" {inf_id} ", 1)[1]
            parts = [p.strip() for p in body.split(" | ")]
            return {
                "gap": parts[0] if parts else "",
                "conclusion": parts[1].removeprefix("推断: ") if len(parts) > 1 else "",
                "basis": parts[2].removeprefix("依据: ") if len(parts) > 2 else "",
            }
        return {}
