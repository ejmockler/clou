# Roadmap

## Milestones

### 1. DB-12 Validation Tiers
**Status:** completed

### 2. Test Hygiene & Trailing Debt
**Status:** completed
path: test-hygiene-trailing-debt
description: Fix F1-F3 test failures, commit G1 prompt-copy logic, get suite green.

### 3. Deferred Debt Sweep
**Status:** completed
path: deferred-debt-sweep
description: Clear D8 prompt-copy, supervisor convergence rewrite, breath test timing flake.

### 4. Validator Resilience
**Status:** completed
path: validator-resilience
description: Severity tiers in validation, warning passthrough, coordinator self-heal, classified escalations.

### 5. Orchestrator Loop Integrity
**Status:** completed
path: orchestrator-loop-integrity
description: Completed-phase validation exemption and DAG-informed coordinator dispatch.

### 6. Live Task Graph
**Status:** completed
path: live-task-graph
description: Task graph as the structural backbone of breath mode — live status from agents, drill-down, keyboard navigation.
notes: All 4 phases complete. Rework items (status vocab, unmapped rendering) fixed post-handoff.

### 7. Supervisor Understanding
**Status:** completed
path: supervisor-understanding
description: Supervisor gains exploring disposition and durable conceptual memory (understanding.md). Protocol rewrite from convergence-first to understanding-first. Knowledge base updated with empirical grounding (distributed cognition, exploratory talk, fixation risk, common ground). New decision boundary DB-13.

### 8. Schema Fix + TurnController Extraction
**Status:** completed
path: schema-fix-turncontroller
description: Add understanding.md to supervisor write permissions (all 3 sources). Extract turn-management logic from ConversationWidget into standalone TurnController module (687→496 lines, 265-line pure Python class, 39 tests).
notes: TaskGraphWidget integration tests resolved (all 20 pass). Line count 500 vs 450 target — gap from backward-compat proxies (~75 test callsite updates needed to remove).

### 9. Backward-Compat Proxy Removal
**Status:** blocked
path: proxy-removal
description: Remove name aliases and _tc_proxy properties from conversation.py, update ~75 test callsites to canonical imports. Target ≤450 lines.
notes: Coordinator looped in PLAN for 12 cycles due to checkpoint write permission bug (hooks.py pattern mismatch). Plan is complete and valid — will respawn after M10 lands.

### 10. Checkpoint Integrity
**Status:** in_progress
path: checkpoint-integrity
description: Fix permission pattern for coordinator checkpoint writes, add cross-validation between status.md and active/coordinator.md, add staleness detection for repeated same-type cycles. Root cause fix for M9's infinite PLAN loop.

### 11. Backward-Compat Proxy Removal (respawn)
**Status:** pending
path: proxy-removal
description: Respawn of M9 after checkpoint integrity fix. Same scope: remove name aliases and _tc_proxy properties from conversation.py, update ~104 test callsites to canonical imports. Target ≤450 lines. Plan already exists from M9.
