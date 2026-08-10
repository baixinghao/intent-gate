# -*- coding: utf-8 -*-
"""summary_lint — 需求分析报告机械检查器 v2。

🔴 此文件逻辑冻结，禁止"重写优化"——它是产物格式契约的机械半边，
任何逻辑改动都必须经过完整回归评估。

机械检查项：
  L0 状态机块存在但边解析为零（CRITICAL，守门员失明兜底，绝不静默跳过）
  L1 状态机成功终态（CRITICAL）
  L2 死状态（MAJOR）
  L3 同状态多出边（MINOR，提示语义复核）
  L4 映射表锚点：章节号存在 + 关键词匹配标题（CRITICAL）
  L5 规则引用 BR-xx 必须有定义（MAJOR）
  L6 表读写矩阵：每张表至少一处写（CRITICAL）/一处读（MINOR）
  L7 映射表行 vs alignment-log Q 编号覆盖：每个 Q 必须有映射行（MAJOR）
  L8 [🟡待澄清] 降级项必须附人类确认记录（MINOR）

矩阵生成（蓝军/人工只填判断列，禁止手建）：
  矩阵① 转移清单  矩阵② 表读写矩阵  矩阵③ 引用核对清单
"""
import re
from pathlib import Path

KW_MAP = {
    "时序图": ["时序", "流程"],
    "决策表": ["决策", "规则"],
    "Redis": ["Redis", "缓存"],
    "状态机": ["状态机", "状态"],
    "数据模型": ["数据模型"],
}
WRITE_KW = ("INSERT", "UPDATE", "save", "写入", "落库", "DELETE")
READ_KW = ("SELECT", "查询", "find", "query", "Query")


def lint(summary_path: Path):
    text = summary_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    findings = []

    # ---- mermaid 块 ----
    blocks, cur, in_block = [], [], False
    for ln in lines:
        if ln.strip().startswith("```mermaid"):
            in_block, cur = True, []
        elif in_block and ln.strip().startswith("```"):
            blocks.append("\n".join(cur))
            in_block = False
        elif in_block:
            cur.append(ln)

    # ---- 状态机边 ----
    # 状态标识符：mermaid stateDiagram-v2 允许 CJK 裸标识符（中文 PRD 常态），
    # 首字符不能钉死 ASCII——否则整台中文状态机解析为零边，L1/L2/L3 会静默失明。
    state_id = r"(?:\[\*\]|[\w一-鿿]+)"
    # 🔴 行内空白必须用 [ \t] 而不是 \s：\s 匹配 \n，无标签的边（如
    # "SUCCESS --> [*]"）会跨行吞掉下一行——边被吃掉，死状态漏检
    edge_re = re.compile(
        rf"^[ \t]*({state_id})[ \t]*-->[ \t]*({state_id})[ \t]*:?[ \t]*(.*)$", re.M)
    edges = []  # (src, dst, event, actions)
    state_blocks = 0
    for b in blocks:
        if "stateDiagram" in b:
            state_blocks += 1
            for m in edge_re.finditer(b):
                label = m.group(3).strip()
                em = re.match(r"([^(]+)(?:\(([^)]*)\))?", label)
                edges.append((m.group(1), m.group(2),
                              (em.group(1).strip() if em else label),
                              (em.group(2).strip() if em and em.group(2) else "")))

    # L0：守门员失明兜底——有状态机块却一条边都没解析出来，必须报警，
    # 绝不允许 L1-L3 静默跳过还把报告伪装成全绿交付。
    if state_blocks and not edges:
        findings.append(("CRITICAL", "L0",
                         f"存在 {state_blocks} 个 stateDiagram 块但未能解析出任何状态边"
                         "（疑似状态命名含非常规字符/引号写法），L1-L3 无法执行——"
                         "必须人工核对状态机后才能交付"))

    if edges:
        states = {s for s, _, _, _ in edges if s != "[*]"} | {t for _, t, _, _ in edges if t != "[*]"}
        if not any(re.search(r"FINISH|SUCCESS|DONE|COMPLETE|成功|已完成|已结束", s) for s in states):
            findings.append(("CRITICAL", "L1", f"状态机无成功终态（现有状态：{', '.join(sorted(states))}）"))
        out_map = {}
        for s, t, _, _ in edges:
            out_map.setdefault(s, set()).add(t)
        for st in sorted(states):
            outs = out_map.get(st, set())
            if not outs:
                findings.append(("MAJOR", "L2", f"死状态 {st}：无出边且未流向 [*]"))
            real = outs - {st}
            if len(real) > 1:
                findings.append(("MINOR", "L3", f"状态 {st} 有 {len(real)} 条出边（→{', →'.join(sorted(real))}），需人工确认触发条件可区分"))

    # ---- 章节 ----
    sections = {}
    for ln in lines:
        m = re.match(r"^#{2,3}\s*(\d+(?:\.\d+)?)[\.、\s]+(.+)$", ln)
        if m:
            sections[m.group(1)] = m.group(2).strip()

    # ---- 映射表 ----
    map_rows, in_map = [], False
    for ln in lines:
        if "意图注入映射表" in ln:
            in_map = True
            continue
        if in_map and ln.startswith("#"):
            in_map = False
        if in_map and ln.strip().startswith("|"):
            map_rows.append(ln)

    anchors = []  # (ref, verdict)
    for ln in map_rows:
        for m in re.finditer(r"§(\d+(?:\.\d+)?)\s*([^\s，。；|]*)", ln):
            num, kw = m.group(1), m.group(2)
            if num not in sections:
                verdict = f"❌ §{num} 不存在"
                findings.append(("CRITICAL", "L4", f"映射表引用 §{num}（{kw}）——该章节不存在"))
            else:
                bad = False
                for key, targets in KW_MAP.items():
                    if key in kw and not any(t in sections[num] for t in targets):
                        verdict = f"❌ §{num} 标题为「{sections[num]}」"
                        findings.append(("CRITICAL", "L4", f"映射表引用「§{num} {kw}」，但 §{num} 标题为「{sections[num]}」，锚点错位"))
                        bad = True
                        break
                if not bad:
                    verdict = f"✅ §{num} {sections[num]}"
            anchors.append((f"§{num} {kw}".strip(), verdict))

    # ---- L5 ----
    # 口径与 mapper.py parse_summary 保持一致：决策表定义行 = strip 后以 "| BR" 开头。
    defined = set(re.findall(r"BR-(\d+)", "\n".join(ln for ln in lines if ln.strip().startswith("| BR"))))
    for r in sorted(set(re.findall(r"BR-(\d+)", text)) - defined):
        findings.append(("MAJOR", "L5", f"引用了 BR-{r} 但决策表中无定义"))

    # ---- L6 表读写 ----
    sql_dir = summary_path.parent.parent.parent.parent / "sql"
    if not sql_dir.exists():
        sql_dir = summary_path.parent / "sql"
    table_matrix = []  # (table, writers, readers)
    for sql in sorted(sql_dir.glob("*.sql")) if sql_dir.exists() else []:
        for tm in re.finditer(r"CREATE TABLE[^`]*`(\w+)`", sql.read_text(encoding="utf-8")):
            tbl = tm.group(1)
            tbl_lines = [ln.strip() for ln in text.split("\n") if tbl in ln]
            writers = [ln[:60] for ln in tbl_lines if any(w in ln for w in WRITE_KW)]
            readers = [ln[:60] for ln in tbl_lines if any(r in ln for r in READ_KW)]
            table_matrix.append((tbl, writers, readers))
            if not writers:
                findings.append(("CRITICAL", "L6", f"表 {tbl} 全报告无任何写入动作（图/文均无 INSERT/UPDATE/save）"))
            if not readers:
                findings.append(("MINOR", "L6", f"表 {tbl} 全报告无读取动作"))

    # ---- L7 映射行 vs 日志 Q 覆盖 ----
    log_path = summary_path.parent / "_review" / "alignment-log.md"
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8")
        q_nums = set(re.findall(r"^## Q(\d+)", log_text, re.M))
        map_nums = set()
        for ln in map_rows:
            m = re.match(r"\s*\|\s*(\d+)", ln)
            if m:
                map_nums.add(m.group(1))
        for q in sorted(q_nums - map_nums, key=int):
            findings.append(("MAJOR", "L7", f"alignment-log Q{q} 在意图注入映射表中无对应行（注入可能未登记落点）"))

    # ---- L8 降级回执 ----
    if "[🟡待澄清]" in text:
        seg_start = text.find("[🟡待澄清]")
        seg = text[seg_start:seg_start + 3000]
        if "确认" not in seg and "同意降级" not in seg:
            findings.append(("MINOR", "L8", "存在 [🟡待澄清] 降级项，但附近未发现人类确认记录字样"))

    return findings, edges, table_matrix, anchors, sections


def run_lint(summary_path: str | Path) -> dict:
    """执行 lint 并落盘 _review/lint-report.md，返回结构化结果。"""
    summary_path = Path(summary_path)
    if not summary_path.exists():
        return {"ok": False, "reason": f"summary 不存在: {summary_path}"}
    findings, edges, table_matrix, anchors, sections = lint(summary_path)
    crit = [f for f in findings if f[0] == "CRITICAL"]

    rep = ["# summary_lint 机械检查报告（v2）", "",
           f"> 对象：{summary_path.name} | CRITICAL {len(crit)} / 共 {len(findings)} 条",
           "> 生成：intent-gate lint_summary（L1-L8 + 三矩阵，逻辑冻结）", "",
           "## Findings", ""]
    for lv, rule, detail in findings:
        rep.append(f"- **[{lv}][{rule}]** {detail}")
    if not findings:
        rep.append("（无发现）")

    rep += ["", "## 矩阵① 状态机转移清单", "",
            "| 转移 | 触发事件 | 技术动作 | 时序图对应步骤（复核填） | 决策表规则（复核填） | 问题（复核填） |",
            "|------|---------|---------|----------------------|--------------------|--------------|"]
    for s, t, ev, act in edges:
        rep.append(f"| `{s} → {t}` | {ev} | {act} | 待核 | 待核 | — |")

    rep += ["", "## 矩阵② 表读写矩阵", "",
            "| 表 | 写入动作 | 读取动作 | 权威数据源（复核填） | 问题（复核填） |",
            "|----|---------|---------|--------------------|--------------|"]
    for tbl, writers, readers in table_matrix:
        w = "<br>".join(writers) if writers else "**无**"
        r = "<br>".join(readers) if readers else "无"
        rep.append(f"| `{tbl}` | {w} | {r} | 待核 | — |")

    rep += ["", "## 矩阵③ 引用核对清单", "",
            "| 落点引用 | 核对结果 |", "|---------|---------|"]
    for ref, verdict in anchors:
        rep.append(f"| {ref} | {verdict} |")

    out = summary_path.parent / "_review" / "lint-report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(rep), encoding="utf-8")

    return {
        "ok": True,
        "critical": len(crit),
        "total": len(findings),
        "findings": [{"level": lv, "rule": rule, "detail": d} for lv, rule, d in findings],
        "edges": len(edges),
        "tables": len(table_matrix),
        "anchors": len(anchors),
        "report": str(out),
        "deliverable": len(crit) == 0,
        "note": "CRITICAL 未归零不得交付（playbook Step 4 纪律）",
    }
