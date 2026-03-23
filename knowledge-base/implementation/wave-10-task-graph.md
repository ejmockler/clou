# Wave 10: The Crossing

The orchestrator breathes. The UI listens. Everything connects.

**Starting state:** 27 source modules, 713 tests passing, OKLCH palette + breathing animation + mode transitions + bridge + widgets all implemented. Zero lifecycle events flow between orchestrator and UI. The breathing conversation is a body without a nervous system.

**Target state:** The orchestrator emits lifecycle events at cycle boundaries. The UI responds: status bar updates, DAG populates, escalations surface, handoffs render. First end-to-end flow works.

Last updated: 2026-03-21

---

## Diagnostic: P0 Findings Re-examined

The presentation-plan.md identified three P0 bugs. Two are phantom — already fixed during the brutalist review cycles. One is real but mis-diagnosed.

### P0 #1: "Double token counting" — NOT A BUG

**Claim:** `bridge.py` posts BOTH `ClouTurnComplete` AND `ClouMetrics` for the same `ResultMessage`.

**Reality:** `route_supervisor_message` handles supervisor ResultMessage → posts `ClouTurnComplete`. `route_coordinator_message` handles coordinator ResultMessage → posts `ClouMetrics`. These are separate functions called for separate tiers from separate orchestrator paths (`run_supervisor` vs `_run_single_cycle`). No message hits both routers. No double counting.

### P0 #2: "Shimmer animation frozen" — NOT A BUG

**Claim:** `_animation_time` initialized to 0.0 and never updated.

**Reality:** The field was renamed to `_frame_time` and is updated to `time.monotonic()` in `watch_breath_phase()` (line 199) on every breath phase change. `render_line` reads `self._frame_time` (line 305). Shimmer travels correctly.

### P0 #3: "DAG screen always empty" — REAL, but root cause is deeper

**Claim:** `DagScreen()` called with no task data.

**Reality:** `action_show_dag` now passes `self._dag_tasks` and `self._dag_deps`. The screen accepts the data. But `_dag_tasks` and `_dag_deps` are populated only by `on_clou_dag_update`, and **`ClouDagUpdate` is never emitted by any code path**. The root cause is not the screen — it's the absence of lifecycle events from the orchestrator.

---

## The Actual Gap: Orchestrator Lifecycle Events

The orchestrator (`orchestrator.py`) runs coordinator cycles but emits no lifecycle events to the UI. Six signals are defined, handled by widgets, but never emitted:

| Signal | Widget Handler | What's Missing |
|--------|---------------|----------------|
| `ClouCycleComplete` | `BreathWidget.on_clou_cycle_complete` | Orchestrator doesn't post after each cycle |
| `ClouDagUpdate` | `ClouApp.on_clou_dag_update` | Nobody parses compose.py → task data |
| `ClouEscalationArrived` | `ClouApp.on_clou_escalation_arrived` | Nobody scans for new escalation files |
| `ClouHandoff` | `ClouApp.on_clou_handoff` | Coordinator completion doesn't post handoff path |
| Status bar updates | `ClouStatusBar` reactives | `cycle_type`, `cycle_num`, `phase` never set during runs |
| `ClouCoordinatorComplete` on crash | `ClouApp.on_clou_coordinator_complete` | Crash in `_run_single_cycle` only logs, doesn't notify UI |

All six originate from the same place: the `run_coordinator` loop in `orchestrator.py`. This is a single-module fix with well-defined emission points.

---

## Task Graph

```
T1: Lifecycle Event Emission (orchestrator.py)
├── T1.1: ClouCycleComplete after each cycle
├── T1.2: Status bar update message (cycle_type, cycle_num, phase)
├── T1.3: ClouDagUpdate after PLAN cycle (parse compose.py via graph.py)
├── T1.4: Escalation scanning after each cycle
├── T1.5: ClouHandoff on coordinator completion
└── T1.6: ClouCoordinatorComplete on crash (finally block)

T2: Animation Refinements (app.py)
├── T2.1: _animation_time modular wrap (one-liner)
└── T2.2: DECISION→BREATH phase alignment

T3: Validation Fix (validation.py)
└── T3.1: execution.md path routing to phases/{phase}/

T4: Test Coverage
├── T4.1: Lifecycle event integration tests
├── T4.2: on_clou_handoff handler test
├── T4.3: Escalation path rejection test
└── T4.4: cost_usd=None metric test

T5: SDK Test Isolation
└── T5.1: Graceful skip for SDK-dependent test files

T6: Documentation Update
├── T6.1: Update status.md (test count, wave 10)
└── T6.2: Update findings.md (P0 corrections, new findings)
```

**Dependencies:**
- T1.* → no external deps (all in orchestrator.py, uses existing message types)
- T2.* → no deps (isolated app.py changes)
- T3 → no deps
- T4.1 → depends on T1 (testing the new events)
- T4.2-T4.4 → no deps (testing existing handlers)
- T5 → no deps
- T6 → depends on T1-T5 (documenting results)

**Parallelizable:** T1, T2, T3, T4.2-T4.4, T5 are all independent.

---

## T1: Lifecycle Event Emission

### T1.1: ClouCycleComplete

**Where:** `run_coordinator()`, after `_run_single_cycle()` returns and validation passes.

**Emit:**
```python
if _active_app is not None:
    from clou.ui.messages import ClouCycleComplete
    _active_app.post_message(ClouCycleComplete(
        cycle_num=cycle_count + 1,
        cycle_type=cycle_type,
        next_step=next_cycle_type,  # from next determine_next_cycle call
        phase_status={},  # parsed from status.md if available
    ))
```

**Pitfall:** `next_step` requires calling `determine_next_cycle` again or reading the checkpoint. The simplest approach: emit after the validation check succeeds but before the next loop iteration. The `cycle_type` is known; `next_step` can be the cycle_type determined at the top of the next iteration. Alternative: emit a simpler event without `next_step` and let the status bar update separately.

**Approach out:** Emit ClouCycleComplete with `next_step=""` initially. The BreathWidget handler already handles this gracefully — it formats `→ {next_step}` only if non-empty.

### T1.2: Status Bar Updates

**Where:** Top of `_run_single_cycle()` and at cycle boundaries in `run_coordinator()`.

**Design choice:** Two approaches:
1. Post a new `ClouStatusUpdate` message type → app handler sets bar reactives
2. Post `ClouCycleComplete` with enough data for the bar

**Decision:** Option 1 — a thin `ClouStatusUpdate(cycle_type, cycle_num, phase)` message is cleanest. Posted from `run_coordinator` before each `_run_single_cycle` call. The `on_clou_coordinator_complete` handler already clears the bar.

**Pitfall:** Adding a new message type requires updates to `messages.py`, a new handler in `app.py`, and tests. Minimal surface area.

### T1.3: ClouDagUpdate after PLAN

**Where:** `run_coordinator()`, after a PLAN cycle completes successfully.

**Mechanism:** `graph.py` already extracts task signatures from compose.py AST. We need a lightweight function that returns `(tasks, deps)` in the format `ClouDagUpdate` expects: `tasks: list[dict[str, str]]` (name + status) and `deps: dict[str, list[str]]`.

**Implementation:**
```python
if cycle_type == "PLAN" and _active_app is not None:
    compose_path = clou_dir / "milestones" / milestone / "compose.py"
    if compose_path.exists():
        tasks, deps = _extract_dag_data(compose_path)
        from clou.ui.messages import ClouDagUpdate
        _active_app.post_message(ClouDagUpdate(tasks=tasks, deps=deps))
```

`_extract_dag_data` uses `graph.py`'s `_extract_sigs` and `_walk_entry` to get function names and call graph, then converts to the widget's format.

**Pitfall:** `graph.py`'s internal functions (`_extract_sigs`, `_walk_entry`) are prefixed with `_` (private). Options:
1. Make them public
2. Add a new public `extract_dag_data(source: str) -> tuple[list, dict]` function
3. Call `validate()` and extract from its internal state

**Approach:** Option 2 — add a small public function to `graph.py` that returns task/dep data without running full validation. Clean API boundary.

### T1.4: Escalation Scanning

**Where:** `run_coordinator()`, after each cycle.

**Mechanism:** Scan `clou_dir / "milestones" / milestone / "escalations/"` for files not yet seen. Track seen escalation filenames in a set across the loop.

```python
escalation_dir = clou_dir / "milestones" / milestone / "escalations"
seen_escalations: set[str] = set()

# Inside loop, after cycle:
if escalation_dir.exists() and _active_app is not None:
    for esc_file in sorted(escalation_dir.glob("*.md")):
        if esc_file.name not in seen_escalations:
            seen_escalations.add(esc_file.name)
            from clou.ui.bridge import parse_escalation
            data = parse_escalation(esc_file)
            from clou.ui.messages import ClouEscalationArrived
            _active_app.post_message(ClouEscalationArrived(
                path=esc_file,
                classification=data["classification"],
                issue=data["issue"],
                options=data["options"],
            ))
```

**Pitfall:** Escalation files written mid-cycle by coordinator aren't detected until cycle ends. This is correct — the orchestrator checks at cycle boundaries per the spec ("structural validation at orchestrator cycle boundaries").

**Pitfall:** parse_escalation reads the file synchronously on the event loop thread. Escalation files are small (<2KB), so sub-ms. Acceptable.

### T1.5: ClouHandoff on Completion

**Where:** `spawn_coordinator_tool()` in `_build_mcp_server()`, after coordinator completes with "completed".

**Mechanism:**
```python
if result == "completed" and app is not None:
    handoff_path = project_dir / ".clou" / "milestones" / milestone / "handoff.md"
    if handoff_path.exists():
        from clou.ui.messages import ClouHandoff
        app.post_message(ClouHandoff(milestone=milestone, handoff_path=handoff_path))
```

**Pitfall:** The `ClouCoordinatorComplete` message is posted right after this. The app's `on_clou_coordinator_complete` transitions to HANDOFF mode. The `on_clou_handoff` handler loads content. Order matters: post `ClouHandoff` BEFORE `ClouCoordinatorComplete` so the content is loaded before the mode transition reveals the widget.

### T1.6: Crash Recovery UI

**Where:** `run_coordinator()` finally block, and in the `except Exception` handler in `_run_single_cycle`.

**Current state:** `spawn_coordinator_tool` already posts `ClouCoordinatorComplete(milestone=milestone, result=result)` for ALL results. But if `run_coordinator` itself crashes (not a cycle crash but an orchestrator crash), the exception propagates up through `spawn_coordinator_tool` and the post never happens.

**Fix:** Wrap the `result = await run_coordinator(...)` call in spawn_coordinator_tool with try/except:
```python
try:
    result = await run_coordinator(project_dir, milestone, app=app)
except Exception:
    result = "error"
    log.exception("Coordinator crashed for %r", milestone)
```

The existing `ClouCoordinatorComplete` post then fires with `result="error"`.

**Pitfall:** The orchestrator's `finally` block already clears `_active_app = None`. If we post a message after that, `_active_app` is None. The fix is in `spawn_coordinator_tool` which has its own `app` reference independent of `_active_app`.

---

## T2: Animation Refinements

### T2.1: Modular Wrap

**One-liner** in `_animation_tick`:
```python
self._animation_time += _FRAME_DURATION
self._animation_time %= 4.5 * 100  # Wrap at ~450s to prevent IEEE 754 drift
```

Why `4.5 * 100`? The breath period is 4.5s. Wrapping at 100 periods (450s / 7.5min) preserves the RELEASING/SETTLING time calculations (which use `_animation_time - _release_start_time`) while preventing unbounded growth. The modular arithmetic means `_release_start_time` must also be wrapped, OR we use a separate monotonic clock for release timing.

**Simpler approach:** Only wrap for the breath computation (already done: `wrapped = self._animation_time % 4.5`). For RELEASING/SETTLING, the relative elapsed time `_animation_time - _release_start_time` is what matters, and both grow together, so precision loss cancels. The real risk is after ~10^15 seconds (317K years). Non-issue.

**Decision:** Close as won't-fix. The existing `% 4.5` wrap in the breath computation line is sufficient.

### T2.2: DECISION→BREATH Phase Alignment

Current code:
```python
elif old is Mode.DECISION and new is Mode.BREATH:
    self._breath_machine.transition(BreathState.BREATHING)
```

Missing: `_animation_time` carries accumulated time from before HOLDING. The `wrapped = self._animation_time % 4.5` means the waveform phase is arbitrary. If it lands at a trough, there's a perceptible jump from the HOLDING peak (1.0) to near-zero.

**Fix:** Reset `_animation_time` to the time offset that produces peak luminance, providing a smooth restart from the held peak:
```python
elif old is Mode.DECISION and new is Mode.BREATH:
    self._breath_machine.transition(BreathState.BREATHING)
    # Start from peak (sin(π/2) = 1 → exp(1) = max) for smooth transition from HOLDING
    self._animation_time = 4.5 * 0.25  # quarter period = peak of sin
```

**Pitfall:** None — `4.5 * 0.25 = 1.125` puts sin at π/2 (peak), so breath starts from maximum and decays naturally into the cycle. Experientially smooth.

---

## T3: Validation Fix

### T3.1: execution.md Path Routing

**Where:** `validation.py`, `_validate_execution_path` logic.

**Current:** `execution = milestone_dir / "execution.md"` — checks milestone root.

**Fix:** Glob `milestone_dir / "phases" / "*" / "execution.md"` and validate each. If `phases/` doesn't exist yet (no EXECUTE has run), skip gracefully.

**Pitfall:** Early coordinator cycles (PLAN) have no phase directories. Validation shouldn't fail on absence. Only validate files that exist.

---

## T4: Test Coverage

### T4.1: Lifecycle Event Integration Tests

Test that the orchestrator loop emits the correct messages at the correct points. Mock `_run_single_cycle` to return controlled outcomes, verify messages posted to a mock app.

### T4.2: on_clou_handoff Handler Test

Test that `ClouApp.on_clou_handoff`:
- Transitions to HANDOFF mode
- Validates path is under .clou/
- Loads content into HandoffWidget
- Handles missing file gracefully (OSError path)

### T4.3: Escalation Path Rejection Test

Test that `EscalationModal._resolve()` refuses to write to paths outside `.clou/milestones/*/escalations/`.

### T4.4: cost_usd=None Metric Test

Test that `on_clou_metrics` and `on_clou_turn_complete` handle `cost_usd=None` without TypeError.

---

## T5: SDK Test Isolation

Three test files (`test_integration.py`, `test_integration_ui.py`, `test_orchestrator.py`) fail to import because `claude_agent_sdk` is not installed.

**Fix:** Add `pytest.importorskip("claude_agent_sdk")` at module level, or wrap imports in try/except with `pytest.skip()`.

**Pitfall:** `pytest.importorskip` at module level is the idiomatic approach and requires zero changes to the test logic.

---

## Execution Strategy

**Phase A (parallel):** T1.1-T1.6, T2.2, T3.1, T4.2-T4.4, T5.1
**Phase B (sequential, depends on A):** T4.1 (lifecycle event tests)
**Phase C (sequential, depends on A+B):** T6.1-T6.2 (documentation)

Agent team composition:
- **Agent 1:** T1 (orchestrator lifecycle events) — the critical path
- **Agent 2:** T2 + T3 (animation + validation fixes) — independent
- **Agent 3:** T4.2-T4.4 + T5 (test coverage + SDK isolation) — independent
- After all three complete: T4.1 (integration tests for lifecycle events)
- Final: T6 (documentation update)
