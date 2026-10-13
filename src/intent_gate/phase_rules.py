"""相位铁律重注入：工具返回值尾部附带「本工具最容易违反的铁律」。

34KB playbook 一次性进上下文，规则沉在上下文中段被注意力衰减；工具返回值
落在上下文最尾部（注意力最高位）且宿主 agent 必然读——每次返回附 3-5 条
铁律（≈150-250 token），对抗衰减靠被动推送，不靠自觉。

🔴 本模块只允许**附加**字段（"phase_rules"），禁止改动任何现有返回字段；
helper 全 try/except 兜底——铁律是提醒，任何异常都不得弄炸工具本身。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .phase import compute_phase
from .workspace import resolve_under_root

# ---- 铁律切片表：工具 → 3-5 条短句（口径与 playbook 约束汇总 22 条一致，🔴=硬性）----
_TOOL_RULES: dict[str, list[str]] = {
    "dispatch_question": [
        "🔴 一题一问：🔴 核心断层单独发，禁止与 🟡 同批混发",
        "🔴 落点锚点禁止手写，正式锚点由 draft_mapping 脚本定位",
        "🔴 同坐标已有在飞题会被机械拒收——先对账再发题",
        "选项 ≥3 个互斥且具体（含状态值/动作/结果），必带「其他」",
    ],
    "resolve_question": [
        "🔴 landing 精确到状态机边/时序步骤/规则号，找不到先回问",
        "🔴 人类原话逐字留档 alignment-log，禁止改写",
        "🔴 批复含否决语义（不置/禁止/作废/不予/不改）必须同步落 FORBIDDEN 行",
        "🔴 核销前草稿图必须已存在，没动笔的核销会被拒收",
    ],
    "lint_summary": [
        "🔴 CRITICAL 未归零不得交付，当场修正再复检",
        "🔴 L23：FORBIDDEN 登记 token 在正文残留即 CRITICAL",
        "MINOR 不拦路但强制认领：逐条出示人类拍板并留痕",
        "权威判定契约见 lint-report.md 头部，随常量自动更新",
    ],
    "record_inference": [
        "🔴 资金主流程与红线规则禁止纯推断，必须发题让人拍板",
        "🔴 basis 必须给显式依据链；会话末 confirm_inferences 批量点头",
        "未经人类确认的推断禁止标 intent_aligned: true",
    ],
    "record_judgment": [
        "🔴 初筛只是绊线，正式判断由你按 playbook 语义分析落账",
        "🔴 黄灯禁止自行假设推进，核心主流程歧义必须当场对齐",
        "complexity 取 simple/medium/complex，confidence 取 🟢/🟡/🔴",
    ],
    "analyze_requirement": [
        "🔴 探测可下放子代理，交互（发题/收答/核销）必须在主对话层",
        "🔴 绘图层第一回合就硬画草稿，画不下去标 TBDn 占位",
        "初筛信号仅供交叉校验，正式结论调 record_judgment 落账",
    ],
    "collect_answers": [
        "🔴 领到答案必须注入图/规则并 resolve_question 当场核销",
        "只领不核销，题会一直挂待决清单（纪律，不是缺陷）",
    ],
    "confirm_inferences": [
        "🔴 approved 必须带 interpretation+landing，确认即注入",
        "🔴 未确认的推断禁止标 intent_aligned: true",
    ],
    "rebroadcast_pending": [
        "每次开工/用户说「继续」时先调它对账，再续核销",
        "🔴 相位由 MCP 返回的 phase 块机械驱动，不靠记忆走流程",
    ],
    "list_pending_questions": [
        "🔴 根错了会读到空现场假「已齐」——先 describe_workspace 确认根",
        "intent_aligned_ready=true 才允许进入 generate 相位",
    ],
    "abandon_question": [
        "废弃正式留痕可追溯，不阻断 intent_aligned_ready",
        "🔴 降级 [🟡待澄清] 必须先获人类明确同意（回执纪律）",
    ],
    "abandon_inference": [
        "推断前提不成立即废弃，禁止留在待确认清单",
        "🔴 相位由 MCP 返回的 phase 块机械驱动，不靠记忆走流程",
    ],
    "describe_workspace": [
        "🔴 新会话开工先调它确认根——根错了落盘全落别的项目",
        "多工作区宿主必须每次调用显式传 project_path",
    ],
    "draft_mapping": [
        "🔴 锚点由脚本真实定位，禁止手写章节号",
        "⚠️ 未定位条目必须当场回问人类，禁止静默放过",
        "补审语义列后并入 summary，映射表迁移归属 Phase B",
    ],
}

# 兜底条：工具无强相关铁律时用（playbook 约束第 17/12/6 条）
_GENERIC_RULES = [
    "🔴 相位由 MCP 返回的 phase 块机械驱动，不靠记忆走流程",
    "🔴 交付前 lint_summary 机械自检，CRITICAL 归零才可交付",
    "🔴 意图对齐三段论：探测可下放，交互必须在主对话层",
]

# 相位特化（第一版只做顺手的一处）：analyze_requirement 续跑（mode=resume）
# 命中 generate/gate 相位时替换尾条——同一工具在不同相位最容易违反的铁律不同
_ANALYZE_RESUME_TAIL = {
    "generate": "意图已齐：按 brief 派 Phase B 生成 subagent，图必须由 draft 演化",
    "gate": "summary 已生成：调 lint_summary 机械自检，CRITICAL 归零才可交付",
}


def feature_from_summary_path(
    workspace_root: str | Path, summary_path: str | Path
) -> str | None:
    """从 summary 路径反推 feature 名（phase.py 目录约定
    {root}/.harness/requests/{feature}/summary.md；lint_summary/draft_mapping
    不带 feature 参数时用）。不符合约定返回 None——调用侧降级为不算相位。"""
    try:
        parts = resolve_under_root(workspace_root, Path(summary_path)).resolve().parts
        if len(parts) >= 4 and parts[-4] == ".harness" and parts[-3] == "requests":
            return parts[-2]
    except Exception:  # noqa: BLE001, S110  # 反推失败属常态（非约定路径），静默降级
        pass
    return None


def with_phase_rules(
    result: dict[str, Any],
    tool: str,
    workspace_root: str | Path | None = None,
    feature: str | None = None,
) -> dict[str, Any]:
    """在工具返回 dict 上附加 "phase_rules" 铁律列表后返回（薄壳层统一挂载点）。

    result 不是 dict（如 collect_answers 的 list）时原样返回；任何异常静默
    返回原 result——铁律注入是加分项，绝不允许弄炸工具本身。workspace_root
    或 feature 缺失时降级为只给工具铁律、不算相位。
    """
    if not isinstance(result, dict):
        return result
    try:
        rules = list(_TOOL_RULES.get(tool) or _GENERIC_RULES)
        try:
            if tool == "analyze_requirement" and result.get("mode") == "resume" \
                    and workspace_root is not None and feature:
                phase = str(compute_phase(workspace_root, feature).get("phase") or "")
                override = _ANALYZE_RESUME_TAIL.get(phase)
                if override:
                    rules[-1] = override
        except Exception:  # noqa: BLE001, S110  # 相位只是调味，算不出就用静态条
            pass
        result["phase_rules"] = rules
    except Exception:  # noqa: BLE001, S110  # 铁律注入绝不弄炸工具本身
        pass
    return result
