# intent-gate

**English** | [简体中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python >=3.11](https://img.shields.io/badge/python-%3E%3D3.11-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/protocol-MCP-green)](https://modelcontextprotocol.io/)

**An intent-alignment engine for coding agents (lightweight plugin).** It dissolves
requirement-intent gaps *before* coding starts: a PRD goes through an intent-confidence
assessment → gaps are resolved through a three-level funnel → the output is technically
annotated Mermaid contracts (state machines / sequence diagrams / decision tables) →
a mechanical lint gate must reach zero → only then is coding allowed to begin.

It runs as an **MCP server (stdio subprocess)** — plug-and-play with Claude Code,
Pi agent, and other MCP clients. **No daemon, no long-lived connections, no external
credentials** — pure stdio, zero extra dependencies, full workflow out of the box.

> Since v0.2.0, all DingTalk interaction (group consensus channel + blocking decision
> gates) has been split into the sister project
> [**intent-gate-service**](https://github.com/baixinghao/intent-gate-service)
> (a standalone MCP service). intent-gate is back to being lightweight: it keeps only
> intent alignment and requirement analysis, and touches no DingTalk at all.

## Why it exists

A major source of code hallucination is not model capability — it is **intent gaps
getting masked by attention and papered over by guessing during coding**. While coding,
the agent's attention is on code generation; gaps in the PRD like "only a happy path,
no failure flow" or "idempotency with no server-side design" get silently filled in
with a plausible-looking guess.

The host's built-in intent prompts (e.g. AskUserQuestion) don't solve this: the
judgment is ad hoc and the answers are ephemeral — one context compaction or session
end, and the alignment state is gone.

intent-gate's answer is **judgment stays with the host, discipline is enforced by code**:

- Semantic judgment (what kind of gap is this, is it a gap at all) is done at full
  strength by the host agent — the MCP never second-guesses it;
- Discipline (no silently dropping gaps, no closing an answer without a landing point,
  no delivery with mechanical errors) is mechanically enforced by MCP tools.
  **Files are the single source of truth**; the process lives and dies with the session
  without losing state, and is naturally resilient to context compaction.

## Where it sits in a vibe-coding workflow

intent-gate is **application-layer harness engineering** — it owns exactly one stage
of the pipeline, the requirement-analysis stage:

```
PRD ──▶ [ intent-gate: confidence gate → intent alignment → Mermaid contracts ]
          ──▶ summary.md (every edge technically annotated, lint CRITICAL = 0)
          ──▶ coding agent (Claude Code / Cursor / any agent) ──▶ tests ──▶ ship
```

The leverage is asymmetric: **if the contracts land well, the coding stage is a free
win** — every edge, step and rule already has a home, so any competent agent can
implement the spec. That is why intent-gate deliberately does NOT touch coding,
review or deployment: downstream agents are interchangeable; the input contract is not.

## Where intent confidence comes from (the epistemological foundation)

**Intent confidence is not the model's self-assessment — it is the closure state of
artifacts.**

Asking a model to rate "how sure are you" is a dead channel: verbal confidence barely
correlates with actual correctness (it's post-hoc rationalization, not a reading);
token-level logprobs can't reach semantic-layer uncertainty ("should there be an
intermediate state after a refund?") and the API doesn't expose them anyway. So this
system never asks the model for a score — **it makes the model produce, and reads the
confidence off the artifacts**.

Drawing diagrams (state machines / sequence diagrams / decision tables) is a
**measuring instrument, not a means of expression**: what natural language can fudge,
formalization cannot — "after the refund is processed, it's done" is one sentence, but
in a state machine you must answer whether REFUNDING has an outgoing edge, where it
points, and on what trigger. Every edge is a forced discrete decision; vague intent is
invisible in prose but a hole on an edge.

Gaps come in four kinds, each with its own detector — "can't draw it" is only layer one:

| Layer | Mechanism | What it catches |
|---|---|---|
| ① Forced formalization | Draw the diagram; mark wherever you can't | **Perceived gaps** — the model knows it doesn't know |
| ② Taxonomy sweep | A nine-category ambiguity checklist (exception paths / rollback / condition combinations / field semantics / idempotency & privilege / terminology…) | **Semi-silent gaps** — the model won't stall on its own, but sweeping element-by-element with the checklist exposes them |
| ③ Mechanical lint | L1 no successful terminal state / L2 dead states / L6 table has no writes… | **Fully silent gaps** — places the model filled in without any awareness; enforced by code, zero reliance on self-discipline |
| ④ Blue-team independent review | Independent session + information diet (optional skill) | **Systematic blind spots of the author's attention** — layers ①–③ are the same pair of eyes; this one swaps in a fresh pair |

So a 🟢 green light doesn't mean "the model feels confident" — it means "every edge of
the state machine is grounded, all nine minefields swept, lint CRITICALs at zero, and a
human has ruled on every gap." **Confidence is a property of the graph, not of the
model.** Drawing is the instrument, lint is the calibrator, human rulings are the
reference source.

## Core mechanisms

**Intent-confidence gate (Step 0)**: assess the light status before analyzing any
requirement. Until 🔴 core-logic gaps are eliminated, the report `status` must be
`blocked`, the first task in any breakdown is forced to be `[BLOCKER]`, and downstream
coding is forbidden from starting.

**Three-level alignment funnel (Step 0.5)** — cost decreases level by level, fully
non-blocking throughout:

```
① Code-grounded verification: technical gaps are checked against the code first;
  a unique ground truth is recorded directly, zero interpersonal cost
② AI-disclosed inference: registered with an explicit evidence chain, batch-confirmed
  by a human at session end (pure inference is forbidden on money-critical main flows)
③ Human ruling: structured-option questions (≥3 mutually exclusive options + "other"),
  one question at a time
  (with the sister project intent-gate-service installed, this level can be sent to a
  DingTalk group @ the corresponding role)
```

**Mechanical enforcement (enforced by MCP tools, not by prompt self-discipline)**:

- Once a gap is registered it is physically persisted to `pending-questions.md`;
  until every box is ticked you don't get `intent_aligned_ready`;
- `resolve_question` mechanically rejects an empty landing point — every injected
  intent must be precise down to a state-machine edge, a sequence-diagram step, or a
  decision-table rule number; if no landing point is found, **closing the question is
  forbidden and it must be asked again**;
- Landing-point anchors may not be handwritten — the `draft_mapping` script locates
  real section/rule/step numbers;
- Pre-delivery `lint_summary` mechanical self-check (L0–L8: unparseable state machine
  (blind-guard) / missing terminal state / dead states / misplaced anchors / BR
  references / table read-write matrix…), **CRITICALs must reach zero before delivery**.

**Mermaid as the coding contract**: every edge of a state diagram carries mandatory
technical-action annotations
(`DRAFT --> PENDING: Submit (IF validation, DB_INSERT, REDIS_ZSET)`), sequence diagrams
use `autonumber` + passed-variable annotations, and rule logic is forced into
decision-table matrices. What the coding agent receives is not an illustration but a
spec where every edge maps to explicit implementation actions — the guessing space is
structurally compressed.

## Skill trigger map (after installation, which scenario auto-uses which)

The plugin's three skills each have a clear trigger surface, working together with the
entry discipline injected at SessionStart:

| Skill | When it triggers | What it does |
|---|---|---|
| `using-intent-gate` | **Auto-injected at the start of every session** (SessionStart hook) | Entry discipline: read the playbook before any analysis, when escalation to a human is mandatory, where the two optional capabilities (red-blue / DingTalk) live; read the landed summary contract before coding |
| `requirement-alignment` | You ask the agent to **analyze a requirement/PRD, draw state machines / sequence diagrams / decision tables, or generate DDL**, or you say "continue" after an interruption | The three-level alignment funnel outline: code-grounded verification → AI-disclosed inference → structured questioning; non-blocking throughout, answers reconciled across sessions |
| `red-blue-review` (optional) | You **explicitly say** "red-blue / blue-team review / requirement review", or you nod after a complex delivery | Blue-team adversarial review: independent session, information diet, R1–R9 nine checks produce findings and drive a gated rectification loop; `approved` comes only from a blue-team PASS or a direct human ruling |

Companion MCP tools (called by the agent automatically, no action needed from you):
`doc_analysis_playbook` (prompt) / `analyze_requirement` / `record_judgment` /
`lint_summary` / `draft_mapping` / `dispatch_question` / `collect_answers` /
`resolve_question` / `record_inference` / `confirm_inferences` /
`rebroadcast_pending` / `list_pending_questions` / `abandon_*`.

> The only operational surface you need to remember: **when analyzing a requirement,
> have it read the playbook first; answer its questions carefully; after a complex
> delivery, say "red-blue" if you want an adversarial review.**

## Optional: red-blue adversarial review (plugin skill)

After the red team (requirement analysis) delivers a complex artifact, you can
optionally start a **blue-team adversarial review**: an independent session with
information asymmetry (the blue team reads only the artifacts, not the red team's
reasoning), running the R1–R9 nine checks (injection fidelity / degradation
compliance / missed ambiguities / cross-diagram consistency…) to produce findings
that drive a disciplined rectification loop; `status` turns `approved` via exactly
two paths — a blue-team PASS or a direct human ruling. The red team never self-grants.

- Trigger: a human calls it by name ("red-blue / blue-team review"), or a human nods
  after a complex delivery; simple/medium passes straight through.
- Form: a pure plugin skill (`skills/red-blue-review/`), **kept out of the MCP tool
  surface** — blue-team validity depends on the information diet; reviewing in the
  same process would degrade adversarial review into self-checking.
- Circuit breaker: at most 2 rounds; still FAIL → marked `ESCALATE` for human ruling;
  infinite polishing is forbidden.
- Red-team rectification is gated too (skill §5.5): every finding is settled in
  `_review/revision-log.md` with a real landing point; lint re-runs to zero CRITICAL
  before round 2; new terms coined by the fix itself must be confirmed via a dispatched
  question; and when a finding conflicts with an already-injected human decision, the
  red team must table the conflict for a human ruling — never pick one silently.

## Sister project: intent-gate-service (DingTalk group consensus channel + decision gates)

The bottleneck of intent alignment is not "how to ask" but **who to ask** — business
gaps belong to business people, technical gaps to technical people. Under the default
`single` channel, gaps are answered one by one by the person in front of the chat box,
never touching DingTalk. With
[intent-gate-service](https://github.com/baixinghao/intent-gate-service)
(a standalone MCP service) installed, funnel level ③ can post to a DingTalk group
@ the corresponding role; replies land in the inbox via callback and are picked up by
intent-gate's `collect_answers`.

The real value of the group channel is **the paper trail**: answers in the group carry
a staffId, carry the original wording, and are publicly visible — whoever made the call
is on record, and **no objection in the group ≈ consensus**. DingTalk is only the
transport layer; the file ledger does not depend on it to survive.

Blocking emergency human escalation during coding (the `ask_human` decision gate)
lives in intent-gate-service too — **all heavy interaction that blocks waiting for a
human reply is in the sister project**; the main plugin is always non-blocking.

## Quick start

```bash
# One-line install (no clone needed, isolated environment)
pipx install git+https://github.com/baixinghao/intent-gate.git
# or: uv tool install git+https://github.com/baixinghao/intent-gate.git

# From a clone (development)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests -v   # core-logic tests (no credentials needed)
intent-gate                      # stdio MCP, waiting to be launched by your agent
```

**All intent-alignment capabilities work with zero configuration** (single channel,
chat dialog as fallback). For the DingTalk group channel, see the
[intent-gate-service README](https://github.com/baixinghao/intent-gate-service).

## MCP integration

### Claude Code (`.mcp.json`)

```json
{
  "mcpServers": {
    "intent-gate": {
      "command": "intent-gate"
    }
  }
}
```

When developing inside the repo, point `command` at the virtualenv interpreter:
`"command": "<repo>\\.venv\\Scripts\\python.exe", "args": ["-m", "intent_gate"]`
(on macOS/Linux: `<repo>/.venv/bin/python`), and set
`"env": { "PYTHONPATH": "<repo>\\src" }`.

### Pi agent and other MCP clients

Launch the same executable over stdio. If your client speaks a network transport,
expose MCP over **SSE** with `intent-gate --mcp-transport sse --mcp-port 8400`.

### As a Claude Code plugin

The repo is also a Claude Code plugin (`.claude-plugin/` + `hooks/` + `skills/`):
installing it auto-registers the MCP server and injects usage discipline via a
SessionStart hook (read the playbook before intent alignment, when escalation to a
human is mandatory, where the two optional capabilities live).
See [docs/PLUGIN.md](docs/PLUGIN.md) for the skeleton.

## Project structure

```
src/intent_gate/
├── config.py / logging.py        # config (HG_* env vars, zero credentials), logging
├── models.py / security.py       # pure-stdlib core: tokens, allowlist, reply parsing, rate limiting
├── __main__.py                   # MCP entrypoint (stdio/SSE)
├── alignment/                    # intent-alignment subsystem (file-in-the-loop, non-blocking)
│   ├── store.py                  #   persistence: pending list / alignment log / inference list / inbox
│   ├── manager.py                #   business layer + contract functions (register_question /
│   │                             #   file_inbound_reply, reused by sister project intent-gate-service)
│   └── tools.py                  #   MCP tool registration (9 intent-alignment tools)
├── analysis/                     # requirement-analysis subsystem
│   ├── playbook.md               #   requirement-analysis playbook (distributed in full via MCP prompt)
│   ├── engine.py                 #   gap adjudication / host-judgment bookkeeping
│   ├── lint.py                   #   mechanical checker for analysis reports (L0-L8 + three matrices)
│   ├── mapper.py                 #   anchor locating for the intent-injection mapping table
│   └── tools.py                  #   MCP tool registration (analysis tools + playbook prompt)
skills/
├── using-intent-gate/            # entry discipline: when to escalate, where the optional capabilities live
├── requirement-alignment/        # intent-alignment workflow outline → points to the MCP prompt
└── red-blue-review/              # optional: red-blue adversarial review playbook (blue-team nine checks + red-team rectification discipline)
(sister repo) intent-gate-service # DingTalk group channel + decision gates (standalone MCP service)
```

## Documentation

- [docs/STRUCTURE.md](docs/STRUCTURE.md) — **structure & usage guide (file-by-file across both repos; read this first)**
- [docs/DESIGN.md](docs/DESIGN.md) — intent-alignment subsystem design (three-level funnel, file contract, sister-project split)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture decisions (layering, long-connection trade-offs, failure posture)
- [intent-gate-service README](https://github.com/baixinghao/intent-gate-service) — sister-project DingTalk configuration and integration

## Roadmap

- [ ] Coding-start gate: `claim_task` validates `approved` + injects contract slices (the last mile of intent alignment)
- [ ] intent-gate-service: interactive cards + button callbacks (no text parsing needed)
- [ ] intent-gate-service: gate audit persistence (SQLite) and replay
- [ ] intent-gate-service: gateway mode for multi-agent instances (stream inbound converged into a single connection)

## License

[MIT](LICENSE)
