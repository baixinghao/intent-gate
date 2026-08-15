"""相位机：对齐 → 生成 → 门禁 → 交付 的机械化判定（只读文件现场）。

相位判定（顺序即优先级）：
  align       有未决题/未确认推断/inbox 未消费——意图未齐，先对齐
  generate    意图已齐但 summary.md 不存在——派 Phase B 生成 subagent（brief 随返回给出）
  gate        summary.md 存在但 lint-report 缺失或 CRITICAL 非零——跑 lint_summary 自检
  deliverable CRITICAL 已归零——可交付评审

🔴 本模块只依赖 ReviewStore 与文件路径，禁止 import engine/manager（防循环导入）。
"""

from __future__ import annotations

import re
from pathlib import Path

from .alignment.store import ReviewStore

# 回流预算缺省值（draft frontmatter 未写 reflow_budget 时按此熔断）
DEFAULT_REFLOW_BUDGET = 2

# lint-report.md 报告头的 CRITICAL 计数行：
#   > 对象：summary.md | CRITICAL 0 / 共 2 条
_LINT_CRITICAL_RE = re.compile(r"CRITICAL\s*[:：]?\s*(\d+)")

# Phase B 生成 subagent 的派工 brief 模板（phase=generate 时随返回透出）。
# 注意：模板内禁止出现裸 {}（.format 填充），占位只用 {feature}/{prd}/{feature_dir}。
_GENERATE_BRIEF = """你是需求「{feature}」的 Phase B 生成 subagent。意图对齐已完成，你的唯一职责是按 playbook Step 1-4 生成产物。

【输入清单】（全部先读再动笔）
- {feature_dir}/_review/analysis-draft.md（解析草稿：型态判定、断层清单、草稿图）
- {feature_dir}/_review/alignment-log.md（意图对齐流水：已核销结论与落点）
- 原始 PRD：{prd}
- {feature_dir}/_review/mapping-draft.md（若存在：映射表草稿）

【产出】
- {feature_dir}/summary.md（按 playbook Step 1-4：型态选定图 + 章节规范 + 三张矩阵 + 映射表）
- 项目根 sql/{{表名}}.sql（DDL 草案，禁止内嵌 summary.md）

【三态纪律】
- 允许：读原始 PRD 取业务细节。
- 禁止：调 dispatch_question 发题（你不是对齐相位）；推翻 alignment-log 已核销结论。
- 生成中新发现的 gap：整理成清单随结论带回，禁止自行假设填掉。

【禁止再派子代理】你是叶子执行者，所有生成亲自动手。
（此纪律只约束你。宿主派你这件事必须发生——宿主不得以「亲自动手等价叶子执行者」为由
在宿主层直行生成：那会把 Phase B 的读写量堆进宿主上下文，正是相位拆分要防的。）

【图演化纪律】summary 的状态机/时序图/决策表必须由 draft 草稿图演化而来，
禁止凭空重画；draft 图内占位（???/TBDn）的消除必须能指回 alignment-log 核销记录。

【映射表迁移归属本相位】先调 draft_mapping 生成正式锚点，补审语义列后并入 summary.md，
禁止手写锚点。

【返回约定】完成后只返回一行结论：生成文件路径 + 遗留 gap 数。"""


def _to_int(text: object, default: int = 0) -> int:
    """frontmatter 值容错转 int（手写改坏的值不炸相位机）。"""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def compute_phase(workspace_root: str | Path, feature: str) -> dict:
    """计算需求当前相位。返回：
    {"phase", "next_action", "brief"(str|None), "reflow_round", "reflow_budget"}"""
    store = ReviewStore(workspace_root, feature)
    meta = store.read_draft_meta()
    base = {
        "reflow_round": _to_int(meta.get("reflow_round", 0)),
        "reflow_budget": _to_int(
            meta.get("reflow_budget", DEFAULT_REFLOW_BUDGET), DEFAULT_REFLOW_BUDGET
        ),
    }

    # ---- 1. align：意图未齐 ----
    pending = store.unchecked_lines()
    inf_pending = sum(
        1
        for line in store._read_lines(store.inference_file)
        if line.startswith("- [ ]")
    )
    inbox_new = (
        len(list(store.inbox_dir.glob("*.md"))) if store.inbox_dir.exists() else 0
    )
    if pending or inf_pending or inbox_new:
        owes = []
        if pending:
            owes.append(f"{len(pending)} 题未决")
        if inf_pending:
            owes.append(f"{inf_pending} 条推断未确认")
        if inbox_new:
            owes.append(f"{inbox_new} 条 inbox 答案未领取")
        # A0 并行探测驱动：草稿尚无绘图层产出（无 mermaid 块）时，
        # 先并行派两个探测子代理，返回合并后再发题——不靠 playbook 文本自觉。
        probe_hint = ""
        if store.draft_file.exists():
            try:
                dtext = store.draft_file.read_text(encoding="utf-8")
            except OSError:
                dtext = ""
            if "```mermaid" not in dtext:
                probe_hint = (
                    "；且草稿尚无绘图层产出——应并行派两个子代理补探测"
                    "（explore 阅读层扫雷 + draw 硬画带 TBDn 草稿图），"
                    "返回后主对话层合并去重再发题"
                )
        return {
            "phase": "align",
            "next_action": (
                f"意图未齐（{'，'.join(owes)}）：先完成对齐——未决题逐题答完调 "
                "resolve_question 核销、推断调 confirm_inferences 确认、"
                f"inbox 调 collect_answers 领取注入{probe_hint}"
            ),
            "brief": None,
            **base,
        }

    feature_dir = Path(workspace_root) / ".harness" / "requests" / feature
    summary = feature_dir / "summary.md"

    # ---- 2. generate：意图已齐，产物未生成 ----
    if not summary.exists():
        prd = meta.get("source") or "（draft frontmatter 未登记 source，请向宿主索取 PRD 路径）"
        return {
            "phase": "generate",
            "next_action": (
                "意图已齐：按 brief 派 Phase B 生成 subagent，产出 summary.md 与 sql/ DDL"
            ),
            "brief": _GENERATE_BRIEF.format(
                feature=feature, prd=prd, feature_dir=feature_dir
            ),
            **base,
        }

    # ---- 3. gate：lint 未跑或 CRITICAL 未归零 ----
    report = store.review_dir / "lint-report.md"
    critical = None
    if report.exists():
        m = _LINT_CRITICAL_RE.search(report.read_text(encoding="utf-8"))
        if m:
            critical = int(m.group(1))
    if critical is None or critical > 0:
        detail = (
            "lint-report.md 缺失或无法解析 CRITICAL 计数"
            if critical is None
            else f"lint-report.md 仍有 {critical} 条 CRITICAL"
        )
        return {
            "phase": "gate",
            "next_action": (
                f"summary.md 已生成但{detail}：调 lint_summary 跑机械自检，"
                "CRITICAL 未归零不得交付（playbook Step 4 门禁）"
            ),
            "brief": None,
            **base,
        }

    # ---- 4. deliverable ----
    return {
        "phase": "deliverable",
        "next_action": "CRITICAL 已归零：summary.md 可进入评审/交付流程",
        "brief": None,
        **base,
    }
