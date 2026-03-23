# Brutalist MCP Integration

## Overview

`@brutalist/mcp` is a multi-perspective code analysis engine that deploys Claude Code, Codex, and Gemini CLI agents to independently critique code, architecture, security, and ideas. The tagline: "All AIs are sycophants. This one demolishes your work before users do."

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

Brutalist is added as an MCP server available to the coordinator and agent teams:

```bash
# User-scope installation (available to all sessions)
claude mcp add brutalist --scope user -- npx -y @brutalist/mcp@latest
```

In SDK options:
```python
options = ClaudeAgentOptions(
    mcp_servers={
        "brutalist": {
            "command": "npx",
            "args": ["-y", "@brutalist/mcp@latest"],
            "type": "stdio"
        }
    },
    allowed_tools=[
        "mcp__brutalist__roast_codebase",
        "mcp__brutalist__roast_architecture",
        "mcp__brutalist__roast_security",
        "mcp__brutalist__roast_product",
        # ... as needed
    ]
)
```

## Brutalist Availability

Brutalist is essential infrastructure, not advisory tooling. If Brutalist MCP becomes unavailable during an ASSESS cycle, this is a **blocking error**:

1. The coordinator writes a `blocked` escalation with the specific error (connection refused, npm failure, etc.)
2. The coordinator exits the cycle — it does not proceed without quality assessment
3. The supervisor reads the escalation and informs the user
4. Resolution: user fixes Brutalist installation/network, then coordinator resumes

Brutalist unavailability is treated identically to any other blocking infrastructure failure. The coordinator does not skip assessment, degrade to self-review, or proceed without external verification. See [DB-05](../decision-boundaries/05-error-recovery.md).

## What Brutalist Does NOT Replace

- **Unit/integration tests** — Brutalist critiques, it doesn't test. Tests verify behavior. Brutalist questions design.
- **Verification protocol** — Brutalist assesses code and experience quality. Verification confirms golden paths work end-to-end.
- **Human judgment** — Brutalist and the coordinator together handle most quality decisions, but the escalation path to the supervisor (and ultimately the user) exists for a reason.
