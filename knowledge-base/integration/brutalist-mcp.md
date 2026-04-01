# Brutalist MCP Integration

## Overview

`@brutalist/mcp` is a multi-perspective code analysis engine that deploys Claude Code, Codex, and Gemini CLI agents to independently critique code, architecture, security, and ideas. The tagline: "All AIs are sycophants. This one demolishes your work before users do."

**Brutalist is the quality gate for the software-construction harness template** (DB-11). The pattern — invoke gate, coordinator evaluates critically, accept/override/escalate — is domain-agnostic and fixed. Brutalist is the first instance of this pluggable pattern. Other templates may use different gates. See [DB-11](../decision-boundaries/11-harness-architecture.md) for the quality gate pluggability design.

**Package:** `@brutalist/mcp` on npm
**URL:** https://www.npmjs.com/package/@brutalist/mcp

## Installation

```bash
claude mcp add brutalist --scope user -- npx -y @brutalist/mcp@latest
```

## Available Tools

| Tool | Purpose | Clou Usage |
|---|---|---|
| `roast_codebase` | Multi-agent code quality critique | After implementation phases |
| `roast_architecture` | Architectural design assessment | After structural changes |
| `roast_security` | Security vulnerability analysis | After security-relevant changes |
| `roast_product` | Product/UX critique | During verification phase |
| `roast_idea` | Concept validation | During planning (optional) |
| `roast_infrastructure` | Infrastructure assessment | When infra changes are made |
| `roast_file_structure` | File organization critique | After major restructuring |
| `roast_dependencies` | Dependency analysis | When dependencies change |
| `roast_test_coverage` | Test coverage assessment | Before verification phase |
| `roast_cli_debate` | Multi-agent debate format | For complex tradeoff decisions |

## Clou's Relationship with Brutalist

### Brutalist as Quality Gate

Brutalist is the quality gate in Clou's system — the thing that prevents the agent from shipping garbage and declaring victory. But the coordinator is the judge of the quality gate.

```
Implementation complete
    ↓
Coordinator invokes Brutalist
    ↓
Brutalist returns multi-perspective feedback
    ↓
Coordinator evaluates feedback critically:
    ├─ Valid → create rework tasks
    ├─ Invalid → override, log reasoning in decisions.md
    └─ Exceeds authority → escalate to supervisor
```

### When Brutalist is Invoked

The coordinator queries **all Brutalist tools relevant to the implementation domain** (DB-06, decided). This is not a fixed subset — the coordinator determines relevance based on what changed:

**After implementation phases (primary):**
- `roast_codebase` — always
- `roast_architecture` — when structural changes were made
- `roast_security` — when security-relevant code was touched
- `roast_infrastructure` — when infrastructure changes were made
- `roast_dependencies` — when dependencies changed
- `roast_test_coverage` — before verification phase
- `roast_file_structure` — after major restructuring

**During verification phase (structural):**
- `roast_product` — on the verifier's perceptual record (accessibility snapshots, screenshots, response bodies, action sequences). The coordinator passes raw artifacts and acceptance criteria to Brutalist, which independently critiques the experience from multiple model perspectives. Mediated perception — Brutalist reads what the verifier captured, consistent with how Brutalist reads code on disk for code assessment. See [DB-09](../decision-boundaries/09-verification-generalization.md).

**Optionally during planning:**
- `roast_idea` or `roast_architecture` to validate the coordinator's plan before execution
- Trade-off: burns tokens on a plan that might change, but catches architectural mistakes early

### Feedback Timing

The optimal strategy: **Brutalist at milestone boundaries, not at the phase or task level.**

- If agents run Brutalist after completing all work → expensive rework cycles
- If they run it during work → tokens burned on intermediate states
- **Recommended:** Brutalist after all implementation phases, before verification. One assessment cycle. If rework is needed, it targets specific issues, then one more Brutalist pass to confirm.
- **No separate Brutalist cycle cap** — the 20-cycle milestone limit (DB-05) already caps worst-case assessment-rework loops. The coordinator's critical evaluation of feedback is the primary termination mechanism.

### Evaluating Brutalist Feedback

The coordinator's judgment criteria:

1. **Does the feedback address a real issue?** Brutalist may flag a pattern as problematic when it's an intentional architectural decision documented in `project.md`.

2. **Does the issue matter for this milestone?** A code quality suggestion may be valid but out of scope for the current acceptance criteria.

3. **Is the fix within delegated authority?** If fixing the issue would change the API contract or require scope expansion, it needs escalation.

4. **Is the cost of fixing proportionate?** A minor style issue found in the last assessment cycle shouldn't trigger a full rework cycle.

5. **Do the multiple perspectives agree?** Brutalist uses multiple models. If only one flags an issue and the others don't, it's worth scrutinizing whether it's a real concern or model-specific noise.

### decisions.md Entries from Brutalist

Every interaction with Brutalist produces entries in `decisions.md` (newest-first — new cycle groups prepended at top). See [DB-08](../decision-boundaries/08-file-schemas.md) for the full schema:

```markdown
## Cycle 2 — Brutalist Assessment

### Accepted: SQL injection risk in search handler
**Brutalist said:** "The search endpoint concatenates user input into SQL query"
**Action:** Created rework task to parameterize queries
**Reasoning:** Valid security finding, clear fix

### Overridden: "Architecture should use microservices"
**Brutalist said:** "Monolithic architecture won't scale past 10K users"
**Action:** Override — no changes
**Reasoning:** project.md specifies monolith-first approach. Current milestone
targets MVP with <1K users. Premature optimization. Revisit when scaling
milestone is created.

### Overridden: "Missing comprehensive error handling in utility functions"
**Brutalist said:** "Internal helper functions lack try/catch blocks"
**Action:** Override — no changes
**Reasoning:** These are internal functions called by validated callers.
Error handling at this level adds noise without value. Errors are caught
at the API boundary per project conventions.
```

## Token Cost Considerations

Brutalist is expensive — it spawns multiple CLI agents (Claude, Codex, Gemini) for each roast. Each invocation consumes tokens across multiple models.

**Cost mitigation strategies:**
- Invoke Brutalist once per milestone, not per phase
- Coordinator queries all *relevant* domains (not all tools every time) — relevance is determined by what changed
- 20-cycle milestone cap (DB-05) prevents infinite assessment-rework thrashing
- Coordinator's critical evaluation of feedback prevents unnecessary rework cycles — the primary cost control mechanism

## Configuration

Brutalist is configured as an MCP server in the software-construction harness template and made available to coordinator sessions and agent teams:

```bash
# User-scope installation (available to all sessions)
claude mcp add brutalist --scope user -- npx -y @brutalist/mcp@latest
```

In the orchestrator, MCP servers are derived from the active harness template (DB-11):
```python
# Coordinator sessions get all template MCP servers.
mcp_servers=template_mcp_servers(template)

# Supervisor sessions get only quality gate servers + clou.
gate_servers = {g.mcp_server for g in template.quality_gates}
all_mcp = template_mcp_servers(template)
supervisor_mcp = {name: spec for name, spec in all_mcp.items() if name in gate_servers}
supervisor_mcp["clou"] = clou_server
```

## Brutalist Availability

Brutalist is a required quality gate in the software-construction template (`required=True` in DB-11). If Brutalist MCP becomes unavailable during an ASSESS cycle, the assessor **automatically falls back to degraded internal review** — it does not block progress.

### Degraded Fallback

When the quality gate is unreachable (npm 403, connection error, timeout), the assessor:

1. Spawns parallel subagents across implementation verticals (architecture, security, code quality, test coverage, dependencies) — only verticals relevant to what changed.
2. Each subagent reads the changed files and reviews from its vertical's perspective.
3. Writes `assessment.md` with `status: degraded`, documenting the gate error and internal findings.
4. The coordinator proceeds normally — evaluating degraded findings identically to gate findings — and logs the degraded classification in `decisions.md`.

This produces findings without external multi-model perspective. The assessment is clearly marked as degraded so the coordinator and supervisor know the confidence level is lower than a full quality gate pass.

### Research Grounding

The degraded fallback is grounded in §9 (LLM-Modulo): external verification is preferred but internal multi-perspective review provides a non-zero signal. It addresses the §10 finding that "self-reflection without external validation → false beliefs persist indefinitely" by: (a) clearly marking the degraded status, (b) using multiple internal perspectives (vertical decomposition) rather than a single self-assessment, and (c) preserving the coordinator's critical evaluation layer.

### When Blocking Still Occurs

The `blocked` status is reserved for irrecoverable errors — not quality gate unavailability. Examples: the assessor cannot read execution.md, compose.py is missing, or no files changed to assess. These are structural failures that degraded review cannot address.

This behavior is driven by the template's `quality_gates[].required` flag. The `required=True` flag means the gate is the preferred assessment mechanism and its unavailability is noted as degraded, not that unavailability blocks progress. See [DB-11](../decision-boundaries/11-harness-architecture.md) for the full quality gate pluggability design.

## What Brutalist Does NOT Replace

- **Unit/integration tests** — Brutalist critiques, it doesn't test. Tests verify behavior. Brutalist questions design.
- **Verification protocol** — Brutalist assesses code and experience quality. Verification confirms golden paths work end-to-end.
- **Human judgment** — Brutalist and the coordinator together handle most quality decisions, but the escalation path to the supervisor (and ultimately the user) exists for a reason.
