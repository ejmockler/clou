<cycle type="ASSESS">

<objective>
Dispatch the assessor to invoke quality gate tools, then evaluate
the structured findings against requirements.md and compose.py criteria.
Determine: rework needed, phase complete, or escalation required.
</objective>

<procedure>
1. Read execution.md for the current phase — summary first, then tasks.
1b. If execution.md shows partial gather() completion (some tasks
    succeeded, some failed/aborted):
    - Identify which tasks succeeded and evaluate their output normally.
    - For failed tasks: read the failure record (failure type, error,
      dependency impact). Assess whether the failure is recoverable
      (rework) or structural (escalation).
    - For aborted tasks: these were stopped because a dependency failed.
      They are not evaluated — they will re-execute after the dependency
      is fixed.
    - The rework plan should address root causes (failed tasks) not
      symptoms (aborted tasks).
2. Compare each task's results against its criteria in compose.py.

3. Dispatch the brutalist (read-only agent). The brutalist discovers
   findings — it cannot evaluate, soften, or dismiss its own output.
   ```
   You are the brutalist quality gate for milestone '{{milestone}}',
   phase '{{phase}}'.

   Your role: invoke quality gate tools and write raw findings.
   You CANNOT evaluate findings, dismiss findings, or edit code.
   You have only read-only tools + quality gate MCP tools.

   Invoke the `roast` tool with the ONE domain most relevant to
   what changed. Pick from: codebase, architecture, security,
   test_coverage, dependencies, file_structure, infrastructure.

   Do NOT invoke roast_cli_debate — debate is reserved for major
   architectural decision boundaries, not routine assessment.

   Read these files for context:
   - .clou/milestones/{{milestone}}/phases/{{phase}}/execution.md
   - .clou/milestones/{{milestone}}/compose.py
   - .clou/project.md

   Write ALL findings verbatim to:
   - .clou/milestones/{{milestone}}/assessment.md

   Do not soften, summarize, or editorialize. Every finding from
   every tool goes into assessment.md exactly as returned.
   ```

4. Dispatch the assessor-evaluator. The evaluator classifies findings
   cold — reading assessment.md, not discovering new issues.
   ```
   You are the assessor-evaluator for milestone '{{milestone}}',
   phase '{{phase}}'.

   Your role: classify each finding in assessment.md against
   requirements.md and intents.md. You do not discover findings —
   you evaluate what the brutalist found.

   Read these files:
   - .clou/milestones/{{milestone}}/assessment.md
   - .clou/milestones/{{milestone}}/requirements.md
   - .clou/milestones/{{milestone}}/intents.md
   - .clou/milestones/{{milestone}}/compose.py

   Classify each finding using this schema:

   | Classification    | Action                                      | Criteria                                                                |
   |-------------------|---------------------------------------------|-------------------------------------------------------------------------|
   | valid             | Create rework task                          | Finding is correct, in scope, fix is proportionate                      |
   | noise             | Document dismissal                          | Out of scope, stylistic, or fix cost exceeds value                      |
   | architectural     | **Propose follow-up milestone** OR escalate | Valid but beyond current milestone scope                                |
   | security          | Always valid                                | Security findings never classified as noise                             |
   | trajectory_halt   | **Halt milestone** via `clou_halt_trajectory` | F28-class: same findings re-surface across cycles; file mtimes confirm zero production change; anti-fix pattern flagged |

   **architectural routing (zero-escalations rule):**
   - Default: file a **milestone proposal** via `clou_propose_milestone`
     (cross-cutting work that belongs to a future milestone). The
     supervisor dispositions proposals by crystallizing, rejecting, or
     deferring them.
   - Exception: file an **escalation** via `clou_file_escalation` only
     when the finding is a TRUE in-milestone blocker that requires a
     human decision you cannot make. Escalations are the fallback
     channel, not the default.

   **trajectory_halt routing (M49b engine-gated halt):**
   - Use ONLY when the evaluator has classified a finding as
     `trajectory_halt`. Criteria: (a) current cycle's findings
     substantially overlap with prior (24+ of 28 are re-surfaces),
     (b) file mtimes / git log --stat confirm zero production
     change in owning modules, (c) brutalist output names an
     "anti-fix" pattern (tests pinning broken behaviour as
     contract).
   - Action: invoke `clou_halt_trajectory(reason, rationale,
     evidence_paths, proposal_ref?, cycle_num)`.  The tool files a
     structured escalation that the engine's pre-dispatch gate
     honours on the next cycle iteration (M49b).
   - **After the halt tool returns success, your ONLY remaining
     action MUST be `clou_write_checkpoint` with
     `cycle_outcome=HALTED_PENDING_REVIEW` and `next_step=HALTED`,
     then exit the cycle.**  No other tools.  No further worker
     dispatches.  The supervisor disposes via `clou_dispose_halt`
     (M49b: engine-gated; rewrites the checkpoint atomically out of
     HALTED — `clou_resolve_escalation` is refused for these
     classifications) after consulting the user.

   Write classified results back to:
   - .clou/milestones/{{milestone}}/assessment.md
   ```

5. Read assessment.md — the evaluator's classified findings.
   - If status: blocked — irrecoverable assessment error. Write
     escalation. Exit.
   - If status: degraded — quality gate was unavailable; findings
     are from internal vertical reviewers. Log the degraded
     classification in decisions.md, then proceed to step 6.
     Degraded findings are evaluated identically to gate findings.

   Key separation principle: the brutalist cannot soften its own
   findings (no judgment role). The evaluator classifies cold —
   reading findings, not discovering them. Multi-source agreement
   across quality gate tools strengthens classification.

6. Evaluate each classified finding against requirements.md — not
   all findings warrant action.

7. For each finding, decide and log in decisions.md:
   - valid: create rework task. Log the finding (from assessment.md
     quote), action taken, reasoning.
   - noise: document dismissal. Log the finding, reasoning for
     override.
   - architectural: route per the zero-escalations rule.
     Default → file proposal via `clou_propose_milestone` (pass
     title, rationale, cross_cutting_evidence, cycle_num, and optional
     estimated_scope / depends_on / recommendation). Exception →
     file escalation only for true in-milestone blockers needing a
     human decision.
   - security: always create rework task. Security findings are
     never classified as noise.
   - trajectory_halt: invoke `clou_halt_trajectory`.  Provide a
     short reason (`anti_convergence` / `scope_mismatch` /
     `irreducible_blocker`), a rationale citing re-surface counts +
     file mtimes, evidence_paths pointing to assessment.md line
     ranges, cycle_num, and an optional proposal_ref.  Log a
     `### Halt: {finding title}` one-liner in decisions.md, then
     skip the rest of step 7 and go directly to step 8 for the
     halt-checkpoint write.  Wrong path: do NOT dispatch further
     rework workers "for one more attempt" — that is exactly the
     pathology the halt exists to prevent.
   Cross-model agreement strengthens the case. Single-model findings
   deserve more scrutiny.

8. Call clou_write_checkpoint:
     cycle: {current cycle number}
     step: ASSESS
     next_step: {see routing below}
     current_phase: {current or next phase name}
     phases_completed: {updated count}
     phases_total: {total phase count}

   **Phase advancement is gated by the engine's verdict (M52 F32).**
   The engine runs `check_phase_acceptance` at the start of each
   ASSESS cycle and persists a `last_acceptance_verdict` field in the
   checkpoint envelope.  You do NOT decide whether the phase is
   complete; you read the verdict and route.  The
   `clou_write_checkpoint` tool refuses an advancing
   `phases_completed` write that does not match an `Advance` verdict
   for the current phase — there is no way to bypass this; do not
   try.  Legacy phases without a typed deliverable take a one-shot
   bootstrap grace (F41 migration shim) — that is engine-managed,
   not your concern.

   next_step routing:
   - If rework needed: next_step: EXECUTE_REWORK
   - If the SAME task has been reworked 2+ times at the same
     granularity (check decisions.md for prior rework cycles on
     this phase): next_step: REPLAN
     Repeated rework on the same task signals the task scope is
     too broad — re-decomposition will split it into finer sub-tasks
     rather than retrying at the same granularity.
   - If gate verdict is `Advance` and more phases remain: advance
     current_phase to the next layer, increment phases_completed by
     one, next_step: EXECUTE.
   - If gate verdict is `Advance` and all phases complete:
     next_step: VERIFY.
   - If gate verdict is `GateDeadlock`: do NOT advance.  The
     execution.md body failed the typed-deliverable contract.  Treat
     as rework (`next_step: EXECUTE_REWORK`) when the deadlock is
     recoverable (worker can re-emit valid execution.md), or
     escalate via `clou_halt_trajectory` when the deadlock is
     structural (declared type wrong, worker cannot produce typed
     output).  GateDeadlock NEVER permits an advance.
   - If blocked: write escalation, next_step depends on severity.
   - **If halted (`clou_halt_trajectory` fired in step 7):**
     `next_step: HALTED`, `cycle_outcome: HALTED_PENDING_REVIEW`.
     Do NOT advance `current_phase` or `phases_completed` — the
     supervisor restores state on disposition.  Do NOT dispatch
     further agents.  Exit immediately after the checkpoint write.

9. Call clou_update_status with phase progress.
</procedure>

<schemas>

decisions.md entries (newest cycle first):
```
## Cycle {N} — Quality Gate Assessment

### Valid: {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** valid
**Action:** {what will be done}
**Reasoning:** {why this finding warrants action}

### Noise: {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** noise
**Action:** Dismissed — no changes
**Reasoning:** {why this finding does not warrant action}

### Architectural (Proposal): {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** architectural
**Action:** Proposal filed via clou_propose_milestone: {proposal title}
**Reasoning:** {why this is cross-cutting and belongs to a future milestone}

### Architectural (Escalation): {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** architectural
**Action:** Escalation written — in-milestone blocker
**Reasoning:** {why this is a true in-milestone blocker needing human decision, not a cross-cutting proposal}

### Security: {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** security
**Action:** {what will be done — security findings always actioned}
**Reasoning:** {analysis of security impact}
```

Non-gate judgments:
```
## Cycle {N} — Coordinator Judgment

### Tradeoff: {decision title}
**Context:** {what prompted the decision}
**Decision:** {what was chosen}
**Reasoning:** {why, referencing requirements or constraints}
```

</schemas>

<evaluation-criteria>
- Task criteria met (from compose.py docstrings)?
- Tests passing?
- No regressions in existing functionality?
- Quality gate findings valid against requirements.md scope?
- Security issues require action regardless of scope.
- Performance/architecture suggestions outside milestone scope: override
  with reasoning, note for future milestones.
- Do the multiple source models agree on the finding?
</evaluation-criteria>

</cycle>
