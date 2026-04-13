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

   | Classification | Action               | Criteria                                              |
   |----------------|----------------------|-------------------------------------------------------|
   | valid          | Create rework task   | Finding is correct, in scope, fix is proportionate     |
   | noise          | Document dismissal   | Out of scope, stylistic, or fix cost exceeds value     |
   | architectural  | Write escalation     | Valid but beyond coordinator authority                  |
   | security       | Always valid         | Security findings never classified as noise             |

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
   - architectural: write escalation. Issue beyond coordinator
     authority.
   - security: always create rework task. Security findings are
     never classified as noise.
   Cross-model agreement strengthens the case. Single-model findings
   deserve more scrutiny.

8. Call clou_write_checkpoint:
     cycle: {current cycle number}
     step: ASSESS
     next_step: {see routing below}
     current_phase: {current or next phase name}
     phases_completed: {updated count}
     phases_total: {total phase count}

   next_step routing:
   - If rework needed: next_step: EXECUTE (rework)
   - If the SAME task has been reworked 2+ times at the same
     granularity (check decisions.md for prior rework cycles on
     this phase): next_step: REPLAN
     Repeated rework on the same task signals the task scope is
     too broad — re-decomposition will split it into finer sub-tasks
     rather than retrying at the same granularity.
   - If phase complete and more phases remain: advance current_phase,
     increment phases_completed, next_step: EXECUTE
   - If all phases complete: next_step: VERIFY
   - If blocked: write escalation, next_step depends on severity.

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

### Architectural: {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** architectural
**Action:** Escalation written
**Reasoning:** {why this exceeds coordinator authority}

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
