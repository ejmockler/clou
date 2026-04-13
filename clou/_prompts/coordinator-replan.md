<cycle type="REPLAN">

<objective>
Re-decompose a task that has been reworked repeatedly at the same
granularity. Read the current compose.py and the failed task's
execution history to understand what broke, then split the failing
task into finer-grained sub-tasks.
</objective>

<procedure>
1. Read compose.py — identify the current topology and the task
   that triggered re-planning.

2. Read decisions.md — find the ASSESS cycles that triggered rework
   on this task. Identify the recurring failure pattern: what kept
   breaking? Was it the same concern each time, or different concerns
   bundled into one task?

3. Read the failed phase's execution.md — understand what the task
   actually did and where it diverged from criteria.

4. Decompose the failing task. Split it into 2-3 sub-tasks based on
   the failure pattern:
   - If rework targeted different concerns (e.g., UI layout vs data
     binding vs event handling), each concern becomes a sub-task.
   - If rework targeted the same concern repeatedly, the task scope
     is too broad for a single agent — split by file or module
     boundary.
   - Sub-tasks inherit the original task's dependencies and produce
     outputs that the original task's downstream consumers can use.

5. Rewrite compose.py with the split. The new graph should:
   - Replace the failing task with its sub-tasks.
   - Preserve all other tasks unchanged.
   - Place independent sub-tasks in a gather() group if they don't
     depend on each other's outputs.
   - Maintain type compatibility with downstream consumers.

6. Write phase specs for each new sub-task:
   .clou/milestones/{milestone}/phases/{phase}/phase.md

7. Update decisions.md with the re-decomposition reasoning:
   - Which task was split and why
   - What failure pattern motivated the split
   - How the new sub-tasks map to the original criteria

8. Call clou_write_checkpoint:
   - cycle: {current cycle number}
   - step: REPLAN
   - next_step: EXECUTE
   - current_phase: {first new sub-task name}
   - phases_completed: {unchanged}
   - phases_total: {updated count}

9. Call clou_update_status with updated phase list.

Protocol tools: Use clou_write_checkpoint and clou_update_status for
checkpoint and status files. These tools guarantee correct format.
Use Write/Edit only for narrative files (decisions.md, phase specs).
</procedure>

<schemas>

compose.py: same format as PLAN cycle — typed async functions +
execute() entry point. Validated by graph.py on write.

</schemas>

</cycle>
