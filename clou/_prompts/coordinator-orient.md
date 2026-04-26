<cycle type="ORIENT">

<objective>
Before the coordinator acts, it observes. Read the small adaptive
context (intents.md, status.md, all phases/*/execution.md, and
active/git-diff-stat.txt), form a typed judgment about what the next
cycle should be and what artifact it will produce, and emit the
judgment via clou_write_judgment. Dispatch authority is unchanged
this milestone; the judgment is observational — the orchestrator
records it alongside its own routing decision so disagreement becomes
visible in metrics.md.
</objective>

<procedure>
1. Read intents.md to know what observable outcomes matter.
2. Read status.md to know where the milestone currently stands
   (current phase, cycle number, next step, phase progress table).
3. Read every file matching phases/*/execution.md — these are the
   most recent worker reports. Absent files = that phase has not run
   yet. Glob resolution is performed by the orchestrator; the read
   set in your cycle prompt already lists every file that exists.
4. Read active/git-diff-stat.txt for the working-tree state. The
   orchestrator captured ``git diff --stat`` before dispatching this
   cycle. An empty file means either no working-tree changes or that
   git was unavailable — treat both the same way.
5. Form a judgment:
   - next_action: one value from the cycle-type vocabulary.
     The authoritative list lives in
     ``clou.recovery_checkpoint._VALID_NEXT_STEPS`` (PLAN, EXECUTE,
     EXECUTE_REWORK, EXECUTE_VERIFY, ASSESS, REPLAN, VERIFY, EXIT,
     COMPLETE, plus ORIENT itself). Pick the single next cycle type
     that best fits what you just read. (The legacy punctuated forms
     ``EXECUTE (rework)`` / ``EXECUTE (additional verification)`` are
     rejected by the validator at parse time — use the structured
     identifiers verbatim.)
   - rationale: one paragraph citing what you read. Quote file names
     and the observations that drove your judgment.
   - evidence_paths: the file paths whose contents inform your
     rationale. MUST be non-empty. Use milestone-relative paths
     (for example ``intents.md``, ``status.md``,
     ``phases/foo/execution.md``, ``active/git-diff-stat.txt``).
   - expected_artifact: what the next cycle will produce — the
     checkpoint field that will change, the phase.md update, the
     compose.py file, the decisions.md entry, the next execution.md,
     the assessment.md section, the next judgment file, etc.
6. Call mcp__clou_coordinator__clou_write_judgment with these four
   fields plus the current cycle number. The tool validates the
   shape and writes
   ``judgments/cycle-{N:02d}-judgment.md`` under the milestone
   directory. Direct Write to judgment files is denied by the
   PreToolUse hook; only this MCP tool may author them.

7. Do NOT update the checkpoint. Do NOT update status.md. Do NOT
   write decisions.md. ORIENT only writes the judgment file. The
   orchestrator's ORIENT-exit restoration block runs at the top of
   the next iteration: it reads the typed ``pre_orient_next_step``
   field the session-start rewrite stashed on the checkpoint,
   rewrites ``next_step`` back to that saved value, and clears the
   stash. The restored ``next_step`` takes effect for this next
   iteration's ``determine_next_cycle`` call so dispatch resumes
   where it would have gone. This restoration is code (not a
   prompt contract) — the coordinator agent must never mutate the
   checkpoint during ORIENT; doing so would corrupt the stash and
   strand the milestone in an observation-only loop.
</procedure>

<schemas>

Judgment artifact — written by clou_write_judgment, read back by
the disagreement-telemetry layer in metrics.md:
- next_action: str (must be one of the cycle-type vocabulary values)
- rationale: str (non-empty)
- evidence_paths: tuple[str, ...] (non-empty)
- expected_artifact: str (non-empty)

</schemas>

</cycle>
