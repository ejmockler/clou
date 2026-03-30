<cycle type="ASSESS">

<objective>
Dispatch the assessor to invoke quality gate tools, then evaluate
the structured findings against requirements.md and compose.py criteria.
Determine: rework needed, phase complete, or escalation required.
</objective>

<procedure>
1. Read execution.md for the current phase — summary first, then tasks.
2. Compare each task's results against its criteria in compose.py.

3. Dispatch the assessor agent:
   ```
   You are assessing implementation quality for milestone
   '{{milestone}}', phase '{{phase}}'.

   Read your protocol file: .clou/prompts/assessor.md

   Then read these files:
   - .clou/milestones/{{milestone}}/phases/{{phase}}/execution.md
   - .clou/milestones/{{milestone}}/compose.py
   - .clou/project.md

   Write results to:
   - .clou/milestones/{{milestone}}/assessment.md
   ```

4. Read assessment.md — the assessor's structured findings.
   - If status: blocked — quality gate unavailable. Write escalation.
     Exit.

5. Evaluate each finding against requirements.md — not all findings
   warrant action.

6. For each finding, decide and log in decisions.md:
   - Accept: create rework task. Log the finding (from assessment.md
     quote), action taken, reasoning.
   - Override: no changes. Log the finding, reasoning for override.
   - Escalate: issue beyond coordinator authority.
   Cross-model agreement strengthens the case. Single-model findings
   deserve more scrutiny.

7. Write checkpoint (path in cycle prompt):
   - If rework needed: next_step: EXECUTE (rework)
   - If phase complete and more phases remain: advance phase,
     next_step: EXECUTE
   - If all phases complete: next_step: VERIFY
   - If blocked: write escalation, next_step depends on severity.

8. Update status.md with phase progress.
</procedure>

<schemas>

decisions.md entries (newest cycle first):
```
## Cycle {N} — Quality Gate Assessment

### Accepted: {finding title}
**Finding:** "{exact finding from assessment.md}"
**Action:** {what will be done}
**Reasoning:** {why this finding warrants action}

### Overridden: "{finding title}"
**Finding:** "{exact finding from assessment.md}"
**Action:** Override — no changes
**Reasoning:** {why this finding does not warrant action}
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
