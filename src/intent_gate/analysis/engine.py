"""analyze_request 引擎实现（纯 stdlib，无第三方依赖，可独立单测）。

型态判定标准（与 doc-analysis skill Step 1 一致）：
  State-Driven   核心实体存在生命周期跳转；状态信号 ≥4 触发
  Process-Driven 多接口串联/跨服务调用；跨系统信号 ≥3 触发
  Rule-Driven    密集业务判断分支；条件分支信号 ≥3 触发
复杂度：simple / medium / complex，含"低强度多型态降级修正"（skill 原规则）。

置信度灯（skill Step 0）：
  🔴 核心逻辑断层（只有成功没有失败流 / 资金规则只有结论没有机制 / 前后矛盾）
  🟡 局部歧义（计算口径不明 / 边界未覆盖 / 术语新造词）
  🟢 流程闭环

歧义点初筛规则来自 skill 的九类歧义点表，只实现正则可判的子集；
判不了的（如字段语义歧义）留给宿主 agent 语义分析。
"""

from __future__ import annotations

import re
from pathlib import Path

# ------------------------------------------------------------ 信号词表
# 状态信号：常见中文状态词 + 英文大写下划线枚举（如 WITHDRAW_CONFIRM）
_STATE_CN = [
    "草稿", "待审核", "待审批", "审批中", "待处理", "处理中", "已完成", "已取消",
    "待支付", "支付中", "已支付", "支付失败", "退款中", "已退款", "已驳回",
    "待放款", "放款中", "已放款", "冻结", "已激活", "待确认", "确认中",
    "已签约", "待签约", "路由中", "待路由", "已完结", "已关闭",
]
_STATE_ENUM_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")  # 大写枚举常量

# 跨系统/技术动作信号：出现即计一次"跨系统触点"
_SYSTEM_WORDS = [
    "第三方", "回调", "网关", "资方", "渠道", "短信", "决策引擎", "风控",
    "Redis", "Redisson", "MQ", "Kafka", "RabbitMQ", "MySQL", "数据库",
    "ECM", "OSS", "HTTP", "接口", "RPC", "Dubbo", "Feign", "API",
]

# 规则分支信号
_RULE_WORDS = [
    "如果", "若", "否则", "否则如果", "当", "条件", "优先级", "优先",
    "白名单", "黑名单", "规则", "仅限", "必须", "不得", "禁止", "超过", "低于",
]

# 失败/异常路径信号：全文一个都没有 = "只有成功没有失败流" 🔴
_FAILURE_WORDS = ["失败", "异常", "超时", "回滚", "补偿", "重试", "降级", "熔断"]

# ------------------------------------------------------------ 歧义点规则
# 每条规则：(需命中正则, 需缺失正则或None, 级别, 类别, gap描述, 候选选项)
# 对应 skill 九类歧义点里正则可判的部分。
_GAP_RULES: list[tuple[str, str | None, str, str, str, list[str]]] = [
    # 资金安全：写操作只提前端防重，无服务端机制 → 🔴（skill 原文视同核心逻辑断层）
    (
        r"防重|重复提交|重复点击|置灰|防止重复",
        r"幂等|分布式锁|唯一索引|乐观锁|Redisson|锁",
        "🔴", "🔧",
        "写操作防重复只提到前端手段，服务端防重机制未说明",
        ["沿用既有分布式锁方案（key 需指定）", "幂等 Token + 分布式锁双保险",
         "仅数据库唯一索引/状态机幂等"],
    ),
    # 越权：可见性/归属只下结论，无校验机制 → 🔴
    (
        r"仅本人|只有自己|越权|所属人|本人可见",
        r"校验|鉴权|验证|检查",
        "🔴", "🔧",
        "数据可见性/归属只有结论，服务端所属人校验机制未说明",
        ["接口层统一校验所属人（拦截器/注解）", "SQL 层带属主条件",
         "领域服务内显式校验并抛越权异常"],
    ),
    # 计算口径："按最高/最低/优先展示"无口径 → 🟡
    (
        r"按.{0,4}(最高|最低|最优|优先).{0,4}(展示|显示|取|选)",
        r"口径|计算规则|排序规则|公式",
        "🟡", "📋",
        "排序/择优展示只有结论，计算口径未说明",
        ["给出明确排序字段与方向", "给出优先级列表（命中即停）",
         "由配置决定，接口返回开关/白名单"],
    ),
    # 模糊跳转："看情况/视情况"无分支条件 → 🔴（skill 原文示例）
    (
        r"看情况|视情况|视情|酌情",
        None,
        "🔴", "📋",
        "存在'看情况'式模糊分支，跳转/处理条件未定义",
        ["给出具体分支条件与判断字段", "删除该分支统一走默认路径",
         "保留人工处理入口，其余走默认"],
    ),
    # 第三方接口契约不明 → 🟡
    (
        r"第三方|短信网关|支付渠道|资方|决策引擎|风控",
        r"限频|限流|超时时间|SLA|契约",
        "🟡", "🔧",
        "第三方接口契约（超时/限频/失败语义）未说明",
        ["补充超时与重试策略", "补充限频与降级方案", "接口契约由对方文档定，引用链接"],
    ),
    # 术语新造词：出现"申请号/订单号/编号"等易与既有术语打架的词 → 🟡
    (
        r"申请号|订单号|业务号|单据号",
        r"术语|沿用|复用",
        "🟡", "📋",
        "编号类字段命名需与 wiki/领域术语对齐（新造词还是沿用既有术语）",
        ["沿用领域术语表既有命名", "新造词，补充术语定义", "字段复用已有列，不新增"],
    ),
]

# 复杂度触发门槛（skill Step 1 原值）
_STATE_THRESHOLD = 4
_PROCESS_THRESHOLD = 3
_RULE_THRESHOLD = 3


def _count_states(text: str) -> tuple[int, list[str]]:
    """状态信号计数：中文状态词命中 + 英文枚举常量去重计数。"""
    hits = {w for w in _STATE_CN if w in text}
    enums = set(_STATE_ENUM_RE.findall(text))
    # 枚举常量过滤掉常见非状态词（HTTP/API/Redis 这类是系统信号不是状态）
    enums -= {"HTTP", "HTTPS", "API", "JSON", "SQL", "ACID", "BASE",
              "GET", "POST", "PUT", "NULL", "TRUE", "FALSE", "NONE"}
    found = sorted(hits) + sorted(enums)
    return len(hits) + len(enums), found


def _count_systems(text: str) -> tuple[int, list[str]]:
    hits = sorted({w for w in _SYSTEM_WORDS if w in text})
    return len(hits), hits


def _count_rules(text: str) -> tuple[int, list[str]]:
    hits = sorted({w for w in _RULE_WORDS if w in text})
    return len(hits), hits


def _judge_pattern(text: str) -> dict:
    """型态判定 + 复杂度（含降级修正），规则与 skill Step 1 对齐。"""
    n_state, states = _count_states(text)
    n_proc, procs = _count_systems(text)
    n_rule, rules = _count_rules(text)

    patterns = []
    if n_state >= 2:  # 命中信号才算有该型态特征（触发门槛另算）
        patterns.append("State-Driven")
    if n_proc >= 2:
        patterns.append("Process-Driven")
    if n_rule >= 2:
        patterns.append("Rule-Driven")

    fired = []
    if n_state >= _STATE_THRESHOLD:
        fired.append("State-Driven")
    if n_proc >= _PROCESS_THRESHOLD:
        fired.append("Process-Driven")
    if n_rule >= _RULE_THRESHOLD:
        fired.append("Rule-Driven")

    # 复杂度：complex=2种以上型态或任一达门槛；medium=单一型态未达门槛；simple=纯CRUD
    if len(patterns) >= 2 or len(fired) >= 1:
        complexity = "complex"
        # 🔴 降级修正：多型态但每种都低于门槛 → medium
        if len(patterns) >= 2 and not fired:
            complexity = "medium"
    elif patterns:
        complexity = "medium"
    else:
        complexity = "simple"

    selected_tools = []
    if "State-Driven" in fired:
        selected_tools.append("stateDiagram-v2")
    if "Process-Driven" in fired:
        selected_tools.append("sequenceDiagram")
    if "Rule-Driven" in fired:
        selected_tools.append("decision_table")

    return {
        "logic_pattern": patterns,
        "fired_thresholds": fired,
        "complexity": complexity,
        "selected_tools": selected_tools,
        "signals": {"states": states, "systems": procs, "rules": rules},
    }


def _scan_gaps(text: str) -> list[dict]:
    """歧义点机械初筛（skill 九类歧义点的正则可判子集）。"""
    gaps = []
    for need, absent, level, category, gap, options in _GAP_RULES:
        if not re.search(need, text):
            continue
        if absent and re.search(absent, text):
            continue  # 机制词已出现，初筛放行（语义终判归宿主）
        gaps.append({
            "gap": gap,
            "severity": level,
            "category": category,
            "suggested_options": options,
        })
    # 只有成功没有失败流：提到成功/流程，但全文无任何失败信号 → 🔴
    if re.search(r"成功|流程|提交|放款|支付", text) and not any(
        w in text for w in _FAILURE_WORDS
    ):
        gaps.append({
            "gap": "全文只有成功路径，失败/异常/超时/回滚路径完全缺失",
            "severity": "🔴",
            "category": "📋",
            "suggested_options": [
                "逐主流程补失败分支（失败终态+提示+是否回滚）",
                "统一兜底：异常即错误码返回，不入状态机",
                "核心链路补补偿/重试，边界链路只记日志",
            ],
        })
    return gaps


def _confidence(gaps: list[dict]) -> str:
    if any(g["severity"] == "🔴" for g in gaps):
        return "🔴"
    if gaps:
        return "🟡"
    return "🟢"


# ================================================================== 主入口
def analyze_request(workspace_root: str | Path, feature: str, prd_path: str | None = None) -> dict:
    """需求解析主入口。两条路自动区分：

    resume: {workspace_root}/.harness/requests/{feature}/_review/ 已有现场
            （待决清单或对齐流水存在）→ 走文件续跑，prd_path 被忽略。
    fresh:  无现场 → 必须给 prd_path，从0解析。

    🔴 feature 校验复用 ReviewStore（审计修复：未校验可路径逃逸写文件）。
    """
    from ..alignment.store import ReviewStore

    store = ReviewStore(workspace_root, feature)  # 非法 feature 在此直接 ValueError
    review_dir = store.review_dir
    pending_file = review_dir / "pending-questions.md"
    log_file = review_dir / "alignment-log.md"

    if pending_file.exists() or log_file.exists():
        return _resume(feature, review_dir)
    return _fresh(workspace_root, feature, prd_path, review_dir)


# ---------------------------------------------------------------- resume
def _resume(feature: str, review_dir: Path) -> dict:
    """中断续跑：只认文件现场，不依赖任何 session 记忆。

    汇报：已答几题、还挂几题、推断几个未确认、draft 是否在、
    以及宿主下一步该调什么工具（动作建议按优先级排序）。"""
    pending_file = review_dir / "pending-questions.md"
    log_file = review_dir / "alignment-log.md"
    inf_file = review_dir / "inference-pending.md"
    draft_file = review_dir / "analysis-draft.md"

    unanswered, answered = [], 0
    if pending_file.exists():
        for line in pending_file.read_text("utf-8").splitlines():
            if line.startswith("- [ ]") and "[HG-" in line:
                unanswered.append(line)
            elif line.startswith("- [x]") and "[HG-" in line:
                answered += 1

    inf_pending = 0
    if inf_file.exists():
        inf_pending = sum(
            1 for line in inf_file.read_text("utf-8").splitlines()
            if line.startswith("- [ ]")
        )

    inbox_new = 0
    inbox_dir = review_dir / "inbox"
    if inbox_dir.exists():
        inbox_new = len(list(inbox_dir.glob("*.md")))

    log_rounds = 0
    if log_file.exists():
        log_rounds = sum(
            1 for line in log_file.read_text("utf-8").splitlines()
            if line.startswith("## Q")
        )

    # 下一步动作建议（宿主照单执行即可）
    next_actions = []
    if inbox_new:
        next_actions.append(f"collect_answers → 有 {inbox_new} 条新答案待领取注入")
    if unanswered:
        next_actions.append(
            f"rebroadcast_pending → 还有 {len(unanswered)} 题未决，对账催单"
        )
    if inf_pending:
        next_actions.append(
            f"confirm_inferences → {inf_pending} 条 AI 推断待批量确认"
        )
    if not unanswered and not inf_pending and not inbox_new:
        next_actions.append(
            "意图已齐（intent_aligned_ready=true）→ 可继续生成产物（playbook Step 1-4）"
        )

    # skill 词表 frontmatter 建议（失真点 Z8 修复，与 list_pending_questions 同口径）
    red_pending = sum(1 for line in unanswered if "🔴" in line.split(" | ", 1)[0])
    ready = not unanswered and not inf_pending and not inbox_new
    status = "blocked" if red_pending else ("pending_review" if ready else "draft")

    return {
        "mode": "resume",
        "feature": feature,
        "intent_status": {
            "answered_questions": answered,
            "pending_questions": len(unanswered),
            "pending_red": red_pending,
            "pending_question_lines": unanswered,
            "pending_inferences": inf_pending,
            "alignment_rounds": log_rounds,
            "inbox_new_answers": inbox_new,
            "draft_exists": draft_file.exists(),
            "intent_aligned_ready": ready,
        },
        "frontmatter_advice": {
            "status": status,
            "intent_aligned": ready,
            "note": "approved 只能由人类/评审授予，MCP 永不自授",
        },
        "next_actions": next_actions,
        "note": "续跑模式只读文件现场，未重新解析 PRD；原 PRD 仅作溯源参考",
    }


# ----------------------------------------------------------------- fresh
def _fresh(
    workspace_root: str | Path, feature: str, prd_path: str | None, review_dir: Path
) -> dict:
    """从0解析：读 PRD → 型态判定 → 置信度灯 → 歧义点初筛 → 落盘 draft。"""
    if not prd_path:
        return {
            "mode": "fresh",
            "ok": False,
            "reason": f"需求 {feature} 无既有现场，从0解析必须提供 prd_path",
        }
    prd = Path(prd_path)
    if not prd.exists():
        return {"mode": "fresh", "ok": False, "reason": f"PRD 文件不存在: {prd_path}"}

    text = prd.read_text("utf-8")
    pattern = _judge_pattern(text)
    gaps = _scan_gaps(text)
    confidence = _confidence(gaps)

    # 🔴 红灯门禁（skill 原规则）：有 🔴 未消除，报告 status 必须 blocked，
    # 任务拆分首任务强制 [BLOCKER]——且必须指向第一道 🔴 题，
    # gaps[0] 是按规则表扫描顺序的第一题，可能是 🟡，指错了 BLOCKER 就白挂
    first_red = next((g for g in gaps if g["severity"] == "🔴"), None)
    gate = {
        "confidence": confidence,
        "status": "blocked" if confidence == "🔴" else "draft",
        "blocker_task": (
            f"[BLOCKER] 消除核心逻辑断层：{first_red['gap']}" if first_red else None
        ),
    }

    # draft 落盘：resume 时靠它还原"引擎当初看到了什么"
    review_dir.mkdir(parents=True, exist_ok=True)
    draft = review_dir / "analysis-draft.md"
    draft.write_text(_render_draft(feature, prd_path, pattern, gaps, gate), "utf-8")

    return {
        "mode": "fresh",
        "ok": True,
        "feature": feature,
        "prd": str(prd),
        "draft_file": str(draft),
        **pattern,
        **gate,
        "gaps": gaps,
        "next_actions": _fresh_next_actions(gaps, confidence),
        "note": "歧义点为机械初筛结果，宿主 agent 应做语义复核后再 dispatch_question",
    }


def _fresh_next_actions(gaps: list[dict], confidence: str) -> list[str]:
    actions = []
    red = [g for g in gaps if g["severity"] == "🔴"]
    yellow = [g for g in gaps if g["severity"] == "🟡"]
    for g in red:
        actions.append(
            f"🔴 dispatch_question（必答）: {g['gap']}（类别 {g['category']}）"
        )
    for g in yellow:
        actions.append(
            f"🟡 dispatch_question 或 record_inference: {g['gap']}（类别 {g['category']}）"
        )
    if confidence == "🟢":
        actions.append("置信度绿灯 → 可直接进入 generate_artifacts")
    if not gaps:
        actions.append("机械初筛未发现歧义，宿主仍应语义复核一遍再定灯")
    return actions


def _render_draft(feature: str, prd_path: str, pattern: dict, gaps: list[dict], gate: dict) -> str:
    """analysis-draft.md：fresh 解析的落盘快照（resume/对账/复核的依据）。"""
    lines = [
        "---",
        f"feature: {feature}",
        f"source: {prd_path}",
        f"complexity: {pattern['complexity']}",
        f"logic_pattern: [{', '.join(pattern['logic_pattern'])}]",
        f"selected_tools: [{', '.join(pattern['selected_tools'])}]",
        f"confidence: {gate['confidence']}",
        f"status: {gate['status']}",
        "intent_aligned: false",
        "generated_by: intent-gate analysis engine（机械初筛信号，正式判断见宿主复核）",
        "---",
        "",
        f"# 需求解析草稿 — {feature}",
        "",
        "## 型态判定信号",
        "",
        f"- 状态信号 ({len(pattern['signals']['states'])}): {', '.join(pattern['signals']['states']) or '无'}",
        f"- 跨系统信号 ({len(pattern['signals']['systems'])}): {', '.join(pattern['signals']['systems']) or '无'}",
        f"- 规则信号 ({len(pattern['signals']['rules'])}): {', '.join(pattern['signals']['rules']) or '无'}",
        "",
        "## 歧义点初筛",
        "",
    ]
    if gaps:
        for i, g in enumerate(gaps, 1):
            lines.append(f"### G{i} {g['severity']} {g['category']} {g['gap']}")
            for j, o in enumerate(g["suggested_options"], 1):
                lines.append(f"  {j}. {o}")
            lines.append(f"  {len(g['suggested_options']) + 1}. 其他（请输入）")
            lines.append("")
    else:
        lines.append("（机械初筛未发现歧义点）")
    lines += [
        "",
        "> 本文件由引擎生成，供 resume 续跑与宿主复核；最终语义判断以宿主 agent 为准。",
    ]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------- record_analysis
def record_analysis(
    workspace_root: str | Path,
    feature: str,
    logic_pattern: list[str],
    complexity: str,
    confidence: str,
    selected_tools: list[str] | None = None,
    gaps: list[dict] | None = None,
    prd_path: str | None = None,
    notes: str = "",
) -> dict:
    """宿主语义判断落账（三刀之②）：判断是宿主做的，MCP 负责校验+落盘。

    与 analyze_requirement(fresh) 的机械初筛分工：
      初筛 = 交叉校验信号（绊线）；record_analysis = 宿主的正式判断。
    落盘后宿主对 gaps 逐题 dispatch_question 分发（单一登记路径，不重复开闸）。

    gaps 元素: {"gap": str, "severity": "🔴"|"🟡", "category": "📋"|"🔧",
               "options": [至少3个], "recommend": str(可选)}
    """
    from ..alignment.store import ReviewStore

    store = ReviewStore(workspace_root, feature)
    if confidence not in ("🟢", "🟡", "🔴"):
        return {"ok": False, "reason": "confidence 只支持 🟢/🟡/🔴"}
    if complexity not in ("simple", "medium", "complex"):
        return {"ok": False, "reason": "complexity 只支持 simple/medium/complex"}
    valid_patterns = {"State-Driven", "Process-Driven", "Rule-Driven"}
    bad = set(logic_pattern) - valid_patterns
    if bad:
        return {"ok": False, "reason": f"非法型态: {sorted(bad)}（只支持 {sorted(valid_patterns)}）"}
    gaps = list(gaps or [])
    for i, g in enumerate(gaps):
        if g.get("severity") not in ("🔴", "🟡") or g.get("category") not in ("📋", "🔧"):
            return {"ok": False, "reason": f"gaps[{i}] 的 severity/category 非法"}
        if not str(g.get("gap", "")).strip():
            return {"ok": False, "reason": f"gaps[{i}] 缺 gap 描述"}
        # 选项数与 dispatch_question 同口径：≥3 个候选，有推荐项可放宽——
        # 落账时不拦，开闸时才被拒 = 账面上挂了一道永远发不出去的题
        if len(g.get("options") or []) < 3 and not str(g.get("recommend", "")).strip():
            return {"ok": False, "reason": f"gaps[{i}] 至少 3 个候选选项（有推荐项可放宽）"}

    tools = list(selected_tools or [])
    status = "blocked" if confidence == "🔴" else "draft"

    # draft 落盘（skill frontmatter 词表，generated_by 标注宿主语义判断）
    store.review_dir.mkdir(parents=True, exist_ok=True)
    draft = store.review_dir / "analysis-draft.md"
    lines = [
        "---",
        f"feature: {feature}",
    ]
    if prd_path:
        lines.append(f"source: {prd_path}")
    lines += [
        f"complexity: {complexity}",
        f"logic_pattern: [{', '.join(logic_pattern)}]",
        f"selected_tools: [{', '.join(tools)}]",
        f"confidence: {confidence}",
        f"status: {status}",
        "intent_aligned: false",
        "generated_by: host agent（语义判断，record_analysis 落账）",
        "---",
        "",
        f"# 需求解析草稿 — {feature}（宿主语义判断版）",
        "",
    ]
    if notes:
        lines += ["## 判断说明", "", notes, ""]
    lines += ["## 歧义点清单（宿主判定）", ""]
    if gaps:
        for i, g in enumerate(gaps, 1):
            lines.append(f"### G{i} {g['severity']} {g['category']} {g['gap']}")
            for j, o in enumerate(g.get("options") or [], 1):
                lines.append(f"  {j}. {o}")
            tail = len(g.get("options") or []) + 1
            lines.append(f"  {tail}. 其他（请输入）")
            if g.get("recommend"):
                lines.append(f"  推荐: {g['recommend']}")
            lines.append("")
    else:
        lines.append("（宿主判定无歧义点）")
    draft.write_text("\n".join(lines) + "\n", encoding="utf-8")

    next_actions = []
    red = [g for g in gaps if g["severity"] == "🔴"]
    if red:
        next_actions.append(
            f"🔴 共 {len(red)} 题核心断层：逐题 dispatch_question(severity='🔴') 必答，"
            f"未消前 status 保持 blocked"
        )
    yellow = [g for g in gaps if g["severity"] == "🟡"]
    if yellow:
        next_actions.append(
            f"🟡 共 {len(yellow)} 题：dispatch_question 或 record_inference（边界 case）"
        )
    if confidence == "🟢":
        next_actions.append("绿灯 → 可直接进入 playbook Step 1-4 生成产物")

    return {
        "ok": True,
        "feature": feature,
        "draft_file": str(draft),
        "status": status,
        "intent_aligned": False,
        "registered_gaps": len(gaps),
        "next_actions": next_actions,
        "note": "判断已落账；gaps 尚未开闸——逐题 dispatch_question 完成登记分发",
    }
