# Implementation Findings

Discoveries during implementation that affect design, refine the spec, or reveal gaps. Newest first. Each entry records what we found, why it matters, and what to do about it.

---

## 2026-03-21 — Wave 10: P0 Diagnostic Corrections

### Three presentation-plan P0s re-examined against actual code

The presentation-plan.md identified three P0 bugs during brutalist review. Cross-referencing each against the actual codebase revealed two phantom bugs and one mis-diagnosed root cause.

**P0 #1: "Double token counting" — NOT A BUG.** The finding claimed `bridge.py` posts both `ClouTurnComplete` and `ClouMetrics` for the same ResultMessage. Reality: `route_supervisor_message` posts `ClouTurnComplete` for supervisor tier; `route_coordinator_message` posts `ClouMetrics` for coordinator tier. These are separate functions called from separate orchestrator paths. No message hits both routers.

**P0 #2: "Shimmer animation frozen" — NOT A BUG.** The finding claimed `_animation_time` is never updated. Reality: the field was renamed to `_frame_time` during an earlier fix and is updated to `time.monotonic()` in `watch_breath_phase()` on every breath phase change. Shimmer travels correctly.

**P0 #3: "DAG screen always empty" — REAL, but mis-diagnosed.** The finding blamed `DagScreen()` being called with no data. Reality: `action_show_dag` already passes `self._dag_tasks` and `self._dag_deps`. The root cause was deeper — `ClouDagUpdate` was never emitted by any code path. Fixed in Wave 10 by adding lifecycle event emission to orchestrator.py.

**Lesson:** Brutalist review at scale produces occasional phantom findings when critics analyze design documents or earlier code snapshots rather than current code. Always cross-reference findings against the actual codebase before triaging.

---

## 2026-03-21 — Orchestrator Lifecycle Event Gap

### The body without a nervous system

The orchestrator ran coordinator cycles but emitted zero lifecycle events to the UI. Six message types were defined, six widget handlers existed, but nothing connected them. The gap was invisible in unit tests (which test individual modules) and only became apparent when tracing the full message flow from orchestrator loop → bridge → widget.

**Root cause:** Waves 5-9 built the UI layer bottom-up (palette → widgets → bridge → app). The orchestrator (Wave 2) was built top-down. Neither wave touched the other's emission/reception boundary. The "crossing" was implicit in the architecture but never explicitly scheduled.

**Resolution:** Added lifecycle event emission at cycle boundaries in `run_coordinator()`. Six signals: ClouStatusUpdate (pre-cycle), ClouCycleComplete (post-cycle), ClouDagUpdate (post-PLAN), ClouEscalationArrived (escalation scan), ClouHandoff (on completion), ClouCoordinatorComplete on crash.

**New public API:** `graph.py:extract_dag_data(source: str)` — extracts task names and dependency graph from compose.py AST for the DAG viewer. Uses existing internal `_extract_sigs` and `_walk_entry`.

**New message type:** `ClouStatusUpdate(cycle_type, cycle_num, phase)` — posted before each `_run_single_cycle` call so the status bar reflects current coordinator activity.

---

## 2026-03-21 — SDK Test Isolation

### pytest.importorskip for graceful degradation

Three test files (`test_integration.py`, `test_integration_ui.py`, `test_orchestrator.py`) failed with `ModuleNotFoundError` when `claude_agent_sdk` was not installed. Added `pytest.importorskip("claude_agent_sdk")` at module level — these files now skip with a clear message instead of erroring. The lifecycle event tests (`test_lifecycle_events.py`) mock the SDK at the `sys.modules` level so they run without the real SDK.

---

## 2026-03-21 — execution.md Path Routing

### Already implemented in validation.py

The earlier finding noted that validation.py checked execution.md at the milestone root instead of `phases/{phase}/execution.md`. On inspection, this was already fixed — the code globs `phases/*/execution.md` and also checks the flat path for backwards compatibility. No change needed. Closing this finding.

---

## 2026-03-19 — Brutalist Assessment: 27 Findings Triaged

### Fixed (8 items — all resolved)
1. **`git_revert_golden_context` silent failure** — now raises `RuntimeError` on non-zero exit
2. **`TokenTracker` mutable state** — properties now return `dict()` copies
3. **Escalation writer double timestamp** — single `datetime.now(UTC)` call, reused for filename and content
4. **Bash bypasses write hooks** — `SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True)` on coordinator sessions. SDK sandbox is the primary boundary; hooks are defense-in-depth.
5. **Milestone name sanitization** — `validate_milestone_name()` in orchestrator.py rejects anything not matching `[a-z0-9][a-z0-9-]*`
6. **Milestone scoping in write boundaries** — `build_hooks(tier, project_dir, milestone=...)` narrows `milestones/*` → `milestones/{milestone}`. 8 new tests verify scoped enforcement.
7. **Cost circuit breaker** — `max_budget_usd=50.0` on coordinator `ClaudeAgentOptions`. SDK enforces the hard cap per session.
8. **Logging** — Structured logging throughout orchestrator.py via `logging.getLogger("clou")`

### Dismissed (11 items, with rationale)
- **Symlink attacks**: Secondary to Bash bypass. Write tool can't create symlinks. Bash concern is the real issue.
- **TOCTOU in hooks**: Theoretical in single-threaded asyncio. PostToolUse reading after write is standard.
- **Prompt injection via validation errors**: Error messages are structural ("missing '## Cycle'"), not raw agent content.
- **spawn_coordinator intercept pattern**: Misunderstands MCP — orchestrator hosts the tool in-process, handles call directly.
- **Filesystem not ACID**: Valid for future parallel coordinators, not for current serial design (principle #6).
- **Golden context concurrent access**: Same — serial by design.
- **Silent fallback in determine_next_cycle**: Defensive. 20-cycle cap catches loops. Could log warning.
- **Recovery module doesn't execute recovery**: By design — primitives here, orchestration in orchestrator.py.
- **HookConfig vs SDK types**: Intentional for testability. Adapter in orchestrator.py.
- **Async functions with sync I/O**: Acceptable for v0.1. File I/O to local disk is fast (~ms).
- **"Orchestrator is 80% of the system"**: Observation, not critique.

---

## 2026-03-19 — execution.md Path Routing Gap

### validation.py checks wrong path for execution.md

The golden context spec puts execution.md at `phases/{phase}/execution.md` (per-phase), but validation.py currently checks `milestone_dir/execution.md` (milestone root). The schema validation logic is correct — the path routing isn't.

**Not blocking:** The phase directory structure doesn't exist yet. When we implement phase management in recovery.py or orchestrator.py, update validation.py to glob `phases/*/execution.md`. The existing tests validate the schema correctly at whatever path they create.

**How to fix:** Change `execution = milestone_dir / "execution.md"` to iterate over `milestone_dir / "phases"` subdirectories.

---

## 2026-03-19 — SDK API Surface Exceeds Spec

### New affordances not captured in orchestrator.md

The Claude Agent SDK (v0.1.49) provides capabilities the spec didn't account for:

| Affordance | Spec assumption | Reality | Implication |
|---|---|---|---|
| `effort` parameter | Not mentioned | "low"/"medium"/"high"/"max" | Use "max" for ASSESS/VERIFY, "high" for EXECUTE |
| `max_budget_usd` | Not mentioned | Hard cost cap per session | Cost guardrail per coordinator run — complements token tracking |
| `fallback_model` | Not mentioned | Auto-fallback on API errors | Resilience without custom retry logic |
| `enable_file_checkpointing` | Not mentioned | Track + rollback file changes | Golden context recovery without git operations |
| `stop_task(task_id)` | Not mentioned | Kill running subagent | Agent team circuit breaker — cleaner than timeout |
| `can_use_tool` callback | Hook-based enforcement | Synchronous in-process callback | Simpler write boundary enforcement than HookMatcher |
| `ThinkingConfig(adaptive)` | Not mentioned | Model decides thinking depth | Better than fixed thinking budgets |
| `RateLimitEvent` | Not mentioned | Rate limit detection + utilization % | Proactive backoff instead of crash recovery |
| `SandboxSettings` | Not mentioned | Bash network/file violation controls | Agent isolation — defense in depth |
| `add_dirs` | Not mentioned | Whitelist additional directories | Worker path restriction |

**Decision needed:** Should `hooks.py` use `can_use_tool` callback or `HookMatcher` for write boundary enforcement? The callback is simpler (one function, synchronous, in-process) but hooks support PostToolUse events (needed for compose.py validation). Likely answer: use both — `can_use_tool` for write boundaries, PostToolUse hook for compose.py validation.

### Agent Teams (experimental)

Claude Code has a separate "Agent Teams" primitive (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) with peer-to-peer communication, shared task lists, and file-based mailboxes. This is different from SDK subagents. Our spec uses subagents (more controllable, no experimental flags). Agent Teams could be a future evolution path but not for v1.

### Claude 4.6 capabilities

- 1M context window GA (Opus 4.6 and Sonnet 4.6)
- 128k output tokens on Opus 4.6 (doubled from 64k)
- Adaptive thinking interleaves reasoning between tool calls automatically
- `context_exhausted()` threshold in spec (200K * 0.75 = 150K) is conservative for 1M context — but session-per-cycle makes this mostly irrelevant

---

## 2026-03-19 — Type-Based Cycle Detection

### Why variable-flow cycle detection is impossible in compose.py

Sequential Python statements are forward-only: each `await` assigns to a new variable. The AST can never contain a data-flow cycle because you can't reference a variable before it's assigned. The "cycle" we're detecting is a type-level property: function A consumes type B (produced by function B) AND function B consumes type A (produced by function A). This is a design error in the task graph, not a runtime error.

**Self-dependency exclusion is critical:** `transform(x: A) -> A` doesn't create a cycle — it consumes type A from a *different* producer. Without this exclusion, any function with matching input/output types would trigger false positives.

This finding validated the spec's approach and is now encoded in `graph.py:_type_deps()`.

---

## 2026-03-19 — Bootstrap Context Engineering

### Research principles applied to our own process

When dispatching agent teams to implement modules, the research constrains our approach:

1. **Each agent gets only the relevant spec section** — not the full orchestrator.md. Per §1 (Gao et al.), even with perfect retrieval, task performance drops 13.9-85% as input length grows.

2. **Agent prompts lead with the structural constraint** — not the role. Per §2, first tokens are architecturally privileged (attention sinks). "Write `clou/hooks.py` that enforces write boundaries" beats "You are a Python developer who will..."

3. **External verification, not self-assessment** — per §9 (Kambhampati), only 12% of autonomously generated plans are executable. Quality gates (ruff, mypy, pytest) are our external verifiers.

4. **Structured intermediate artifacts** — per §10 (MetaGPT), structured outputs dramatically reduce error propagation. This file and status.md are our execution.md equivalent.

These principles apply to every agent dispatch going forward.
