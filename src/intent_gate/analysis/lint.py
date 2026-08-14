# -*- coding: utf-8 -*-
"""summary_lint — 需求分析报告机械检查器 v2。

🔴 此文件逻辑冻结，禁止"重写优化"——它是产物格式契约的机械半边，
任何逻辑改动都必须经过完整回归评估。

机械检查项：
  L0 状态机块存在但边解析为零（CRITICAL，守门员失明兜底，绝不静默跳过）
  L1 状态机成功终态（CRITICAL，词表见 L1_SUCCESS_RE——只收完整终态词，
      裸「完成」「结束」不收：「未完成」含「完成」会造成假通过）
  L2 死状态（MAJOR）
  L2b 仅有自环、无对外出边的状态（MAJOR，L2/L3 之间的漏网带：
      outs={st} 使 L2 失明，real=∅ 使 L3 失明——错题集 2026-08-11 收录）
  L3 同状态多出边（MINOR，提示语义复核）
  L4 映射表锚点：章节号存在 + 关键词匹配标题（CRITICAL。
      错题集 2026-08-12：§3.2/§5.1 连写会被吞并漏检——
      关键词捕获组必须排除 / § 括号 顿号，多锚点各自独立校验）
  L5 规则引用 BR-xx 必须有定义（MAJOR）
  L6 表读写矩阵：每张表至少一处写（CRITICAL）/一处读（MINOR。
      关键词双侧小写比较；建表正则反引号可选、大小写不敏感）
  L7 映射表行 vs alignment-log Q 编号覆盖：每个 Q 必须有映射行（MAJOR。
      第一列接受裸数字或 Q 前缀，见 L7_MAP_ROW_RE）
  L8 [🟡待澄清] 降级项必须附人类确认记录（MINOR）
  L9 路径隔离：DDL 禁止内嵌 summary.md，只落项目根 sql/（CRITICAL。
      错题集 2026-08-12：SQL 全写进 summary.md 时 L6 输入源 sql/*.sql 为空而
      静默失明——内嵌 CREATE TABLE 与「登记 sql/ 却无文件」都要抓）
  L10 空矩阵②兜底：存在「数据模型」章节但矩阵②为空（CRITICAL。
      错题集 2026-08-12：§7 写 Markdown 字段表 + 不登记 sql/ 路径即可绕过
      L9——一致性检查之外补存在性强制）
  L11 complex 技术打标强校验：frontmatter 声明 complexity: complex 时，
      状态机每条边 label 必须含 (技术动作)（CRITICAL。错题集 2026-08-12）
  L12 映射表锚点纯散文：数据行无一行含 §/BR- 锚点（MAJOR——
      L4 对散文锚点无从校验，锚点必须可机检）
  L13 图内占位符残留 ???/TBDn（CRITICAL。占位只允许被代码实证或人类拍板消除，
      禁止猜测填空；先于边解析全文扫描 mermaid 块——裸 ??? 边字符集不收会被静默吃掉）
  L13b 草稿图占位仍在但待决清单无在飞题（MINOR，单方向防漏发题）
  L14 入口态失败出边（MAJOR。错题集 2026-08-14 LOADING 案：入口态只画成功边，
      资方匹配失败去向漏画——playbook 失败边四类必查的机械半边；
      图内显式统一兜底注释 → 降 MINOR。门禁逼表态，不逼画边）
  L15 DDL 状态枚举 vs 状态机一致性（MAJOR。同案：routing_status 枚举含 FAILED
      而状态机没画——实现层想到、图里漏画，产物自相矛盾。含别名解析
      state "…" as X / X : 描述 / 散文 X=ENUM；显式声明非生命周期字段 → 降 MINOR；
      无状态机不启用）

矩阵生成（蓝军/人工只填判断列，禁止手建）：
  矩阵① 转移清单  矩阵② 表读写矩阵  矩阵③ 引用核对清单

报告头部附「机械判定契约」（_contract_text() 从常量插值生成，
禁止手抄第二份——防漂移测试见 ContractDisclosureTests）。
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
WRITE_KW = ("INSERT", "UPDATE", "save", "写入", "落库", "DELETE", "保存", "新增")
READ_KW = ("SELECT", "查询", "find", "query", "Query", "读取", "获取")

# ---- 判定契约常量：改动即改判定行为，契约文本自动跟随 ----
L1_SUCCESS_RE = r"FINISH|SUCCESS|DONE|COMPLETE|成功|已完成|已结束|已完结|已通过"
SECTION_RE = r"^#{2,3}\s*(\d+(?:\.\d+)?)[\.、\s]+(.+)$"
L4_ANCHOR_RE = r"§(\d+(?:\.\d+)?)\s*([^\s，。；|/§()（）、]*)"
DDL_TABLE_RE = r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?"
L7_LOG_Q_RE = r"^## Q(\d+)"
L7_MAP_ROW_RE = r"\s*\|\s*Q?(\d+)"
L11_COMPLEX_RE = r"^complexity:\s*complex\s*$"  # frontmatter 自称 complex 才启用打标强校验；simple/medium 不误伤
L12_ANCHOR_RE = r"§|BR-\d+"  # 映射表落点列的可机检锚点形态（§x.y 或 BR-n）；纯散文 L4 无从校验
# L10 豁免通道：无表需求/复用旧表是正当场景，但必须显式声明留痕（蓝军复核），
# 偷偷不写才 CRITICAL——门禁逼的是表态，不是逼建表
L10_EXEMPT_RE = r"无新增表|无表变更|无需建表|复用旧表|复用现有表|沿用旧表|不涉及(新增)?表|无\s*DDL\s*变更"
# 建表语句形态（表名+开括号）才算内嵌 DDL；散文提及「CREATE TABLE 语句…」不误伤
L9_EMBED_DDL_RE = r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?\w+`?\s*\("
L9_SQL_REF_RE = r"\bsql/"              # 登记了 SQL 相对路径（playbook §3.4 要求的格式）；\b 锚定防 sqlite/、mysql/ 误报
# L13 占位符（双层意图对齐·绘图层探测器）：画图卡壳处的标准占位形态是
#   state "???待确认" as TBDn（节点）/ --> TBDn（边）——TBDn 命中 [\w一-鿿]+ 字符集，
#   边解析零噪音；裸 ??? 仅作兼容扫描（字符集不含 ?，裸 ??? 边会被静默吃掉）。
PLACEHOLDER_RE = r"\?\?\?|TBD\d+"
# L14 失败语义词表：入口态出边的 label/目标态/别名命中其一即算「画了失败路径」
L14_FAIL_RE = r"失败|异常|超时|回滚|拒绝|错误|失效|无效|FAILED|TERMINATE|ERROR|INVALID|FAIL"
# L14 豁免通道：确有全局兜底须图内显式注释留痕（蓝军复核），偷偷不画才 MAJOR
L14_EXEMPT_RE = r"统一兜底|全局兜底|失败路径统一|失败统一处理|异常统一兜底"
# L15 状态枚举：列 COMMENT 含「状态」字样才扫（费用类型等非状态枚举不误伤），
# 枚举组形态为斜杠分隔的大写下划线词
L15_ENUM_RE = r"[A-Z][A-Z0-9_]+(?:/[A-Z][A-Z0-9_]+)+"
L15_COLUMN_RE = r"^\s*(\w+)\s+\w+(?:\([^)]*\))?[^\n]*?COMMENT\s*'([^']*)'"
# L15 豁免通道：字段确非生命周期（如路由过程字段）须在报告显式声明 → 降 MINOR
L15_EXEMPT_RE = r"非生命周期|过程字段|不入状态机|非状态机字段|非状态机"


def _state_aliases(blocks, text):
    """状态名别名表：ID → 别名集合（调用侧统一 upper 比较）。

    错题集 2026-08-14：L15 必须把「已完成=FINISHED」这类业务态别名解析出来，
    否则 DDL 枚举 vs 状态机一致性检查对中文业务态集体误报。三条通道：
      1. mermaid 声明 `state "描述" as ID`
      2. mermaid 描述行 `ID : 描述`（stateDiagram 块内、非边行）
      3. 散文显式等式 `ID=ENUM`（如「已完成=FINISHED」，仅收 CJK 起头 ID）
    """
    alias = {}
    decl_re = re.compile(r'state\s+"([^"]+)"\s+as\s+([\w一-鿿]+)')
    desc_re = re.compile(r"^[ \t]*([\w一-鿿]+)[ \t]*:[ \t]*(\S.*)$")
    prose_re = re.compile(r"([一-鿿][\w一-鿿]*)\s*[=＝]\s*([A-Z][A-Z0-9_]{2,})")
    for b in blocks:
        if "stateDiagram" not in b:
            continue
        for m in decl_re.finditer(b):
            alias.setdefault(m.group(2), set()).add(m.group(1))
        for ln in b.split("\n"):
            if "-->" in ln:
                continue
            m = desc_re.match(ln)
            if m:
                alias.setdefault(m.group(1), set()).add(m.group(2).strip())
    for m in prose_re.finditer(text):
        alias.setdefault(m.group(1), set()).add(m.group(2))
    return alias


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

    # ---- L13 占位符残留（绘图层探测器，先于边解析执行）----
    # 🔴 必须先于边解析——占位符可能让边解析静默失明（裸 ??? 不在状态标识符
    # 字符集内，边被吃掉），必须最先报；只扫 mermaid 块，散文中引用 PRD 原文
    # 如「§8 TBD」不算占位，不可误伤。
    for b in blocks:
        hits = sorted(set(re.findall(PLACEHOLDER_RE, b)))
        if hits:
            findings.append(("CRITICAL", "L13",
                             f"图内残留占位符 {', '.join(hits)}——占位只允许被代码实证或"
                             "人类拍板消除，禁止猜测填空后交付（playbook Step 0.5 绘图层纪律）"))

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
        if not any(re.search(L1_SUCCESS_RE, s) for s in states):
            findings.append(("CRITICAL", "L1", f"状态机无成功终态（现有状态：{', '.join(sorted(states))}）"))
        out_map = {}
        for s, t, _, _ in edges:
            out_map.setdefault(s, set()).add(t)
        for st in sorted(states):
            outs = out_map.get(st, set())
            if not outs:
                findings.append(("MAJOR", "L2", f"死状态 {st}：无出边且未流向 [*]"))
            elif outs == {st}:
                # L2b（错题集 2026-08-11）：自环让 outs 非空（L2 失明），
                # real=∅ 又让 L3 失明——永驻态/死循环从两条规则的夹缝漏网
                findings.append(("MAJOR", "L2b",
                                 f"状态 {st} 仅有自环、无对外出边（疑似永驻态/死循环），"
                                 "需人工确认退出路径"))
            real = outs - {st}
            if len(real) > 1:
                findings.append(("MINOR", "L3", f"状态 {st} 有 {len(real)} 条出边（→{', →'.join(sorted(real))}），需人工确认触发条件可区分"))

        # L11（错题集 2026-08-12）：frontmatter 自称 complex 即承诺约束第 3 条——
        # 每条边 label 必须含 (技术动作)；裸边在 complex 场景不许交付
        if re.search(L11_COMPLEX_RE, text, re.M):
            bare = [f"{s} → {t}" for s, t, _, act in edges if not act]
            if bare:
                findings.append(("CRITICAL", "L11",
                                 f"complexity: complex 但 {len(bare)} 条状态机边缺 (技术动作) 打标"
                                 f"（如 {', '.join(bare[:3])}{'…' if len(bare) > 3 else ''}）——"
                                 "每条边必须 触发动作 (技术动作1, 技术动作2)"))

        # L14（错题集 2026-08-14 LOADING 案）：入口态（[*] --> X 的 X）是流程第一张
        # 多米诺，外部依赖失败/超时/非法输入/前置不满足的失败路径最容易在此画漏。
        # 入口态出边集合（不含自环）无一条失败语义边 → MAJOR；图内显式注释统一
        # 兜底 → 降 MINOR（门禁逼表态，不逼画边——确有全局兜底声明即合法）。
        aliases = _state_aliases(blocks, text)
        entries = {t for s, t, _, _ in edges if s == "[*]"}
        for ent in sorted(entries):
            outs = [(t, ev, act) for s, t, ev, act in edges if s == ent and t != ent]
            if not outs:
                continue  # 死入口归 L2 管，不重复报案
            fail_hit = any(
                re.search(L14_FAIL_RE,
                          f"{ev} {act} {t} {' '.join(aliases.get(t, ()))}", re.I)
                for t, ev, act in outs)
            if fail_hit:
                continue
            if any(re.search(L14_EXEMPT_RE, b) for b in blocks if "stateDiagram" in b):
                findings.append(("MINOR", "L14",
                                 f"入口态 {ent} 无失败语义出边，图内已声明统一兜底——"
                                 "请人工确认兜底声明成立（蓝军复核）"))
            else:
                findings.append(("MAJOR", "L14",
                                 f"入口态 {ent} 的出边集合无失败语义边"
                                 f"（词表：{L14_FAIL_RE}）——失败边四类必查："
                                 "外部依赖失败/超时/非法输入/前置不满足；"
                                 "确由全局兜底须在 stateDiagram 块内显式注释"))

    # ---- 章节 ----
    sections = {}
    for ln in lines:
        m = re.match(SECTION_RE, ln)
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
        for m in re.finditer(L4_ANCHOR_RE, ln):
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

    # ---- L12 映射表锚点纯散文（错题集 2026-08-12）----
    # 「决策表 R1 / 时序图 4.3 步骤 2」这类散文锚点让 L4 无从校验——
    # 映射表有数据行就至少要有一行可机检锚点（§x.y 或 BR-n）
    map_data_rows = [ln for ln in map_rows if re.match(L7_MAP_ROW_RE, ln)]
    if map_data_rows and not any(re.search(L12_ANCHOR_RE, ln) for ln in map_data_rows):
        findings.append(("MAJOR", "L12",
                         f"意图注入映射表 {len(map_data_rows)} 个数据行的落点均为散文格式"
                         "（无 §x.y / BR-n 锚点）——L4 锚点校验无从执行，"
                         "落点必须可机检（draft_mapping 脚本定位可免手写）"))

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
        for tm in re.finditer(DDL_TABLE_RE, sql.read_text(encoding="utf-8"), re.I):
            tbl = tm.group(1)
            tbl_lines = [ln.strip() for ln in text.split("\n") if tbl in ln]
            # 双侧小写比较：SQL 关键词大小写混写（insert/Insert）不许漏判
            writers = [ln[:60] for ln in tbl_lines
                       if any(w.lower() in ln.lower() for w in WRITE_KW)]
            readers = [ln[:60] for ln in tbl_lines
                       if any(r.lower() in ln.lower() for r in READ_KW)]
            table_matrix.append((tbl, writers, readers))
            if not writers:
                findings.append(("CRITICAL", "L6", f"表 {tbl} 全报告无任何写入动作（图/文均无 INSERT/UPDATE/save）"))
            if not readers:
                findings.append(("MINOR", "L6", f"表 {tbl} 全报告无读取动作"))

    # ---- L10 空矩阵②兜底（错题集 2026-08-12）----
    # playbook Step 2：数据模型强制输出 sql/{表名}.sql。声明了「数据模型」章节
    # 却没有任何 sql/*.sql 建表（矩阵②为空）= 存在性强制，与 L9 一致性检查互补。
    # 豁免通道：无表需求/复用旧表须显式声明（L10_EXEMPT_RE）——声明降级 MINOR
    # 留蓝军复核真伪；偷偷不写才 CRITICAL。
    if any("数据模型" in title for title in sections.values()) and not table_matrix:
        exempt = re.search(L10_EXEMPT_RE, text)
        if exempt:
            findings.append(("MINOR", "L10",
                             f"表读写矩阵为空，报告以「{exempt.group(0)}」豁免 DDL 产出——"
                             "请人工确认豁免成立（确实无表变更/复用旧表）"))
        else:
            findings.append(("CRITICAL", "L10",
                             "报告含「数据模型」章节但表读写矩阵为空——未在 sql/ 产出任何 "
                             "CREATE TABLE DDL，且未声明无表变更/复用旧表"
                             "（playbook §2/§3.4：DDL 草案强制输出 sql/{表名}.sql；"
                             "确无表需求须在数据模型章节显式声明）"))

    # ---- L7 映射行 vs 日志 Q 覆盖 ----
    log_path = summary_path.parent / "_review" / "alignment-log.md"
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8")
        q_nums = set(re.findall(L7_LOG_Q_RE, log_text, re.M))
        map_nums = set()
        for ln in map_rows:
            m = re.match(L7_MAP_ROW_RE, ln)
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

    # ---- L13b 草稿占位无在飞题（MINOR，单方向）----
    # 防"有断层却没发题"：草稿图内仍有占位但待决清单无在飞题，疑似猜测填空或
    # 漏发题。只扫草稿的 mermaid 块——draft 断层清单的散文引用（如
    # 「✏️绘图层：TBD2」）不算占位，不可误伤。
    draft_path = summary_path.parent / "_review" / "analysis-draft.md"
    if draft_path.exists():
        dtext = draft_path.read_text(encoding="utf-8")
        dblocks = re.findall(r"```mermaid(.*?)```", dtext, re.S)
        if any(re.search(PLACEHOLDER_RE, b) for b in dblocks):
            pending_path = summary_path.parent / "_review" / "pending-questions.md"
            unchecked = ([ln for ln in pending_path.read_text(encoding="utf-8").split("\n")
                          if ln.strip().startswith("- [ ]")] if pending_path.exists() else [])
            if not unchecked:
                findings.append(("MINOR", "L13b",
                                 "analysis-draft 的图内仍有占位符，但待决清单无在飞题——"
                                 "疑似猜测填空或漏发题；若占位已消除请同步更新草稿"))

    # ---- L9 路径隔离：DDL 只落项目根 sql/，禁止内嵌 summary.md ----
    # 错题集 2026-08-12：SQL 全写进 summary.md 时 L6 的输入源（sql/*.sql）为空 →
    # L6 静默失明。机械半边补在这里：内嵌 CREATE TABLE 与「登记了 sql/ 路径但
    # 目录无 SQL 文件」都报 CRITICAL（sql_dir 复用 L6 的定位结果）。
    if re.search(L9_EMBED_DDL_RE, text, re.I):
        findings.append(("CRITICAL", "L9",
                         "summary.md 内嵌 CREATE TABLE——DDL 禁止内嵌报告，"
                         "迁至项目根 sql/{表名}.sql（playbook §3.4/Step 4 路径隔离）"))
    if re.search(L9_SQL_REF_RE, text) and (not sql_dir.exists()
                                           or not list(sql_dir.glob("*.sql"))):
        findings.append(("CRITICAL", "L9",
                         "报告登记了 SQL 相对路径（sql/）但 sql/ 目录无 SQL 文件——"
                         "DDL 未按 playbook §2/§3.4 落到项目根 sql/（定位目录："
                         f"{sql_dir}）"))

    # ---- L15 DDL 状态枚举 vs 状态机一致性（MAJOR，错题集 2026-08-14）----
    # routing_status 案：DDL 注释写了 PROCESSING/SUCCESS/FAILED，状态机却没画
    # FAILED——实现层想到、图里漏画，产物自相矛盾。枚举值必须在状态机状态集
    # （含 _state_aliases 别名：state "..." as X / X : 描述 / 散文 X=ENUM）中出现。
    # 豁免：报告显式声明该字段非生命周期（L15_EXEMPT_RE）→ 降 MINOR。
    # 无状态机不启用（没图谈何一致）；注释无「状态」字样的列不扫（不误伤普通枚举）。
    if edges:
        known = {s.upper() for s in states}
        for toks in aliases.values():
            known |= {t.upper() for t in toks}
        for sql in sorted(sql_dir.glob("*.sql")) if sql_dir.exists() else []:
            for ln in sql.read_text(encoding="utf-8").split("\n"):
                cm = re.match(L15_COLUMN_RE, ln, re.I)
                if not cm or "状态" not in cm.group(2):
                    continue
                field = cm.group(1)
                for grp in re.findall(L15_ENUM_RE, cm.group(2)):
                    missing = [v for v in grp.split("/") if v.upper() not in known]
                    if not missing:
                        continue
                    exempt = re.search(
                        rf"{re.escape(field)}[^\n]{{0,40}}(?:{L15_EXEMPT_RE})", text)
                    if exempt:
                        findings.append(("MINOR", "L15",
                                         f"{sql.name} 字段 {field} 枚举 {', '.join(missing)} "
                                         "不在状态机中，报告已声明非生命周期字段——"
                                         "请人工确认声明成立（蓝军复核）"))
                    else:
                        findings.append(("MAJOR", "L15",
                                         f"{sql.name} 字段 {field} 枚举值 "
                                         f"{', '.join(missing)} 在状态机中无对应状态/别名"
                                         "——实现层想到、图里漏画，图与 DDL 必须一致；"
                                         "确为非生命周期字段须在报告显式声明"))

    return findings, edges, table_matrix, anchors, sections


def _contract_text() -> list[str]:
    """机械判定契约——全部从常量插值生成，禁止手抄第二份。

    披露即契约：红军/蓝军照此写作即可一次过检，无需试错反推。
    """
    return [
        "## 机械判定契约（从 lint.py 常量插值生成，随逻辑自动更新）", "",
        f"- **L1 成功终态词表**：`{L1_SUCCESS_RE}`（状态名命中其一即算成功终态；"
        "裸「完成」「结束」刻意不收。业务终态用 mermaid 描述语法复合命名："
        "`已完结 : 放款成功` 或 `state \"已完结（放款成功）\" as 已完结`——"
        "🔴 禁止把带括号的复合名直接当状态 ID 写进边（如 `已完结（放款成功） --> [*]`），"
        "括号不在状态标识符字符集内，该边会被静默吃掉，触发 L2/L0 误报）",
        f"- **章节标题识别**：`{SECTION_RE}`（##/### 开头 + 数字编号）",
        f"- **L4 锚点解析**：`{L4_ANCHOR_RE}`（多落点连写请用 / 等分隔，"
        "各锚点独立校验；关键词命中下列词时校验标题匹配）",
        f"  - 关键词→标题应含：{'; '.join(f'{k}→{v}' for k, v in KW_MAP.items())}",
        f"- **L6 写入词表**：`{WRITE_KW}`；**读取词表**：`{READ_KW}`"
        "（双侧小写比较，大小写混写不影响判定）",
        f"- **L6 建表识别**：``{DDL_TABLE_RE}``（大小写不敏感，反引号可选）",
        "- **L6 SQL 目录定位**：summary 上溯四级找 sql/，不存在则退化为 summary 同级 sql/",
        f"- **L7 alignment-log 题号**：`{L7_LOG_Q_RE}`（## Q 开头的二级标题）",
        f"- **L7 映射表行识别**：`{L7_MAP_ROW_RE}`（表格首列为裸数字或 Q+数字）",
        f"- **L9 DDL 内嵌识别**：``{L9_EMBED_DDL_RE}``（建表语句形态才算内嵌——"
        "散文提及 CREATE TABLE 字样不误伤；DDL 只落项目根 sql/，禁止内嵌报告）",
        f"- **L9 SQL 登记检查**：出现 `{L9_SQL_REF_RE}`（相对路径登记）时，sql/ 目录必须"
        "存在且含 .sql 文件，否则 CRITICAL（目录定位同 L6）",
        "- **L10 空矩阵②兜底**：存在「数据模型」章节但表读写矩阵为空 → CRITICAL"
        "（存在性强制：DDL 草案必须落 sql/，与 L9 一致性检查互补）。"
        f"豁免：命中 `{L10_EXEMPT_RE}`（显式声明无表变更/复用旧表）→ 降 MINOR，蓝军复核",
        f"- **L11 complex 打标强校验**：frontmatter 命中 `{L11_COMPLEX_RE}` 时，"
        "状态机每条边 label 必须含 (技术动作)，裸边 CRITICAL；simple/medium 不启用",
        f"- **L12 锚点纯散文**：映射表数据行无一行含 `{L12_ANCHOR_RE}` 锚点 → MAJOR"
        "（落点必须可机检，散文锚点 L4 无从校验）",
        f"- **L13 占位符识别**：mermaid 块内 `{PLACEHOLDER_RE}`（标准形态 "
        "`state \"???待确认\" as TBDn` / `--> TBDn`；残留即 CRITICAL——"
        "占位只允许被代码实证或人类拍板消除，禁止猜测填空）",
        "- **L13b 草稿占位无在飞题**：草稿图内有占位但待决清单无 `- [ ]` 题 → MINOR"
        "（单方向，防\"有断层却没发题\"；散文中的 TBD 引用不算占位）",
        f"- **L14 入口态失败出边**：`[*] --> X` 的入口态 X，出边集合（不含自环）"
        f"无一条失败语义边（label/目标态/别名命中 `{L14_FAIL_RE}`）→ MAJOR。"
        f"豁免：stateDiagram 块内显式注释 `{L14_EXEMPT_RE}` → 降 MINOR，蓝军复核",
        "- **L15 DDL 状态枚举 vs 状态机**：sql/*.sql 列 COMMENT 含「状态」的枚举组"
        f"（``{L15_ENUM_RE}``），枚举值须在状态机状态集或别名"
        "（`state \"...\" as X` / `X : 描述` / 散文 `X=ENUM`）中出现 → MAJOR。"
        f"豁免：报告显式声明该字段 `{L15_EXEMPT_RE}` → 降 MINOR；无状态机不启用",
        "- **L5 规则定义行**：strip 后以 `| BR` 开头的表格行视为 BR-xx 定义", ""]


def run_lint(summary_path: str | Path) -> dict:
    """执行 lint 并落盘 _review/lint-report.md，返回结构化结果。"""
    summary_path = Path(summary_path)
    if not summary_path.exists():
        return {"ok": False, "reason": f"summary 不存在: {summary_path}"}
    findings, edges, table_matrix, anchors, sections = lint(summary_path)
    crit = [f for f in findings if f[0] == "CRITICAL"]

    rep = ["# summary_lint 机械检查报告（v2）", "",
           f"> 对象：{summary_path.name} | CRITICAL {len(crit)} / 共 {len(findings)} 条",
           "> 生成：intent-gate lint_summary（L1-L15 + 三矩阵，逻辑冻结）", ""]
    rep += _contract_text()
    rep += ["## Findings", ""]
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
