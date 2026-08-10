---
name: using-intent-gate
description: Use when starting any conversation in a project that has the intent-gate MCP - establishes requirement intent-alignment discipline (never guess on ambiguity), where the analysis playbook lives, and the two optional add-ons (red-blue adversarial review skill, intent-gate-service sister service for DingTalk group consensus)
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, ignore this skill.
Escalation to humans only works from the main conversation.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
If there is even a 1% chance a decision belongs to a human — irreversible operations,
production data, business-rule ambiguity, conflicting requirements — you MUST escalate
through intent-gate instead of guessing.

A "reasonable technical assumption" is NOT intent alignment. Silent assumptions on
core flows are the #1 source of rework this gate exists to prevent.
</EXTREMELY-IMPORTANT>

## The Rule

**Before acting on anything uncertain, irreversible, or business-critical, check the gate:**

1. **Requirement analysis tasks** → FIRST read the MCP prompt `doc_analysis_playbook`
   in full. It is the law: confidence assessment (Step 0), intent alignment loop
   (Step 0.5), artifact gates (Step 4). Do not analyze from memory.
2. **Confidence red/yellow on a decision** → use the intent-gate tools instead of
   assuming. Red light on a core flow means the work is `blocked` until a human answers.
3. **Everything is non-blocking** — dispatch the question, persist it, continue other
   work or end the turn. Answers are reconciled on session resume.

## Optional Add-ons（可选项，不用不启动）

- **红蓝对抗评审（skill `red-blue-review`）** — after a complex requirement's
  summary.md is delivered, the human may opt into an adversarial review: a blue army
  in an INDEPENDENT session audits the artifacts against R1-R9 and writes findings.
  PASS is one of only two legitimate paths to `status: approved` (the other is a
  direct human decision). Never run it uninvited; never run it in the red army's session.
- **钉钉群共识通道（姊妹篇 intent-gate-service MCP 服务）** — intent questions whose rightful
  answerer sits in a DingTalk group (📋 business / 🔧 technical) can be dispatched
  there via intent-gate-service's `group_dispatch` tool; blocking human-decision gates
  (`ask_human`) also live there. If the intent-gate-service MCP is not mounted, this project
  simply uses the dialog fallback — do not treat its absence as an error.

## Red Flags

These thoughts mean STOP — you're rationalizing your way around the gate:

| Thought | Reality |
|---------|---------|
| "The answer is obvious from context" | Obvious to you ≠ decided by a human. Escalate or use code evidence. |
| "Asking slows things down" | Dispatch is non-blocking. Guessing wrong on a core flow costs days. |
| "I'll note the assumption and move on" | Silent assumptions are forbidden; inferences must be registered via `record_inference` with an explicit evidence chain. |
| "This is a small change, no gate needed" | Small irreversible changes are exactly what gates exist for. |
| "The tests pass, so the intent is right" | Tests verify your interpretation, not the human's intent. |
| "I'll ask everything in one big batch" | One question at a time. Batching = diffusion of responsibility = no answers. |
| "No one answered, so I'll decide myself" | No timeout means no answer = not urgent. Re-broadcast on resume; never self-decide a 🔴. |
| "I'll self-review my own analysis" | Self-review is not red-blue. The blue army needs an independent session and information asymmetry — invite the human to opt in. |

## Discipline Summary

- 🔴 Core-flow ambiguity: escalate, never infer. Inference on fund/money paths or
  red-line rules is forbidden — a human must decide (nod-confirm is fine).
- 🔴 Every human answer must be written to the alignment log with the original
  wording, verbatim. Interpretation goes in a separate field.
- 🔴 Files are the source of truth (`.harness/requests/{feature}/_review/`);
  chat channels are only transport. Process restarts lose nothing.
- 🔴 `approved` status is never self-granted. Readiness comes from the checklist,
  not from your confidence.

## Coding-Phase Contract Discipline

Before writing code in a project, check for landed analysis artifacts:

- **If `.harness/requests/*/summary.md` exists** → the mermaid diagrams and
  decision tables in it are the implementation contract. Before coding against
  a requirement, you MUST read its `summary.md` (state machine / sequence /
  decision table / DDL) and its `_review/lint-report.md`:
  - lint CRITICAL > 0 → the contract itself carries mechanical errors. Do NOT
    code against it; report back and get the contract fixed first.
  - `status: blocked` or `intent_aligned: false` in the frontmatter → the
    requirement is not aligned; coding it is forbidden until it clears.
- **If no `.harness/requests/` directory exists** → this project doesn't use
  the contract workflow. This means ONLY that the contract-reading step is
  skipped. 🔴 It must NEVER interrupt, pause, block, or escalate the coding
  task, and NEVER prompt the user about it — proceed with the task exactly as
  if this section did not exist.
- Mermaid files that drift from code are bugs. If you must deviate from the
  diagram while coding, stop and escalate — the contract gets amended first,
  then the code follows.

## User Instructions

User instructions (CLAUDE.md, AGENTS.md, direct requests) take precedence over this
skill. Only skip gate workflows when your human partner has explicitly told you to.
