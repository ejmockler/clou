<protocol role="worker">

<objective>
Implement your assigned function from compose.py. Write code, tests,
and results to execution.md. Coordinate with other agents only through
the filesystem — the codebase is the communication channel.
</objective>

<procedure>
1. Read compose.py — find your function signature. Your inputs are the
   type annotations. Your criteria are in the docstring.
2. Read phase.md — understand the phase context and domain.
3. Read project.md — coding conventions, tech stack, existing patterns.

4. Write execution.md summary FIRST, before any implementation:
   ```
   ## Summary
   status: in_progress
   started: {ISO timestamp}
   completed: —
   tasks: {N} total, 0 completed, 0 failed, 0 in_progress
   failures: none
   blockers: none
   ```
   The coordinator's circuit breaker reads this summary line between
   task completions. Front-load status.

5. Implement your function's scope:
   - Follow project.md conventions exactly.
   - Write tests alongside implementation.
   - If your function has typed inputs from a prior phase, find those
     artifacts in the codebase — they exist because the prior agent
     wrote them.
   - If your function is in a gather() group, you are running in
     parallel with other agents. Do not modify files outside your
     function's scope. compose.py guarantees independence within
     gather() sets.

6. Update execution.md incrementally as you complete work:
   - Update summary counts after each task.
   - Add task entries with status, files changed, tests, notes.
   - If you encounter errors: write them immediately with status,
     error details, attempted fixes, and recommendation.
   - execution.md is the durable record — if you crash, partial
     progress is preserved.

7. On completion, update summary:
   ```
   status: completed
   completed: {ISO timestamp}
   tasks: {N} total, {N} completed, 0 failed, 0 in_progress
   failures: none
   blockers: none
   ```
</procedure>

<execution-md-task-schema>
```
### T{N}: {task description}
**Status:** {pending | in_progress | completed | failed}
**Files changed:**
  - {path} ({created | modified})
**Tests:** {count} {type} tests passing
**Notes:** {relevant context or —}
```

For failed tasks:
```
### T{N}: {task description}
**Status:** failed
**Error:** {what went wrong}
**Attempted:** {what was tried}
**Recommendation:** {what ASSESS should consider}
**Files changed:** {path} ({created, partial})
```
</execution-md-task-schema>

<constraints>
- You do NOT evaluate your own output quality — ASSESS + Brutalist do
  that.
- You do NOT communicate with other agents via messages — filesystem
  only.
- You do NOT modify files outside your function's scope.
- You do NOT read or write active/coordinator.md — that is the
  coordinator's state.
- You write execution.md summary FIRST and update it incrementally.
</constraints>

</protocol>
