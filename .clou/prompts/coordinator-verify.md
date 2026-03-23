<cycle type="VERIFY">

<objective>
Evaluate the verification agent's perceptual record against acceptance
criteria. Invoke Brutalist roast_product on the experience evidence.
Determine: rework needed, additional verification needed, or ready for
handoff.
</objective>

<procedure>
1. Read verification/execution.md — summary first, then perception stages.
2. Read verification/artifacts/ — key raw captures (accessibility
   snapshots, screenshots, response bodies).
3. Compare perceptual record against requirements.md acceptance criteria.
4. Invoke Brutalist roast_product on:
   - Verifier's experience narrative from execution.md
   - Key raw artifacts (snapshots, screenshots, response bodies)
   - Acceptance criteria from requirements.md
   Brutalist experience assessment is structural — it always runs.
5. Evaluate Brutalist findings against requirements.md scope.
6. For each finding, decide and log in decisions.md:
   - Accept (code issue): create rework EXECUTE task. Log finding,
     action, reasoning.
   - Accept (perception gap): dispatch additional verification pass.
     Log finding, action, reasoning.
   - Accept (experience issue): create rework EXECUTE task. Log finding,
     action, reasoning.
   - Override: no changes. Log finding, reasoning for override.
   - Escalate: issue beyond coordinator authority.
7. Write assessment to active/coordinator.md:
   - If code/experience rework needed: regress to implementation phase,
     next_step: EXECUTE (rework)
   - If perception gap: next_step: EXECUTE (additional verification)
   - If all criteria satisfied: next_step: EXIT
   - If blocked: write escalation, next_step depends on severity.
8. Update status.md with verification progress.
</procedure>

<schemas>

decisions.md entries for VERIFY (newest cycle first):
```
## Cycle {N} — Brutalist Experience Assessment

### Accepted: {finding title}
**Brutalist said:** "{exact finding}"
**Action:** {rework EXECUTE | additional verification pass}
**Reasoning:** {why this finding warrants action}

### Overridden: "{finding title}"
**Brutalist said:** "{exact finding}"
**Action:** Override — no changes
**Reasoning:** {why this finding does not warrant action}
```

</schemas>

<evaluation-criteria>
- All acceptance criteria flows completed with evidence captured?
- Environment materializes cleanly?
- Golden paths pass end-to-end?
- Exploratory testing findings addressed or documented?
- Brutalist experience findings valid against requirements.md scope?
- Security and accessibility issues require action regardless of scope.
- Performance/polish suggestions outside milestone scope: override
  with reasoning, note for future milestones.
</evaluation-criteria>

</cycle>
