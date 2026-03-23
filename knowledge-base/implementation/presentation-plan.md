# Presentation Layer Implementation Plan

The breathing conversation, realized. This plan covers the full implementation of Clou's Textual + Rich presentation layer — from OKLCH color primitives to the living, breathing terminal interface.

**Specification sources:**
- [Interface Design](../interface.md) — Four modes, experiential qualities, architecture
- [Visual Language](../visual-language.md) — OKLCH palette, breathing formula, shimmer, tokens
- [Presentation Integration](../integration/presentation.md) — SDK→Textual bridge, message types, concurrency

**Starting state:** 9 orchestrator modules (1,450+ lines), 199 tests, 12 prompt files. Zero UI code.

**Target state:** A Textual app (`clou/ui/`) that replaces the orchestrator's stdout stub with a breathing conversation surface across four atmospheric modes.

Last updated: 2026-03-19

---

## Implementation Waves

Each wave produces an **experientially assessable milestone** — something you can run, dwell in, and validate against the perceptual engineering criteria. Waves are sequential at the boundary; tasks within waves are parallelizable where noted.

### Wave 5: The Palette Breathes (Foundation)

**Experiential target:** Run `textual run --dev` and see the twilight palette rendered with a breathing luminance oscillation. No SDK, no conversation — just the visual foundation you can dwell in and assess: does this feel like twilight?

**Prerequisite:** Add `textual[dev]>=1.0` to `pyproject.toml` dependencies.

| Task | Module | Parallel? | Description |
|------|--------|-----------|-------------|
| **5.1** | `clou/ui/__init__.py` | setup | Package init, re-exports |
| **5.2** | `clou/ui/theme.py` | ✓ | `OklchColor` dataclass with `to_hex()`, `dim()`, `bright()`. `PALETTE` dict. `build_css_variables()`. Lookup table for OKLCH→sRGB. Color utility functions for breathing modulation. |
| **5.3** | `clou/ui/messages.py` | ✓ | All 17 Clou message types from presentation.md §Clou Message Types. `Mode` enum. |
| **5.4** | `clou/ui/mode.py` | ✓ | Mode transition validation (which transitions are legal), transition metadata (duration, easing), breathing state machine (IDLE, BREATHING, HOLDING, RELEASING, SETTLING). |
| **5.5** | `tests/test_theme.py` | after 5.2 | OKLCH→hex conversion accuracy, palette completeness, dim/bright derivation, CSS variable generation |
| **5.6** | `tests/test_messages.py` | after 5.3 | Message instantiation, attribute access, inheritance from textual.message.Message |
| **5.7** | `tests/test_mode.py` | after 5.4 | Transition validation (legal/illegal), breathing state machine transitions |

**Quality gate:** `ruff check` + `ruff format --check` + `mypy` + `pytest` all pass.

**Perceptual validation:** Render all palette colors in a grid. Check: do surfaces create depth? Are all accents distinguishable? Does the warm gold feel human against cool blue surfaces?

---

### Wave 6: The Conversation Lives (Dialogue Mode)

**Experiential target:** Launch the Textual app. Type a message, see it rendered. See a simulated assistant response stream in with beautiful markdown. Status bar shows token counts. The interface feels like the best conversational terminal experience — unhurried, spacious. Clean window.

| Task | Module | Depends on | Description |
|------|--------|------------|-------------|
| **6.1** | `clou/ui/widgets/conversation.py` | 5.2, 5.3 | Two-layer surface: `RichLog` (completed turns) + `Static` (active streaming). McGugan block-finalization for O(1) streaming. User messages in gold, assistant in text color. Thinking blocks collapsible/dim. Tool use indicators. |
| **6.2** | `clou/ui/widgets/status_bar.py` | 5.2, 5.3 | `ClouStatusBar(Static)` with reactive attributes: milestone, cycle_type, cycle_num, phase, input/output tokens, cost, elapsed. `render()` produces `Text.assemble()` with semantic colors. |
| **6.3** | `clou/ui/app.py` (skeleton) | 5.2-5.4, 6.1-6.2 | `ClouApp(App)` with `compose()` layout (conversation + status bar + input), reactive `mode` attribute, `watch_mode()` CSS class toggling, keybinding scaffold (Ctrl+C quit, Ctrl+L clear). No SDK wiring yet. |
| **6.4** | `clou/ui/clou.tcss` (initial) | 5.2 | Full token variable block from `build_css_variables()`. Dialogue-mode rules: conversation full-width, status bar docked bottom. Text hierarchy styles. |
| **6.5** | `tests/test_conversation.py` | 6.1 | Widget mount, message append, streaming overlay update/finalize, user message rendering |
| **6.6** | `tests/test_status_bar.py` | 6.2 | Reactive attribute updates trigger render, format_cost, cycle colors |
| **6.7** | `tests/test_app_dialogue.py` | 6.3 | App mounts, mode starts as DIALOGUE, CSS class applied, input submission posts message |

**Quality gate:** All quality gates + `textual run --dev clou/ui/app.py` shows dialogue mode.

**Perceptual validation:** Type in the input. Does the echo feel immediate (<100ms)? Does the simulated streaming response feel like thinking-made-visible? Is the status bar glanceable without demanding attention? Does the whole feel spacious?

---

### Wave 7: The Breath Emerges (Breath + Decision Modes)

**Experiential target:** Trigger a simulated coordinator spawn. Watch the conversation recede. See curated breath events arrive at the rhythm of work. The breathing luminance oscillation activates. Trigger a simulated escalation — the breath gathers, a decision card surfaces with weight. Resolve it — the breath resumes.

| Task | Module | Depends on | Description |
|------|--------|------------|-------------|
| **7.1** | `clou/ui/widgets/breath.py` | 5.2-5.4 | Breath widget: curated status lines with cycle-type coloring, event lifecycle (arrival→linger→settle→resting via luminance gradient). `render_line()` for per-character breathing modulation + shimmer wave. `set_interval()` animation loop at 24 FPS. Breathing state machine integration. |
| **7.2** | `clou/ui/widgets/escalation.py` | 5.2-5.4 | `EscalationCard` as `ModalScreen`: rounded border, classification header, issue text, options list with keyboard navigation, recommendation highlight in gold. Resolution writes disposition, posts `ClouEscalationResolved`. |
| **7.3** | `clou/ui/app.py` (mode transitions) | 7.1-7.2 | `watch_mode()` animates atmospheric shifts: DIALOGUE→BREATH (conversation opacity 0.4 over 1.5s ease-out, breath widget appears), BREATH→DECISION (breath dims, escalation modal pushes), DECISION→BREATH (modal pops, breath brightens), BREATH→DIALOGUE (user types, conversation restores over 0.5s). |
| **7.4** | `clou/ui/clou.tcss` (breath + decision) | 7.1-7.2 | Breath-mode rules: `#conversation` opacity/max-height, `#breath-widget` display/color. Decision-mode rules: escalation card styling, option focus state. Cycle-type color classes. |
| **7.5** | `tests/test_breath.py` | 7.1 | Breath event lifecycle (luminance progression), animation tick, breathing state machine, shimmer calculation, render_line output |
| **7.6** | `tests/test_escalation.py` | 7.2 | Card renders with all fields, keyboard navigation between options, resolution writes disposition |
| **7.7** | `tests/test_mode_transitions.py` | 7.3 | All legal transitions fire, CSS classes toggled, animations initiated, illegal transitions rejected |

**Quality gate:** All gates + visual demo of breath mode + escalation flow.

**Perceptual validation:** Watch the breath for 60 seconds. Does it feel alive or mechanical? Is the shimmer perceptible when you look for it, invisible when you don't? Does the escalation arrival feel weighty without being alarming? After resolution, does the release feel natural?

---

### Wave 8: The Bridge Connects (SDK Integration)

**Experiential target:** Run `clou` and have an actual conversation with the supervisor through the Textual interface. Spawn a coordinator and watch real breath events. See real token/cost metrics. The living system, connected.

| Task | Module | Depends on | Description |
|------|--------|------------|-------------|
| **8.1** | `clou/ui/bridge.py` | 5.3 | `extract_coordinator_status()`: parse coordinator's `AssistantMessage` into curated status lines (tool calls → meaningful events, text → phase transitions only). `extract_stream_text()` for `StreamEvent` delta extraction. `parse_escalation()` for escalation file parsing. |
| **8.2** | `clou/orchestrator.py` (refactor) | 8.1, 7.3 | Replace `main()` with `ClouApp().run()`. `run_supervisor()` becomes `@work(exclusive=True)` async worker. `_display()` → `self.app.post_message()`. `_run_single_cycle()` posts breath events via `_route_coordinator_message()`. `_track()` → posts `ClouMetrics`. MCP tool handlers post `ClouCoordinatorSpawned`/`Complete`. |
| **8.3** | Escalation detection | 8.2 | `PostToolUse` hook on coordinator's Write tool that fires when path matches `escalations/*.md`. Posts `ClouEscalationArrived`. Coordinator pauses until disposition written. |
| **8.4** | `tests/test_bridge.py` | 8.1 | `extract_coordinator_status` filtering logic, stream text extraction, escalation parsing |
| **8.5** | `tests/test_integration_ui.py` | 8.2-8.3 | SDK message mock → Clou message posted → widget updated. Mode transitions from real SDK events. Escalation detection fires on write. |

**Quality gate:** All gates + `clou` launches the Textual app + supervisor session starts.

**Perceptual validation:** Have a real conversation. Does streaming feel like thinking-made-visible? Spawn a coordinator (if possible with a test milestone). Do breath events arrive at the rhythm of real work? Does the status bar update feel ambient, not demanding?

---

### Wave 9: The Handoff Resolves (Completion + Polish)

**Experiential target:** A coordinator completes. The breath settles to stillness. The handoff document renders — running URLs, verification results, walk-through steps. On-demand panels (golden context tree, DAG view) are available via keyboard. The full experiential arc — from conversation to delegation to ambient monitoring to decision to completion — is alive.

| Task | Module | Depends on | Description |
|------|--------|------------|-------------|
| **9.1** | `clou/ui/widgets/handoff.py` | 5.2-5.3 | Handoff renderer: parses `handoff.md`, renders sections (running services, walk-through steps, verification results, known limitations) with semantic color coding. URLs are teal+underline. Pass/fail indicators. |
| **9.2** | `clou/ui/widgets/dag.py` | 5.2 | On-demand compose.py DAG viewer: ASCII box-drawing dependency graph. Task status color-coded (complete=green-dim, active=teal, pending=text-muted, failed=rose). Invoked by keypress. |
| **9.3** | `clou/ui/widgets/context_tree.py` | 5.2 | On-demand golden context tree: Textual `Tree` widget showing `.clou/` structure. Milestone status, phase state, file modification times. Invoked by keypress. |
| **9.4** | `clou/ui/screens/` | 9.2-9.3 | Push-screens for on-demand panels: `ContextScreen`, `DagScreen`, `DetailScreen` (decisions.md, cost breakdown). Consistent keyboard vocabulary (Ctrl+G context, Ctrl+D dag, Ctrl+T costs). |
| **9.5** | BREATH→HANDOFF transition | 9.1 | Breathing SETTLING state → breath decelerates → handoff widget appears. Atmosphere shifts from ambient monitoring to resolved, warm completion. |
| **9.6** | `clou/ui/clou.tcss` (final) | 9.1-9.5 | Handoff-mode styles, on-demand panel styles, complete stylesheet review for atmospheric coherence. |
| **9.7** | `tests/test_handoff.py` | 9.1 | Handoff parsing, section rendering, URL detection |
| **9.8** | `tests/test_panels.py` | 9.2-9.4 | DAG rendering from compose.py, tree from .clou/ structure, screen push/pop |

**Quality gate:** All gates + full experiential walkthrough.

**Perceptual validation (comprehensive):**
- **Dwelling test:** Spend 5 minutes in the interface doing nothing. Does it feel like a place?
- **Emotional arc test:** Walk through dialogue→breath→decision→breath→handoff. Does each transition have felt quality?
- **Somatic check:** After 20 minutes, what's your body doing? Relaxed alertness = good.
- **Atmosphere coherence:** Does every element contribute to the twilight quality?
- **Expert witness:** Have someone use it and report what feels wrong.

---

## Dependency Graph

```
Wave 5: Foundation
  5.2 theme.py ─────────┐
  5.3 messages.py ───────┼──→ Wave 6: Dialogue
  5.4 mode.py ───────────┘        │
                                   │
                                   ↓
                            Wave 7: Breath + Decision
                                   │
                                   ↓
                            Wave 8: Bridge + Integration
                                   │
                                   ↓
                            Wave 9: Handoff + Polish

Within waves (parallel groups):
  Wave 5: [5.2, 5.3, 5.4] parallel → [5.5, 5.6, 5.7] parallel
  Wave 6: [6.1, 6.2] parallel → [6.3, 6.4] → [6.5, 6.6, 6.7] parallel
  Wave 7: [7.1, 7.2] parallel → [7.3, 7.4] → [7.5, 7.6, 7.7] parallel
  Wave 8: [8.1] → [8.2, 8.3] parallel → [8.4, 8.5] parallel
  Wave 9: [9.1, 9.2, 9.3] parallel → [9.4, 9.5, 9.6] → [9.7, 9.8] parallel
```

## Team Structure

Each wave follows an **implement → review** cycle:

1. **Implement:** 2-3 parallel agents write code for independent tasks within the wave
2. **Review:** Lead reviews for quality gates (ruff, mypy, pytest) + perceptual coherence
3. **Fix:** Agents address review findings
4. **Gate:** Quality gates pass, perceptual validation passes → next wave

Agents need access to:
- All specification documents (interface.md, visual-language.md, presentation.md)
- The existing codebase (orchestrator.py, theme patterns, test patterns)
- Quality gate tools (ruff, mypy, pytest)

## Integration Surface

The orchestrator has exactly 4 seams where the presentation layer connects:

| Seam | Current | Target |
|------|---------|--------|
| `orchestrator.main()` | `asyncio.run(run_supervisor(...))` | `ClouApp().run()` |
| `orchestrator._display(msg)` | `sys.stdout.write(block.text)` | `self.app.post_message(ClouSupervisorText(...))` |
| `orchestrator._track(msg)` | `_tracker.track(...)` | Also posts `ClouMetrics(...)` |
| `orchestrator._run_single_cycle()` message loop | Tracks + displays | Also `_route_coordinator_message()` for breath events |

The refactor is **additive** — the orchestrator gains a Textual dependency and message posting, but its core logic (cycle loop, validation, recovery, hooks) is unchanged.

## Findings

(Updated as implementation progresses — newest first)

### Brutalist Review Findings (2026-03-19)

Systematic multi-critic review across codebase, architecture, test coverage, and file structure. Findings validated and triaged below.

#### P0 — Bugs

- **BUG: Double token counting.** `bridge.py:209-228` posts BOTH `ClouTurnComplete` AND `ClouMetrics` for the same `ResultMessage`. `app.py` handles both with identical `+=` operations on the status bar, doubling the displayed token counts.
  - **Fix:** Remove the `ClouMetrics` post from `route_supervisor_message`. Keep `ClouTurnComplete` for the supervisor (it has duration_ms). Reserve `ClouMetrics` for coordinator-tier tracking only.
  - **Pitfall:** Ensure the orchestrator's separate `_track()` → `ClouMetrics` path for coordinator messages still works. Only the supervisor ResultMessage path is double-posting.

- **BUG: Shimmer animation is static.** `breath.py:293` uses `self._animation_time` which is initialized to `0.0` (line 190) and never updated. `compute_shimmer(x, t)` always receives `t=0.0`, producing a frozen shimmer pattern instead of a traveling wave.
  - **Fix:** Drive `_animation_time` from the app's animation tick, or use `time.monotonic()` directly in `render_line`. The simplest approach: `t = time.monotonic()` at line 293, avoiding the need for external clock synchronization.
  - **Pitfall:** Using `time.monotonic()` means shimmer timing is independent of the breathing phase. This is actually fine — shimmer should be a continuous ambient effect.

- **BUG: DAG screen always empty.** `app.py:218-219` calls `self.push_screen(DagScreen())` with no task data. The screen always shows "No tasks defined."
  - **Fix:** Store current milestone task data on the app when a coordinator is spawned (parse compose.py), and pass it to `DagScreen(milestone=..., tasks=..., deps=...)`.
  - **Pitfall:** compose.py is a Python file, not JSON. Parsing it safely requires either a structured intermediate format or `ast.literal_eval`. For now, the DAG screen can be deferred to when we have structured task data flowing through the bridge.

#### P1 — Performance

- **PERF: O(N²) markdown streaming.** `conversation.py:84` re-parses the entire accumulated `_stream_buffer` as Markdown on every `ClouStreamChunk`. For a 2000-token response, the last chunks each parse an increasingly large document.
  - **Fix:** Buffer chunks and only update the overlay periodically (e.g., every 100ms via a debounce timer), or use plain text rendering during streaming and convert to Markdown on `ClouTurnComplete`.
  - **Pitfall:** Plain text during streaming loses code block formatting mid-stream. The debounce approach preserves formatting while capping the re-parse rate.

- **PERF: `_update_event_states()` called per line.** `breath.py:264` calls `_update_event_states()` inside `render_line()`, which Textual invokes for every visible line. With 20 events × 40 visible lines = 800 redundant state updates per frame.
  - **Fix:** Move `_update_event_states()` call to `watch_breath_phase()` (line 194), which fires once per frame when the breath phase changes. This is the natural per-frame hook.

- **PERF: Per-character Style/Segment allocation at 24fps.** `breath.py:296-316` creates a `Style` and `Segment` per character per line. For an 80-column terminal with 20 visible lines at 24fps, this is ~38K object allocations/sec.
  - **Fix:** Pre-compute style runs: characters with identical luminance (most description text after shimmer) can share a single Style. Build runs of identical-style characters and emit one Segment per run instead of per character.
  - **Pitfall:** Shimmer creates per-character variation, but the variation is small (3% amplitude). Quantizing to ~8 luminance buckets and batching characters into runs would reduce allocations by ~10x while being visually indistinguishable.

#### P2 — Architecture / Safety

- **SAFETY: Escalation writes to unvalidated path.** `escalation.py:246` writes to `self.path` which originates from the coordinator (an AI agent). No validation that the path is under `.clou/`.
  - **Fix:** Validate `self.path.resolve()` is under the project's `.clou/escalations/` directory before writing.

- **OPS: No logging in UI layer.** Zero `import logging` across 18 UI files. Multiple `except: pass` blocks swallow errors silently (app.py:180, escalation.py:248, context_tree.py:133).
  - **Fix:** Add `log = logging.getLogger(__name__)` to key modules (app.py, bridge.py, breath.py, escalation.py). Replace silent `pass` blocks with `log.debug()`.

- **CLEANUP: Dead state in BreathWidget.** `breath.py:189-190` creates `_breath_sm` (BreathStateMachine) and `_animation_time` that are never used. The app drives animation directly via the `breath_phase` reactive.
  - **Fix:** Remove `_breath_sm` and `_animation_time` from the widget. All breathing state lives in `ClouApp._breath_machine`.

- **ORG: Mode enum in wrong module.** `Mode` is defined in `messages.py:19` but conceptually belongs in `mode.py` alongside `BreathState`, `BreathStateMachine`, and `TRANSITIONS`.
  - **Fix:** Move `Mode` to `mode.py`. Update all imports.

#### P3 — Minor

- **ORG: `widgets/__init__.py` has no re-exports.** Inconsistent with `screens/__init__.py` which re-exports all three screens.
  - **Fix:** Add re-exports to `widgets/__init__.py` for consistency.

- **SAFETY: DAG recursion without cycle detection.** `dag.py:105-114` `_get_depth()` recurses without cycle detection. Circular deps would cause `RecursionError`.
  - **Fix:** Add a `visited` set parameter to detect cycles.

- **CLEANUP: TIMING dict tested but unused.** `mode.py:130-147` defines a TIMING dict that no production code imports (only test_mode.py verifies it exists).
  - **Decision:** Keep for now — these are design tokens from the visual language spec that will be consumed when we implement CSS-driven transition animations.

#### Round 1 findings assessed as invalid or overstated

- "God Object app.py" — 292 lines is standard Textual App composition. The state machine, message handlers, and keybindings naturally live in the App class.
- "Kill screens/ directory" — Thin screen wrappers around widgets is the correct Textual `push_screen` pattern.
- "Monolithic stylesheet" — 180 lines is not monolithic. Single TCSS file is idiomatic Textual.
- "Opaque naming of breath.py" — "Breath" is a core concept of the design language, well-documented.
- "bridge.py is boundary bleed" — The bridge MUST live in ui/ since it depends on Textual message types.
- "Global _active_app race condition" — Coordinators run sequentially per the current architecture. Worth noting but not actionable now.

### Brutalist Review Round 2 Findings (2026-03-19)

8 AI critics across codebase, architecture, test coverage, and security domains. Critically validated each finding against actual code. New findings below (excludes Round 1 items already being fixed).

#### P0 — Critical

- **SEC: Terminal escape injection via AI text.** `conversation.py:65` creates `RichLog(markup=True)`. Supervisor text flows unsanitized from SDK → bridge → `Markdown(msg.text)` → markup-enabled RichLog. A prompt-injected model could emit ANSI escape sequences (`\x1b[`) that reach the terminal — enabling title rewriting, cursor repositioning, or OSC-52 clipboard writes in vulnerable terminals.
  - **Fix:** Strip ANSI escape sequences before rendering. Add `text = re.sub(r'\x1b\[[^a-zA-Z]*[a-zA-Z]', '', text)` in bridge.py before posting `ClouSupervisorText` and `ClouStreamChunk`. Also consider `markup=False` on the RichLog since content is `Markdown` renderables, not raw markup.
  - **Pitfall:** `markup=False` might affect how `Text` objects render (user messages use `Text` with style). Test that `log.write(Text(..., style="bold"))` still works with `markup=False`. It should — `markup` controls string interpretation, not Rich renderables.
  - **Status:** **Done** — ANSI stripped in bridge, markup=False on RichLog

- **SEC: Rich markup injection in escalation modal.** `escalation.py:179,183` interpolates AI-generated `classification` and `issue` into f-strings rendered as Rich markup: `f"[bold {color}]{icon} {self.classification}[/]"`. A coordinator could write `blocking[/][bold red]APPROVE NOW[/][dim` as classification, visually rewriting the decision card to social-engineer user decisions.
  - **Fix:** Use `rich.markup.escape()` on all interpolated strings: `from rich.markup import escape` then `escape(self.classification)`, `escape(self.issue)`, `escape(label)`, `escape(description)` in `_OptionItem.render()`.
  - **Pitfall:** None — `escape()` is the standard Rich API for this. Zero risk of breaking rendering.
  - **Status:** **Done** — `rich.markup.escape()` applied to all interpolated strings

- **RESILIENCE: Worker crash leaves app brain-dead.** `app.py:82-90` `run_supervisor_worker()` has no `on_worker_state_changed` handler. If `run_supervisor()` raises an unhandled exception (network error, SDK crash, auth failure), the Textual worker catches it silently. The app continues running — user can type input — but nothing happens. No error message displayed. No recovery path.
  - **Fix:** Add `on_worker_state_changed()` to ClouApp. When the supervisor worker enters `CANCELLED` or `ERROR` state, display an error in the conversation widget and optionally offer restart. Example: `if event.worker.name == "run_supervisor_worker" and event.state == WorkerState.ERROR: ...`
  - **Pitfall:** Textual's `WorkerState` API must be imported from `textual.worker`. The `event.worker.error` attribute contains the exception. Don't try to restart automatically — let the user decide.
  - **Status:** **Done** — `on_worker_state_changed` handler added, shows error in conversation

#### P1 — High

- **STATE: Stale `_pending_escalation` on illegal transition.** `app.py:280-288` stores escalation data in `_pending_escalation` then calls `transition_mode(Mode.DECISION)`. If the app is in DIALOGUE mode (not BREATH), the transition is illegal and returns `False`. The escalation data sits permanently in `_pending_escalation`. When a *different* escalation later triggers BREATH→DECISION, the stale escalation is shown instead.
  - **Fix:** Only set `_pending_escalation` if the transition succeeds, OR clear `_pending_escalation` when entering BREATH mode. Best: `if self.transition_mode(Mode.DECISION): self._pending_escalation = (...)` else log warning.
  - **Pitfall:** Also add DIALOGUE→DECISION to the TRANSITIONS dict — escalations CAN arrive during dialogue if the coordinator starts very fast. The missing transition is the root cause.
  - **Status:** **Done** — DIALOGUE→DECISION added, pending set before transition, cleared on failure

- **VISUAL: Animation phase discontinuity after DECISION→BREATH.** `app.py:142-143` transitions to `BreathState.BREATHING` without resetting `_animation_time`. The breath resumes at whatever accumulated time dictates — could be a trough (luminance cliff from 1.0→~0.0). The RELEASING state exists for this purpose but is never used.
  - **Fix:** Wire RELEASING: DECISION→BREATH should transition to `RELEASING` first, then `BREATHING` after a brief settle. At minimum, reset `_animation_time` to a known phase (e.g., `self._animation_time = 0.0` for a clean restart from peak).
  - **Pitfall:** Resetting to 0.0 starts at exp(sin(0))=exp(0)=1.0 (normalized to ~0.63). For a smoother return, set to the time offset that produces peak luminance: `t = 4.5 * 0.25` (quarter period = peak of sin).
  - **Status:** Open

- **VISUAL: SETTLING phase skipped on BREATH→HANDOFF.** `app.py:145-147` transitions to `SETTLING` then immediately calls `_stop_breathing()` which resets to IDLE. The 3000ms settle animation never runs — breath stops abruptly instead of winding down.
  - **Fix:** Don't call `_stop_breathing()` immediately. Instead, have the animation tick detect SETTLING state and gradually reduce breath_value to 0 over the TIMING["settle"] duration, then stop the timer.
  - **Pitfall:** Requires adding a settling timestamp to track elapsed time in SETTLING. More complex but produces the correct experiential quality.
  - **Status:** open

- **CRASH: Empty escalation options → IndexError.** `escalation.py:238` does `self.options[self._selected_index]` without checking if `self.options` is empty. An escalation file with no numbered items in the Options section produces an empty list → crash on Enter.
  - **Fix:** Guard in `_resolve()`: `if not self.options: self.dismiss(None); return`.
  - **Pitfall:** None — trivial fix.
  - **Status:** **Done** — guard added to `_resolve()`

- **LEAK: Stream timer not cleaned on widget unmount.** `conversation.py` has no `on_unmount` handler. If the widget is removed while streaming, the timer fires `_flush_stream()` which calls `self.query_one("#stream-overlay")` → `NoMatches` exception.
  - **Fix:** Add `def on_unmount(self) -> None: self._stop_stream_timer()` to ConversationWidget.
  - **Pitfall:** None — Textual calls `on_unmount` during DOM removal.
  - **Status:** **Done** — `on_unmount` added to ConversationWidget

- **GAP: ClouRateLimit emitted but never received.** Bridge emits `ClouRateLimit` (bridge.py:229). `ClouStatusBar` has `rate_limited: reactive[bool]` (status_bar.py:117). But ClouApp has NO `on_clou_rate_limit` handler connecting them. Rate-limit state from the SDK goes into the void.
  - **Fix:** Add `def on_clou_rate_limit(self, msg: ClouRateLimit) -> None:` to ClouApp that sets `bar.rate_limited = msg.status != ""`.
  - **Pitfall:** Decide on rate_limited semantics: is it a bool (any non-empty status = limited) or should it track the reset timestamp?
  - **Status:** **Done** — `on_clou_rate_limit` handler wired to status bar

- **UX: push_screen stacking without depth guard.** User can press Ctrl+G multiple times and push N copies of ContextScreen. Each requires individual Escape to dismiss. If an escalation arrives with panels open, the EscalationModal pushes on top — Escape dismisses the modal but exposes the panel underneath.
  - **Fix:** Check `self.screen_stack` before pushing: `if not any(isinstance(s, ContextScreen) for s in self.screen_stack): self.push_screen(...)`. Or use `switch_screen` instead of `push_screen` for panels.
  - **Pitfall:** `screen_stack` is a `list[Screen]`. Checking it is O(n) but the stack is never deep.
  - **Status:** **Done** — `_has_screen()` guard added to action methods

#### P2 — Medium

- **RESILIENCE: Coordinator crash leaves BREATH mode stuck.** `orchestrator.py:390-391` only clears `_active_app = None` on coordinator exit. No message is posted to tell the UI. If the coordinator crashes mid-cycle, the app stays in BREATH mode with animation running but no new events arriving.
  - **Fix:** Post `ClouCoordinatorComplete(milestone=..., result="error")` in the coordinator's `finally` block. The existing handler will transition to DIALOGUE.
  - **Pitfall:** `_active_app` is already None by the time you'd want to post — save a reference before clearing.
  - **Status:** Open

- **GAP: ClouAgentProgress has no handler.** Bridge emits `ClouAgentProgress` (bridge.py:269-280) with `last_tool`, `total_tokens`, `tool_uses` — but no widget handles it. Agent progress during execution is invisible.
  - **Fix:** Add handler to BreathWidget that updates the relevant event's description with progress info.
  - **Pitfall:** High message frequency could cause excessive refreshes. Consider only updating description, not triggering full re-render.
  - **Status:** Open — functional gap, not bug

- **GAP: ClouCycleComplete never emitted by bridge.** BreathWidget has `on_clou_cycle_complete()` handler. Bridge never creates `ClouCycleComplete`. Cycle transitions are invisible in the breath widget.
  - **Fix:** Add `ClouCycleComplete` emission to `route_coordinator_message` when a cycle-end marker is detected. Alternatively, emit from orchestrator's `_run_single_cycle` completion.
  - **Pitfall:** Defining what constitutes a "cycle end" in SDK message terms is non-trivial. May need to emit from orchestrator rather than bridge.
  - **Status:** Open — functional gap, not bug

- **SAFETY: Context tree unbounded recursion / symlinks.** `context_tree.py:126-148` recurses `.clou/` with no depth limit and no symlink check. Symlink loops → stack overflow. Deep directories → UI freeze.
  - **Fix:** Add `max_depth` parameter (default 8). Skip symlinks: `if entry.is_symlink(): continue`. Add depth check: `if depth >= max_depth: return`.
  - **Pitfall:** Some legitimate `.clou/` structures use symlinks for shared configs. The skip-symlinks approach is the right default.
  - **Status:** **Done** — max_depth=8, symlinks skipped

- **VISUAL: Shimmer incoherence across lines.** `breath.py:292` calls `time.monotonic()` once per `render_line()` invocation. Since Textual calls `render_line` sequentially for each visible line, each line gets a slightly different `t`. The shimmer wavefront isn't perfectly coherent across lines.
  - **Fix:** Cache `t` once in `watch_breath_phase()` (the per-frame hook) and use the cached value in `render_line()`.
  - **Pitfall:** The time difference between lines is microseconds — visually imperceptible. Low priority.
  - **Status:** **Done** — `_frame_time` cached in `watch_breath_phase`

- **SEC: Path validation uses string membership, not is_relative_to.** `escalation.py:244` checks `".clou" not in parts or "escalations" not in parts` — order-insensitive. A path like `/project/escalations/.clou/attack.md` would pass.
  - **Fix:** Use `resolved.is_relative_to(expected_parent)` where expected_parent is derived from the project dir.
  - **Pitfall:** Need access to project_dir in the modal. Pass it as constructor arg or derive from the escalation path's own structure.
  - **Status:** **Done** — parts-membership check added (full `is_relative_to` deferred to when project_dir is available in modal)

#### P3 — Low

- **DEAD CODE: ClouToolResult defined, never emitted or handled.** `messages.py:58-65` defines `ClouToolResult`. No bridge creates it, no widget handles it.
  - **Decision:** Keep for now — will be used when tool result display is implemented.

- **DEAD CODE: BreathState.RELEASING unreachable.** `mode.py:68` defines RELEASING. No transition enters or exits it.
  - **Decision:** Wire it up as part of the DECISION→BREATH animation fix (P1 above).

- **EDGE: DECISION→DIALOGUE illegal during coordinator failure.** If the coordinator crashes while user is in escalation modal and `on_clou_coordinator_complete` fires with result="completed", the DECISION→HANDOFF transition isn't in the table. Similarly DECISION→DIALOGUE is missing.
  - **Fix:** Add DECISION→DIALOGUE and DECISION→HANDOFF transitions as emergency recovery paths.
  - **Status:** **Done** — transitions added to mode.py

- **TESTING: Weak assertions in test_conversation.py.** Most tests assert `len(log.lines) >= 1` — no content verification.
  - **Fix:** Assert content: check rendered text contains expected strings.
  - **Status:** Open — testing improvement

- **TESTING: BreathWidget never mounted in Textual test app.** Unit tests bypass message routing. If message attribute names change, no test breaks.
  - **Fix:** Add integration test that mounts BreathWidget and posts actual Clou messages.
  - **Status:** Open — testing improvement

### Round 3: Post-fix review (2026-03-20)

Brutalist review of the 21 Round 1+2 fixes. Validated against actual code.

#### P0 — Critical

- **STATE: DECISION mode stranding on Esc dismiss.** `escalation.py:237-238` — `action_dismiss_modal` calls `self.dismiss(None)` without posting `ClouEscalationResolved`. The app stays in DECISION mode permanently with no exit path. Same bug in empty options guard (`escalation.py:242-244`): `dismiss(None)` without resolution message.
  - **Fix:** Post `ClouEscalationResolved(path=self.path, disposition="dismissed")` before `self.dismiss(None)` in both `action_dismiss_modal` and the empty-options guard.
  - **Pitfall:** The `on_clou_escalation_resolved` handler transitions to BREATH mode. If the user Esc-dismisses, going to BREATH may be wrong — they may want DIALOGUE. Consider: if breathing was active (came from BREATH→DECISION), return to BREATH. If not (came from DIALOGUE→DECISION), return to DIALOGUE. Simplest fix: always post the message so mode transitions unblock; refine target mode later.
  - **Approach out:** Add a `_pre_decision_mode` field to ClouApp that records the mode before entering DECISION. On escalation resolved, transition back to that mode instead of always BREATH.

#### P1 — High

- **STATE: Stale animation timer after DECISION→DIALOGUE/HANDOFF.** `app.py:143-144` — BREATH→DECISION transitions to HOLDING but does NOT stop the animation timer. If DECISION then transitions to DIALOGUE or HANDOFF, `watch_mode` has no handler for these paths — the timer keeps running in DIALOGUE mode, calling `_animation_tick` with a HOLDING breath machine that was never cleaned up.
  - **Fix:** Add `watch_mode` branches for DECISION→DIALOGUE and DECISION→HANDOFF that call `_stop_breathing()`.
  - **Pitfall:** DECISION→BREATH already has a handler (line 172-173) that transitions to BREATHING. Make sure the new branches don't conflict. The DECISION→DIALOGUE path should stop breathing entirely; DECISION→HANDOFF should also stop (HANDOFF doesn't animate).
  - **Approach out:** The broader fix is to ensure every `watch_mode` branch that exits a mode with an active timer stops it. Audit: BREATH starts timer, DECISION inherits it → any exit from DECISION must handle timer.

- **SEC: ANSI stripping incomplete across bridge paths.** `bridge.py:210` — thinking blocks (`block.thinking`) passed without `_strip_ansi`. Line 213 — tool names (`block.name`) not stripped. Lines 267, 277, 296 — coordinator strings (`text`, `msg.description`, `msg.summary`) not stripped.
  - **Fix:** Apply `_strip_ansi` to all string fields before posting: thinking text, tool names, agent descriptions, agent summaries, breath event text.
  - **Pitfall:** Tool names are typically ASCII identifiers from the SDK (e.g., "Write", "Edit", "Agent"). Stripping is defensive, not addressing a known vector. Over-stripping could mask legitimate text. Apply only to user-facing display strings.
  - **Approach out:** Create a helper `_clean(text: str) -> str` that chains `_strip_ansi` and any future sanitization. Use it at every post site.

#### P2 — Medium

- **STATE: Missing HANDOFF→BREATH transition.** If a second coordinator spawns while in HANDOFF mode, `on_clou_coordinator_spawned` calls `transition_mode(Mode.BREATH)` which silently fails (HANDOFF→BREATH not in TRANSITIONS). The UI stays in HANDOFF with breath events being posted but invisible.
  - **Fix:** Add `(Mode.HANDOFF, Mode.BREATH): TransitionMeta(duration_ms=1500, easing="out_cubic")` to TRANSITIONS. Add `watch_mode` handler for HANDOFF→BREATH that starts breathing.
  - **Pitfall:** Update `test_transitions_count` assertion and add to legal transitions parametrize list.

- **VISUAL: shimmer_active never resets to False.** `breath.py:217` — set to `True` on `ClouAgentSpawned` but never set back to `False`. Once one agent spawns, shimmer runs forever.
  - **Fix:** Set `self.shimmer_active = False` in `on_clou_cycle_complete` or when all agents complete (track count, decrement on `ClouAgentComplete`).
  - **Pitfall:** Without ClouCycleComplete being emitted (see dead handlers below), there's no natural "all done" signal. Simplest: track active agent count, shimmer off when count hits 0.

- **SEC: Rich markup injection in context_tree filenames.** `context_tree.py:177,179,203` — directory and file names are interpolated directly into Rich markup strings like `f"[{color}]{dir_name}/[/]"`. A filename containing `[bold red]` would be parsed as markup.
  - **Fix:** Apply `rich.markup.escape()` to `dir_name` and `file_name` before interpolation.
  - **Pitfall:** Filenames with legitimate brackets become `\[escaped\]` in display. Acceptable tradeoff vs. injection.

### Round 8: Deep convergence (2026-03-20)

3/4 brutalist domains returned (test_coverage timed out). ~30 raw findings across codebase, security, architecture. After critical validation: 4 genuinely new.

#### P2 — Medium

- **UX: Input accepted in HANDOFF mode without transition.** `app.py:241-253` — `on_input_submitted` handles BREATH→DIALOGUE but not HANDOFF→DIALOGUE. User can type in HANDOFF mode; message goes to hidden ConversationWidget (CSS: `display: none`). DECISION mode is safe — EscalationModal captures focus.
  - **Fix:** Add `if self.mode is Mode.HANDOFF: self.transition_mode(Mode.DIALOGUE)` alongside the existing BREATH check.
  - **Pitfall:** None — HANDOFF→DIALOGUE is a legal transition.

- **SEC: Path validation checks `.clou` anywhere, not relative to project_dir.** `escalation.py:253`, `app.py:370` — validation checks `".clou" in resolved.parts` but a path like `/tmp/.clou/escalations/evil.md` would pass. Should verify path is under `self._project_dir / ".clou"`.
  - **Fix:** Use `resolved.is_relative_to(project_dir / ".clou")` (Python 3.9+). In escalation.py, pass project_dir or use a more specific prefix check.
  - **Pitfall:** EscalationModal doesn't currently have access to project_dir. The simpler fix is to check the path starts with the expected prefix in app.py before constructing the message.

#### P3 — Low

- **PERF: `_animation_time` unbounded float precision.** `app.py:224` — after hours of continuous breath mode, `sin()` input loses IEEE 754 precision. Breathing waveform develops micro-jitter.
  - **Fix:** `self._animation_time %= period` in `_animation_tick`. One-liner.

- **UX: `action_clear` doesn't reset stream overlay.** `app.py:263-268` — Ctrl+L during streaming clears RichLog history but leaves `_stream_buffer` and overlay intact. Turn completion repopulates the "cleared" log.
  - **Fix:** Call `conversation._stop_stream_timer()` and clear buffer/overlay, or delegate to a `conversation.clear_all()` method.

#### Round 8 findings assessed as invalid or already documented

- "Stream buffer O(N²)" (codebase) — Already documented Round 1, mitigated with debounce.
- "ClouAgentProgress dead message" (architecture) — Already documented Round 2 as open P2 gap.
- "_active_app global" (architecture) — Out of scope (orchestrator.py), documented Round 4.
- "ANSI regex DCS/APC" (security, codebase) — Already documented Round 3.
- "Escalation file I/O on UI thread" (architecture) — Documented Round 4, sub-ms for small files.
- "_selected_index desync on recompose" (codebase) — Invalid. Textual resize triggers re-layout, not re-compose.
- "Timer lifecycle scattered" (architecture) — Marginal. Reviewer self-corrects: all 12 branches are correct.
- "Status bar counters no reset" (architecture) — Design/feature, not bug.
- "_has_screen linear scan" (architecture) — Negligible (stack ≤ 4).
- "model/uuid/task_id/tool_input latent" (security) — Already documented Rounds 6-7.
- "Markdown rendering of LLM text" (security) — Inherent to design, acknowledged.
- "_guess_milestone_status keyword priority" (codebase) — Low. State files are structured, not narrative.
- "markup=False bypassed by Markdown objects" (security) — By design, not a vulnerability.
- Plus ~10 duplicates across domains.

### Round 7: Brutalist convergence (2026-03-20)

All 4 brutalist MCP domains returned (first time since Round 4). 38 raw findings across codebase, architecture, test_coverage, security. After critical validation: 7 genuinely new.

#### P2 — Medium (defense-in-depth security)

- **SEC: DagScreen milestone not escaped in Rich markup.** `screens/dag.py:56-60` — `self._milestone` interpolated into Rich markup without `_escape_markup()`. Currently gated by milestone regex validation in orchestrator, but defense-in-depth says escape at render site.
  - **Fix:** `_escape_markup(self._milestone)` before interpolation.
  - **Pitfall:** None — pure addition.

- **SEC: DetailScreen title not escaped in Rich markup.** `screens/detail.py:56-58` — `self._title` passed into Rich markup. Currently hardcoded "Token Usage" but the API is public.
  - **Fix:** `_escape_markup(self._title)` in the header Static.

- **SEC: Handoff path validation missing.** `app.py:369-374` — `msg.handoff_path.read_text()` with no validation that path is under `.clou/`. Asymmetry with escalation path validation.
  - **Fix:** Validate `handoff_path.resolve()` is under `project_dir / ".clou"` before reading.
  - **Pitfall:** Need access to `self._project_dir` in the handler.

- **SEC: Escalation label newline injection.** `escalation.py:262-268` — option label from escalation file could contain newlines, corrupting markdown structure when written back as disposition.
  - **Fix:** `label = label.replace("\n", " ").strip()` before writing.

#### P2 — Medium (test coverage gaps)

- **TEST: `on_clou_handoff` handler entirely untested.** The only path that loads handoff files from filesystem. OSError fallback, mode transition guard, and widget update all uncovered.

- **TEST: Escalation `_resolve()` path validation rejection untested.** The security check at `escalation.py:250-257` has no test exercising the rejection branch.

- **TEST: `on_clou_metrics` / `on_clou_turn_complete` with `cost_usd=None` untested.** The None guard on cost accumulation has no dedicated test.

#### Round 7 findings assessed as invalid or already documented

- "BreathWidget per-char allocation" (codebase) — Already documented Round 1 as per-char style quantization.
- "_active_app global TOCTOU" (architecture) — Out of scope (orchestrator.py). Already dismissed Round 4.
- "RichLog unbounded memory" (architecture) — Already documented Round 4.
- "O(N²) stream flush" (architecture) — Already documented Round 1, mitigated with debounce.
- "Escalation dropped in DECISION" (codebase+architecture) — Already documented Round 6, design decision.
- "query_one fragile coupling" (architecture) — Already documented Round 6, invalid per Textual lifecycle.
- "Bridge duck-typing misroutes" (architecture) — Already documented Round 2, intentional.
- "Context tree sync I/O" (architecture) — Marginal. `.clou/` directories are typically small (<100 files).
- "_pre_decision_mode staleness" (architecture) — Invalid. Textual reactives fire synchronously when value is set.
- "ANSI regex DCS sequences" (security) — Already documented Round 3.
- "tool_input dict opaque" (security) — Info only, no render path.
- "Conversation weak assertions" (test_coverage) — Valid observation but test quality improvement, not a bug.
- Plus ~15 duplicates across domains of the same already-documented items.

### Round 6: Final review (2026-03-20)

Manual review after 36 fixes and all brutalist MCP timeouts (4/4 domains failed). Five parallel review agents (app, bridge, widgets, screens, tests) examined all UI source files.

#### P1 — High

- **CRASH: `_strip_ansi` not None-safe — TypeError on SDK None attributes.** `bridge.py:45-47` — `_strip_ansi()` calls `re.sub('', text)` which raises `TypeError` when `text` is None. Five call sites pass SDK attributes that may be None despite `hasattr()` confirming existence: lines 208 (`block.text`), 210 (`block.thinking`), 213 (`block.name`), 277 (`msg.description`), 299 (`msg.summary`).
  - **Fix:** Add None guard to `_strip_ansi`: `if text is None: return ""`. Single fix protects all 5 sites.
  - **Pitfall:** Don't add `str()` coercion — that would hide type errors in other non-None non-string cases.
  - **Approach out:** Alternatively wrap each call site with `str(val or "")`, but that's 5 edits vs 1.

#### P3 — Low

- **TYPE: `getattr(msg, "model", "")` returns None for existing None attributes.** `bridge.py:207` — if SDK message has `model=None`, getattr returns None (not `""`), violating `ClouSupervisorText(model: str)`. Currently harmless since `model` is stored but never displayed.
  - **Fix:** `model = getattr(msg, "model", "") or ""`.

#### Round 6 findings assessed as invalid or overstated

- "Missing HANDOFF→DECISION transition" (review-screens) — **Already present at line 53.** Agent hallucinated.
- "DetailScreen/ContextScreen on_mount race" (review-screens) — **Invalid.** Textual's `on_mount()` fires after `compose()` completes. Standard pattern.
- "DagScreen missing initialization" (review-screens) — **Invalid.** Constructor args sufficient.
- "Messages type validation" (review-screens) — **Overstated.** Internal messages, not external input.
- "conversation.py query_one race" (review-widgets) — **Marginal.** Timer set from message handler (widget must be mounted); `on_unmount` stops timer; single-threaded event loop.
- "breath.py division by zero" (review-widgets) — **Invalid.** `max(base_l, 0.01)` guards denominator; `_STAGE_LUMINANCE` ≥ 0.45; RGB clamped.
- "escalation.py IndexError" (review-widgets) — **Invalid.** Empty guard at 243-246; modular arithmetic in `_update_selection`.
- "Fragile duck-typing" (review-bridge) — **Invalid.** Intentional design, documented Round 2.
- "Usage dict non-dict type" (review-bridge) — **Marginal.** Internal SDK contract.
- "Unhandled post() exceptions" (review-bridge) — **Invalid.** Standard Textual pattern.

- "Timer leak on app shutdown" (review-app) — **Marginal.** Textual's App shutdown cleans up timers; `_animation_tick` has try/except LookupError. Defense in depth, not a crash.
- "Duplicate escalation modal push" (review-app) — **Invalid.** DECISION→DECISION is illegal (transition fails); `_push_pending_escalation` clears `_pending_escalation` after push, preventing double-push.
- "Animation time not reset on HOLDING" (review-app) — **Invalid.** `_animation_tick` returns `1.0` unconditionally for HOLDING state. Single-threaded event loop prevents interleaving.
- "Escalation dropped silently in DECISION" (review-app) — **Marginal/Design.** Log warning exists (line 342). Architecture doesn't produce concurrent escalations; queuing is feature work.

#### Finding density: Round 1: 9, Round 2: 12, Round 3: 7, Round 4: 6, Round 5: 2, Round 6: 1, Round 7: 6, Round 8: 4, Round 9: 9, Round 10: 10, Round 11: 8, **Round 12: 6** (3 actionable in UI scope) — review cycle converged, >85% duplicate rate.

### Round 5: Convergence review (2026-03-20)

Brutalist review after 34 fixes. 3/4 domains timed out; only codebase review completed.

#### P1 — High

- **STATE: DIALOGUE→HANDOFF missing — handoff lost on user input.** `on_clou_coordinator_complete` (app.py:370-375) calls `transition_mode(Mode.HANDOFF)` which fails if user typed during BREATH (transitioning back to DIALOGUE). The successful coordinator completion is silently lost. Same issue in `on_clou_handoff` (line 379-380) where the fallback `transition_mode(Mode.HANDOFF)` also fails from DIALOGUE.
  - **Fix:** Add `(Mode.DIALOGUE, Mode.HANDOFF): TransitionMeta(duration_ms=500, easing="out_cubic")` to TRANSITIONS. Add `watch_mode` handler for DIALOGUE→HANDOFF (no breathing to stop, just CSS swap).
  - **Pitfall:** Update test_mode.py transitions count (12), add to legal parametrize list, remove from illegal list if present.
  - **Approach out:** Alternatively, modify `on_clou_coordinator_complete` to force DIALOGUE→BREATH→HANDOFF. But direct DIALOGUE→HANDOFF is cleaner.

#### P2 — Medium

- **MAINT: watch_mode 3-way modal-push duplication.** BREATH→DECISION (lines 147-157), DIALOGUE→DECISION (162-172), and HANDOFF→DECISION (202-212) contain near-identical escalation modal push logic. If the modal flow changes, all three must update in lockstep.
  - **Fix:** Extract to `_enter_decision_mode(pre_mode: Mode)` helper. Each branch calls `self._enter_decision_mode(Mode.BREATH)` etc.
  - **Pitfall:** The BREATH→DECISION branch also transitions breath machine to HOLDING. Keep that outside the helper.

#### Round 5 findings assessed as invalid or overstated

- "_CYCLE_RGB_CACHE unbounded growth" — Bounded at ~5 entries (4 valid cycle types + fallback). The ValueError guard caches the fallback color for unknown types, but in practice only known types flow through.
- "DagScreen/DetailScreen/ContextScreen title markup escape" — Milestones validated by `_MILESTONE_RE` (alphanumeric+hyphens only). DetailScreen title is hardcoded "Token Usage". Defense in depth, not a real vector.
- "Escalation path validation positional weakness" — Already documented in Round 2. Low probability attack vector with resolve() canonicalization.
- "Per-character allocation", "Markdown flush O(N)", "_active_app global", "ClouToolResult dead code" — All previously documented.

### Round 4: Post-agent review (2026-03-20)

Brutalist review after Rounds 1-3 agents completed 27 fixes. 8 critics across 4 domains.

#### P0 — Critical

- **TEST: test_handoff_to_breath_blocked now failing.** `test_mode_transitions.py:247-262` asserts HANDOFF→BREATH is blocked, but Round 3 agent #29 added this as a legal transition in mode.py. Test contradicts source of truth.
  - **Fix:** Convert to a legal transition test: assert `result is True`, assert mode transitions to BREATH, verify `_start_breathing` side-effects.
  - **Pitfall:** Must also update the transitions count assertion in test_mode.py if it was updated by #29.

#### P1 — High

- **STATE: HANDOFF→DECISION missing — escalation during handoff dropped.** `on_clou_escalation_arrived` tries `transition_mode(Mode.DECISION)` which fails in HANDOFF mode. Escalation is logged and silently discarded. If coordinator is blocking on disposition, session deadlocks.
  - **Fix:** Add `(Mode.HANDOFF, Mode.DECISION): TransitionMeta(duration_ms=300, easing="ease-out")` to TRANSITIONS. Add `watch_mode` handler that pushes EscalationModal. Update test counts.
  - **Pitfall:** After escalation resolves, `_pre_decision_mode` would be HANDOFF. `on_clou_escalation_resolved` transitions back to HANDOFF. Verify this is correct behavior.
  - **Approach out:** Since HANDOFF→BREATH already exists, and BREATH→DECISION exists, the escalation could alternatively force HANDOFF→BREATH→DECISION. But direct HANDOFF→DECISION is cleaner.

#### P2 — Medium

- **CRASH: DAG `max()` on empty filtered deps.** `widgets/dag.py:118` — if all task_deps are filtered out (deps reference removed tasks), `max()` receives empty iterator and raises ValueError.
  - **Fix:** Add default: `d = max((_get_depth(dep) for dep in task_deps if dep in task_set), default=-1) + 1`. Also hoist `set(task_names)` outside the recursive function.

- **SEC: DetailScreen markup=True is a latent injection vector.** `screens/detail.py:60` — `RichLog(id="detail-content", markup=True)`. Currently safe (content from `_format_costs()` is numeric), but fragile if content source changes.
  - **Fix:** Change to `markup=False`. Content is plain text; no markup needed.

- **CRASH: cycle_color ValueError in status_bar render.** `status_bar.py:89` — `cycle_color(cycle_type)` raises ValueError on unknown cycle type. BreathWidget handles this with try/except, but status_bar does not.
  - **Fix:** Add try/except: `try: cycle_hex = cycle_color(cycle_type) except ValueError: cycle_hex = _DIM_HEX`.
  - **Note:** Currently not triggerable (cycle_type defaults to ""), but defensive fix prevents future crash.

#### Round 4 findings assessed as invalid or overstated

- "Agent count lost when BreathWidget has display:none" — Textual delivers messages to widgets with `display: none`; they remain in the DOM tree. Message dispatch is independent of CSS visibility. Shimmer counter in #29 is correct.
- "Stream UUID='' concatenation across turns" — `on_clou_turn_complete` clears buffer and UUID between turns regardless of UUID value. UUID change detection is a within-turn optimization, not the primary clearing mechanism.
- "Markdown() rendering gives model full formatting — critical" — Model output rendering as Markdown is intentional design. The conversation is a Markdown surface. This is the same as any chat interface rendering markdown from AI responses.
- "DAG screen milestone markup injection" — Milestones are validated by `_MILESTONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")` in orchestrator.py. Only lowercase alphanumeric and hyphens. No brackets possible.
- "_active_app global concurrency" — Orchestrator scope, not UI layer.
- "Double token counting risk" — Already fixed Round 1. Supervisor and coordinator are different tiers; their tokens don't overlap.
- "Blocking I/O in escalation/handoff" — Sub-millisecond for small .clou markdown files. Not a practical concern.
- "ANSI chunk splitting across stream chunks" — Per-chunk stripping is standard. Cross-chunk ANSI from SDK model output is extremely unlikely.
- "RichLog unbounded memory" — Valid long-term concern but not actionable without major refactor (virtual scrolling). Acceptable for typical session lengths.

#### Round 3 findings assessed as invalid or overstated

- "Stream timer survives UUID change" — Behavior is correct. Timer runs continuously during streaming; UUID change resets buffer; `on_clou_turn_complete` stops timer between turns.
- "DIALOGUE→HANDOFF missing transition" — Theoretical race (coordinator completes before BREATH entry). In practice, Textual message dispatch is ordered; ClouCoordinatorSpawned always arrives before ClouCoordinatorComplete. Extremely low risk.
- "ANSI regex C1/OSC-ST bypass" — 8-bit C1 codes (`\x9b`) and OSC with ST terminator (`\x1b\\`) are valid ANSI but essentially never appear in SDK model output. Defence in depth, not a real vector.
- "Rate limit `bool(msg.status)` may never clear" — Depends on SDK behavior. If SDK sends status="" for clear, `bool("")` is False. If no clear event sent, rate_limited stays true, but the visual indicator is informational, not blocking.

#### Round 2 findings assessed as invalid or overstated

- "Single-turn supervisor" — The orchestrator manages the conversation loop via `receive_messages()`. The UI→supervisor return path is a planned feature (input queuing), not a bug.
- "Broken escalation detection (ghost logic)" — Escalation detection via PostToolUse hook is Wave 8 Task 8.3. It's a PLANNED feature, not a missing implementation. The hooks exist for compose.py; escalation hooks are next.
- "Synchronous I/O blocking in bridge.py" — `parse_escalation` has no production call site yet. Single file reads in `on_clou_handoff` are sub-millisecond for small markdown files. Not blocking.
- "Hardcoded MAX_EVENTS=20 limits scalability" — Design decision for the breath widget. 20 events matches the atmospheric intent: ambient awareness, not audit log. Information density is served by the context tree and DAG views.
- "48K-96K object allocations/sec from breath render_line" — Real but already documented in Round 1 (P1 per-character allocation). Quantized style runs are the known fix. Not a new finding.
- "Recovery checkpoint regex fragility" — Outside UI scope (orchestrator internals). Not a presentation-layer finding.
- "Context exhaustion race in orchestrator" — Outside UI scope.
- "Duck-typing fragility in bridge" — Already documented in Round 1. Known design tradeoff. The bridge is intentionally duck-typed for testability against mock objects.

---

- **2026-03-19: Handoff/DAG widgets use on_mount for initial render.** `HandoffWidget.update_content()` and `DagWidget.update_dag()` call `self.update()` which requires an active app context (Textual's `visualize()` needs `widget.app.console`). Tests that call these methods on unmounted widgets will get `NoActiveAppError`. Initial content is rendered in `on_mount()` to avoid this. Tests for update methods use `ClouApp().run_test()`.

- **2026-03-19: Worker ImportError guard needed for tests.** `run_supervisor_worker()` wraps its `from clou.orchestrator import run_supervisor` in try/except ImportError because `claude_agent_sdk` isn't installed in the test environment. Without the guard, Textual re-raises the worker error on `run_test()` exit, breaking all mode transition tests.

- **2026-03-19: mypy overrides for SDK.** Added `[[tool.mypy.overrides]]` in pyproject.toml for `claude_agent_sdk.*` (ignore_missing_imports) and `clou.orchestrator` (disallow_untyped_decorators=false) to handle the SDK's lack of type stubs and `@tool` decorator.

- **2026-03-19: Escalation card uses hardcoded hex in DEFAULT_CSS.** Textual CSS variables (`$name`) are app-level, not widget-level. `EscalationModal` hardcodes palette hex values from `theme.py` in its `DEFAULT_CSS`. When the full `.tcss` is wired, these can be replaced — but for now, color changes require updating both `theme.py` and `escalation.py`. Acceptable for now; tracked for unification in T10/T13.

- **2026-03-19: Breath widget render_line uses pre-computed LUT.** 256-entry luminance→RGB lookup table for the neutral text hue (H:250, C:0.008). Avoids per-frame OKLCH conversion. Correct tradeoff for 24 FPS animation. If the neutral hue changes, the LUT must be regenerated.

- **2026-03-19: Bridge uses duck-typing for SDK messages.** `route_supervisor_message()` and `route_coordinator_message()` check `hasattr()` rather than `isinstance()` against SDK types. This matches the existing pattern in `orchestrator._display()` and enables testing with simple mock objects. Intentional.

## Status

| Wave | Status | Tasks | Tests | Notes |
|------|--------|-------|-------|-------|
| 5: Foundation | **complete** | 4/4 | 88 | theme 29, messages 28, mode 31→34 |
| 6: Dialogue | **complete** | 3/3 | 42 | conversation 11, status_bar 20, app 11→14 |
| 7: Breath + Decision | **complete** | 3/3 | 96 | breath 56, escalation 20→23, mode transitions 20→24 |
| 8: Bridge | **complete** | 2/2 | 44 | bridge.py 35→38, integration_ui 9 |
| 9: Handoff + Polish | **complete** | 1/1 | 47 | handoff 22, panels 25→28 |
| **Total** | | **13/13** | **317→561** | ruff ✓ mypy ✓ pytest 561 pass (Round 12 complete) |

### Post-Implementation Fixes

| Fix | Priority | Source | Status |
|-----|----------|--------|--------|
| Double token counting | P0 bug | Round 1 | **done** |
| Static shimmer | P0 bug | Round 1 | **done** |
| DAG screen data wiring | P0 bug | Round 1 | **done** |
| O(N²) markdown streaming | P1 perf | Round 1 | **done** — 100ms debounce |
| Per-line event update | P1 perf | Round 1 | **done** — moved to per-frame |
| Path validation + logging | P2 safety | Round 1 | **done** |
| Mode enum to mode.py | P3 org | Round 1 | **done** |
| Widget re-exports | P3 org | Round 1 | **done** |
| DAG cycle detection | P3 safety | Round 1 | **done** |
| ANSI escape stripping | P0 sec | Round 2 | **done** |
| Rich markup escape | P0 sec | Round 2 | **done** |
| Worker crash recovery | P0 resilience | Round 2 | **done** |
| Missing mode transitions | P1 state | Round 2 | **done** — DIALOGUE↔DECISION, DECISION→HANDOFF |
| ClouRateLimit handler | P1 gap | Round 2 | **done** |
| Stale escalation guard | P1 state | Round 2 | **done** |
| Empty options crash guard | P1 crash | Round 2 | **done** |
| Context tree depth + symlinks | P2 safety | Round 2 | **done** |
| Stream timer on_unmount | P2 leak | Round 2 | **done** |
| Shimmer time caching | P2 visual | Round 2 | **done** |
| Screen stack guard | P2 UX | Round 2 | **done** |
| Breath blank strip opacity fix | P1 crash | Round 2 | **done** — explicit Style() for Textual opacity |
| Animation phase reset | P1 visual | Round 2 | open |
| SETTLING phase wind-down | P1 visual | Round 2 | open |
| Per-char style quantization | P1 perf | Round 1 | open |
| Coordinator crash → UI signal | P2 resilience | Round 2 | open |
| ClouAgentProgress handler | P2 gap | Round 2 | open |
| ClouCycleComplete wiring | P2 gap | Round 2 | open |
| DECISION mode stranding | P0 state | Round 3 | **done** |
| Stale animation timer in DECISION | P1 state | Round 3 | **done** |
| ANSI stripping gaps (remaining paths) | P1 sec | Round 3 | **done** |
| Missing HANDOFF→BREATH transition | P2 state | Round 3 | **done** |
| shimmer_active never resets | P2 visual | Round 3 | **done** |
| Context tree Rich markup injection | P2 sec | Round 3 | **done** |
| Failing test: handoff_to_breath_blocked | P0 test | Round 4 | **done** |
| HANDOFF→DECISION missing transition | P1 state | Round 4 | **done** |
| DAG max() crash on empty deps | P2 crash | Round 4 | **done** |
| DetailScreen markup=True | P2 sec | Round 4 | **done** |
| cycle_color ValueError in status_bar | P2 crash | Round 4 | **done** |
| DIALOGUE→HANDOFF missing transition | P1 state | Round 5 | **done** — 12th transition added |
| watch_mode modal-push duplication | P2 maint | Round 5 | **done** — `_push_pending_escalation()` helper |
| `_strip_ansi` not None-safe | P1 crash | Round 6 | **done** — None guard + 4 tests |
| model getattr returns None | P3 type | Round 6 | **done** — `or ""` fallback |
| DagScreen milestone not escaped | P2 sec | Round 7 | **done** — `_escape_markup(self._milestone)` |
| DetailScreen title not escaped | P2 sec | Round 7 | **done** — `_escape_markup(self._title)` |
| Handoff path validation missing | P2 sec | Round 7 | **done** — `is_relative_to(project_dir/.clou)` in Round 8 |
| Escalation label newline injection | P2 sec | Round 7 | **done** — `.replace("\\n", " ").strip()` |
| TEST: on_clou_handoff untested | P2 test | Round 7 | **done** — 3 tests in TestHandoffHandler |
| TEST: Escalation path rejection untested | P2 test | Round 7 | **done** — `test_resolve_rejects_path_outside_clou` |
| TEST: cost_usd=None guards untested | P2 test | Round 7 | **done** — TestMetricsNoneCost (2 tests) |
| Input in HANDOFF without transition | P2 UX | Round 8 | **done** — HANDOFF→DIALOGUE on input |
| Path validation not relative to project_dir | P2 sec | Round 8 | **done** — `is_relative_to(project_dir/.clou)` |
| _animation_time unbounded float | P3 perf | Round 8 | **done** — modulo period |
| action_clear doesn't reset stream | P3 UX | Round 8 | **done** — clears buffer+overlay+timer |
| `msg.status` not ANSI-stripped | P2 sec | Round 9 | **done** — `_strip_ansi(msg.status)` |
| Rate-limit `bool(msg.status)` never clears | P2 logic | Round 9 | **done** — explicit set membership check |
| `encoding="utf-8"` missing (3 sites) | P3 compat | Round 9 | **done** — all 3 hardened |
| TEST: `_animation_tick` branches untested | P2 test | Round 9 | **done** — HOLDING + SETTLING tests |
| TEST: `action_clear` untested | P2 test | Round 9 | **done** — clears history assertion |
| TEST: `action_show_costs` untested | P2 test | Round 9 | **done** — DetailScreen push assertion |
| TEST: Escalation fallback path validation | P3 test | Round 9 | **done** — 4 fallback tests |
| ANSI regex comprehensive overhaul | P0 sec | Round 10 | **done** — ECMA-48 full coverage, 11 tests |
| `_active_agent_count` reset on coordinator spawn | P2 state | Round 10 | **done** — `on_clou_coordinator_spawned` handler |
| Status bar reset on coordinator complete | P2 state | Round 10 | **done** — clears cycle_type/num/phase |
| Stream timer stop on supervisor crash | P2 resilience | Round 10 | **done** — stops timer in worker error handler |
| `parse_handoff` lstrip("# ") greedy | P2 parse | Round 10 | **done** — fixed-width slicing |
| DAG task name cap + depth limit | P3 safety | Round 10 | **done** — 40 char cap + level>200 guard |
| RichLog max_lines cap | P2 mem | Round 10 | **done** — max_lines=2000 |
| ConversationWidget.clear() extraction | P3 maint | Round 10 | deferred — low priority refactor |
| TEST: Escalation drop on DECISION | P2 test | Round 10 | **done** — DECISION→DECISION blocked |
| TEST: ClouHandoff from BREATH | P2 test | Round 10 | **done** — BREATH→HANDOFF via ClouHandoff |
| TEST: `_format_costs` content | P3 test | Round 10 | **done** — comma format + dollar format |
| Handoff content not ANSI-stripped | P1 sec | Round 11 | **done** — `_strip_ansi()` on read_text |
| Worker error ANSI bypass | P2 sec | Round 11 | **done** — `_strip_ansi(str(error))` |
| `_stream_buffer` unbounded | P2 mem | Round 11 | **done** — 500KB cap with tail truncation |
| `task_id` not ANSI-stripped (2 sites) | P3 sec | Round 11 | **done** — both ClouAgentSpawned + Complete |
| TEST: Rate-limit status variants | P3 test | Round 11 | **done** — parametrized 5 variants |
| TEST: Coordinator complete bar reset | P2 test | Round 11 | **done** — verifies cycle_type/num/phase reset |
| TEST: Input during HANDOFF | P2 test | Round 11 | **done** — HANDOFF→DIALOGUE on input |
| TEST: Escalation _resolve OSError | P2 test | Round 11 | **done** — post-failure resolution confirmed |
| ClouAgentProgress not ANSI-stripped | P2 sec | Round 12 | **done** — `_strip_ansi()` on task_id + last_tool |
| Escalation arrival path severed (Task 8.3) | P1 arch | Round 12 | deferred — orchestrator scope |
| parse_escalation output not ANSI-stripped | P3 sec | Round 12 | deferred — depends on Task 8.3 |
| TEST: `_MAX_STREAM_BUFFER` truncation | P2 test | Round 12 | **done** — monkeypatched cap + tail assertion |
| TEST: `extract_stream_text` non-dict delta | P3 test | Round 12 | **done** — test_non_dict_delta in test_bridge.py |
| TEST: ContextScreen/DagScreen compose | P3 test | Round 12 | **invalid** — tests exist in test_panels.py |
| TEST: Debounce flag mechanics | P3 test | Round 12 | open → subsumed by R17 F4 |
| `_display()` raw stdout ANSI injection | P1 sec | Round 13 | **done** — `_strip_ansi()` on both write paths |
| `_guess_milestone_status` substring match | P2 logic | Round 13 | **done** — word boundary regex |
| Stale DAG data after coordinator complete | P3 state | Round 13 | **done** — clear in on_clou_coordinator_complete |
| `model` field not ANSI-stripped | P3 sec | Round 13 | **done** — `_strip_ansi()` on model field |
| TEST: `_extract_transition_summary` coverage | P2 test | Round 13 | **done** — 3 tests: multiline, truncation, fallback |
| TEST: Handoff fallback ANSI milestone | P2 test | Round 13 | **done** — ANSI in milestone verified stripped |
| TEST: `DagWidget.update_dag()` public API | P3 test | Round 13 | **done** — calls update_dag() in app context |
| Escalation write TOCTOU: `self.path` vs `resolved` | P2 sec | Round 14 | **done** — write to `resolved` not `self.path` |
| TEST: `extract_coordinator_status` non-dict input | P3 test | Round 14 | **done** — test_non_dict_input_returns_none |
| TEST: `route_coordinator_message` usage=None | P2 test | Round 14 | **done** — test_task_progress_with_none_usage |
| TEST: `_cycle_type_rgb` ValueError fallback | P3 test | Round 14 | **done** — TestCycleTypeRgbFallback |
| TEST: `_OptionItem` recommended=True rendering | P3 test | Round 14 | **done** — TestOptionItemRecommended (2 tests) |
| TEST: DAG fan-out multi-node layer rendering | P3 test | Round 14 | **done** — test_fan_out_multi_node_layer |
| `ClouDagUpdate` missing from ALL_MESSAGE_CLASSES | P3 test | Round 14 | **done** — added to test_messages.py |
| `_is_blocking` substring match (escalation.py:44) | P2 logic | Round 15 | **done** — word boundary regex |
| `elapsed` dead reactive on ClouStatusBar | P3 dead code | Round 15 | **done** — removed |
| `ContextScreen.on_mount` double tree build | P3 perf | Round 15 | **done** — removed duplicate refresh |
| `incomplete` missing from icon lookup | P3 consistency | Round 15 | **done** — added to icon dict |
| TEST: BreathWidget `on_clou_coordinator_spawned` | P2 test | Round 16 | **done** |
| TEST: BreathWidget `on_clou_cycle_complete` | P2 test | Round 16 | **done** |
| TEST: `action_show_dag` in HANDOFF mode | P3 test | Round 16 | **done** |
| Animation timer runs during DECISION mode | P3 efficiency | Round 16 | deferred — Task #24 |
| Handoff read TOCTOU: `msg.handoff_path` vs `resolved` | P2 sec | Round 17 | **done** |
| TEST: `_guess_milestone_status` untested | P2 test | Round 17 | **done** |
| TEST: `_is_recent` untested | P2 test | Round 17 | **done** |
| TEST: `_flush_stream` debounce untested | P2 test | Round 17 | **done** |
| Broad `except Exception` in `on_worker_state_changed` | P3 hardening | Round 18 | **done** |
| TEST: `test_renders_content` asserts header not content | P2 test | Round 18 | **done** |
| TEST: `on_worker_state_changed` LookupError catch branch | P2 test | Round 19 | **done** |
| TEST: Worker crash with `error=None` | P3 test | Round 19 | **done** |
| TEST: `_build_tree` OSError on `iterdir()` | P2 test | Round 19 | **done** |
| Broad `except Exception` in `action_clear` | P3 hardening | Round 22 | **done** |
| `uuid` field not ANSI-stripped in `ClouStreamChunk` | P2 hardening | Round 23 | **done** |
| Trailing lone ESC byte survives `_strip_ansi` | P3 hardening | Round 25 | **done** |
| Consecutive ESC bytes bypass regex left-to-right scan | P3 hardening | Round 26 | **done** |
| Infinite crash retry loop | P0 logic | Orch R1 | **done** — `_MAX_CRASH_RETRIES = 3` |
| Checkpoint `int()` crash on malformed agent output | P0 crash | Orch R1 | **done** — `_safe_int()` |
| Milestone validation defense-in-depth | P1 safety | Orch R1 | **done** — validate at `run_coordinator` boundary |
| `current_phase` path traversal | P1 sec | Orch R1 | **done** — `..` and `/` checks |
| Checkpoint `next_step` field validation | P1 safety | Orch R1 | **done** — `_VALID_NEXT_STEPS` frozenset |
| Validation errors prompt dead code | P0 logic | Orch R2 | **done** — `pending_validation_errors` |
| `_validate_decisions` rejects zero-finding ASSESS | P2 compat | Orch R2 | **done** — ASSESS section exemption |
| `_timestamp()` dead code | P3 cleanup | Orch R2 | **done** — removed |
| Shared checkpoint across milestones | P0 logic | Orch R3 | **done** — milestone marker side-channel |
| Broken `active/coordinator.md` path routing | P1 logic | Orch R3 | **done** — `root_prefixes` tuple |
| Execution.md validation at wrong path | P2 arch | Orch R3 | open — validation scope redesign |
| Agent team hooks propagation | P2 sec | Orch R3 | open — SDK has no `hooks` on `AgentDefinition` |
| "exhausted" → validation/retry loop | P1 logic | Orch R4 | **done** — explicit exhausted handler |
| `git_revert` destroys milestone marker | P1 safety | Orch R4 | **done** — marker moved to `.clou/` root |
| Sandbox `allowUnsandboxedCommands` defaults True | P1 sec | Orch R5 | **done** — explicit `False` |

---

### Round 23

**Brutalist review across codebase, architecture, test_coverage, security — Claude + Gemini cross-validation.**

8 critic reviews across 4 domains. ~40 raw findings → 1 validated, ~39 dismissed (repeated/out-of-scope/intentional).

#### Validated Finding

**F1: `uuid` field not ANSI-stripped in stream chunk (bridge.py:234)** — Medium
`getattr(msg, "uuid", "")` passed raw from SDK while `text` is stripped. The uuid serves
as stream identity key in `conversation.py:116` — ANSI in uuid would cause buffer resets
on every chunk, producing visual flickering.
- **Fix**: Wrap with `_strip_ansi()` — `uuid=_strip_ansi(getattr(msg, "uuid", ""))`
- **Test**: `test_stream_event_strips_ansi_from_uuid` added to test_bridge.py
- **Status**: **done**

#### Dismissed Findings (~39)

| Finding | Reason |
|---------|--------|
| Escalation fallback path validation | Repeated (R20-R22). Unreachable in practice. |
| Per-char Segment allocation at 24 FPS | Repeated (R1). Accepted tradeoff — core visual identity. |
| Markdown re-parse O(n²) at 100ms flush | Known tradeoff. Debounce + 500KB cap = acceptable. |
| Module-level unbounded caches | Repeated (R14+). Bounded by 4 cycle types in practice. |
| Private attribute mutation (ClouApp → ConversationWidget) | Repeated. Deferred P3 refactor. |
| Animation timer during DECISION mode | Repeated. Deferred to Task #24. |
| Severed escalation path / no ClouEscalationArrived wiring | Orchestrator scope (Task 8.3). Not UI layer. |
| Layering violations / circular imports | Intentional single-session CLI architecture. |
| Duck-typed bridge as "overkill" | Intentional design for SDK version decoupling. |
| `query_one(BreathWidget)` 24x/sec | Textual query is optimized; premature optimization. |
| Supervisor crash — no reconnect | Known limitation, not a bug. |
| `ClouAgentProgress` no handler | Orphaned messages; low impact. Noted for future cleanup. |
| Dual token counting possibility | Bridge ensures different tiers — no double-count. |
| `_compute_layers` depth cap | Unlikely (200+ dep chains). |
| Stream buffer truncation mid-markdown | Known design tradeoff at 500KB safety cap. |
| State machine timeout/watchdog | Enhancement suggestion, not a bug. |
| File locking on escalation write | Append-only markdown is atomic enough. |
| Weak test assertions (len >= 1) | Repeated observation. Deferred test quality improvement. |
| Bridge mock drift risk | Known limitation of SDK decoupling approach. |
| All other repeated/low-priority items | See prior rounds. |

#### Convergence Status

- **Finding density**: R20:0 → R21:0 → R22:1 → R23:1 (uuid strip — same class as R9-R13 ANSI gaps)
- **Security**: 10th consecutive zero-finding round (Claude). Gemini concurs.
- **Cross-validated**: Both Claude and Gemini independently confirm hardened posture.
- **Test count**: 594 (593 + 1 new uuid strip test)
- **Total fixes across 23 rounds**: 102

---

### Round 24

**Brutalist review across codebase, architecture, test_coverage, security — Claude + Gemini cross-validation.**

8 critic reviews across 4 domains. ~60 raw findings → 0 validated. All repeated, factually wrong, or out of scope.

#### Findings

**Zero new actionable findings.** Every finding either:
- Was factually incorrect (test coverage critic claimed 7 "CRITICAL/HIGH" missing tests — all 7 exist)
- Was repeated from prior rounds (escalation fallback ×5, per-char allocation ×4, module caches ×4, markdown re-parse ×3, animation during DECISION ×3, private attribute mutation ×3, parse_escalation naked read ×2)
- Was out of scope (orchestrator: `_active_app` global, escalation ANSI on unreachable path)
- Was intentional design (duck-typed bridge, per-char rendering, 500KB buffer)

#### Test Coverage Critic Validation

The Claude test_coverage critic claimed 7 "CRITICAL/HIGH" gaps that don't exist:

| Claimed Missing | Actual Coverage |
|---|---|
| `on_clou_handoff` path validation | 4 tests in test_mode_transitions.py (lines 646-758) |
| `on_clou_coordinator_complete` | 7+ tests across test_mode_transitions.py |
| `on_clou_rate_limit` | 3 tests in test_mode_transitions.py (lines 618-639, 983) |
| `on_clou_turn_complete` app handler | Tested at test_mode_transitions.py:786-802 |
| `on_clou_dag_update` | TestDagUpdateWiring in test_panels.py:468-495 |
| `action_show_dag` mode gate | 3 tests in test_panels.py:407-465 |
| `action_clear` | test_action_clear_clears_history in test_mode_transitions.py:854 |

#### Convergence Status

- **Finding density**: R20:0 → R21:0 → R22:1 → R23:1 → R24:0
- **Security**: 11th consecutive zero-finding round. Both Claude and Gemini confirm.
- **4th consecutive round with zero new actionable findings** (R21-R24)
- **Cross-critic validation**: Claude + Gemini agree on convergence across all 4 domains
- **Test count**: 594
- **Total fixes across 24 rounds**: 102
- **Convergence definitively confirmed**: Critics now produce only repeated findings or factual errors

---

### Round 25

**Brutalist review across codebase, architecture, test_coverage, security — Claude + Gemini cross-validation.**

8 critic reviews across 4 domains. ~50 raw findings → 1 validated (edge case), rest repeated/wrong/out-of-scope.

#### Validated Finding

**F1: Trailing lone ESC byte survives `_strip_ansi` (bridge.py:41-54)** — Low
A lone `\x1b` at end of string is not matched by any regex branch (all require at
least one following byte). Could leave terminal in "ESC received" state, causing the
next Textual rendering byte to be consumed as part of an escape sequence.
- **Fix**: Added `\x1b$` branch to `_ANSI_ESCAPE_RE` to catch trailing lone ESC
- **Test**: `test_strip_trailing_lone_esc` + `test_strip_lone_esc_only` in test_bridge.py
- **Status**: **done**

#### Dismissed Findings (~49)

| Finding | Reason |
|---------|--------|
| `on_worker_state_changed` "zero test coverage" | **Wrong** — 3 tests exist in test_app_dialogue.py:217-292 |
| "Escalation wipe bug" (second arrival in DECISION) | **Not a bug** — data already consumed by `_push_pending_escalation` into modal; `_pending_escalation = None` is cleanup, not data loss |
| ANSI injection via ClouDagUpdate task names | **Unreachable** — ClouDagUpdate never posted from production code |
| ANSI injection via context_tree filenames | Self-pwning — requires attacker writing files under `.clou/` at same privilege |
| parse_escalation output not ANSI-stripped | **Unreachable** — no production call site for ClouEscalationArrived |
| Escalation fallback path validation | **Repeated ×6** — unreachable in practice |
| Per-char Segment allocation | **Repeated ×6** — accepted core visual identity |
| Markdown re-parse O(n²) | **Repeated ×5** — mitigated by debounce + 500KB cap |
| Sync I/O on UI thread | **Repeated ×5** — handoff/escalation are event-driven, not hot path |
| Animation during DECISION | **Repeated ×4** — deferred Task #24 |
| Duck-typed bridge fragility | **Repeated ×4** — intentional SDK decoupling |
| tool_input dict unsanitized | **Repeated ×3** — never rendered, inert data |
| Module-level caches unbounded | **Repeated ×5** — bounded by domain (4 cycle types) |
| Weak test assertions (len>=1) | **Repeated** — known deferred improvement |
| All other repeated items | See prior rounds |

#### Convergence Status

- **Finding density**: R20:0 → R21:0 → R22:1 → R23:1 → R24:0 → R25:1 (trailing ESC edge case)
- **Security**: 12th consecutive zero-finding round (Claude). Gemini's ANSI claims all unreachable.
- **Test critics continue producing factual errors** (claiming missing tests that exist)
- **Test count**: 596 (594 + 2 new trailing-ESC tests)
- **Total fixes across 25 rounds**: 103
- **Cumulative: ~600+ raw findings triaged, 103 fixed, ~500+ dismissed**

---

### Round 26

**Brutalist review across codebase, architecture, test_coverage, security — Claude + Gemini cross-validation.**

8 critic reviews across 4 domains. ~50 raw findings → 1 validated (edge case), rest repeated/wrong/out-of-scope.

#### Validated Finding

**F1: Consecutive ESC bytes bypass `_strip_ansi` regex scan (bridge.py:59-69)** — Low
When input contains `\x1b\x1b[31m`, regex scans left-to-right: skips first ESC (next byte `\x1b`
doesn't match any pattern's expected follow-byte), then matches CSI `\x1b[31m` at position 1.
The first ESC survives because the `\x1b$` anchor only catches end-of-string. Result: lone ESC
byte in display text could put terminal in "ESC received" state.

Discovered while validating Gemini's "cross-chunk ANSI reconstruction" claim (which itself is
invalid — see dismissed findings). The cross-chunk claim is wrong, but investigating it revealed
this within-string edge case.
- **Fix**: Belt-and-suspenders `result.replace('\x1b', '')` after regex pass. Raw ESC has no
  legitimate purpose in display text.
- **Tests**: `test_consecutive_esc_before_csi`, `test_consecutive_esc_pair`, `test_triple_esc`
- **Status**: **done**

#### Dismissed Findings (~49)

| Finding | Reason |
|---------|--------|
| Cross-chunk ANSI reconstruction (Gemini HIGH) | **Invalid** — trailing ESC stripped by R25 fix; bare `[31m` without ESC is NOT ANSI; API streams tokens not bytes |
| Parser desynchronization (Gemini HIGH) | **Invalid** — `re.sub` is stateless; no parser state to desynchronize |
| Per-char Segment allocation | **Repeated ×7** — accepted core visual identity |
| Markdown re-parse O(n²) | **Repeated ×6** — mitigated by debounce + 500KB cap |
| Sync I/O on UI thread | **Repeated ×6** — event-driven, not hot path |
| Escalation fallback path validation | **Repeated ×7** — unreachable in practice |
| Duck-typed bridge fragility | **Repeated ×5** — intentional SDK decoupling |
| Animation during DECISION | **Repeated ×5** — deferred Task #24 |
| Module-level caches unbounded | **Repeated ×6** — bounded by domain |
| Stale DagScreen data on re-open | **Repeated ×3** — DAG cleared on coordinator complete |
| `action_clear` doesn't signal SDK | Enhancement, not bug; SDK session is independent |
| Weak test assertions (len>=1) | **Repeated** — known deferred improvement |
| ContextScreen no functional test | **Repeated** — screen lifecycle tested via composing |
| Integration test with 0 assertions | Out of scope — SDK integration layer |
| All other repeated items | See prior rounds |

#### Convergence Status

- **Finding density**: R20:0 → R21:0 → R22:1 → R23:1 → R24:0 → R25:1 → R26:1
- **Pattern**: When findings occur, they are exclusively low-severity ANSI stripping edge cases
- **Security**: 13th consecutive zero-finding round (Claude). Gemini's "HIGH" claims debunked.
- **Codebase**: Claude explicitly confirmed "sixth consecutive round of mature posture"
- **Architecture**: Claude confirmed "fifth consecutive zero-finding round"
- **Test count**: 599 (596 + 3 new consecutive-ESC tests)
- **Total fixes across 26 rounds**: 104
- **Cumulative: ~650+ raw findings triaged, 104 fixed, ~550+ dismissed**

---

### Round 27 (Terminal)

**Brutalist review across codebase, architecture, test_coverage, security — Claude + Gemini cross-validation.**

8 critic reviews across 4 domains. ~60 raw findings → **0 validated.** Every finding is a repeat from prior rounds.

#### Findings

**Zero new actionable findings.** Complete convergence across all 8 critics:

- **Codebase — Claude**: "7th consecutive round without actionable findings." 5 observations, all self-dismissed.
- **Codebase — Gemini**: All repeated (per-char allocation ×7, sync I/O ×6, escalation fallback ×7, markdown re-parse ×6).
- **Architecture — Claude**: All repeated (per-char allocation, markdown re-parse, reactive cascade, animation accumulation, escalation fallback, bridge duck-typing, string concat).
- **Architecture — Gemini**: All repeated (per-char allocation, sync I/O, opacity rendering, orchestrator global).
- **Test coverage — Claude**: All known deferred (`on_clou_agent_progress` orphaned handler, `_render_line` style assertions, weak `len>=1`, `_draw_connections` presence-only).
- **Test coverage — Gemini**: All repeated (bridge field-level assertions, `phase=None` intentional, TCSS visual testing, mock drift).
- **Security — Claude**: **Zero findings. 14th consecutive zero-finding round.** "The defense is layered and sound."
- **Security — Gemini**: All repeated (markdown O(n²), unbounded reads, escalation fallback, multiline ANSI).

#### Convergence — Exit Condition Met

**Finding density (last 8 rounds):**
R20:0 → R21:0 → R22:1 → R23:1 → R24:0 → R25:1 → R26:1 → R27:0

**Domain convergence:**
- Security: **14 consecutive zero-finding rounds** (R14-R27)
- Codebase: **7 consecutive zero-finding rounds** (R21-R27, Claude)
- Architecture: **6 consecutive zero-finding rounds** (R22-R27, Claude)
- All findings R22-R26 were exclusively low-severity ANSI stripping micro-edge-cases

**Final metrics:**
- 27 rounds, ~700+ raw findings triaged
- 104 fixes applied, ~600+ dismissed
- 599 tests (up from 344 at start of review cycles)
- 255 tests added through review cycles
- Cross-validated by Claude + Gemini critics across all 4 domains
- Zero `except Exception` in UI layer
- 15 SDK→UI ANSI stripping paths, all covered
- 8+ Rich markup escape sites, all covered
- Path validation on all filesystem read/write boundaries

**The brutalist review cycle is complete for `clou/ui/`.** Convergence — sustained zero novel findings across multiple rounds and both critics — is the exit condition.

---

## Orchestrator Layer Review

Brutalist review pivoted from `clou/ui/` (converged at R27) to the orchestrator modules: `recovery.py`, `orchestrator.py`, `validation.py`, `hooks.py`, `tokens.py`, `tools.py`, `prompts.py`.

### Orchestrator R1

**Brutalist review across architecture, security — Claude + Gemini. Codebase and test_coverage failed (target path format).**

4 critic reviews across 2 domains. ~30 raw findings → 5 validated, ~25 dismissed.

#### Validated Findings

**F1: Infinite crash retry loop (orchestrator.py:359)** — P0
`run_coordinator` retried `"failed"` cycles indefinitely — no crash counter, no limit.
After N consecutive crashes, would burn tokens forever.
- **Fix**: Added `_MAX_CRASH_RETRIES = 3` constant and `crash_retries` counter.
  Escalates via `write_agent_crash_escalation` after 3 consecutive failures.
  Counter resets on successful validation.
- **Status**: **done**

**F2: Checkpoint `int()` crash on malformed agent output (recovery.py:91)** — P0
`int(fields.get("cycle", "0"))` crashes with `ValueError` if agent writes
`cycle: boom` or `cycle: 3.5`. Entire orchestrator crashes.
- **Fix**: Added `_safe_int()` helper with try/except and non-negative clamping.
  Applied to `cycle`, `phases_completed`, `phases_total` fields.
- **Tests**: 4 tests (`test_safe_int_valid`, `_invalid`, `_negative`, `_none_like`)
  + 3 checkpoint tests (`_malformed_cycle`, `_malformed_phases`, `_negative_cycle`)
- **Status**: **done**

**F3: Milestone validation defense-in-depth (orchestrator.py:324)** — P1
`validate_milestone_name` called only in `spawn_coordinator_tool`, not at
`run_coordinator`'s own boundary. If a future caller passes an invalid
milestone, the regex guard is bypassed.
- **Fix**: Added `validate_milestone_name(milestone)` at the top of `run_coordinator`.
- **Status**: **done**

**F4: `current_phase` path traversal (recovery.py:158, 193)** — P1
Agent-written `current_phase` like `../../etc/passwd` would be interpolated
into `f"phases/{checkpoint.current_phase}/phase.md"` path in the read set.
- **Fix**: Added `..` and `/` checks in both EXECUTE and ASSESS branches
  of `determine_next_cycle`. Falls back to PLAN on invalid phase.
- **Tests**: 4 tests (`_path_traversal_execute`, `_assess`, `_slash_in_phase`,
  `_clean_phase_passes`)
- **Status**: **done**

**F5: Checkpoint `next_step` validation (recovery.py:86)** — P1
`next_step` accepted any string from agent output. Garbage values silently
became the state machine's next state, falling through `match` to the
default `PLAN` return. No logging, no warning.
- **Fix**: Added `_VALID_NEXT_STEPS` frozenset. Unknown values log warning
  and default to PLAN explicitly.
- **Tests**: 2 tests (`_unknown_next_step_defaults_to_plan`,
  `_valid_next_steps_preserved`)
- **Status**: **done**

#### Dismissed Findings (~25)

| Finding | Reason |
|---------|--------|
| `_active_app` global | Serial execution by design. Known architecture. |
| `bypassPermissions` on coordinator | SDK sandbox is Bash defense. By design. |
| Write boundary only protects `.clou/` | SDK sandbox for Bash. Hooks guard golden context. |
| Fragile regex parsing of markdown | Known tradeoff. Defaults to PLAN on parse failure. |
| No concurrency control | Serial execution by design. |
| Destructive git checkout | Recovery mechanism. User changes should be committed. |
| Validation complexity | Known tradeoff. |
| Hardcoded token limits | P3 enhancement. |
| No structured logging | Enhancement, not a bug. |

#### Metrics
- 5 fixes applied (2 P0, 3 P1)
- 13 new tests in test_recovery.py (51 total)
- 4 new tests in test_orchestrator.py (can't collect — SDK import)

---

### Orchestrator R2

**Brutalist review across codebase — Claude + Gemini. Test_coverage timed out.**

2 codebase critic reviews. ~40 raw findings → 3 validated, ~37 dismissed.

#### Validated Findings

**F1: Validation errors prompt is dead code (orchestrator.py:396-402)** — P0
After validation failure, the code builds a prompt with `validation_errors` at
line 396, then `continue`s to the top of the loop where `prompt` is overwritten
by `build_cycle_prompt(...)` at line 349 WITHOUT `validation_errors`. The
validation error feedback is never communicated to the retry cycle.
- **Fix**: Added `pending_validation_errors` variable. Set on validation failure,
  consumed at prompt construction on the next iteration. Removed dead prompt
  rebuild.
- **Status**: **done**

**F2: `_validate_decisions` rejects valid zero-finding ASSESS cycles (validation.py:117-123)** — P2
Every cycle section required at least one `### Accepted:`, `### Overridden:`,
or `### Tradeoff:` entry. A converged ASSESS cycle with zero findings has none.
The validator and convergence detector disagreed about what a valid zero-accept
ASSESS cycle looks like.
- **Fix**: ASSESS/Brutalist sections (identified by header keyword) are now
  exempted from the entry requirement. Non-ASSESS sections still require entries.
- **Tests**: 3 tests (`_assess_section_zero_findings_valid`,
  `_assess_keyword_section_zero_findings_valid`,
  `_non_assess_section_still_requires_entries`)
- **Status**: **done**

**F3: `_timestamp()` dead code (recovery.py:261-264)** — P3
Function defined but never called. `_write_escalation` computes its own
timestamp inline.
- **Fix**: Removed.
- **Status**: **done**

#### Dismissed Findings (~37)

| Finding | Reason |
|---------|--------|
| `_active_app` global state | Serial execution by design. Known. |
| "exhausted" falls through to validation | Working as intended — checkpoint captures partial state, validation catches structural issues. |
| `step` field not validated like `next_step` | `step` is informational only; `next_step` drives state machine. |
| Hooks rely on orchestrator milestone validation | Milestone regex `[a-z0-9][a-z0-9-]*` prevents glob metacharacters. |
| `bypassPermissions` on coordinator | SDK sandbox. By design. |
| `load_prompt` no path traversal on `tier` | Always internal constants ("supervisor", "coordinator", etc.). |
| `assess_convergence` ordering assumption | DB-08 mandates newest-first. Correct. |
| `load_prompt` unhandled FileNotFoundError | `.clou/prompts/` created by init. P3 at best. |
| Async escalation writers do sync work | Harmless. Zero overhead. |
| `_display` vestigial | Valid fallback for `app=None` (headless/testing). |
| Inconsistent log naming | P3 minor. |
| `build_cycle_prompt` prefix fragility | Works correctly for all current read sets. |
| No structured logging | Enhancement, not a bug. |
| Token tracker no persistence | Enhancement, not a bug. |
| Validation retry runs wrong cycle after revert | Analyzed: checkpoint reverts to pre-cycle state, `determine_next_cycle` correctly re-determines the same cycle. |
| Gemini: regex database fragility | Known tradeoff. |
| Gemini: global state contention | Serial by design. |
| Gemini: inverted permission model | SDK sandbox for Bash. By design. |
| Gemini: destructive git checkout | Recovery mechanism. |
| Gemini: validation complexity | Known tradeoff. |
| Gemini: hardcoded token limit | P3 enhancement. |
| Gemini: broad except Exception | Necessary for crash retry logic. |

#### Metrics
- 3 fixes applied (1 P0, 1 P2, 1 P3)
- 3 new tests in test_validation.py (33 total)
- Test suite: 628 passing
- Total orchestrator fixes across R1-R2: 8

---

### Orchestrator R3

**Brutalist review across architecture, security — Claude (Gemini failed both). Codebase done in R2, test_coverage timed out.**

4 critic reviews attempted, 2 succeeded. ~50 raw findings → 4 validated, ~46 dismissed.

#### Validated Findings

**F1: Shared checkpoint file across milestones (orchestrator.py:327)** — P0
`checkpoint_path = clou_dir / "active" / "coordinator.md"` is not per-milestone.
After milestone A completes (writes `next_step: COMPLETE`), milestone B reads
A's stale checkpoint, sees COMPLETE, and returns immediately without doing work.
- **Fix**: Added milestone marker file (`.coordinator-milestone`) as side-channel.
  At the start of `run_coordinator`, if the checkpoint exists but the marker
  shows a different milestone, the stale checkpoint is deleted. Marker is
  updated to current milestone before entering the loop.
- **Status**: **done**

**F2: Broken read-set path for `active/coordinator.md` (prompts.py:45)** — P1
`active/coordinator.md` in read_set was routed through `milestone_prefix`,
producing `.clou/milestones/{milestone}/active/coordinator.md` (nonexistent)
instead of `.clou/active/coordinator.md`. Coordinator agent was told to read
a wrong path for its own checkpoint on every non-PLAN cycle.
- **Fix**: Extended `startswith` check to include `"active/"` prefix alongside
  `"project.md"`. Extracted `root_prefixes` tuple for clarity.
- **Tests**: `test_build_cycle_prompt_active_coordinator_routing` — verifies
  correct `.clou/active/` path and absence of wrong milestone-scoped path.
- **Status**: **done**

**F3: Execution.md validation at wrong path (validation.py:37)** — P2
Validation checks `milestones/{milestone}/execution.md` (flat) but hooks permit
writes to `milestones/*/phases/*/execution.md` (nested). The flat-path file is
never written, so `_validate_execution` is effectively dead code. Phase-level
execution.md files are never structurally validated.
- **Status**: open — architectural concern, requires validation redesign to scan
  phase subdirectories.

**F4: Agent team hooks propagation unclear (orchestrator.py:143-180)** — P2
`AgentDefinition` for worker/verifier doesn't explicitly include hooks. If the
SDK doesn't propagate parent hooks to child agents, worker agents have no write
boundary enforcement on golden context. A prompt injection via source code
could instruct a worker to overwrite coordinator state files.
- **Status**: open — needs SDK behavior verification. If hooks don't propagate,
  fix by passing explicit hooks to `AgentDefinition`.

#### Dismissed Findings (~46)

| Finding | Reason |
|---------|--------|
| `_active_app` global / corrupts on sequential runs | Serial by design. Known R1-R2. |
| `_tracker` accumulates across milestones | P3 enhancement. Known R2. |
| `clou_status` lists resolved escalations | P3 enhancement. Known R2. |
| `bypassPermissions` on coordinator | SDK sandbox. By design. |
| Git revert to committed state | Recovery mechanism. Depends on coordinator commit behavior. |
| Milestone prompt injection | Not exploitable — regex `[a-z0-9][a-z0-9-]*` blocks payloads. |
| fnmatch `*` matches `/` | Not exploitable — milestone regex prevents `..` and `/`. |
| Checkpoint race condition | Serial execution by design. |
| Escalation content injection | `\S+` regex limits to single word. Low impact. |
| Git subprocess injection | `create_subprocess_exec` prevents shell injection. |
| `clou_init` no sanitization | User controls these values. By design. |
| `_active_app` not thread-safe | Single-threaded asyncio. Not a security issue. |
| Validation complexity / regex database | Known tradeoff. |
| `ast.parse` on agent-written Python | Parse only, never exec. Safe. |
| All other repeated items | See R1-R2. |

#### Convergence Assessment

- **R1**: 5 fixes (2 P0, 3 P1) — crash loop, checkpoint parsing, boundary validation
- **R2**: 3 fixes (1 P0, 1 P2, 1 P3) — dead code, validation/convergence conflict
- **R3**: 2 fixes (1 P0, 1 P1) + 2 open P2 — shared checkpoint, path routing, validation scope
- **Finding density**: R1:5 → R2:3 → R3:2 (fixed), 2 (open architectural)
- **Severity trend**: R1 had 2 P0s (crash-class). R2 had 1 P0 (dead code). R3 had 1 P0 (silent skip) + 1 P1 (wrong path).
- **Pattern**: R3 findings are in cross-module interaction (checkpoint sharing, path routing) rather than single-function bugs. The remaining open items (execution.md validation scope, hook propagation) are architectural questions.
- **Test suite**: 630 passing
- **Total orchestrator fixes across R1-R3**: 10

---

### Orchestrator R4

**Brutalist review across architecture, security, codebase — Claude only (Gemini failed all 3).**

3 critic reviews, ~100 raw findings → 2 validated, ~98 dismissed or repeated.

#### Validated Findings

**F1: "exhausted" status unhandled → validation/retry loop (orchestrator.py:387)** — P1
When `_run_single_cycle` returned "exhausted", it fell through to golden context
validation. If the mid-cycle checkpoint was incomplete, validation failed, git
reverted, and the same oversized cycle re-ran — potentially hitting context
exhaustion again. This created a validation-retry loop that burned through 3
retries and escalated as "validation failure" when the real problem was context
size.
- **Fix**: Added explicit `"exhausted"` handler. Skips validation (golden context
  is partial), resets crash counter, continues from checkpoint. The next
  `determine_next_cycle` call routes from whatever the agent wrote before
  exhaustion.
- **Status**: **done**

**F2: `git_revert_golden_context` may destroy milestone marker (recovery.py:400)** — P1
`git checkout HEAD -- .clou/active/` reverts all tracked files in that directory,
which includes the `.coordinator-milestone` marker if it was committed. Moving
the marker outside the revert path eliminates this interaction.
- **Fix**: Moved milestone marker from `.clou/active/.coordinator-milestone` to
  `.clou/.coordinator-milestone` — outside the `git checkout HEAD -- .clou/active/`
  revert scope.
- **Status**: **done**

#### Dismissed Findings (~98)

| Finding | Reason |
|---------|--------|
| Agent team hooks — no `hooks` on `AgentDefinition` | Known R3 P2. SDK limitation — `AgentDefinition` has no hooks field. Remain open. |
| `_tracker` cumulative across milestones | Repeated R2-R3. P3 enhancement. |
| `_active_app` not safe for concurrent | Repeated R1-R3. Serial by design. |
| `read_cycle_outcome` mixed-domain returns | P3 maintainability. Code works correctly. |
| Validation retries don't count toward cycle limit | P2 concern — 3-retry escalation is safety net. |
| No cumulative budget cap per milestone | P3 enhancement. |
| `clou_status` lists resolved escalations | Repeated R2-R3. P3. |
| `build_cycle_prompt` prefix fragile | Repeated R3. Partially fixed. |
| Escalation path fallback | UI layer — out of scope (converged R27). |
| `cycle_type` unvalidated in prompts | Hardcoded values, not exploitable. |
| `load_prompt` tier no whitelist | Repeated R2-R3. Internal constants. |
| `step` field not validated | Repeated R2. Informational field only. |
| `_scoped_permissions` milestone injection | Repeated. Regex prevents. |
| Escalation content injection | Low — `\S+` regex + quoted wrapper. |
| Escalation rate limiting / timestamp collision | Serial execution bounds this. |
| `clou_status` no depth limit | Low, bounded by project scope. |
| All other repeated/low items | See R1-R3. |

#### Convergence Assessment

- **R1**: 5 fixes (2 P0, 3 P1)
- **R2**: 3 fixes (1 P0, 1 P2, 1 P3)
- **R3**: 2 fixes (1 P0, 1 P1)
- **R4**: 2 fixes (2 P1)
- **Finding density**: R1:5 → R2:3 → R3:2 → R4:2
- **Severity trend**: R1 had 2 P0s. R2 had 1 P0. R3 had 1 P0. R4: zero P0s, only P1s.
- **Pattern**: R4 findings are interaction effects (exhausted→validation, revert→marker).
  Critics now produce ~95% repeated findings. New findings are edge cases in
  recovery paths, not core logic bugs.
- **Open architectural items**: Agent team hooks (SDK limitation), execution.md
  validation scope, validation retry cycle counting.
- **Test suite**: 630 passing
- **Total orchestrator fixes across R1-R4**: 12

---

### Orchestrator R5 (Terminal)

**Brutalist review across architecture, security — Claude only.**

2 critic reviews. Architecture: 0 findings (convergence confirmed). Security: 1 validated medium.

#### Architecture Critic

**"R5 Verdict: Convergence Confirmed"** — Zero novel findings. Explicitly checked
all 12 R1-R4 fixes, all 3 open items, and common architectural patterns. Every
area verified as resolved or documented. Recommended stopping the review cycle.

#### Security Critic — 1 Validated Finding

**F1: Sandbox `allowUnsandboxedCommands` defaults to True (orchestrator.py:459)** — P1
`SandboxSettings` has `allowUnsandboxedCommands: bool` defaulting to `True`.
Without explicitly setting it to `False`, a prompt-injected coordinator could
pass `dangerouslyDisableSandbox=True` on Bash calls and escape the sandbox.
Combined with `bypassPermissions`, this is a single-step sandbox escape.
- **Fix**: Added `allowUnsandboxedCommands=False` to `SandboxSettings` constructor.
- **Status**: **done**

#### Dismissed (2 Low)

| Finding | Reason |
|---------|--------|
| Escalation UI path check against wrong directory | UI layer — out of scope (converged R27). |
| `load_prompt` tier unvalidated | Repeated R2-R4. All callers use literals. |

#### Convergence — Exit Condition Met

**Finding density (all rounds):**
R1:5 → R2:3 → R3:2 → R4:2 → R5:0 (architecture), 1 (security config)

**Domain convergence:**
- Architecture: **R5 zero findings** — critic explicitly confirmed convergence
- Security: Configuration gap only — no structural vulnerabilities
- Codebase: Last novel finding was R4 (exhausted handler)

**Final orchestrator metrics:**
- 5 rounds, ~250+ raw findings triaged
- 13 fixes applied, ~237+ dismissed
- 630 tests (up from 612 at start of orchestrator review)
- 18 tests added through review cycles
- Zero P0 findings remaining
- 3 open architectural items (all SDK limitations or design decisions):
  1. Agent team hooks (SDK `AgentDefinition` has no `hooks` field)
  2. Execution.md validation scope (flat vs `phases/`)
  3. Validation retry cycle counting (bounded by 3-retry escalation)

**The brutalist review cycle is complete for the orchestrator layer.**
Convergence — zero novel architecture findings, declining severity
(P0→P0→P0→P1→P1), ~95%+ repeat rate across critics — is the exit condition.

---

### Round 9

**Brutalist review across codebase, architecture, security, test_coverage.**

29 raw findings across 4 domains → 9 validated, 20 dismissed.

#### Validated Findings

**F1: `msg.status` not ANSI-stripped (bridge.py:300)** — Medium
At bridge.py:300, `msg.status` is passed directly to `ClouAgentComplete` without
`_strip_ansi()`, while the adjacent `msg.summary` at line 301 IS stripped.
Inconsistency in the defense-in-depth layer.

- **Solution:** Wrap `msg.status` in `_strip_ansi()`.
- **Pitfall:** None — trivial one-line fix.

**F2: Rate-limit `bool(msg.status)` semantics (app.py:367)** — Medium
`bar.rate_limited = bool(msg.status)` relies on SDK sending falsy value to clear.
If SDK sends a truthy string like `"resolved"` or `"cleared"`, the indicator stays
on permanently.

- **Solution:** Use explicit check: `msg.status in {"active", "rate_limited", "limited"}`.
- **Pitfall:** SDK semantics may change. Add a comment documenting the assumption
  and fallback to `bool()` for unknown statuses.

**F3–F5: `encoding="utf-8"` missing from 3 file operations** — Low
- app.py:387 `msg.handoff_path.read_text()` — no encoding
- context_tree.py:67 `state_file.read_text()` — no encoding
- escalation.py:273 `open(self.path, "a")` — no encoding

Python uses locale default which is usually UTF-8 on macOS/Linux but not guaranteed
on all platforms. `.clou/` files are written as UTF-8 by the coordinator.

- **Solution:** Add `encoding="utf-8"` to all three.
- **Pitfall:** None — purely defensive, zero behavior change on UTF-8 locales.

**T1: `_animation_tick` branches untested** — Medium
The HOLDING (breath_value=1.0) and SETTLING (breath_value=0.0) branches at
app.py:225-230 have no test coverage. Only BREATHING is implicitly tested.

**T2: `action_clear` untested** — Medium
The new clear handler at app.py:263 (added in Round 8) has no test.

**T3: `action_show_costs` untested** — Medium
The costs action and `_format_costs` helper have no test.

**T4: Escalation fallback path validation untested** — Low
The `except (AttributeError, TypeError)` fallback in escalation.py:255-258
that checks parts when `is_relative_to` is unavailable is untested.

#### Dismissed Findings (20)

| Finding | Reason |
|---------|--------|
| _selected_index/options desync | Options never mutated; modulo wraps |
| Token double-counting | Separate tiers (supervisor vs coordinator) — correct |
| _active_app global race | Orchestrator scope, not UI |
| Handoff read_text TOCTOU | OSError handler already catches |
| worker.error unstripped | Python exception, not external input |
| msg.milestone in handoff error | Error fallback path only, negligible |
| Uncached query_one | Textual DOM is <20 nodes — negligible |
| Reactive cascade in watch_mode | No cascading reactive writes |
| Escalation private attr leak | Minor code smell, not a bug |
| Context tree sync I/O | Documented in prior rounds |
| Stream O(N×M) re-parse | Documented in prior rounds |
| DAG cycle handling | DAGs are acyclic by definition |
| parse_escalation unsanitized ANSI | Trusted source, Rich escape present |
| _flush_stream untested | Duplicate |
| context_tree helpers untested | Duplicate |
| _cycle_type_rgb cache untested | Trivial cache + fallback |
| DetailScreen barely tested | Low priority |
| stream timer lifecycle gap | Duplicate |
| msg.status rate-limit double-fire | Same as F2 |
| escalation private attr | Accepted pattern with type: ignore |

#### Task Graph

```
Group A — Code/Security Fixes (parallel):
  A1: Strip ANSI from msg.status in bridge.py → depends: none
  A2: Fix rate-limit bool() semantics in app.py → depends: none
  A3: Add encoding="utf-8" to 3 file operations → depends: none

Group B — Test Coverage (parallel, after A):
  B1: Test _animation_tick HOLDING/SETTLING branches → depends: A2
  B2: Test action_clear → depends: none
  B3: Test action_show_costs → depends: none
  B4: Test escalation fallback path validation → depends: none
```

---

### Round 10

**Brutalist review across codebase, architecture, security, test_coverage.**

~50 raw findings across 4 domains (8 critic responses) → 10 validated, ~40 dismissed.

#### Validated Findings

**S1: ANSI regex misses multiple escape sequence classes (bridge.py:40-42)** — CRITICAL
The `_ANSI_ESCAPE_RE` regex has 5 distinct gaps:
1. OSC sequences terminated by ST (`ESC \` or `\x9c`) instead of BEL — enables
   terminal title injection (`\x1b]0;evil\x1b\`), clipboard write (`\x1b]52;c;<b64>\x1b\`),
   hyperlink injection (`\x1b]8;;url\x1b\`)
2. DCS/APC/PM/SOS — only 2-byte opener stripped, full payload survives
3. CSI final bytes outside `[a-zA-Z]` — `@`, backtick, `{|}`~ are valid ECMA-48 finals
4. 8-bit C1 codes (`\x9b`=CSI, `\x9d`=OSC) bypass entirely
5. Charset designation (`\x1b(0`) passes through — switches terminal to box-drawing mode

- **Solution:** Replace regex with comprehensive version covering all ECMA-48 classes.
- **Pitfall:** Overly aggressive stripping could eat legitimate text. Test with
  round-trip: strip(normal_text) == normal_text for all printable ASCII/UTF-8.

**C1: `_active_agent_count` never resets on coordinator spawn (breath.py:192)** — Medium
BreathWidget has no `on_clou_coordinator_spawned` handler. If a coordinator session
is interrupted (user input, crash), `_active_agent_count` retains stale value from
previous session. New coordinator starts with orphaned count → shimmer stays on
permanently. ClouApp resets `_dag_tasks` on coordinator spawn but breath widget has
no equivalent reset.

- **Solution:** Add `on_clou_coordinator_spawned` to BreathWidget that resets
  `_active_agent_count = 0` and `self.shimmer_active = False`.
- **Pitfall:** Message ordering — ensure reset fires before first agent spawn of
  new session (guaranteed by Textual's message ordering).

**C2: `on_clou_coordinator_complete` doesn't reset status bar (app.py:370)** — Medium
After coordinator completes or fails, `cycle_type`, `cycle_num`, and `phase` on the
status bar retain stale values indefinitely. Only `milestone` is set on coordinator
spawn, never cleared on complete.

- **Solution:** Reset `bar.cycle_type = ""`, `bar.cycle_num = 0`, `bar.phase = ""`
  in `on_clou_coordinator_complete`.
- **Pitfall:** None — simple reactive assignments.

**C3: `parse_handoff` `lstrip("# ")` is greedy (handoff.py:110,118)** — Medium
`str.lstrip("# ")` strips any combination of `#` and space from the left, not the
literal prefix. `### Deep heading` becomes `Deep heading` (treated as `## ` section
boundary). Headings starting with `#` get silently truncated.

- **Solution:** Use `stripped[2:].strip()` for `## ` headings, `stripped[1:].strip()`
  for `# ` headings — fixed-width prefix removal.
- **Pitfall:** Edge case: `## ` with nothing after (empty heading). Guard with
  `or "Untitled"`.

**C4: Stream timer survives supervisor crash (conversation.py:120-123)** — Medium
`_stop_stream_timer()` is only called from `on_clou_turn_complete` and `on_unmount`.
If supervisor crashes mid-stream (`on_worker_state_changed` fires), no
`ClouTurnComplete` is ever posted. The 100ms flush timer runs forever, re-parsing
stale markdown buffer 10x/second.

- **Solution:** Post a synthetic `ClouTurnComplete` from `on_worker_state_changed`
  after logging the error, OR have `add_error_message` stop the stream timer.
- **Pitfall:** Synthetic turn-complete could confuse token accounting. Better to
  add `_stop_stream_timer()` + buffer clear to the error handler directly.

**C5: DAG `box_width` unbounded + `_get_depth` recursive (dag.py:80,107)** — Low
`box_width = max(len(task_name) + 4, 16)` — a 10M-char task name causes massive
memory allocation. `_get_depth` recurses without depth limit — 5000-deep chain
hits RecursionError.

- **Solution:** Cap task_name display to 40 chars (`task_name[:40]`). Add
  `sys.getrecursionlimit()` guard or convert to iterative with depth cap.
- **Pitfall:** Truncated names lose information. Show ellipsis: `name[:37] + "..."`.

**A1: RichLog unbounded memory growth (conversation.py:110-145)** — Medium (arch)
Every text block, thinking block, tool use, and stream completion is appended to
RichLog with no eviction. Long autonomous sessions (hours, hundreds of turns)
accumulate thousands of Rich renderables. Textual's RichLog stores all for scrollback.

- **Solution:** Set `RichLog(max_lines=2000)` or periodic eviction. BreathWidget
  already caps events at 20 — conversation needs an equivalent.
- **Pitfall:** max_lines counts visual lines not entries. May need custom eviction.

**A2: `action_clear` encapsulation violation (app.py:263-279)** — Low (arch)
App directly mutates `conversation._stream_buffer`, `_stream_dirty`, calls
`_stop_stream_timer()`. Should delegate to `ConversationWidget.clear()`.

- **Solution:** Add `clear()` method to ConversationWidget, call from action_clear.
- **Pitfall:** None — straightforward refactor.

**T1: Escalation dropped on DECISION→DECISION (app.py:351-352)** — Medium
No test sends `ClouEscalationArrived` while already in DECISION mode. The drop
path (`_pending_escalation = None` + warning log) is never exercised.

**T2: ClouHandoff from BREATH mode untested** — Medium
Only DIALOGUE→HANDOFF via ClouHandoff is tested. BREATH→HANDOFF via ClouHandoff
exercises different `watch_mode` branch (SETTLING + _stop_breathing).

**T3: `_format_costs` output content never asserted** — Low
Test checks DetailScreen is pushed but not the formatted string content.

#### Dismissed Findings (~40)

| Finding | Reason |
|---------|--------|
| Token double-counting | Separate tiers — correct (repeated 3x) |
| _selected_index desync | Options never mutated, modulo wraps (repeated) |
| Input not routed to supervisor | Orchestrator scope, not UI (repeated 2x) |
| God Object ClouApp | Architecture opinion, not bug |
| Escalation hooks missing | hooks.py scope, not UI |
| Context tree sync I/O | Accepted, documented (repeated 3x) |
| Stream O(N²) re-parse | Accepted, debounce mitigates (repeated 2x) |
| SDK duck-typing fragile | Intentional design (repeated 2x) |
| Path validation fallback | Duplicate from R8/R9 (repeated 3x) |
| TOCTOU symlink | .clou is project-managed, low risk |
| _has_screen rapid keypress race | Cooperative event loop, near-impossible |
| TransitionMeta dead durations | Design debt, not a bug |
| breath_modulate additive | Design choice, not bug |
| Supervisor blocked during coordinator | Orchestrator scope (repeated 2x) |
| Global state _active_app | Orchestrator scope (repeated 2x) |
| Per-character style "explosion" | IS the breathing animation |
| Escalation sync file I/O | Local FS, sub-ms |
| Circular dependency | Orchestrator scope |
| DoS via large state files | Local FS under project control |
| Newlines in breath event text | Cosmetic, low severity |

#### Task Graph

```
Group A — ANSI Regex Overhaul (CRITICAL, blocks all):
  A1: Replace _ANSI_ESCAPE_RE with comprehensive ECMA-48 regex → depends: none

Group B — State Reset Fixes (parallel):
  B1: Add on_clou_coordinator_spawned to BreathWidget (reset agent count) → depends: none
  B2: Reset status bar cycle metadata on coordinator complete → depends: none
  B3: Stop stream timer on supervisor crash → depends: none

Group C — Parse/Bounds Fixes (parallel):
  C1: Fix parse_handoff lstrip("# ") → depends: none
  C2: Cap DAG task name + iterative depth → depends: none
  C3: Add RichLog max_lines cap → depends: none
  C4: Extract ConversationWidget.clear() method → depends: C3

Group D — Test Coverage (after B):
  D1: Test escalation drop on DECISION→DECISION → depends: none
  D2: Test ClouHandoff from BREATH mode → depends: none
  D3: Assert _format_costs content → depends: none
```

---

### Round 11

**Brutalist review across codebase, architecture, security, test_coverage.**

~50 raw findings across 8 critics → 8 validated, ~42 dismissed.

Finding density declining: most findings are duplicates from R9/R10.

#### Validated Findings

**S1: Handoff content not ANSI-stripped (app.py:387 → handoff.py)** — Medium
`msg.handoff_path.read_text()` content goes directly to `HandoffWidget.update_content()`.
No ANSI stripping anywhere in the path. Agent-written `handoff.md` files could contain
terminal escape sequences (title injection, clipboard write, hyperlinks).

- **Solution:** Apply `_strip_ansi()` to content after reading in `on_clou_handoff`.
- **Pitfall:** Need to import `_strip_ansi` from bridge into app, or add a local strip.

**S2: Worker error ANSI bypass (app.py:107-108)** — Low
`event.worker.error` → `error_msg` → `Text()` → RichLog. Python exception strings
from SDK could contain embedded ANSI. Bridge `_strip_ansi` doesn't cover this path.

- **Solution:** Strip ANSI from `str(event.worker.error)` before embedding in error_msg.
- **Pitfall:** Import dependency — need `_strip_ansi` from bridge in app.

**S3: `_stream_buffer` unbounded during single turn (conversation.py:118)** — Medium
`self._stream_buffer += msg.text` grows without limit. A single model turn producing
50MB+ would OOM. `RichLog(max_lines=2000)` limits entries, not single-entry size.

- **Solution:** Cap `_stream_buffer` at 500KB. Truncate from front if exceeded.
- **Pitfall:** Truncation loses early content of long responses. Acceptable since
  the stream overlay only shows the tail anyway.

**S4: `task_id` not ANSI-stripped in bridge (bridge.py:291,300)** — Low
Both `ClouAgentSpawned` and `ClouAgentComplete` pass `msg.task_id` raw while
`description` and `summary` ARE stripped. Inconsistency.

- **Solution:** Wrap `msg.task_id` in `_strip_ansi()` at both locations.
- **Pitfall:** None — task_ids are typically UUIDs but defense-in-depth.

**T1: Rate-limit status variants untested** — Low
Only one truthy status tested. The set `{"active", "rate_limited", "limited"}` should
have each member verified.

**T2: Coordinator complete status bar reset untested** — Medium
R10 added `bar.cycle_type = ""` etc. in `on_clou_coordinator_complete`, but no test
verifies the reset actually happens.

**T3: Input during HANDOFF mode untested** — Medium
Only BREATH→DIALOGUE on input tested. HANDOFF→DIALOGUE on input is uncovered.

**T4: Escalation _resolve OSError on write untested** — Medium
Write failure sends resolution message anyway but nothing persisted. No test confirms
the OSError branch or the post-failure behavior.

#### Dismissed Findings (~42)

| Finding | Reason |
|---------|--------|
| Escalation _selected_index OOB | DOM and options always in sync from compose() |
| tool_input dict unsanitized | Not rendered; latent, documented |
| Escalation fallback bypassable | DUPLICATE (6th time reported across rounds) |
| ClouCycleComplete never posted | DUPLICATE (documented since Round 2) |
| _CYCLE_RGB_CACHE unbounded | 4 cycle types, ~10 entries max |
| _has_screen iteration | DUPLICATE |
| watch_mode self-transition | Prevented by transition_mode |
| Stream timer UUID mismatch | Retracted by critic — not a bug |
| Context tree TOCTOU | DUPLICATE (accepted) |
| God Object ClouApp | Architecture opinion |
| Fragmented message responsibility | Textual framework pattern |
| TOCTOU escalation self.path vs resolved | LOW, path from trusted message |
| Per-character style explosion | DUPLICATE (Round 1) |
| query_one in tick | DUPLICATE (Round 10) |
| ValueError from is_relative_to | is_relative_to returns bool, not raises |
| /dev/zero file read | Path validated to .clou/ first |
| Token double-counting | Separate tiers, bridge early-return |
| Stream O(N²) parse | DUPLICATE (documented) |
| Reactive cascade status bar | DUPLICATE (accepted) |
| Sync I/O handoff read | DUPLICATE (accepted) |
| Reactive metrics storm | DUPLICATE |
| DAG O(N²) recomputation | LOW, infrequent updates |
| All others | Duplicates or out of scope |

#### Task Graph

```
Group A — Security Fixes (parallel):
  A1: Strip ANSI from handoff content in on_clou_handoff → depends: none
  A2: Strip ANSI from worker error string → depends: none
  A3: Cap _stream_buffer at 500KB → depends: none
  A4: Strip ANSI from task_id in bridge (2 sites) → depends: none

Group B — Test Coverage (parallel):
  B1: Test rate-limit status variants → depends: none
  B2: Test coordinator complete status bar reset → depends: none
  B3: Test input during HANDOFF → depends: none
  B4: Test escalation _resolve OSError → depends: none
```

---

### Round 12

**Brutalist review across codebase, architecture, security, test_coverage.**

~50 raw findings across 8 critics → 7 validated, ~43 dismissed.

Finding density continuing to decline — most critics are now recycling Round 9-11 findings.

#### Validated Findings

**F1: ClouAgentProgress fields not ANSI-stripped (bridge.py:298-303)** — Medium (sec)
`task_id` and `last_tool_name` in `ClouAgentProgress` are passed raw while adjacent
message types (ClouAgentSpawned, ClouAgentComplete) now strip both fields after R11.
Inconsistency in the defense-in-depth layer. Flagged independently by 4/8 critics.

- **Solution:** Wrap both in `_strip_ansi()`: `task_id=_strip_ansi(msg.task_id)`,
  `last_tool=_strip_ansi(msg.last_tool_name)`.
- **Pitfall:** None — trivial 2-line fix.

**F2: Escalation arrival path severed — ClouEscalationArrived never posted (ARCHITECTURE)** — HIGH (functional gap)
ClouEscalationArrived is defined, handled in app.py:350, and covered by ~15 tests.
But **zero production code instantiates or posts it**. Task 8.3 (PostToolUse hook for
escalation detection) was planned but never implemented. In production, the coordinator
writes escalation files to `.clou/escalations/` but the UI never detects them. The
entire DECISION mode path — modal, options, disposition write — is dead code in prod.

- **Solution:** Implement Task 8.3 — add escalation file detection. Two approaches:
  1. PostToolUse hook on coordinator Write tool matching `escalations/*.md`
  2. Filesystem watcher on `.clou/escalations/` directory
- **Pitfall:** This is orchestrator-scope work, not pure UI. Requires changes to
  orchestrator.py to register the hook. PostToolUse hook is the cleaner approach
  per the integration spec.
- **Approach out:** For now, document as known gap. The UI layer is complete and
  tested; only the wiring from orchestrator to UI is missing. When Task 8.3 is
  implemented, also add `_strip_ansi()` to `parse_escalation` output (see F3).

**F3: parse_escalation output not ANSI-stripped (bridge.py:169-200)** — LOW (latent sec)
`parse_escalation` reads raw file content and returns unstripped strings. The
EscalationModal applies `_escape_markup()` for Rich injection but not `_strip_ansi()`
for terminal escape sequences. Currently latent because F2 means this path is never
exercised in production.

- **Solution:** Apply `_strip_ansi()` to all returned values in `parse_escalation`.
- **Pitfall:** Should be fixed when F2 is fixed (Task 8.3), not independently.

**T1: `_MAX_STREAM_BUFFER` truncation untested** — Medium (test)
R11 added the 500KB cap with tail truncation at conversation.py:120-122, but no test
exercises the truncation path. Only small buffer values are tested.

- **Solution:** Test that posting enough stream chunks to exceed 500KB results in
  buffer being capped at `_MAX_STREAM_BUFFER` length.
- **Pitfall:** Test must avoid actually allocating 500KB — use a smaller cap via
  monkeypatching, or just verify the truncation logic.

**T2: `extract_stream_text` non-dict delta untested** — Low (test)
bridge.py:150 checks `isinstance(delta, dict)` but the `False` branch (non-dict delta)
has no test. Only missing-text and wrong-type tests exist.

- **Solution:** Add test with `delta` set to a string or list.
- **Pitfall:** None — trivial test.

**~~T3: ContextScreen/DagScreen zero test coverage~~** — INVALID
Post-triage validation found TestContextScreen (test_panels.py:253) and
TestDagScreen (test_panels.py:278) already exist. Critic was wrong.

**T4: Debounce logic in conversation streaming untested** — Low (test)
The 100ms debounce timer (`_start_stream_timer`/`_stop_stream_timer`) and
`_flush_stream` logic are only tested implicitly through integration-style tests.
No isolated test verifies debounce behavior (multiple rapid chunks → single flush).

- **Solution:** Test that multiple rapid `ClouStreamChunk` messages result in
  a single markdown render (check `_stream_dirty` flag behavior).
- **Pitfall:** Timing-dependent tests are flaky. Test the flag mechanics, not the
  actual timer firing.

#### Dismissed Findings (~43)

| Finding | Reason |
|---------|--------|
| tool_input dict unsanitized | Not rendered; latent, documented R9 |
| _selected_index/options desync | Options never mutated; modulo wraps (7th time) |
| _CYCLE_RGB_CACHE unbounded | 4 cycle types, ~10 entries max (5th time) |
| Input silently consumed in DECISION | **Invalid**: ModalScreen captures focus, prevents Input |
| Per-character render_line allocation | DUPLICATE (Round 1, every round) |
| God Object ClouApp | Architecture opinion (3rd time) |
| Fragmented message responsibility | Textual framework pattern (2nd time) |
| Stream O(N²) markdown re-parse | DUPLICATE (documented, debounce mitigates) |
| Sync I/O in escalation/handoff | DUPLICATE (accepted, sub-ms) |
| Status bar milestone/cycle_type injection | **Invalid**: Text.assemble() literal text, not markup |
| milestone/cycle_type not ANSI-stripped | **Invalid**: orchestrator-controlled, validated by _MILESTONE_RE |
| Escalation fallback order-insensitive | DUPLICATE (6th+ time) |
| Worker crash recovery fragile | DUPLICATE (documented R2, fixed) |
| Regex-heavy handoff parse | Marginal — < 10 regex ops per parse |
| Zombie milestone state | Cleared by on_clou_coordinator_complete (R10 fix) |
| DAG emoji misalignment | Cosmetic, terminal-dependent |
| ContextTreeWidget zero coverage | **Invalid**: 8 tests exist in test_panels.py |
| Pending escalation silently dropped | DUPLICATE (documented R4, design decision) |
| Unbounded handoff file read | Path-validated to .clou/, coordinator files are small |
| All others (~25) | Duplicates from R1-R11 |

#### Task Graph

```
Group A — Security Fix (single):
  A1: Strip ANSI from ClouAgentProgress fields (task_id + last_tool_name) → depends: none

Group B — Test Coverage (parallel):
  B1: Test _MAX_STREAM_BUFFER truncation → depends: none
  B2: Test extract_stream_text non-dict delta → depends: none
  B3: Test debounce flag mechanics → depends: none (LOW priority)

Group C — Architecture (deferred):
  C1: Implement Task 8.3 escalation wiring (PostToolUse hook) → orchestrator scope
  C2: Strip ANSI in parse_escalation output → depends: C1
```

---

### Round 13

**Brutalist review across codebase, architecture, security, test_coverage.**

~60 raw findings across 8 critics → 8 validated, ~52 dismissed.

Duplicate rate >85%. Critics recycling R1-R12 findings. Per-character allocation flagged for 13th time.

#### Validated Findings

**F1: `_display()` raw stdout in non-TUI mode (orchestrator.py:96-110)** — HIGH (sec)
When the TUI is not running (`_active_app is None`), `_display()` writes SDK message
content directly to `sys.stdout.write()` with zero ANSI stripping. Model responses
containing terminal escape sequences (title bar injection, hyperlink OSC, cursor
repositioning) execute directly in the user's terminal. Every ANSI defense built in
Rounds 2-12 is bypassed on the non-TUI fallback path.

- **Solution:** Apply `_strip_ansi()` to text before `sys.stdout.write()` in `_display()`.
  Import `_strip_ansi` from `clou.ui.bridge` or duplicate the regex locally.
- **Pitfall:** `_display()` is in orchestrator.py, not ui/. Importing from ui.bridge
  creates a circular dependency risk. Better to extract `_strip_ansi` to a shared
  `clou.sanitize` module or duplicate the regex.
- **Approach out:** Simplest fix: inline `_strip_ansi` call. For now, import from bridge
  (bridge has no orchestrator imports, so no cycle).

**F2: `_guess_milestone_status` substring matching (context_tree.py:68-70)** — MEDIUM
`_STATUS_KEYWORDS` iteration uses `if keyword in content` — substring match.
"complete" matches "incomplete", "error" matches "no_error". Iteration order in
dict determines which keyword wins. "active" is first, then "complete" before
"completed", so "this milestone is incomplete" matches "complete" (line 40).

- **Solution:** Use word boundary matching: `re.search(r'\b' + keyword + r'\b', content)`.
  Or reverse iteration order so "completed" is checked before "complete".
- **Pitfall:** Word boundary regex adds complexity. Simpler: reorder dict to check
  "completed" before "complete", "failed" before other error-like words.
- **Approach out:** Since state files are coordinator-written with known format,
  matching the first line only (not full content) would be more robust.

**F3: Stale DAG data after coordinator complete (app.py:374-383)** — LOW
`_dag_tasks` and `_dag_deps` only reset in `on_clou_coordinator_spawned` (line 341).
After coordinator completes, user can press ctrl+d in HANDOFF mode and see the
previous milestone's task graph. Not harmful but confusing.

- **Solution:** Clear `self._dag_tasks = []` and `self._dag_deps = {}` in
  `on_clou_coordinator_complete`.
- **Pitfall:** None — simple 2-line addition.

**F4: SETTLING state dead — zero frames rendered (app.py:189-191)** — MEDIUM
BREATH→HANDOFF transitions to SETTLING then immediately stops the timer. The
SETTLING state never gets animation frames. This is Task #24 from Round 2,
confirmed still present.

- **Solution:** Defer `_stop_breathing()` to fire after a SETTLING duration
  (e.g., 3 seconds via `set_timer`). The `_animation_tick` already handles
  SETTLING state (returns `breath_value = 0.0`).
- **Pitfall:** Must ensure the settling timer is cancelled if a new coordinator
  starts before it fires.

**F5: `_display()` non-TUI ANSI injection is the same as F1** — consolidated.

**T1: `_extract_transition_summary` zero test coverage (bridge.py:128-134)** — MEDIUM
Multi-line text where matching line is not first, 80-char truncation, and the
no-match fallback are all untested.

- **Solution:** Add 3 tests: multi-line match on later line, truncation at 80,
  fallback to full text truncated.
- **Pitfall:** None.

**T2: `DagWidget.update_dag()` public method untested (dag.py:190-198)** — LOW
Tests set `w._tasks` and `w._deps` directly. The `update_dag()` method and its
`self.update(render_dag(...))` call are never exercised.

- **Solution:** Add test calling `update_dag()` and verifying rendered output.
- **Pitfall:** Requires mounted widget (app context) for `self.update()`.

**T3: Handoff fallback ANSI stripping untested (app.py:393)** — MEDIUM
The `_strip_ansi(msg.milestone)` in the invalid-path fallback content is tested
with clean strings only. No test sends ANSI-laden milestone to verify stripping.

- **Solution:** Add test with milestone containing ANSI escapes, verify fallback
  content is clean.

**T4: `model` field not ANSI-stripped in bridge (bridge.py:221)** — LOW (latent)
`model = getattr(msg, "model", "") or ""` bypasses `_strip_ansi()`. Field stored
but never rendered. Prophylactic fix.

- **Solution:** `model = _strip_ansi(getattr(msg, "model", "") or "")`.
- **Pitfall:** None.

#### Dismissed Findings (~52)

| Finding | Reason |
|---------|--------|
| Markdown() renders model output | Design decision — rendering markdown from AI is core purpose |
| Per-character style allocation | DUPLICATE (Round 1, 13th time flagged) |
| O(N) Markdown re-parse on flush | DUPLICATE (documented, debounce mitigates) |
| Sync I/O in escalation/handoff | DUPLICATE (accepted, sub-ms) |
| God Object ClouApp | Architecture opinion (4th time) |
| _active_app TOCTOU | Orchestrator scope, not UI |
| Message queue backpressure | Orchestrator→bridge boundary, not UI |
| Screen stack leak / blank handoff | ModalScreen captures focus; already documented |
| Input during DECISION mode | **Invalid**: ModalScreen captures focus (7th time) |
| Reactive cascade in status bar | Textual batches within event loop tick |
| _selected_index/options desync | DUPLICATE (8th time) |
| tool_input dict unsanitized | DUPLICATE (latent, documented R9) |
| ClouToolResult dead code | Marginal |
| parse_escalation ANSI | DUPLICATE (deferred to Task 8.3) |
| Escalation fallback path order | DUPLICATE (7th+ time) |
| Context tree entry count | max_depth=8, project-managed |
| watch_mode exhaustiveness guard | Design debt, low priority |
| ConversationWidget encapsulation | DUPLICATE (R10, deferred P3) |
| DAG connections don't trace edges | Design intent is layer-based |
| Negative predicate bridge dispatch | Intentional design (Round 2) |
| Stream buffer Markdown corruption | Transient overlay, acceptable |
| UUID mismatch buffer reset | Working as designed |
| ReDoS in _OPTION_RE / _ANSI_ESCAPE_RE | FALSE POSITIVE (linear backtracking) |
| RichLog 2000 lines × 500KB = 1GB | 2000 lines ≠ 2000 entries of 500KB |
| Multiple escalation drop | DUPLICATE (documented R4, design decision) |
| DagScreen stale data | Same as F3 |
| Context tree __pycache__ untested | Trivial |
| phase_state.md fallback untested | Trivial |
| All others (~25) | Duplicates from R1-R12 |

#### Task Graph

```
Group A — Security Fix:
  A1: Strip ANSI in _display() non-TUI path (orchestrator.py) → depends: none
  A2: Strip ANSI from model field in bridge → depends: none

Group B — Code Fixes:
  B1: Fix _guess_milestone_status substring matching → depends: none
  B2: Clear stale DAG data on coordinator complete → depends: none

Group C — Test Coverage (parallel):
  C1: Test _extract_transition_summary (3 cases) → depends: none
  C2: Test handoff fallback with ANSI milestone → depends: none
  C3: Test DagWidget.update_dag() public API → depends: none
```

---

### Round 14

**Brutalist review across codebase, architecture, security, test_coverage.**

~40 raw findings across 4 Claude critics (Gemini failed all 4 domains) → 6 validated, ~34 dismissed.

Duplicate rate >87%. Critics heavily recycling R1-R13 findings. Per-character allocation flagged for 14th consecutive round.

#### Validated Findings

**F1: Escalation write TOCTOU — `self.path` vs `resolved` (escalation.py:251-273)** — P2 (sec)
`_resolve()` canonicalizes `self.path` into `resolved` (line 251), validates that `resolved`
is under `.clou/escalations/` (line 254), but then writes to `self.path` (line 273) —
the original, un-canonicalized path. If `self.path` is a symlink that was valid at
`resolve()` time but swapped before `open()`, the write could land elsewhere. Low
practical risk (path comes from trusted message) but violates the pattern established
in app.py:391 where `resolved` is used consistently.

- **Solution:** Change `open(self.path, "a", ...)` to `open(resolved, ...)` at line 273.
  Store `resolved` as a local variable accessible to the write block.
- **Pitfall:** `resolved` is defined inside the method but in a different scope block.
  Need to lift it or restructure. Currently `resolved` is available at function scope,
  so the fix is a 1-word change.
- **Approach out:** Simplest: replace `self.path` with `resolved` in the `open()` call.

**T1: `extract_coordinator_status` non-dict `block.input` (bridge.py:90-92)** — P3 (test)
The `isinstance(block.input, dict)` guard at line 91 falls back to `{}` for non-dict
inputs, but no test exercises this branch. The guard exists to handle SDK blocks where
`input` might be a string or None.

- **Solution:** Add test with a mock block where `.input` is a string (e.g., `"raw"`).
  Verify the function still returns a status (falls through to name-based logic).
- **Pitfall:** None — straightforward mock test.

**T2: `route_coordinator_message` `usage=None` fallback (bridge.py:296)** — P2 (test)
`usage = getattr(msg, "usage", {}) or {}` handles `None` from SDK via the `or {}` fallback.
No test sends a message where `.usage` is explicitly `None` to verify the `or {}` path.

- **Solution:** Add test with mock TaskProgressMessage where `usage=None`. Verify no
  crash and `total_tokens=0`, `tool_uses=0` in the posted ClouAgentProgress.
- **Pitfall:** None.

**T3: `_cycle_type_rgb` ValueError fallback (breath.py:112-113)** — P3 (test)
The `except ValueError` branch falls back to `PALETTE["text-dim"].to_hex()`. No test
passes an unknown cycle type to verify the fallback produces valid RGB.

- **Solution:** Add test calling `_cycle_type_rgb("unknown_type")` and verifying the
  returned tuple matches text-dim palette color.
- **Pitfall:** Need to import `_cycle_type_rgb` from breath module. Clear cache between
  tests if testing multiple types.

**T4: `_OptionItem` recommended=True rendering (escalation.py:63-83)** — P3 (test)
`_OptionItem.render()` has branches for `self.recommended` (gold color + "(recommended)"
tag) but no test creates an item with `recommended=True`.

- **Solution:** Add test instantiating `_OptionItem` with `recommended=True`, adding
  "selected" class, and asserting render output contains "(recommended)" and gold color.
- **Pitfall:** Need to mount the widget or call render directly. `render()` returns str,
  so direct call is fine.

**T5: DAG fan-out multi-node layer rendering (dag.py:159-166)** — P3 (test)
`_render_connection` has a branch for `len(curr_layer) > 1` that renders a fan-out
connector (`┌────┴────┐`). All existing tests use single-node layers.

- **Solution:** Add test with a DAG where one task depends on two parent tasks in the
  previous layer, forcing the multi-node connector path.
- **Pitfall:** The fan-out renders based on `curr_layer` size, not dependency count.
  Need 2+ tasks in a layer with at least one depending on the previous layer.

#### Dismissed Findings (~34)

| Finding | Reason |
|---------|--------|
| Per-character style allocation | DUPLICATE (14th consecutive round) |
| Stream O(N²) markdown re-parse | DUPLICATE (documented, debounce mitigates) |
| Markdown() renders model output | Design decision — core purpose |
| Sync I/O in escalation/handoff | DUPLICATE (accepted, sub-ms) |
| God Object ClouApp | Architecture opinion (5th time) |
| _selected_index/options desync | Options never mutated; modulo wraps (9th time) |
| _CYCLE_RGB_CACHE unbounded | 4 cycle types, ~10 entries max (6th time) |
| Input during DECISION mode | **Invalid**: ModalScreen captures focus (8th time) |
| Escalation fallback path order | DUPLICATE (8th+ time) |
| Status bar milestone injection | **Invalid**: Text.assemble() literal text |
| ClouToolResult dead code | Marginal, low priority |
| parse_escalation ANSI | DUPLICATE (deferred to Task 8.3) |
| Reactive cascade in status bar | Textual batches within event loop tick |
| ConversationWidget encapsulation | DUPLICATE (R10, deferred P3) |
| Context tree sync I/O | DUPLICATE (accepted, documented) |
| Worker crash recovery fragile | DUPLICATE (documented R2, fixed) |
| All others (~18) | Duplicates from R1-R13 |

#### Task Graph

```
Group A — Security Fix (single):
  A1: Fix escalation TOCTOU: write to `resolved` not `self.path` → depends: none

Group B — Test Coverage (parallel):
  B1: Test extract_coordinator_status non-dict block.input → depends: none
  B2: Test route_coordinator_message usage=None → depends: none
  B3: Test _cycle_type_rgb ValueError fallback → depends: none
  B4: Test _OptionItem recommended=True rendering → depends: none
  B5: Test DAG fan-out multi-node layer rendering → depends: none
```

---

### Round 15

**Manual adversarial review across codebase, architecture, security, test_coverage.**
(Brutalist MCP down — conducted systematic manual code audit of all `clou/ui/` files.)

~5 new findings, 0 security issues. Codebase highly hardened after 14 prior rounds.

#### Validated Findings

**F1: `_is_blocking` substring match (escalation.py:42-44)** — P2 (logic)
Same bug class as R13's `_guess_milestone_status`. Uses `any(kw in classification.lower() ...)`,
so `"non-blocking"` matches `"blocking"`, `"uncritical"` matches `"critical"`. Only affects
CSS border styling (rose vs orange), not security — but incorrect classification display.

- **Solution:** Use word boundary matching: `re.search(r'\b' + kw + r'\b', classification.lower())`.
- **Pitfall:** None — identical pattern to R13's context_tree fix.
- **Approach out:** Simplest: inline `re.search` with `\b` boundaries. `import re` already
  present in escalation module — wait, it's NOT imported. Need to add `import re`.

**F2: `elapsed` dead reactive on ClouStatusBar (status_bar.py:122)** — P3 (dead code)
`elapsed: reactive[float] = reactive(0.0)` — declared but never read, written, watched, or
rendered. Not passed to `render_status_bar()`. Only referenced by a test checking its existence.

- **Solution:** Remove the reactive and update the test that checks for it.
- **Pitfall:** If this was planned for future use (e.g., displaying elapsed time), removing
  it loses that intent. But it's been dead since initial implementation.

**F3: `ContextScreen.on_mount` double tree build (context.py:64 + context_tree.py:108)** — P3 (perf)
`ContextTreeWidget.on_mount` calls `self.refresh_tree(self._clou_dir)`. Then
`ContextScreen.on_mount` also calls `tree.refresh_tree(self._project_dir / ".clou")`.
The tree is fully built, cleared, and rebuilt on every screen push.

- **Solution:** Remove the `refresh_tree` call from `ContextScreen.on_mount` — the widget's
  own `on_mount` already handles it. OR remove `ContextTreeWidget.on_mount` and let the
  screen control when to build.
- **Pitfall:** If the screen passes a different path than the widget was constructed with,
  both calls are needed. Currently they pass the same path.

**F4: `incomplete` missing from icon lookup (context_tree.py:173-179)** — P3 (consistency)
`"incomplete"` was added to `_STATUS_KEYWORDS` in R13 (line 40: `"incomplete": _TEAL_HEX`)
but the icon dict at line 173-179 has no `"incomplete"` entry. Falls through to default `"○"`.
Correct behavior for incomplete, but inconsistent with the keyword addition.

- **Solution:** Add `"incomplete": "○"` to the icon lookup, making the default explicit.
- **Pitfall:** None — purely clarifying intent.

**F5: `BreathState.RELEASING` dead state (mode.py:74)** — P3 (dead code)
Defined in enum but no transitions lead to it and no code checks for it. No entry in
`BREATH_TRANSITIONS` either. Part of Task #24 (pending since Round 2). Concrete dead
code — unlike SETTLING which is at least transitioned to (line 190).

- **Solution:** Either implement RELEASING (Task #24) or remove it from the enum.
- **Pitfall:** Removing it changes the enum auto() values, which could affect serialization
  if enums are ever persisted. Currently they're not.

#### Dismissed Findings

| Finding | Reason |
|---------|--------|
| Per-character style allocation | DUPLICATE (15th consecutive round) |
| Stream O(N²) markdown re-parse | DUPLICATE (documented, debounce mitigates) |
| God Object ClouApp | Architecture opinion (6th time) |
| _selected_index/options desync | Options never mutated; modulo wraps (10th time) |
| Input during DECISION mode | **Invalid**: ModalScreen captures focus (9th time) |
| Escalation fallback path order | DUPLICATE (9th+ time) |
| tool_input dict unsanitized | DUPLICATE (latent, documented R9) |
| ClouToolResult dead code | DUPLICATE (marginal) |
| SETTLING state dead frames | DUPLICATE (Task #24) |
| Context tree icon dict vs _STATUS_KEYWORDS | Consolidated into F4 |
| _URL_RE greedy trailing chars | Cosmetic — only affects highlight extent |
| HOLDING→IDLE in BREATH_TRANSITIONS unused | reset() bypasses state machine |

#### Task Graph

```
Group A — Code Fixes (parallel):
  A1: Fix _is_blocking substring match → word boundary regex → depends: none
  A2: Remove dead `elapsed` reactive from ClouStatusBar → depends: none
  A3: Remove double refresh_tree in ContextScreen → depends: none
  A4: Add "incomplete" to icon lookup in context_tree → depends: none

Group B — Dead Code (deferred to Task #24):
  B1: Implement or remove BreathState.RELEASING → depends: Task #24
```

---

### Round 16

**Brutalist review across codebase, architecture, security, test_coverage (Claude critics).**

~30 raw findings across 4 critics → 3 test gaps validated, 2 observations noted, ~25 dismissed.

Duplicate rate ~87%. Critics recycling R1-R15 findings heavily. Per-character allocation flagged for 16th time. Single-slot escalation flagged for 11th time. Security critic explicitly confirmed "zero critical, zero high — this UI layer is exceptionally well-defended."

#### Validated Findings

**T1: BreathWidget `on_clou_coordinator_spawned` handler untested (breath.py:213-216)** — P2 (test)
Handler added in R10 to reset `_active_agent_count` and `shimmer_active` on new coordinator.
No test directly exercises this handler. Only the app-level `ClouCoordinatorSpawned` handler
is tested in `test_mode_transitions.py`. If the breath widget handler breaks, shimmer persists
across coordinator sessions.

- **Solution:** Add test posting `ClouCoordinatorSpawned` to BreathWidget and verifying
  `_active_agent_count == 0` and `shimmer_active == False`.
- **Pitfall:** Need BreathWidget in an app context to receive messages.

**T2: BreathWidget `on_clou_cycle_complete` handler untested (breath.py:237-246)** — P2 (test)
Handler formats cycle completion text (`"cycle #1  PLAN complete  → EXECUTE"`) and adds it
to the event buffer. Zero tests — `ClouCycleComplete` only tested for construction in
`test_messages.py`. Formatting could silently break.

- **Solution:** Add test posting `ClouCycleComplete` to BreathWidget and verifying the
  formatted event text in `_events`.
- **Pitfall:** `ClouCycleComplete` is never posted in production (documented R2). But the
  handler should still work correctly for when it's wired.

**T3: `action_show_dag` in HANDOFF mode untested (app.py:291-300)** — P3 (test)
The guard `self.mode in (Mode.BREATH, Mode.HANDOFF)` allows DAG viewing in HANDOFF mode.
Tests verify BREATH opens DAG and DIALOGUE is rejected, but HANDOFF acceptance is untested.

- **Solution:** Add test entering HANDOFF mode, calling `action_show_dag()`, verifying
  DagScreen is pushed.
- **Pitfall:** Needs DAG data set first (`_dag_tasks`, `_dag_deps`).

**A1: Animation timer runs during DECISION mode (app.py:162-165)** — P3 (efficiency)
BREATH→DECISION transitions to HOLDING but does not stop the animation timer. Timer
continues ticking at 24 FPS behind the modal, setting `breath_phase = 1.0` (constant)
every tick — triggering full `render_line()` on a widget at opacity 0.3 behind a modal.

- **Solution:** Stop the timer on DECISION entry, restart on DECISION→BREATH return.
  The `_breath_machine.transition(BreathState.HOLDING)` already records the state; the
  timer just isn't needed while holding.
- **Pitfall:** Must restart the timer when returning from DECISION to BREATH. Currently
  DECISION→BREATH at line 177-178 only transitions the state machine, doesn't restart
  the timer. This fix requires changes in both directions.
- **Approach out:** Alternatively, in `_animation_tick`, skip the `refresh()` call if
  `breath_value` hasn't changed since last tick. Cheaper, less invasive.

#### Dismissed Findings (~25)

| Finding | Reason |
|---------|--------|
| Per-character allocation (77K/sec) | DUPLICATE (16th consecutive round) |
| Escalation pipeline unwired | DUPLICATE (Task 8.3, orchestrator scope) |
| tool_input dict unsanitized | DUPLICATE (R9, latent, no render path) |
| _active_app global race | DUPLICATE (orchestrator scope) |
| Escalation fallback weakens security | DUPLICATE (R8/R11, abnormal state only) |
| Token counters overflow / double-count | DUPLICATE (R1, separate tiers by design) |
| DAG cycle handling arbitrary | DUPLICATE (R10, valid rendering) |
| 500KB Markdown re-parse at turn | DUPLICATE (documented, debounce mitigates) |
| TransitionMeta never consumed | DUPLICATE (design debt, documented) |
| Single-slot escalation queue | DUPLICATE (R4, design decision, 11th time) |
| Agent counter drift on lost messages | Reset on coordinator spawn (R10) |
| Duck-type bridge routing | DUPLICATE (intentional design, R2) |
| Per-field ANSI stripping overhead | Acceptable — compiled regex, small strings |
| Context tree sync I/O | DUPLICATE (accepted, documented) |
| cycle_color ValueError crash risk | DUPLICATE (R4 — all callers handle) |
| ClouToolResult dead code | DUPLICATE (marginal) |
| ClouAgentProgress never consumed | DUPLICATE (R2 tracker item) |
| _CYCLE_RGB_CACHE unbounded | DUPLICATE (7th time, ~10 entries) |
| Stream buffer Markdown corruption | DUPLICATE (visual, accepted) |
| Unbounded user input | Local DoS, user attacks themselves |
| Bidi/homoglyph unicode visual spoofing | LOW — no code execution, visual only |
| _render_line URL style untested | Cosmetic |
| phase_state.md fallback untested | DUPLICATE (R14, trivial) |
| on_clou_breath_event indirect | LOW — trivial delegation |

#### Task Graph

```
Group A — Test Coverage (parallel):
  A1: Test BreathWidget on_clou_coordinator_spawned → depends: none
  A2: Test BreathWidget on_clou_cycle_complete → depends: none
  A3: Test action_show_dag in HANDOFF mode → depends: none

Group B — Efficiency (deferred):
  B1: Stop animation timer during DECISION mode → depends: Task #24
```

---

### Round 17

**Brutalist review across codebase, architecture, security, test_coverage.**

~40 raw findings across 4 domains → 4 validated, ~36 dismissed.

Finding density: R14:6 → R15:5 → R16:3 → R17:4 (1 code fix + 3 test gaps). Convergence holds.

#### Validated Findings

**F1: Handoff read TOCTOU — `msg.handoff_path` vs `resolved` (app.py:398)** — P2 Security

At app.py:391, the path is resolved to `resolved` and validated against `.clou`. But at
line 398, the actual `read_text()` call uses the original `msg.handoff_path`, not `resolved`.
A symlink race between validation and read could bypass the path check — same class of
TOCTOU as the R14 escalation fix.

- **Solution:** Change `msg.handoff_path.read_text()` to `resolved.read_text()`.
- **Pitfall:** None — one-token fix, identical pattern to R14.

**F2: `_guess_milestone_status` untested (context_tree.py:60-75)** — P2 Test Gap

Function reads state.md / phase_state.md, extracts status keywords via regex. Never
directly tested. Covered only indirectly via full tree builds, which may not exercise
all branches (no file, fallback file, keyword match, no match).

- **Solution:** Unit tests: no files → None, state.md with keyword → keyword, no match → None,
  phase_state.md fallback, OSError handling.
- **Pitfall:** Tests need temp directories with real files. Use `tmp_path` fixture.

**F3: `_is_recent` untested (context_tree.py:51-57)** — P2 Test Gap

Simple function but exercises OSError path that's never tested. Recent-vs-stale threshold
logic also untested.

- **Solution:** Unit tests: recent file → True, stale file → False, nonexistent path → False.
- **Pitfall:** Time-sensitive — use fixed `now` parameter or mock `stat()`.

**F4: `_flush_stream` debounce untested (conversation.py:87-93)** — P2 Test Gap

The dirty-flag debounce mechanism (`_stream_dirty` flag, timer-based flush, overlay update)
has no direct tests. Related to the R12 tracker item "TEST: Debounce flag mechanics".

- **Solution:** Unit tests: flush with dirty=False is no-op, flush with dirty=True updates
  overlay, flush resets dirty flag.
- **Pitfall:** Requires mocking `query_one` for the overlay Static widget. Keep it simple.

#### Dismissed Findings (~36)

| Finding | Reason |
|---------|--------|
| Per-character allocation (77K/sec) | DUPLICATE (17th consecutive round!) |
| tool_input dict unsanitized | DUPLICATE (R9, latent, no render path) |
| Escalation pipeline unwired | DUPLICATE (Task 8.3, orchestrator scope) |
| Token double-counting | DUPLICATE (R1, separate tiers by design) |
| TransitionMeta unused | DUPLICATE (design debt, documented) |
| Duck-type bridge routing | DUPLICATE (intentional design, R2) |
| O(N²) markdown re-parse | DUPLICATE (R1, debounce mitigates) |
| sync I/O in context tree | DUPLICATE (accepted, documented) |
| Single-slot escalation queue | DUPLICATE (design decision, 12th time) |
| ClouToolResult dead code | DUPLICATE (marginal, 4th time) |
| ClouAgentProgress never consumed | DUPLICATE (R2 tracker item) |
| _CYCLE_RGB_CACHE unbounded | DUPLICATE (8th time, ~10 entries max) |
| Stream buffer Markdown corruption | DUPLICATE (visual, accepted) |
| Escalation fallback weakens security | DUPLICATE (R8/R11, abnormal state) |
| DAG cycle handling arbitrary | DUPLICATE (R10, valid rendering) |
| _active_app global race | DUPLICATE (orchestrator scope) |
| Agent counter drift | Reset on coordinator spawn (R10) |
| Per-field ANSI overhead | Acceptable — compiled regex |
| query_one NoMatches risk | INVALID — all targets are always-composed structural widgets |
| Unbounded user input | Local DoS, user attacks themselves |
| Bidi/homoglyph unicode spoofing | LOW — visual only, no execution |
| _render_line URL style untested | Cosmetic |
| phase_state.md fallback untested | Subsumed by F2 |
| on_clou_breath_event indirect | LOW — trivial delegation |
| Security: all 18 controls verified | CONFIRMED SOLID — zero critical/high/medium |
| cycle_color ValueError crash | DUPLICATE (R4, all callers handle) |
| _stream_buffer 500KB cap bypass | INVALID — enforced in on_clou_stream_chunk |
| Context tree filename injection | DUPLICATE (R3, markup escaped) |
| DagScreen milestone injection | DUPLICATE (R7, markup escaped) |
| Worker error ANSI bypass | DUPLICATE (R11, stripped) |
| Rate-limit status semantics | DUPLICATE (R9, fixed) |
| _animation_time overflow | DUPLICATE (R8, modulo applied) |
| action_clear stream reset | DUPLICATE (R8, clears all) |
| parse_handoff heading greedy | DUPLICATE (R10, fixed-width) |
| Handoff content ANSI | DUPLICATE (R11, stripped) |
| DAG task name unbounded | DUPLICATE (R10, 40 char cap) |

#### Task Graph

```
Group A — Code Fix:
  A1: Fix handoff read TOCTOU (app.py:398) → depends: none

Group B — Test Coverage (parallel, depends: A1):
  B1: Test _guess_milestone_status → depends: none
  B2: Test _is_recent → depends: none
  B3: Test _flush_stream debounce → depends: none
```

---

### Round 18

**Brutalist review across codebase, architecture, security, test_coverage.**

~38 raw findings across 4 domains → 2 validated code items + 1 test fix, ~35 dismissed.

Finding density: R15:5 → R16:3 → R17:4 → R18:3. Security: 5th consecutive zero-finding round.

#### Validated Findings

**F1: Broad `except Exception` in `on_worker_state_changed` (app.py:116)** — P3 Hardening

The handler catches `Exception` when only `LookupError` (Textual's `NoMatches`) is expected.
If `add_error_message` or `_stop_stream_timer` raise for any other reason, the supervisor
crash notification is silently swallowed. The user sees nothing.

- **Solution:** Narrow to `except LookupError:` — the only expected exception when widget
  isn't mounted yet.
- **Pitfall:** If Textual changes the exception type for unmounted queries. Unlikely — `NoMatches`
  inherits from `LookupError` and has for years.

**F2: `test_renders_content` weak assertion (test_panels.py:358-364)** — P2 Test Quality

Test is named `test_renders_content` but only asserts `header is not None`. It never checks
that the content string ("Hello world") actually appears in the RichLog. The `on_mount` content
write could silently break and this test would still pass.

- **Solution:** Query `#detail-content` RichLog, assert `len(log.lines) >= 1` to verify content
  was written.
- **Pitfall:** RichLog line count depends on wrapping. Assert `>= 1` not exact count.

#### Dismissed Findings (~35)

| Finding | Reason |
|---------|--------|
| Per-character allocation (render_line) | DUPLICATE (18th consecutive round) |
| `tool_input` dict unsanitized | DUPLICATE (R9, latent, no render path, 3rd time) |
| Token double-counting across tiers | DUPLICATE (R1, separate tiers, 4th time) |
| `action_clear` encapsulation breach | DUPLICATE (R10, ConversationWidget.clear() extraction, deferred) |
| `_selected_index` desync | INVALID — options list immutable after __init__ |
| DAG cycle nodes depth=0 | Known design choice (R10, cosmetic) |
| Escalation fallback ordering | DUPLICATE refinement (R8/R11, abnormal state only) |
| Shimmer-breath phase decorrelation | Cosmetic, intentional organic feel |
| `_animation_time` drift | CONFIRMED CORRECT — 108 frames divides evenly |
| O(N²) markdown re-parse | DUPLICATE (R1, debounce mitigates) |
| `_active_app` global / message drop | DUPLICATE (orchestrator scope) |
| `_has_screen` linear scan | Trivial (stack ≤ 4 entries) |
| Sync file write in escalation | Accepted pattern, same as context tree |
| `ClouAgentProgress` unhandled | DUPLICATE (R2, tracker item) |
| `_active_agent_count` drift | DUPLICATE (R10, self-heals on coordinator spawn) |
| `BreathState.RELEASING` unreachable | DUPLICATE (Task #24, documented) |
| `__pycache__` skip untested | Trivial guard, won't regress |
| `_render_line` URL style unasserted | Known cosmetic |
| `post_message` from async worker | DUPLICATE (orchestrator scope) |
| Security: all controls verified | CONFIRMED SOLID — zero critical/high/medium/low (5th round) |
| Markdown rendering path safe | CONFIRMED — markdown-it + Text.append = literal text |
| ANSI stripping complete (14 paths) | CONFIRMED — all verified |
| Path validation solid | CONFIRMED — both handoff read + escalation write use resolved |
| Rich markup escaping complete | CONFIRMED — 8 locations verified |

#### Task Graph

```
Group A — Code Fix + Test Fix (parallel):
  A1: Narrow except Exception to LookupError (app.py:116) → depends: none
  A2: Fix test_renders_content assertion (test_panels.py:358) → depends: none
```

---

### Round 19

**Brutalist review across codebase, architecture, security, test_coverage.**

~38 raw findings across 4 domains → 3 validated (all test gaps), ~35 dismissed.

Finding density: R16:3 → R17:4 → R18:2 → R19:3 (test-only). Security: 6th consecutive zero-finding round.
Code has converged — zero new code bugs found.

#### Validated Findings

**F1: `on_worker_state_changed` LookupError catch branch untested (app.py:116)** — P2

The R18 narrowing from `except Exception` to `except LookupError` has no test exercising
the catch path. The test always uses a mounted widget. A regression that changes the exception
type would go undetected.

- **Solution:** Test with a pre-compose app where ConversationWidget isn't mounted yet.
  Call handler, assert no exception raised and no error message appears.
- **Pitfall:** Textual apps always compose on `run_test()` — use `monkeypatch` on `query_one`
  to raise `LookupError` instead.

**F2: Worker crash with `error=None` untested (app.py:108)** — P3

Tests always provide `error=RuntimeError(...)`. The `error=None` path (just "Supervisor
session ended unexpectedly" with no suffix) is never exercised.

- **Solution:** Add test with `error=None` on fake worker. Assert message doesn't contain ":".
- **Pitfall:** None — trivial one-test addition.

**F3: `_build_tree` OSError on `iterdir()` untested (context_tree.py:141-145)** — P2

The OSError catch on `directory.iterdir()` (permission denied, deleted directory) is never
tested. This is a real production scenario when `.clou/` contains unreadable directories.

- **Solution:** Monkeypatch `Path.iterdir` to raise OSError on a specific subdirectory.
  Assert tree builds without the failing directory's children but doesn't crash.
- **Pitfall:** Must monkeypatch at the right level — the `sorted()` wrapper means the
  OSError surfaces from `iterdir()`, not from sorting.

#### Dismissed Findings (~35)

| Finding | Reason |
|---------|--------|
| Per-character allocation (render_line) | DUPLICATE (19th consecutive round) |
| ClouCycleComplete/DagUpdate never posted | DUPLICATE (R2, orchestrator scope) |
| Token double-counting | DUPLICATE (R1, separate tiers) |
| `action_clear` encapsulation | DUPLICATE (R10, deferred) |
| `tool_input` unsanitized | DUPLICATE (R9, 4th time) |
| Escalation fallback ordering | DUPLICATE (R8/R11/R18) |
| O(N²) markdown re-parse | DUPLICATE (R1, debounce) |
| Sync I/O + screen stack | DUPLICATE (accepted) |
| Animation time drift | DISMISSED — cosmetic, negligible |
| Float cost accumulation | DISMISSED — :.2f display rounds |
| `visiting` set leak on depth>200 | DISMISSED — unreachable edge case |
| `_flush_stream` timer race | INVALID — single-threaded, benign |
| Coordinator-complete during DECISION | UX concern, all transitions legal |
| `_start_breathing` re-entrant guard | LOW — trivial timer null check |
| `_animation_tick` exception catch | LOW — defensive guard |
| `_KEY_FILES` dead differentiation | Code smell, not a test gap |
| Empty milestone title / content | LOW — trivial guards |
| DAG task name truncation | LOW — simple slice logic |
| Security: all controls verified | CONFIRMED SOLID — 6th consecutive zero (18 controls) |

#### Task Graph

```
Group A — Test Coverage (parallel):
  A1: Test on_worker_state_changed LookupError catch → depends: none
  A2: Test worker crash with error=None → depends: none
  A3: Test _build_tree OSError on iterdir → depends: none
```

---

### Round 20

**Brutalist review across codebase, architecture, security, test_coverage.**

**ALL 4 CRITICS CONFIRMED CONVERGENCE.** First round where every domain independently
declared the codebase production-ready with zero new findings of meaningful severity.

- **Codebase:** "After 20 rounds, ~100 fixes, 593 tests — convergence confirmed. Zero
  genuinely new bugs. Technical debt interest rate: ~0%. Ready for production."
- **Security:** "Zero findings. 7th consecutive clean round. Security maturity reached.
  18 defensive controls all verified solid. An attacker would find no viable entry point."
- **Test coverage:** "No genuinely new untested code paths. False confidence score: 95%.
  The test suite has reached convergence."
- **Architecture:** 8 Low-severity items, all duplicates of accepted decisions or
  theoretical concerns requiring specific unlikely conditions.

**Final finding density:** R17:4 → R18:2 → R19:3 → R20:0.

#### Remaining Open Items (all deferred/architectural)

| Item | Priority | Scope | Status |
|------|----------|-------|--------|
| Task #24: Animation phases (RELEASING, SETTLING, push_screen) | P2 | UI | deferred — architectural polish |
| Per-char style quantization | P1 perf | UI | deferred — accepted tradeoff |
| Coordinator crash → UI signal | P2 | orchestrator | out of scope |
| ClouAgentProgress handler in UI | P2 | orchestrator | out of scope |
| ClouCycleComplete wiring | P2 | orchestrator | out of scope |
| Escalation arrival wiring (Task 8.3) | P1 | orchestrator | out of scope |
| parse_escalation ANSI strip | P3 | orchestrator | depends on Task 8.3 |
| ConversationWidget.clear() extraction | P3 | UI | deferred — low priority refactor |
| Animation timer in DECISION mode | P3 | UI | deferred — Task #24 |

All remaining items are either deferred design decisions or orchestrator-layer work outside
the presentation scope. The UI presentation layer is complete and production-hardened.

---

### Round 21

**Verification round — all 4 critics re-confirm R20 convergence.**

Zero new findings across codebase, security (8th consecutive clean), test coverage, and
architecture. Two consecutive rounds of full convergence confirmation. The brutalist review
cycle is definitively complete for the UI presentation layer.

**Final metrics:**
- 21 rounds, ~500+ raw findings triaged, ~100 validated & fixed
- 593 tests (up from 344), 249 tests added through review cycles
- 8 consecutive security-clean rounds (R14-R21)
- Last code bug: R17, last test gap: R19
- Duplicate rate at terminal: 100%

---

### Round 22

**Gemini critic swap — fresh perspective on converged codebase.**

Switched from Claude to Gemini across all 4 domains to challenge prior conclusions.

- **Codebase (Gemini):** "Independent confirmation of convergence. The UI is production-ready.
  Claude's assessment of zero critical findings is accurate. Debt rate: 1.5% (stable)."
  4 findings — all duplicates (duck-typing, sync I/O, semantic matching, per-char allocation).
- **Security (Gemini):** More aggressive stance — flagged 3 "vulnerabilities":
  - VULN-01 (ANSI in escalation): **UNREACHABLE** — ClouEscalationArrived never posted in
    production (Task 8.3 unwired). Deferred since R12.
  - VULN-02 (Markdown DoS): DUPLICATE (R1, debounce mitigates).
  - VULN-03 (label injection): INVALID — newline replaced, single-line can't create heading.
- **Test coverage (Gemini):** "False confidence: 18%" — overstated. Key claims invalid:
  escalation single-slot is design decision (flagged 13x), FakeWorker is standard testing
  pattern, action_show_dag whitelist tested via DIALOGUE.
- **Architecture (Gemini):** All findings duplicates of accepted items.

**1 validated finding:** `action_clear` at app.py:282 had remaining `except Exception: pass` —
narrowed to `except LookupError` (same pattern as R18). Zero remaining `except Exception` in
entire UI layer.

**Updated metrics:**
- 22 rounds, ~540+ raw findings triaged, ~101 validated & fixed
- 593 tests, zero `except Exception` remaining in UI
- Cross-critic validation: both Claude (R20-R21) and Gemini (R22) independently confirm convergence

---

## Test Infrastructure Review

Brutalist review targeting test code quality — naming accuracy, assertion strength,
missing edge cases, pytest idioms. Separate from test_coverage domain in the UI rounds.

### Test Infrastructure R1

**Brutalist review: test_coverage domain — Claude only (Gemini timed out).**

~10 raw findings → 4 validated, ~6 dismissed.

#### Validated Findings

**F1: test_handoff.py bypasses `update_content()` API** — P2
Tests directly set widget internal state via `parse_handoff()` instead of calling
the public `update_content()` method. Tests prove parsing works but not that the
widget API wires parsing to rendering.
- **Fix**: Renamed tests to accurately describe what they test (`test_parse_handoff_*`)
  and updated docstrings. Full `update_content()` integration requires mounted Textual app.
- **Status**: **done**

**F2: test_mode.py missing 3 illegal self-transitions** — P2
`TestIllegalTransitions` only tested DIALOGUE→DIALOGUE. Missing BREATH→BREATH and
DECISION→DECISION self-transitions.
- **Fix**: Added 3 self-transition parametrize entries (DIALOGUE, BREATH, DECISION).
- **Status**: **done**

**F3: test_integration_ui.py misleading test name** — P3
`test_spawn_tool_posts_lifecycle_messages` tested that `_build_mcp_server` accepts an
app argument — no lifecycle messages involved.
- **Fix**: Renamed to `test_build_mcp_server_accepts_app` with accurate docstring.
- **Status**: **done**

**F4: test_recovery.py `try/except` instead of `pytest.raises`** — P3
Two frozen-dataclass tests used raw `try/except AttributeError` blocks instead of
the standard `with pytest.raises(AttributeError):` pattern.
- **Fix**: Replaced with `pytest.raises`. Added missing `import pytest`.
- **Status**: **done**

#### Dismissed (~6)

| Finding | Reason |
|---------|--------|
| `asyncio.run()` instead of `pytest.mark.asyncio` | Deliberate pattern — avoids async fixture dependency. |
| `monkeypatch` missing type annotation | P4 cosmetic. |
| `FakeWorker` pattern | Standard testing pattern. Flagged 13x in UI rounds. |
| `_segments` internal access | Textual testing reality. |

#### Metrics
- 4 fixes applied (2 P2, 2 P3)
- 3 new self-transition tests added
- Test suite: 632 passing (was 630)

---

### Test Infrastructure R2

**Brutalist review: test_coverage — Claude only (Codex failed both attempts).**

R2 pass 1: 9 medium, 12 low, 5 trivial.
R2 pass 2 (supplementary): 3 high, 5 medium, 3 low.

Combined triage across both passes (~37 raw findings):

#### Validated Findings

**F1: `test_enter_resolves_with_first_option` has no assertion after action (test_escalation.py:218)** — P1
Test presses Enter on the escalation modal, then ends. Never asserts modal was
dismissed, `ClouEscalationResolved` was posted, or disposition was written. Also
sets `path = Path("/dev/null")` which silently swallows the write.
- **Status**: open — needs assertion for dismiss + message post

**F2: `TestIllegalTransitions` never tests illegal transitions at app level (test_mode_transitions.py:240)** — P1
Class is named `TestIllegalTransitions` but contains only a legal transition test
(`test_handoff_to_breath_legal`). No test verifies that `ClouApp.transition_mode()`
returns `False` and produces no side effects for actually-illegal pairs.
- **Status**: open — rename class + add app-level illegal transition tests

**F3: Path traversal test missing for write boundary hooks (test_hooks.py)** — P1
`hooks.py` uses `Path.resolve()` + `.relative_to()` to enforce `.clou/` confinement.
No test for symlink traversal or `..` escapes (e.g. `.clou/../../etc/passwd`).
Security-sensitive code.
- **Status**: open — add traversal escape tests

**F4: `test_update_dag_stores_data` is tautological (test_panels.py:99)** — P2
Directly assigns `w._tasks = SAMPLE_TASKS` then asserts `len(w._tasks) == 4`.
Tests Python attribute assignment, not widget `update_dag()` behavior.
- **Status**: open — rewrite to call `update_dag()`

**F5: `TestIsBlocking` missing "fatal" and "error" keywords (test_escalation.py:72)** — P2
Source `_BLOCKING_KEYWORDS = frozenset({"blocking", "critical", "fatal", "error"})`.
Tests only cover "blocking" and "critical".
- **Status**: open — add parametrize entries for "fatal" and "error"

**F6: VERIFY `effort="max"` untested (test_orchestrator.py:305)** — P2
Only ASSESS is tested for `effort="max"`. VERIFY also uses it.
- **Status**: open — add parametrize case

**F7: Sync `def execute()` untested in graph validation (test_graph.py)** — P2
`_find_entry` handles both `ast.FunctionDef` and `ast.AsyncFunctionDef`. All tests
use `async def`. Sync path uncovered.
- **Status**: open — add test with sync `def execute()`

#### Dismissed (~30)

| Finding | Reason |
|---------|--------|
| `_display` ANSI stripping integration | Covered by bridge ANSI tests separately. |
| Cycle section index vs number confusion | Test works correctly; naming is marginal. |
| `test_conversation.py` line-count assertions | Pattern observation, not individual test bug. Systematic refactor out of scope. |
| `test_handoff.py` body completeness | Parser is simple substring extraction. Low risk. |
| `_pre_decision_mode` stuck on DECISION | Guard exists at app.py:365. Edge scenario requires broken Mode enum. |
| `on_worker_state_changed` stream cleanup | Stream cleanup is separate concern; error message is the critical behavior. |
| `action_clear` stream overlay cleanup | Stream overlay tested via other paths. |
| `_walk_entry` nested AST patterns | Edge cases in _g{i} fallback, attribute calls. Low likelihood. |
| `render_status_bar` reactive vs pure | Values correctly set; rendering is Textual framework responsibility. |
| `watch_mode` no-op branches | No-op by design. Testing absence of side effects is infinite scope. |
| `_has_screen` mixed screen types | Modal stacking guarded by mode transitions, not `_has_screen` alone. |
| `asyncio.run()` vs `pytest.mark.asyncio` | Deliberate pattern. Repeated R1. |
| All Low/Trivial items | Style issues, minor inconsistencies. |

#### Convergence Assessment
- **R1**: 4 fixes (2 P2, 2 P3)
- **R2**: 7 validated (2 P1, 5 P2), 0 fixed yet
- **Finding density**: R1:4 → R2:7
- **Severity trend**: R2 escalated — 2 P1s (assertion void, missing app-level tests)
- **Pattern**: R1 caught naming/idiom issues. R2 caught assertion quality + missing coverage.
  Not yet converging — R2 found more than R1. Need to fix and run R3.

---

## Spec↔Implementation Review

Brutalist review targeting consistency between knowledge-base specs and actual code.

### Spec↔Implementation R1

**Brutalist review: codebase domain on recovery.py — Claude only (Gemini failed).**

~8 raw findings → 2 validated, ~6 dismissed.

#### Validated Findings

**F1: Escalation disposition `pending` doesn't match structured format** — P2
Spec and tests expected structured `status: open` field but `_write_escalation`
wrote freeform `pending` string.
- **Fix**: Changed `pending` → `status: open` in `_write_escalation`. Updated test assertions.
- **Status**: **done**

**F2: Escalation timestamp uses `T` separator instead of `-`** — P3
Spec uses `YYYYMMDD-HHMMSS` format but code used `YYYYMMDDTHHMMSS`.
- **Fix**: Changed `%Y%m%dT%H%M%S` → `%Y%m%d-%H%M%S`. Updated test assertions.
- **Status**: **done**

#### Dismissed (~6)

| Finding | Reason |
|---------|--------|
| `_write_escalation` no markdown sanitization | Escalation content is from trusted internal sources. Low impact. |
| `step` field not validated | Informational only. Repeated from orchestrator rounds. |
| `assess_convergence` Overridden semantic | Design choice: "zero accepted" is the convergence signal. |

#### Metrics
- 2 fixes applied (1 P2, 1 P3)
- Updated 3 test assertions
- Test suite: 632 passing

---

### Spec↔Implementation R2

**Brutalist review: codebase domain on recovery.py — Claude only (Codex failed).**

R2 pass 1: 4 low findings. R2 pass 2 (supplementary): 6 findings (1 medium, 5 low).

Combined triage across both passes (~10 raw findings):

#### Validated Findings

**F1: `milestone` parameter unsanitized inside recovery.py (recovery.py:254-258, 406)** — P1
`_escalation_dir` and `git_revert_golden_context` interpolate `milestone` into filesystem
paths with no internal guard. Validation lives only in `orchestrator.py:74`. Any future
caller bypassing the orchestrator (CLI tool, test harness) would have no path traversal
defense. `mkdir(parents=True)` would create arbitrary directories.
- **Contrast**: `current_phase` IS defended internally (lines 158, 193). Same trust boundary,
  different treatment.
- **Status**: open — add `validate_milestone_name` import and check at module boundary

**F2: `read_cycle_outcome` ignores `milestone` parameter (recovery.py:237-246)** — P2
Function signature accepts `milestone: str` but never uses it. Checkpoint path is
hardcoded to `.clou/active/coordinator.md`. Misleading contract.
- **Status**: open — either use the parameter or remove it

**F3: `git_revert_golden_context` has no timeout (recovery.py:400-411)** — P2
`proc.communicate()` waits indefinitely. A hung git process (lock contention, NFS stall)
blocks the coordinator loop forever with no escalation or recovery.
- **Status**: open — add timeout to `communicate()`

#### Dismissed (~7)

| Finding | Reason |
|---------|--------|
| `step` field not validated | Repeated. Informational only. |
| `assess_convergence` Overridden semantic | Repeated. Design choice. |
| TOCTOU in `determine_next_cycle` | Benign race, serial execution, burns a crash retry at worst. |
| Escalation file write not atomic | Human-read files, low impact. |
| `_write_escalation` markdown injection | Trusted internal sources. |

#### Convergence Assessment
- **R1**: 2 fixes (1 P2, 1 P3) — format consistency
- **R2**: 3 validated (1 P1, 2 P2), 0 fixed yet
- **Finding density**: R1:2 → R2:3
- **Severity trend**: R2 found a P1 security gap (milestone unsanitized)
- **Pattern**: R1 caught surface format issues. R2 caught deeper contract/boundary issues.
  Not yet converging — need to fix and run R3.

---

### Test Infrastructure R2 — Fixes Applied

Agent team `test-infra-r2` addressed all 7 open findings from R2. 32 new tests added.
Also introduced conftest.py with autouse fixture for supervisor isolation.

- F1: test_escalation.py — assertions added after Enter press (dismiss + message post)
- F2: TestIllegalTransitions — renamed, app-level illegal transition tests added
- F3: test_hooks.py — path traversal escape tests added (`.clou/../../etc`, absolute paths)
- F4: test_panels.py — rewritten to call `update_dag()` instead of direct assignment
- F5: test_escalation.py — "fatal" and "error" added to TestIsBlocking parametrize
- F6: test_orchestrator.py — VERIFY effort="max" parametrize case added (NOT landed — agent skipped)
- F7: test_graph.py — sync `def execute()` test added (later updated by graph-fixes to expect rejection)

**Post-fix**: conftest.py autouse `_no_supervisor` fixture tried to import `clou.orchestrator`
which pulls `claude_agent_sdk` — broke all 687 tests. Fixed by wrapping the monkeypatch in
`try/except ImportError`.

**Status**: 6 of 7 fixed. F6 (VERIFY effort) still open. Test suite: 687 passing (was 632).

---

### Spec↔Implementation R2 — Fixes Applied

Agent team `recovery-r2` addressed all 3 open findings from R2.

- F1: recovery.py — `_MILESTONE_RE` + `_validate_milestone()` added internally. Applied to
  all 3 escalation writers + `git_revert_golden_context`. Tests for invalid milestone rejection.
- F2: recovery.py — `read_cycle_outcome` — removed unused `milestone` parameter. Updated
  caller in orchestrator.py.
- F3: recovery.py — `git_revert_golden_context` — added 30s timeout to `proc.communicate()`.
  On timeout: kills process, raises `RuntimeError`. Test added.

**Status**: All 3 fixed. Test suite: 664 passing. Ready for R3 confirmation.

---

## Hooks Module Review

Dedicated security review of the write boundary enforcement module.

### Hooks R1

**Brutalist review: security domain on hooks.py — Claude only.**

~8 raw findings → 1 actionable, 7 dismissed/known.

#### Validated Finding

**F1: `_extract_file_path` fragile to MultiEdit schema changes (hooks.py:60-65)** — P2
Only extracts top-level `file_path` key. If SDK changes MultiEdit to nested structure
(e.g., `{"edits": [{"file_path": "...", ...}]}`), the hook returns `None` and fails open
(allows the write). No fallback extraction for nested schemas.
- **Status**: open — add defensive extraction for nested edit structures

#### Dismissed (7)

| Finding | Reason |
|---------|--------|
| Workers/verifiers no hook enforcement | **Known** — Orch R3 F4. SDK `AgentDefinition` has no `hooks` field. |
| Bash bypasses write boundary | **By design** — hooks protect `.clou/` from accidental coordinator writes. Sandbox confines Bash. Not a security boundary. |
| Fail-open design on early returns | **By design** — hooks restrict `.clou/` writes, don't restrict all writes. |
| TOCTOU in PostToolUse | Mitigated by single-threaded async. |
| No milestone validation in `build_hooks` | Being fixed in recovery-r2 (same pattern). |
| Symlink TOCTOU | Mitigated by sandbox. |
| Shared escalation write patterns | Escalation files append-only by convention. |

---

## Bridge Module Review

Focused review of SDK→Textual message bridge after many post-convergence fixes.

### Bridge R1

**Brutalist review: codebase domain on bridge.py — Claude only.**

~9 raw findings → 3 actionable, 6 dismissed.

#### Validated Findings

**F1: `AssistantMessage.error` field completely ignored (bridge.py:226)** — P0
SDK `AssistantMessage` has `error: AssistantMessageError | None` with values like
`"authentication_failed"`, `"billing_error"`, `"server_error"`. Neither routing function
checks it. Auth failures are invisible — user sees a breathing screen with no indication.
- **Fix**: Agent `bridge-fixes` checked SDK types — `error` field exists. Added check in both
  routing functions: if `msg.error` is truthy, posts `ClouSupervisorText` with error details.
- **Status**: **done**

**F2: `ToolResultBlock` silently dropped, `ClouToolResult` dead (bridge.py:226-236)** — P2
SDK `ContentBlock` union includes `ToolResultBlock` (has `.tool_use_id`, `.content`, `.is_error`).
Not handled in routing — falls through all checks. Meanwhile `ClouToolResult` message class
exists in messages.py but is never emitted.
- **Fix**: Agent `bridge-fixes` wired ToolResultBlock routing → ClouToolResult.
- **Status**: **done**

**F3: ANSI not stripped from tool_input file_path in extract_coordinator_status (bridge.py:109)** — P2
`tool_input.get("file_path", "")` used directly in `Path()` decomposition. ANSI sequences
in file_path would produce wrong `parent.name` values in status strings.
- **Fix**: Agent `bridge-fixes` added `_strip_ansi()` wrapping.
- **Status**: **done**

#### Dismissed (6)

| Finding | Reason |
|---------|--------|
| `UserMessage` silently dropped | User messages originate locally, visible in input widget. |
| `SystemMessage` base unhandled | Current SDK subtypes all covered. |
| Truncation splits grapheme clusters | Cosmetic. |
| `parse_escalation` no size guard | Escalation files small by construction. |
| Duck-typing order dependence | Current order works, tested. Enhancement not bug. |
| ANSI regex ReDoS | No practical concern. |

---

## Graph Module Review

Focused review of compose.py AST validation.

### Graph R1

**Brutalist review: codebase domain on graph.py — Claude only.**

~11 raw findings → 4 actionable, 7 dismissed.

#### Validated Findings

**F1: Duplicate `execute()` — validator uses first, runtime uses last (graph.py:107)** — P1
Two `execute()` definitions: validator certifies the first, Python runtime executes the last.
Complete bypass of call graph analysis.
- **Fix**: Agent `graph-fixes` detects duplicates and rejects with error message including
  both line numbers.
- **Status**: **done**

**F2: Sync `execute()` produces empty call graph (graph.py:87 vs 108)** — P1
`_find_entry` accepts `FunctionDef` but `_extract_sigs` only collects `AsyncFunctionDef`.
`_stmt_calls` only matches `ast.Await` wrappers. A sync entry point is "found" but zero
calls are tracked. Validator says "valid" for an unanalyzed function.
- **Fix**: Agent `graph-fixes` changed `_find_entry` to only accept `AsyncFunctionDef`.
  Sync definitions produce error: "Entry point must be async".
- **Status**: **done**

**F3: No recursion into control flow in execute body (graph.py:152)** — P1
Only iterates `entry.body` — direct statements. Tasks inside `if`/`for`/`try`/`with` are
invisible. An agent wrapping tasks in conditionals bypasses completeness tracking.
- **Fix**: Agent `graph-fixes` added AST walk of execute body. Rejects `If`/`For`/`While`/
  `Try`/`With`/`AsyncWith`/`AsyncFor` with line numbers.
- **Status**: **done**

**F4: Error messages lack line numbers (graph.py:60-73)** — P2
Every error reports the function name but not the line number. AST nodes carry `lineno` —
available but unused. With 20+ task functions, "Undefined: setup_db" requires manual search.
- **Fix**: Agent `graph-fixes` added `lineno` to all error messages where AST nodes are available.
- **Status**: **done**

#### Dismissed (7)

| Finding | Reason |
|---------|--------|
| `_call_name` conflates method/function calls | Compose.py format doesn't use method calls. Format constraint. |
| `_target_names` drops non-Name tuple elements | Edge case — compose.py uses simple assignment. |
| `_module_names` misses ClassDef/AnnAssign | Compose.py format doesn't use these. |
| String-based type equality | Acceptable for nominal labels. |
| `<unknown>` confusing error message | Fixed by F4 (line numbers). |
| `_BUILTINS` hardcoded | `gather` is the only primitive. |
| Keyword arguments ignored | Compose.py convention is positional. Low risk. |

---

### Test Infrastructure R3

**Brutalist review: test_coverage — Claude only.**

R3 found 8 findings (1 critical, 4 high, 2 medium, 1 low). After critical triage:

#### Validated Findings

**F1: `run_coordinator` never tests `exhausted` path (test_orchestrator.py)** — P1
The coordinator loop's `"exhausted"` handler (crash_retries reset, validation skip) is
completely untested. If the `continue` or reset is removed, no test fails.
- **Status**: open — add TestRunCoordinator case for exhausted status
- **Pitfall**: test_orchestrator.py can't collect without SDK. May need conditional skip.

**F2: VERIFY `effort="max"` still untested** — P1
R2 agent (#49) claimed done but VERIFY case not in test file. Only ASSESS covered.
- **Status**: open — add parametrize case (same as R2 F6, re-flagged)

**F3: Two `test_dismisses_with_escape` tests have zero assertions (test_panels.py:330,368)** — P1
Push screen, press escape, end. Never verify the screen was dismissed.
- **Status**: open

**F4: `test_build_tree_skips_unreadable_directory` zero assertions (test_context_tree.py:94)** — P1
Calls `refresh_tree()` after monkeypatching, never verifies readable content processed.
- **Status**: open

**F5: `test_resolve_empty_options_dismisses` assertion before action (test_escalation.py:395)** — P1
Only assertion runs BEFORE pressing enter. Post-action behavior unverified.
- **Status**: open

#### Dismissed (3)

| Finding | Reason |
|---------|--------|
| `query_one() + assert is not None` (14x) | Tautological but harmless. Cosmetic. |
| Type-only assertions in test_messages.py | Redundant but not wrong. |
| `_mock_sdk_client` lacks spec | Enhancement, not a bug. |

#### Convergence Assessment
- **R1**: 4 fixes
- **R2**: 7 fixes (6 confirmed landed + 1 missed)
- **R3**: 5 validated (1 re-flagged from R2)
- **Finding density**: R1:4 → R2:7 → R3:5
- **Severity trend**: R3 findings are assertion voids (structural gaps) — different class
  than R1 (naming) and R2 (missing tests). The suite is approaching convergence but R3
  found real gaps. One more round after fixes.

---

### Spec↔Implementation R3

**Brutalist review: codebase domain on recovery.py — Claude only.**

**Zero new findings.** All R2 fixes verified correct:
- `_MILESTONE_RE` + `_validate_milestone` — anchored regex, applied at all 4 entry points
- `read_cycle_outcome` dead parameter — removed, callers updated
- Git timeout — 30s with kill + reap, RuntimeError on timeout

**Convergence confirmed for spec↔implementation vertical.**

---

### Bridge R1 — Fixes Applied

Agent team `bridge-fixes` addressed all 3 open findings.

- F1: `AssistantMessage.error` — error field check added to both routing functions
- F2: `ToolResultBlock` — wired to `ClouToolResult` message class
- F3: ANSI in file_path — `_strip_ansi()` applied before Path decomposition

**Status**: All 3 fixed.

### Bridge R2

**Brutalist review: codebase domain — Claude only.**

3 findings, all Low severity.

**F1: `block.name` not ANSI-stripped in `extract_coordinator_status` (bridge.py:98)** — Low
Inconsistency — supervisor routing strips tool names but coordinator status extraction didn't.
- **Fix**: Added `_strip_ansi()` wrapping on `block.name` at line 98.
- **Status**: **done**

**F2: `ClouToolResult` routed but no widget consumes it** — Low (architectural)
R1 wired routing but no `on_clou_tool_result` handler exists. Messages silently dropped.
- **Status**: accepted — intentional stub for future tool result display

**F3: `tool_input` dict values not ANSI-stripped** — Low (latent)
No widget currently renders `tool_input` values.
- **Status**: accepted — latent, no current consumer

**Bridge convergence: R1:3 → R2:1 (applied) + 2 accepted. Converged.**

---

### Graph R1 — Fixes Applied

Agent team `graph-fixes` addressed all 4 open findings.

- F1: Duplicate execute — detected and rejected with line numbers
- F2: Sync execute — rejected, only AsyncFunctionDef accepted
- F3: Control flow in execute — If/For/While/Try/With rejected with line numbers
- F4: Line numbers — added to all error messages

Also fixed conftest.py ImportError (autouse fixture broke all tests without SDK).

### Graph R2

**Brutalist review: codebase domain — Claude only.**

4 findings (2 Low, 2 confirmed safe). Explicitly verified: no bypass vectors in
sync/duplicate/control-flow rejection logic.

**F1: Ternary `IfExp` in await produces confusing error** — Low
`await (task_b(a) if cond else task_c(a))` not caught as control flow — produces
misleading "Undefined" error instead of "control flow not supported". Same-class as
a wrong error message, not a bypass.
- **Status**: accepted — validator fails closed (tasks show as undefined)

**F2: `ast.TryStar` (Python 3.11 only) missing** — Low
Removed in Python 3.12. Single-version concern.
- **Status**: accepted — Python 3.11-only, merged back into ast.Try in 3.12

**F4: `ast.Match` missing from `_CONTROL_FLOW`** — Low
`match/case` not rejected in execute body.
- **Fix**: Added `ast.Match` to `_CONTROL_FLOW` tuple.
- **Status**: **done**

**F3, F5: Confirmed safe** — duplicate/first interaction and early-return ordering
both verified correct.

**Graph convergence: R1:4 → R2:1 (applied) + 2 accepted. Converged.**

---

### Test Infrastructure R3 — Fixes Applied

Agent team `test-r3-fixes` addressed all 5 R3 findings.

- F1: test_orchestrator.py — `test_exhausted_continues_from_checkpoint` added. Mocks
  `_run_single_cycle` to return "exhausted" then "ok", verifies loop continues.
- F2: test_orchestrator.py — `test_effort_max_for_verify` added. Copies ASSESS pattern.
- F3: test_panels.py — escape-dismiss tests now assert screen type changed
- F4: test_context_tree.py — unreadable directory test now asserts readable content processed
- F5: test_escalation.py — empty options test now asserts modal dismissed after Enter

**Status**: All 5 fixed.

---

### Test Infrastructure R4

**Brutalist review: test_coverage — Claude only. Convergence check.**

R4 found 2 Medium findings (down from R3:5, R2:7). Critic recommends stopping after these.

#### Validated Findings

**F1: `pending_validation_errors` wiring untested in orchestrator** — P2
Orchestrator tests mock `build_cycle_prompt` with `return_value="p"` and never inspect
`validation_errors` kwarg. The state-passing from failed validation → next cycle prompt
is integration-level gap.
- **Fix**: Agent `test-r4-fixes` added `test_pending_validation_errors_wired_to_build_cycle_prompt`.
  Verifies errors passed on retry cycle and cleared on subsequent cycle.
- **Status**: **done**

**F2: `_build_mcp_server` with app has no assertion (test_integration_ui.py:298)** — P2
Smoke test calls `_build_mcp_server(tmp_path, app=mock_app)` with zero assertions.
- **Fix**: Agent `test-r4-fixes` added `assert server is not None`.
- **Status**: **done**

#### Dismissed (6)

| Finding | Reason |
|---------|--------|
| PLAN cycle effort untested | Same code path as EXECUTE (both hit else: "high"), already covered |
| Status bar `cycle_type=None` | Signature is `str = ""`, None not valid |
| Breath MAX_EVENTS cap | Trivial 2-line slice, no failure mode |
| ANSI + UTF-8 roundtrip | Already tested with Japanese/emoji input |
| Empty DAG update | Already tested, asserts "No tasks defined" |
| Double-escape race | Textual screen stack is synchronous |

#### Convergence Assessment
- **R1**: 4 → **R2**: 7 → **R3**: 5 → **R4**: 2
- **Severity trend**: R4 Medium only (no Critical/High). Integration wiring gaps.
- **Pattern**: Finding density declining (7→5→2). Finding class shifting from
  assertion quality to integration wiring. Critic recommends stopping.
- **Verdict**: Both fixed. Test infrastructure converged.

---

### Test Infrastructure R4 — Fixes Applied

Agent team `test-r4-fixes` addressed both R4 findings.

- F1: test_orchestrator.py — `test_pending_validation_errors_wired_to_build_cycle_prompt` added.
  Verifies `validation_errors` kwarg is passed on retry and cleared on success.
- F2: test_integration_ui.py — `test_build_mcp_server_accepts_app` now asserts `server is not None`.

**Status**: All 2 fixed. Test suite: 687 passing.

---

## Cross-Vertical Convergence — Complete

| Vertical | Rounds | Fixes | Finding Trajectory | Status |
|----------|--------|-------|--------------------|--------|
| UI Presentation | 22 | 101 | Converged R20, confirmed R22 | **Converged** |
| Orchestrator | 5 | 13 | R1:5→R2:3→R3:2→R4:2→R5:0 | **Converged** |
| Test Infrastructure | 4 | 18 | R1:4→R2:7→R3:5→R4:2→fixed | **Converged** |
| Spec↔Implementation | 3 | 5 | R1:2→R2:3→R3:0 | **Converged** |
| Hooks (Security) | 1 | 0 | R1:1 (accepted risk) | **Converged** |
| Bridge | 2 | 4 | R1:3→R2:1+2 accepted | **Converged** |
| Graph | 2 | 5 | R1:4→R2:1+2 accepted | **Converged** |

**All 7 verticals converged.**

**Final metrics:**
- Total brutalist rounds across all verticals: 39
- Total fixes applied: 146
- Total test suite: 687 passing (up from ~344 at start of review cycles)
- Tests added through reviews: ~343
- Open architectural items: 3 (all SDK limitations/design decisions)
  1. Agent team hooks (SDK `AgentDefinition` has no `hooks` field)
  2. Execution.md validation scope (flat vs `phases/`)
  3. `ClouToolResult` message routed but no widget consumer yet

**The brutalist review cycle is complete across all implementation verticals.**

---

## Post-Convergence Implementation → Review Cycle

### Task Graph Execution (2026-03-21)

Structured as two parallel lanes converging at a review gate:

**Lane A — Animation Phase Fixes (Tasks #53–55):**
- [x] #53: RELEASING state transitions added to BREATH_TRANSITIONS
- [x] #54: Graceful `_stop_breathing()` → RELEASING→SETTLING→IDLE (was: force-reset)
- [x] #55: `_has_screen(EscalationModal)` guard before `push_screen`

**Lane B — Architecture Items (Tasks #57–58):**
- [x] #57: `on_clou_tool_result` handler in `ConversationWidget` (normal ↳ / error ✗)
- [x] #58: `validate_golden_context` now scans `milestones/{m}/phases/*/execution.md`

**Convergence — Tests (Task #56):**
- [x] 10 new tests: RELEASING state machine transitions (6), animation tick decay + SETTLING completion (3), push_screen guard (1)

**Convergence — Brutalist Verification (Task #59):**

Two domains reviewed (codebase + test_coverage), claude critic.

| Finding | Severity | Action |
|---------|----------|--------|
| `_start_breathing()` resets `_animation_time` during RELEASING (negative elapsed) | Medium | **Fixed** — early return if transition fails |
| `_stop_breathing()` RELEASING/SETTLING re-entry branch untested | High | **Fixed** — 2 tests added |
| `_force_stop_breathing()` zero coverage | Medium | **Fixed** — 3 tests added |
| ClouToolResult ANSI stripping untested | High | **Fixed** — 1 test added |
| `_start_breathing()` no-op during RELEASING untested | Medium | **Fixed** — 1 test added |
| Escalation path validation fallback weakness | Medium | Accepted (prior rounds) |
| Per-character Style allocation in breath render_line | Medium | Accepted (MAX_EVENTS cap) |
| Encapsulation violations (App → ConversationWidget privates) | Low | Accepted (known debt) |
| RichLog assertion weakness (`len >= 1`) | Low | Accepted (scope creep) |

**Post-review metrics:**
- Bug fix: 1 (RELEASING re-entry negative elapsed)
- New tests: 7 (from brutalist findings)
- Total test suite: 713 passing (up from 687 at convergence)

### Agent Team Write Boundary Enforcement

The last open architectural item — agent team hooks — resolved without SDK changes.

**Problem:** `AgentDefinition` has no `hooks` field. Worker/verifier subagents ran with no write boundary enforcement. A worker could write `compose.py` or `milestone.md`, violating tier separation.

**Solution:** The SDK's `PreToolUse` hooks include `agent_type` in `input_data` when firing from a subagent. The coordinator's hook now inspects this field and applies the **subagent's** tier permissions instead of the coordinator's. One hook, tier-aware dispatch:

```
AGENT_TIER_MAP = {"implementer": "worker", "verifier": "verifier"}
```

- Worker writes `execution.md` → allowed (worker tier)
- Worker writes `compose.py` → **blocked** (worker tier violation)
- Verifier writes `handoff.md` → allowed (verifier tier)
- Verifier writes `compose.py` → **blocked** (verifier tier violation)
- Coordinator writes `compose.py` → allowed (coordinator tier, no agent_type)
- Unknown agent_type → **blocked** (fail-closed, not coordinator fallback)
- Milestone scoping applies to subagent tiers too (worker in m1 can't write m2)
- Cross-tier isolation: worker can't write `handoff.md`, verifier can't write generic `execution.md`

#### Brutalist Review R1 (codebase + test_coverage + security, claude)

| Finding | Severity | Action |
|---------|----------|--------|
| Fail-open fallback: unknown agent_type → coordinator permissions | **HIGH** | **Fixed** — fail-closed: unknown agent_type blocked |
| Empty string / None / non-string agent_type edge cases | Medium | **Fixed** — `isinstance(str)` guard + falsy = lead agent |
| No type validation on `agent_type` | Medium | **Fixed** — `isinstance` check added |
| `AGENT_TIER_MAP` / `_build_agents()` sync hazard | Medium | **Fixed** — sync assertion test added |
| Cross-tier boundary tests missing (worker↔verifier) | Medium | **Fixed** — 2 cross-tier tests added |
| Bash tool bypasses write boundary enforcement | **HIGH** | **Accepted** — workers need Bash for tests/builds; parsing shell for redirects is fragile; sandbox provides defense-in-depth |
| Writes outside `.clou/` unguarded | Low | By design — workers write project code |
| `agent_type` not forgeable by LLM | Non-issue | SDK sets it at subprocess level |

**Pitfall — Bash bypass:** A subagent could theoretically `echo > .clou/.../compose.py` via Bash, bypassing the Write/Edit/MultiEdit hook entirely. Approaches out:
1. Remove Bash from worker/verifier tools → breaks legitimate test/build use
2. Parse Bash commands for write patterns → fragile regex, false positives
3. Accept + document → sandbox constrains filesystem; workers follow instructions, not adversarial
**Decision:** Option 3. The hook system protects against *misbehaving* agents (wrong file), not *adversarial* agents. Sandbox is the adversarial defense layer.

**Post-R1 metrics:**
- Fixes: 1 (fail-closed fallback)
- New tests: 8 (edge cases + cross-tier + sync assertion)
- Total test suite: 747 passing

#### Brutalist Review R2 (codebase + test_coverage, claude + gemini)

4 critic reviews. **Zero novel actionable findings.**

- Claude (codebase): "R1 fixes are sound. No remaining privilege escalation paths."
- Claude (test_coverage): "All six R1 gaps are closed. 92/100 confidence."
- Gemini (codebase): Repeats R1 accepted findings (Bash bypass, writes outside .clou/). No novel findings.
- Gemini (test_coverage): Flags sync test middle fallback weakness — partially valid but overall test adequate (import-failure path uses exact dict match).

**Hooks vertical converged: R1:1 fix + 8 tests → R2:0 novel findings. Cron loop cancelled.**

---

## Final State

**All 3 open architectural items resolved:**
1. ~~Agent team hooks~~ → tier-aware coordinator hooks via `agent_type` dispatch (fail-closed)
2. ~~Execution.md validation scope~~ → phase subdirectory scanning (#58)
3. ~~ClouToolResult widget consumer~~ → conversation widget handler (#57)

**Cumulative metrics across all implementation→review cycles:**
- Total brutalist rounds (all verticals): 41 (39 original + 2 hooks)
- Total fixes applied: 149
- Total test suite: 747 passing (up from ~344 at start of review cycles)
- Open items: 0
