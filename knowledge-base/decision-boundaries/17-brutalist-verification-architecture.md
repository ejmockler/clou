# DB-17: Brutalist Verification Architecture

**Status:** DECIDED
**Decided:** 2026-03-30
**Severity:** High — determines how quality assessment findings are generated, filtered, and acted upon
**Question:** How should brutalist quality gates relate to the assessment and verification cycles?

## Decision

**Brutalists are structurally separated from assessment judgment. Brutalists run as read-only critics that cannot edit code. The assessor reads brutalist output and classifies each finding as valid (fix needed), noise (dismiss), or architectural (escalate). Valid findings route to EXECUTE rework. Architectural findings escalate to the supervisor. The convergence trajectory (bugs → edge cases → test gaps → stop) is the assessor's stopping criterion.**

## The Separation

The current architecture combines critique and judgment in the assessor role — the assessor invokes quality gates AND evaluates their findings. This decision separates those concerns:

1. **Brutalist phase** (read-only): Invokes quality gate tools (roast_codebase, roast_architecture, roast_security, etc.). Captures findings verbatim. Cannot edit code, cannot make judgments about findings. Writes raw findings to assessment.md.

2. **Assessor evaluation** (judgment): Reads brutalist findings from assessment.md. Classifies each finding against requirements.md and intents.md. Writes classification decisions to decisions.md. Routes: valid → rework task, noise → documented dismissal, architectural → escalation.

This separation ensures the critic cannot soften its own findings (it has no judgment role) and the judge cannot be influenced by the act of finding (it reads findings after the fact, not during discovery).

## Design Rationale

### Separation of Critique and Judgment

The brutalist's job is adversarial: find everything that could be wrong. The assessor's job is evaluative: determine which findings warrant action given the project's requirements and scope. These are different cognitive tasks that benefit from different tool access and prompt framing.

When combined in one agent:
- The critic self-censors (aware that its findings will be evaluated, it softens them)
- The judge is anchored by the discovery process (having found an issue, harder to dismiss it)
- The convergence signal is noisy (mixing "found nothing new" with "found things but dismissed them")

When separated:
- The brutalist finds everything without filtering — its assessment.md is maximally adversarial
- The assessor evaluates findings cold — reading a document, not participating in discovery
- The convergence trajectory is clean: when brutalists find only test gaps (not bugs or edge cases), the assessor's classification pattern shifts visibly

### Convergence Trajectory as Stopping Criterion

The trajectory is bugs → edge cases → test gaps → stop. This maps to the assessor's classification pattern:

- **Early cycles:** Brutalist finds bugs. Assessor classifies most as valid. Many rework tasks.
- **Middle cycles:** Brutalist finds edge cases. Assessor classifies some as valid, some as noise (out of scope for this milestone). Fewer rework tasks.
- **Late cycles:** Brutalist finds only test gaps and style issues. Assessor classifies few or none as valid. Convergence.

The existing `assess_convergence()` in recovery.py counts consecutive zero-accept ASSESS cycles. This machinery stays — but now it operates on assessor classifications of brutalist findings, not on combined find-and-judge output. The signal is cleaner because the brutalist always runs maximally (it doesn't converge — it always finds things). What converges is the assessor's acceptance rate.

### Brutalists Cannot Edit

This is a structural constraint, not prompt guidance. The brutalist phase's agent definition includes only read-only tools:

- **Allowed:** Read, Glob, Grep, quality gate MCP tools (roast_codebase, roast_architecture, roast_security, roast_cli_debate, brutalist_discover)
- **Not allowed:** Write, Edit, Bash (with write operations)

The brutalist cannot fix what it finds, by construction. This prevents the "doctor who prescribes their own diagnosis" failure mode — a critic that also fixes may find only things it knows how to fix, biasing toward easy problems.

### Architectural Findings Escalate

Some brutalist findings are valid but beyond the coordinator's authority: "this authentication approach has a fundamental design flaw" or "the data model can't support the stated scalability requirement." The assessor classifies these as architectural — they escalate to the supervisor, who discusses with the human.

This uses the existing escalation protocol (protocols/escalation.md) with classification type: `architectural_finding`. Severity is typically `degraded` (coordinator continues independent work) unless the finding blocks all remaining phases.

## The ASSESS Cycle After This Decision

Before:
1. Coordinator dispatches assessor
2. Assessor invokes quality gates + evaluates findings (combined)
3. Coordinator reads assessment.md and decisions.md

After:
1. Coordinator dispatches brutalist (read-only, quality gate invocation only)
2. Brutalist writes assessment.md (raw findings, verbatim)
3. Coordinator dispatches assessor-evaluator (reads assessment.md, classifies)
4. Assessor-evaluator writes decisions.md (classifications and routing)
5. Coordinator reads decisions.md for routing

Two dispatches per ASSESS cycle instead of one. The additional dispatch cost is offset by cleaner convergence (fewer unnecessary rework cycles) and better finding quality (brutalist is maximally adversarial).

## Finding Classification Schema

| Classification | Action | Criteria |
|---|---|---|
| **valid** | Create rework task in next EXECUTE | Finding is correct, in scope, fix is proportionate to milestone requirements |
| **noise** | Document dismissal in decisions.md | Finding is out of scope, stylistic, or fix cost exceeds value for this milestone |
| **architectural** | Write escalation file | Finding is valid but beyond coordinator's delegated authority |
| **security** | Always accept as valid | Security findings from roast_security are never classified as noise |

Multi-source agreement strengthens classification: if roast_codebase AND roast_architecture flag the same issue, it's more likely valid than noise.

## Relationship to Existing Decisions

### Refines DB-09: Verification Generalization

DB-09 established composable verification modalities. The brutalist phase adds adversarial code review as a verification input. Brutalist findings feed the VERIFY cycle's evaluations — the verifier can check whether brutalist-identified issues are actually observable in the running environment.

### Consistent with DB-10: Team Communication

Stigmergic coordination: the brutalist writes assessment.md, the assessor reads it. No direct communication between agents. The filesystem is the coordination medium.

### Extends DB-05: Error Recovery

Convergence detection operates on assessor classifications of brutalist findings. The existing `assess_convergence()` machinery is unchanged — it already counts zero-accept rounds in decisions.md. The signal is cleaner with separated roles.

### Consistent with DB-11: Harness Architecture

HarnessTemplate quality gate configuration specifies brutalist tool set. Different harness templates can configure different quality gate tools for the brutalist phase while the assessor-evaluator's tool set remains constant (read-only on golden context).

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-09 (Verification)** | Adversarial code review added as verification input. Brutalist findings feed VERIFY evaluation. |
| **DB-10 (Communication)** | New agent role (brutalist) with read-only tool set. Stigmergic: writes assessment.md. |
| **DB-05 (Recovery)** | Convergence detection unchanged mechanically; operates on cleaner signal from separated roles. |
| **DB-11 (Harness)** | HarnessTemplate quality gate config specifies brutalist tool set separately from assessor. |

## Implementation Scope

| Component | Change |
|---|---|
| `coordinator-assess.md` | Two-dispatch protocol: brutalist first (read-only), then assessor-evaluator. |
| `assessor.md` prompt | Becomes brutalist prompt: invoke quality gates, write raw findings, no evaluation. |
| `assess-evaluator.md` prompt | Reads assessment.md, classifies findings against requirements.md + intents.md, writes decisions.md. |
| `orchestrator.py` | ASSESS cycle dispatches two agents sequentially: brutalist then evaluator. |
| Agent definitions | Brutalist agent with read-only tool set. Assessor-evaluator tools unchanged. |
| `recovery.py` | `assess_convergence()` unchanged — already operates on decisions.md accepted count. |
