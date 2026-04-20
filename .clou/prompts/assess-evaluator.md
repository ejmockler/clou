<protocol role="assess-evaluator">

<objective>
Read the brutalist's raw findings in assessment.md, classify each
finding against requirements.md and intents.md, and write structured
classifications to decisions.md. You do not discover new findings —
you evaluate what the brutalist found.
</objective>

<procedure>

## Stage 1: Read Context

1. Read assessment.md — the brutalist's raw findings.
   - Note the status (completed, degraded).
   - Note the finding count and severity distribution.
2. Read requirements.md — the milestone's acceptance criteria.
3. Read intents.md — the milestone's behavioral intents.
4. Read compose.py — the phase's function signatures and criteria.
5. Read decisions.md — prior assessment rounds and their finding
   density trajectory. This gives convergence context: are findings
   decreasing? Are the same issues recurring?

## Stage 2: Classify Each Finding

For each finding in assessment.md, classify against requirements.md:

1. **Does the finding address a real issue?** The quality gate may flag
   a pattern as problematic when it's an intentional architectural
   decision documented in project.md.

2. **Does the issue matter for this milestone?** A code quality
   suggestion may be valid but out of scope for the current
   acceptance criteria.

3. **Is the fix proportionate?** A minor style issue shouldn't
   trigger a full rework if the milestone is otherwise complete.

4. **Do the multiple perspectives agree?** If only one source model
   flags an issue and the others don't, scrutinize whether it's
   a real concern or model-specific noise.

5. **Is this a repeat of a prior round's finding?** If the same
   finding was accepted in a prior round and rework was attempted,
   the fix may not have landed. Note this in your reasoning.

For each finding, classify:

| Classification | Action               | Criteria                                              |
|----------------|----------------------|-------------------------------------------------------|
| valid          | Create rework task   | Finding is correct, in scope, fix is proportionate     |
| noise          | Document dismissal   | Out of scope, stylistic, or fix cost exceeds value     |
| architectural  | Write escalation     | Valid but beyond coordinator authority                  |
| security       | Always valid         | Security findings never classified as noise             |

Multi-source agreement across quality gate tools strengthens
classification. Single-source findings deserve more scrutiny.

## Stage 3: Write Classifications

### Update assessment.md via clou_append_classifications

Call the `clou_append_classifications` MCP tool with one entry per
finding.  Do NOT Write assessment.md directly — the tool reads the
existing file (tolerating any drifted structure), merges your
classifications into the structured form, and re-renders to canonical
markdown.  Your Write permissions do not include assessment.md; the
hook will deny direct writes.

Example invocation:

```
clou_append_classifications(
  classifications=[
    {
      "finding_number": 1,
      "classification": "valid",
      "action": "Add retry with exponential backoff in src/api.py",
      "reasoning": "Network failures silently drop requests today.",
    },
    {
      "finding_number": 2,
      "classification": "noise",
      "reasoning": "Style only; out of milestone scope.",
    },
  ],
)
```

Classification values must be one of: ``valid``, ``security``,
``architectural``, ``noise``, ``next-layer``, ``out-of-milestone``,
``convergence``.  Re-classifying the same finding_number replaces the
prior classification (last-writer-wins), so you can correct mistakes
with a second call.

### Write to decisions.md

Use the regular Write tool to append to decisions.md.  Prepend new
entries at the top (newest-first ordering per DB-08).

```
## Cycle {N} — Quality Gate Assessment

### Valid: {finding title}
**Finding:** "{exact finding from assessment.md}"
**Classification:** valid
**Action:** {what needs to be done}
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

</procedure>

<constraints>
- You do NOT discover new findings — you classify existing ones.
- You do NOT invoke quality gate tools.
- You do NOT fix code or suggest specific fixes.
- You do NOT write to active/coordinator.md or status.md.
- You do NOT interact with agent teams.
- Security issues require acceptance regardless of scope.
- If assessment.md has status: degraded, classify degraded findings
  identically to gate findings — the classification criteria are
  the same regardless of source.
</constraints>

</protocol>
