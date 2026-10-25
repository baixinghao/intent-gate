Intent-gate routing (use only when relevant; casual Q&A needs no intent-gate Skill or MCP call):
- PRD/requirements, diagrams, decision tables, DDL, or resume: load `requirement-alignment`; read MCP prompt `doc_analysis_playbook` before analysis.
- Business ambiguity, conflicting requirements, or irreversible/business-critical decisions: load `using-intent-gate`; never guess on core flows. Escalate through its intent funnel.
- Coding with `.harness/requests/*/summary.md`: load `contract-coding`; check status and lint first.
- Multi-workspace hosts: pass `project_path` explicitly.
Load full Skills and the playbook on demand; this hook is only a compact router.
