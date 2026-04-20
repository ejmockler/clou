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

   compose.py expresses parallelism at the phase level: each function in
   a gather() group is its own phase, so each worker writes to its OWN
   phase directory.  One canonical execution.md per phase — no sharding,
   no slug freeform.  Stale shards from prior cycles are swept by the
   orchestrator before EXECUTE dispatches.

   a. gather() group (tasks in same layer, >1 task):
      - Spawn one agent per function simultaneously.
      - Each worker writes to its own phase's execution.md (see template).
      - Monitor TaskNotificationMessages.
      - SELECTIVE ABORT: if any member fails, compute which remaining
        tasks transitively depend on the failed task using the DAG deps.
        Abort only those dependents. Let independent siblings continue.
        If ALL remaining tasks depend on the failed task, write
        checkpoint (see step 6) with next_step: ASSESS and exit.
      - Collect all completion states.

   b. Sequential task (single-task layer):
      - Spawn one agent.
      - Worker writes to execution.md.
      - On completion: read execution.md summary status line.
      - CIRCUIT BREAKER: if failures or blockers detected, write
        checkpoint (see step 6) with next_step: ASSESS. Exit.
      - If clean: proceed to next layer.

5. Agent briefing for each spawned worker.

   Call the `clou_brief_worker` MCP tool to get the canonical briefing
   text.  Pass the worker's `function_name`, plus optional `intent_ids`
   (from the DAG Context intent mapping) and `extra_reads` (additional
   files the worker needs).  Use the returned text verbatim as the
   Task tool's prompt — do NOT construct briefings by hand.  The tool
   computes the deterministic execution.md path and bakes it into the
   briefing, eliminating any opportunity for per-invocation slug drift.

   Example:
   ```
   clou_brief_worker(
     function_name="extend_logger",
     intent_ids=["I3"],
     extra_reads=["src/logger.ts"],
   )
   → "You are implementing `extend_logger` for milestone '...' ..."
   ```

6. After all tasks complete:
   - Call clou_update_status with current phase progress.
   - Call clou_write_checkpoint:
     cycle: {current cycle number}
     step: EXECUTE
     next_step: ASSESS
     current_phase: {current phase name}
     phases_completed: {count of completed phases}
     phases_total: {total phase count}
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
