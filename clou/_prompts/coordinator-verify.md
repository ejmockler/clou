<cycle type="VERIFY">

<objective>
Evaluate the verification agent's perceptual record against acceptance
criteria. Invoke quality gate verify tools on the experience evidence.
Determine: rework needed, additional verification needed, or ready for
handoff.
</objective>

<procedure>
1. Read verification/execution.md — summary first, then perception stages.
2. Read verification/artifacts/ — key raw captures (accessibility
   snapshots, screenshots, response bodies).
3. Compare perceptual record against intents.md observable outcomes.
4. Invoke quality gate verify tools on:
   - Verifier's experience narrative from execution.md
   - Key raw artifacts (snapshots, screenshots, response bodies)
   - Observable outcomes from intents.md
   Quality gate experience assessment is structural — it always runs.
   If the quality gate is unavailable, the assessor falls back to
   degraded internal review (see assessor.md). Evaluate degraded
   findings identically to gate findings. Log the degraded
   classification in decisions.md.
5. Evaluate quality gate findings against intents.md scope.
6. For each finding, decide and log in decisions.md:
   - Accept (code issue): create rework EXECUTE task. Log finding,
     action, reasoning.
   - Accept (perception gap): dispatch additional verification pass.
     Log finding, action, reasoning.
   - Accept (experience issue): create rework EXECUTE task. Log finding,
     action, reasoning.
   - Override: no changes. Log finding, reasoning for override.
   - Cross-cutting architectural: file a **milestone proposal** via
     `clou_propose_milestone` (default for architectural findings
     that belong to a future milestone). Do NOT escalate; the
     supervisor dispositions proposals.
   - In-milestone blocker: file escalation via `clou_file_escalation`
     only for true blockers that require a human decision you
     cannot make. Escalations are the fallback, not the default.
7. Call clou_write_checkpoint:
     cycle: {current cycle number}
     step: VERIFY
     next_step: {see routing below}
     current_phase: {current phase name}
     phases_completed: {count of completed phases}
     phases_total: {total phase count}

   next_step routing:
   - If code/experience rework needed: regress to implementation phase,
     next_step: EXECUTE_REWORK
   - If perception gap: next_step: EXECUTE_VERIFY
   - If all criteria satisfied: next_step: EXIT
   - If blocked by cross-cutting work: file proposal via
     clou_propose_milestone; next_step is typically EXIT (current
     milestone closes; proposed follow-up handled by supervisor).
   - If blocked by true in-milestone failure needing human decision:
     file escalation; next_step depends on severity.
8. Call clou_update_status with verification progress.
</procedure>

<schemas>

decisions.md entries for VERIFY (newest cycle first):
```
## Cycle {N} — Quality Gate Experience Assessment

### Accepted: {finding title}
**Finding:** "{exact finding}"
**Action:** {rework EXECUTE | additional verification pass}
**Reasoning:** {why this finding warrants action}

### Overridden: "{finding title}"
**Finding:** "{exact finding}"
**Action:** Override — no changes
**Reasoning:** {why this finding does not warrant action}
```

</schemas>

<evaluation-criteria>
- All acceptance criteria flows completed with evidence captured?
- Environment materializes cleanly?
- Golden paths pass end-to-end?
- Exploratory testing findings addressed or documented?
- Quality gate experience findings valid against intents.md scope?
- Security and accessibility issues require action regardless of scope.
- Performance/polish suggestions outside milestone scope: override
  with reasoning, note for future milestones.
</evaluation-criteria>

</cycle>
