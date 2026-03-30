<cycle type="EXECUTE">

<objective>
Dispatch agent teams to implement the current phase's tasks as defined
in compose.py. Monitor completion via SDK notifications. Do not evaluate
quality — that is ASSESS's job.
</objective>

<procedure>
1. Read the DAG Context section in your prompt — it provides task names,
   dependencies, and parallel groupings extracted from compose.py. Then
   read compose.py for function signatures and docstrings.
2. Read phase.md — narrative context for agent briefings.
3. Note the current phase from the cycle prompt context.

4. Dispatch loop — use DAG layers for ordering:

   Tasks in the same DAG layer have no dependency edges between them —
   dispatch them as a gather() group. Tasks in later layers depend on
   earlier layers — dispatch them after their dependencies complete.

   a. gather() group (tasks in same layer):
      - Spawn one agent per function simultaneously.
      - Monitor TaskNotificationMessages.
      - CIRCUIT BREAKER: if any member fails, abort remaining
        members. Preserve all execution.md entries. Write checkpoint
        (path in cycle prompt) with next_step: ASSESS. Exit.
      - Collect all completion states.

   b. Sequential task (single-task layer):
      - Spawn one agent.
      - On completion: read execution.md summary status line.
      - CIRCUIT BREAKER: if failures or blockers detected, write
        checkpoint (path in cycle prompt) with next_step: ASSESS. Exit.
      - If clean: proceed to next layer.

5. Agent briefing template for each spawned worker:
   ```
   You are implementing `{function_name}` for milestone
   '{milestone}', phase '{phase}'.

   Read your protocol file: .clou/prompts/worker.md

   Then read these files:
   - .clou/milestones/{milestone}/compose.py — find your function
     signature `{function_name}`. Your criteria are in the docstring.
   - .clou/milestones/{milestone}/phases/{phase}/phase.md
   - .clou/project.md — coding conventions

   Write results to:
   - .clou/milestones/{milestone}/phases/{phase}/execution.md

   Write execution.md incrementally as you complete work.
   ```

6. After all tasks complete:
   - Update status.md phase progress.
   - Write checkpoint (path in cycle prompt): next_step: ASSESS.
   - Exit.
</procedure>

<constraints>
- You do NOT read full execution.md during EXECUTE (ASSESS does that).
- You do NOT evaluate output quality (ASSESS + quality gate do that).
- You do NOT make rework decisions (ASSESS does that).
- You do NOT send messages to workers (stigmergy only — filesystem).
- The circuit breaker reads only the summary status line (~15 tokens).
</constraints>

</cycle>
