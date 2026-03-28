# DB-06: Token Economics

**Status:** DECIDED
**Severity:** Medium — affects cost and model selection
**Decisions:** Opus everywhere, coordinator queries all relevant quality gate tools (per active template, DB-11), track cost in tokens, no budget limit, hardcoded model selection

**Prior resolutions that constrain this decision:**
| Decision | Impact on DB-06 |
|---|---|
| DB-01 (Orchestrator) | `ResultMessage.total_cost_usd` and `ResultMessage.usage` available per session. `ClaudeAgentOptions.model` set per session by orchestrator. |
| DB-05 (Error Recovery) | 20-cycle milestone limit caps worst-case token burn. Inter-phase smoke tests add per-phase cost but reduce total cost via early failure detection. |

## Decisions

### 1. Model Selection: Opus Everywhere

All tiers use Opus (Claude's most capable model). Maximum quality at every tier.

- **Supervisor:** Opus — strategic reasoning, user interaction quality
- **Coordinator:** Opus — judgment loop requires strongest reasoning
- **Agent Teams:** Opus — code generation quality, codebase comprehension
- **Verification Agent:** Opus — accurate interpretation of verification results

**Rationale:** Clou's thesis is that the planning layer is the bottleneck. Degrading model quality at any tier introduces rework cycles that cost more than the savings from cheaper models. The coordinator's critical evaluation of Brutalist feedback, the agent teams' code quality, and the verification agent's accuracy all benefit from maximum capability. Cost control comes from the 20-cycle cap (DB-05) and the coordinator's judgment about when to proceed, not from model downgrading.

### 2. Quality Gate Tool Selection: All Relevant Domains

The coordinator queries all quality gate tools relevant to the implementation domain, as defined by the active harness template (DB-11). This is not a fixed subset — the coordinator determines which tools are relevant based on what changed. The assessor agent's tool list (from the template) is the single source of truth for available gate tools.

For the software-construction template (Brutalist MCP):

- `roast_codebase` — always, after implementation phases
- `roast_architecture` — when structural changes were made
- `roast_security` — when security-relevant code was touched
- `roast_product` — during verification phase (UX assessment)
- `roast_infrastructure` — when infrastructure changes were made
- `roast_dependencies` — when dependencies changed
- `roast_test_coverage` — before verification phase
- `roast_file_structure` — after major restructuring

Other templates define their own quality gate tools (DB-11 D4). The coordinator's judgment criteria are gate-agnostic: Is the feedback real? Does it matter for this milestone? Is the fix within authority? Is the cost proportionate?

### 3. No Separate Quality Gate Cycle Cap

The 20-cycle milestone limit (DB-05) already caps the worst-case number of assessment-rework loops. The coordinator's critical evaluation of quality gate feedback — is this valid? does it matter? is the fix proportionate? — is the primary termination mechanism. A separate gate-specific cycle cap is redundant.

If the coordinator is spending too many cycles on gate-driven rework, the 20-cycle cap will catch it, and the coordinator will escalate with a diagnosis of why convergence failed.

### 4. Cost Tracking: Tokens

Cost is tracked in tokens, not USD. Token counts are stable across pricing changes and directly reflect context window consumption.

The orchestrator tracks per-cycle token usage from `ResultMessage.usage`:

```markdown
## Token Usage (in active/coordinator.md)
| Cycle | Type | Input | Output |
|---|---|---|---|
| 1 | PLAN | 25,000 | 8,000 |
| 2 | EXECUTE | 42,000 | 15,000 |
| 3 | ASSESS | 38,000 | 12,000 |
| cumulative | — | 105,000 | 35,000 |
```

The orchestrator also maintains a global token tracker across all sessions (supervisor, coordinators, agent teams) for milestone-level and project-level totals.

At milestone completion, the orchestrator writes `metrics.md` (see DB-08) by aggregating the JSONL span log (`clou.telemetry`). This persists per-cycle token deltas, agent token usage, and incident counts into golden context for the supervisor's future planning. The in-memory `TokenTracker` (`clou.tokens`) provides the per-cycle snapshots; the span log provides the persistence layer.

### 5. No Budget Limit

No hard token budget per milestone. The 20-cycle cap (DB-05) is the cost ceiling. A budget limit would add complexity without clear benefit — the cycle cap already prevents runaway spending, and the coordinator's critical evaluation prevents unnecessary rework cycles.

Budget limits may be revisited as Clou accumulates real-world cost data across projects.

### 6. Hardcoded Model Selection

Model selection is hardcoded in the orchestrator: Opus for all tiers. No per-project configurability. This is a deliberate simplification — configurability adds engineering effort for a feature whose optimal value is not yet empirically established.

Configurability (e.g., a `model_selection` field in `project.md`) is deferred until real-world usage reveals cases where different projects genuinely benefit from different model strategies.

## Cost Model

### Per-Tier Token Consumption

**Supervisor:**
- Low-moderate usage
- Context: project.md, roadmap.md, requests.md, checkpoint, escalations
- Activity: mostly reading golden context, writing specs, conversing with user
- Context window fill: ~20-30% typical

**Coordinator:**
- High usage
- Context: milestone spec, requirements, plan, decisions, execution results, Brutalist feedback
- Activity: planning, dispatching, assessing, judgment loop
- Context window fill: grows with each cycle, 50-80% after 3-4 cycles

**Agent Teams:**
- Highest per-task usage (but each task is scoped)
- Context: phase spec, plan, codebase files relevant to task
- Activity: reading code, writing code, running tests
- Context window fill: 60-90% on large codebases
- Multiple concurrent sessions

**Quality Gate (Brutalist for software-construction):**
- Very high — spawns 3+ independent model sessions per roast
- Each roast tool fires Claude Code, Codex, and Gemini CLI agents independently
- Cost multiplied by number of gate tools invoked per assessment cycle
- Other templates' gates may have different cost profiles

### Cost Multipliers

A single milestone might involve:
- 1 supervisor session (low)
- 1 coordinator with 3-5 cycles × Opus (moderate-high)
- 3-5 agent team sessions per phase × 3-5 phases × Opus (high)
- 1-3 quality gate invocations × 3 models each for Brutalist (very high)
- 1 verification session with Playwright × Opus (moderate)

### Cost Control Mechanisms

1. **20-cycle milestone cap (DB-05)** — hard ceiling on worst-case burn per milestone
2. **Coordinator critical evaluation** — the primary cost control. A coordinator that correctly identifies low-value rework and proceeds saves more tokens than model downgrading
3. **Inter-phase smoke tests (DB-05)** — early failure detection reduces rework scope
4. **Session-per-cycle (DB-03)** — fresh sessions prevent context window bloat from accumulating across cycles
5. **Token tracking in golden context** — visibility into spend, enabling informed decisions about when to escalate vs. continue
