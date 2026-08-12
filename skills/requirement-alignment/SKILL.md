---
name: requirement-alignment
description: Use when analyzing a requirement/PRD (分析需求/解析需求/解析PRD/分析PRD), building state machines (画状态机), sequence diagrams (时序图), decision tables (决策表) or DDL from a spec, or resuming an interrupted requirement analysis (用户说"继续") - drives the three-level intent-alignment funnel (code evidence, registered inference, structured escalation) through the intent-gate MCP tools. NOT for writing implementation code (实现需求/编码/写代码): coding against a landed contract belongs to the contract-coding skill; a contract rejected by the coding gate (status/lint) goes back to the HUMAN for a ruling, never auto-resumes alignment
---

# Requirement Intent Alignment

## Overview

Ambiguity in a requirement is resolved through a funnel that gets cheaper per level,
never by silent assumption. The MCP tools are the enforcement: they reject malformed
questions, refuse to settle answers without a landing point, and gate delivery on
mechanical lint.

**The full law is the MCP prompt `doc_analysis_playbook` — read it in full before
starting Step 0. This skill is the map; the playbook is the territory.**

**Dual-layer detection — the diagram is the instrument, not a deliverable.**
Layer 1 (reading): sweep the PRD text for explicit ambiguities (the nine-category
checklist). Layer 2 (drawing): do NOT wait for answers before drawing — hard-draw
draft diagrams in the FIRST turn; every node/edge you cannot draw becomes a `TBDn`
placeholder (`state "???待确认" as TBDn` / `--> TBDn`; never bare `--> ???`, lint's
state-id charset eats it). Each placeholder is a gap: 🔧 technical → code evidence
first, 📋 business → dispatch_question. Placeholders are cleared ONLY by code
evidence or a human ruling — never by guessing. The first resolve_question is
mechanically gated on the draft existing (analysis-draft.md with a mermaid block).

## The Funnel

Resolve each gap at the cheapest level that can close it:

1. **Code evidence (🔧 technical gaps first)** — search the codebase
   (codegraph `search_graph` / `trace_path`, or grep). If the code has a single
   ground truth, inject it and settle with `resolve_question(source="code")`.
   Zero human cost.
2. **Registered inference** — no direct ground truth but a strong analogy?
   `record_inference` with an explicit evidence chain, batch-confirm with the
   human at session end via `confirm_inferences`. 🔴 Forbidden on core
   money flows and red-line rules — those need a human decision.
3. **Group escalation (optional, sister intent-gate-service MCP)** — judgment calls whose
   rightful answerer sits in a DingTalk group: intent-gate-service's `group_dispatch`
   with category 📋 business / 🔧 technical, @ the right role. One question in
   flight at a time. Core-flow questions carry your inference as recommended
   option 1, so the human nods instead of drafting. Skip this level when
   intent-gate-service is not mounted.
4. **Dialog fallback (default)** — the host asks the user directly,
   3+1 options per question.

## Question Format (strictly enforced)

```
🟡 意图对齐：{specific gap, citing the position on the diagram}

  1. {option 1: concrete state/action/result}
  2. {option 2}
  3. {option 3}
  4. 其他（请输入）
```

- At least 3 mutually exclusive options + "other" — the tool mechanically rejects fewer.
- One ambiguity per question — never batch.
- Options must be concrete (state values, actions, results); "handle normally" is banned.

## The Loop (turn-based, non-blocking)

```
record_judgment (your formal verdict: patterns / complexity / light / gaps)
  → dispatch_question per gap (persist BEFORE sending)
  → end turn freely — nothing blocks on humans
  → on resume: rebroadcast_pending → collect_answers
  → inject each answer into the diagrams, resolve_question to settle
     (every settled answer appends a standard alignment-log entry)
  → re-assess confidence → 🟢 proceed to artifact generation; else loop
```

## Hard Gates

- 🔴 🔴 lights unresolved → report `status: blocked`, first task is `[BLOCKER]`.
- 🔴 Every injected intent needs a precise landing point (diagram edge / step /
  rule id / field). Anchors come from `draft_mapping` (script-located), never
  hand-written. No landing point → ask the human again on the spot.
- 🔴 Downgrading an item to `[🟡待澄清]` requires explicit human confirmation,
  recorded per item. Self-downgraded = alignment incomplete.
- 🔴 Before delivery: `lint_summary` must report zero CRITICAL.
- 🔴 New-coined terminology must be confirmed via a dispatched question, with or
  without a terminology wiki — "no baseline" is not an exemption.

## After Delivery: Optional Red-Blue Review（可选项）

When a **complex** requirement's summary.md is delivered (`status: pending_review`),
offer the human the optional adversarial review: skill `red-blue-review` runs a
blue army in an INDEPENDENT session against the artifacts (R1-R9), and its PASS
is one of the two legitimate paths to `status: approved` (the other is a direct
human decision). The red army files the review request
(`_review/review-request.md`, template in that skill) when the human opts in.
Never auto-start it, and never review your own session's work.

## Working Files

All state lives under `.harness/requests/{feature}/_review/`:
`pending-questions.md` (checklist), `alignment-log.md` (settled answers, verbatim
human wording), `inference-pending.md`, `inbox/`. Resume works from files alone —
never rely on session memory.
