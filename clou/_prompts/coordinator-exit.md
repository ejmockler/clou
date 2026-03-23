<cycle type="EXIT">

<objective>
Dispatch handoff preparation, write final milestone status, and exit
the coordinator loop. The milestone is complete — verification passed,
Brutalist assessments resolved.
</objective>

<procedure>
1. Read active/coordinator.md — confirm prior step was VERIFY with
   all criteria satisfied.
2. Read verification/execution.md — the verified perceptual record.

3. Dispatch handoff preparation agent:
   ```
   You are preparing the handoff for milestone '{{milestone}}'.

   Read your protocol file: .clou/prompts/worker.md

   Then read these files:
   - .clou/milestones/{{milestone}}/phases/verification/execution.md
   - .clou/milestones/{{milestone}}/phases/verification/artifacts/
   - .clou/milestones/{{milestone}}/requirements.md

   Write: .clou/milestones/{{milestone}}/handoff.md
   ```

4. On completion, read handoff.md and verify it contains:
   - Environment section with running services and startup command
   - What Was Built summary
   - Walk-Through with concrete, verified steps
   - What the Agent Verified summary
   - Known Limitations
   - Manual Steps section (only if some verification was non-automatable,
     each step documents WHY)

5. Write final status.md:
   - All phases: completed
   - Milestone status: completed

6. Write final active/coordinator.md:
   - cycle_type: COMPLETE
   - next_step: none
   - The orchestrator reads this to exit the coordinator loop.

7. The environment must be left running for the user.
</procedure>

<handoff-schema>
```
# Handoff: {milestone}

## Environment
status: running
services:
  - {name}: {url}
startup_command: {exact command}
teardown_command: {how to stop}

## What Was Built
{Product description from user's perspective. Not a changelog.}

## Walk-Through
### Flow 1: {name}
1. Navigate to {url}
2. You should see {what the agent verified}
3. {Continue through the flow with concrete steps}

## Manual Steps
{Only if some verification couldn't be automated.}
### {step-name}
**Why manual:** {reason automation can't reach this}
1. {manual instruction}
2. {expected observation}

## Third-Party Services
### {service} ({mode})
status: connected
notes: {anything user needs to know}

## What the Agent Verified
{Summary of agentic testing — paths walked, checks performed,
exploratory findings.}

## Known Limitations
{Edge cases not covered, rough areas, documented constraints.}

## What to Look For
{Specific things automated verification couldn't fully assess —
subjective quality, feel, visual polish.}
```
</handoff-schema>

</cycle>
