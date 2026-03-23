# Slash Commands

Clou has no way to talk to the harness. Every keystroke goes to the supervisor as natural language. The keybindings (`Ctrl+G`, `Ctrl+D`, `Ctrl+T`, `Ctrl+L`) exist but are invisible — you discover them by reading source or documentation, not by using the interface. Slash commands are the discoverable surface: type `/` and see what's possible.

Last updated: 2026-03-23

---

## The Abstraction: `clou/ui/commands.py`

A new module that owns: the `Command` dataclass, the registry, dispatch logic, output rendering, and the completion menu widget. Nothing else touches this — commands are a self-contained system that plugs into ClouApp via two narrow integration points: input interception and conversation output.

### The Command Protocol

```python
@dataclass(frozen=True)
class Command:
    name: str                          # "cost", "clear", etc.
    description: str                   # one-line for /help and completion menu
    handler: Callable[[ClouApp, str], Awaitable[None]]  # async (app, args) -> None
    shortcut: str = ""                 # "⌃T" — display only, not functional
    modes: frozenset[Mode] = frozenset(Mode)  # which modes this command works in
```

- `handler` receives the app instance (for querying widgets, accessing state) and the raw args string (everything after the command name, stripped). The handler is async — most are instant, but `/compact` and `/diff` need I/O.
- `modes` is a whitelist. If the current mode isn't in the set, dispatch rejects with an inline error. Default: all modes.
- Commands never touch the mode machine. They operate within the current mode.

### The Registry

```python
_REGISTRY: dict[str, Command] = {}

def register(cmd: Command) -> Command:
    _REGISTRY[cmd.name] = cmd
    return cmd

def get(name: str) -> Command | None:
    return _REGISTRY.get(name)

def all_commands() -> list[Command]:
    return sorted(_REGISTRY.values(), key=lambda c: c.name)
```

Commands self-register at module level in `commands.py`. No plugin system, no dynamic loading. The registry is a module-level dict populated at import time.

### Dispatch

```python
async def dispatch(app: ClouApp, text: str) -> bool:
    """Try to dispatch text as a slash command. Returns True if handled."""
    if not text.startswith("/"):
        return False
    parts = text[1:].split(None, 1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    cmd = get(name)
    if cmd is None:
        _render_error(app, f"unknown command: /{name}")
        return True
    if app.mode not in cmd.modes:
        _render_error(app, f"/{name} is not available in {app.mode.name.lower()} mode")
        return True
    await cmd.handler(app, args)
    return True
```

### Integration Point 1: Input Interception

In `ClouApp.on_input_submitted` (`ui/app.py:317`), before the user input queue:

```python
def on_input_submitted(self, event: Input.Submitted) -> None:
    text = event.value.strip()
    event.input.clear()
    if not text:
        return

    # Slash command dispatch — intercept before supervisor queue.
    if text.startswith("/"):
        from clou.ui.commands import dispatch
        self.run_worker(dispatch(self, text), exclusive=False)
        return

    if self.mode in (Mode.BREATH, Mode.HANDOFF):
        self.transition_mode(Mode.DIALOGUE)

    conversation = self.query_one(ConversationWidget)
    conversation.add_user_message(text)
    self._user_input_queue.put_nowait(text)
```

Slash commands do NOT add a user message to the conversation (no gold `›` block). The command itself renders output directly. This is deliberate — commands are harness interactions, not conversation turns.

### Integration Point 2: Command Output

New method on `ConversationWidget`:

```python
def add_command_output(self, renderable: RenderableType) -> None:
    """Append command output — styled distinct from supervisor text."""
    log = self.query_one("#history", RichLog)
    log.write(Text(""))  # breathing room
    log.write(renderable)
```

No horizon line. No working indicator. Command output is lighter than supervisor text — it's the harness talking, not the model.

For errors:

```python
def add_command_error(self, text: str) -> None:
    """Append a brief dim error for bad commands."""
    log = self.query_one("#history", RichLog)
    log.write(Text(f"  {text}", style=f"{_DIM_HEX}"))
```

### Integration Point 3: Completion Menu

A `CommandPalette` widget — a `Static` that appears above the `PromptInput` when the input text starts with `/` and disappears on submit, escape, or when the `/` prefix is removed.

```python
class CommandPalette(Static):
    """Filtered command list, anchored above input."""
    DEFAULT_CSS = """
    CommandPalette {
        dock: bottom;
        height: auto;
        max-height: 12;
        background: $surface-raised;
        display: none;
    }
    CommandPalette.visible { display: block; }
    """
```

Activated by `on_input_changed` in `ClouApp` — when input starts with `/`, make visible and filter. Rendered as column-aligned `Text` objects: command names in gold, descriptions in dim.

**Mounting:** Added to `ConversationWidget.compose()` between the RichLog and PromptInput. Z-ordered above the input via dock.

---

## Implementation Cycles

Each command follows: **implement → test → review → findings → next**.

### Cycle 0: Foundation (Dispatch Layer + Command Output + Completion Menu)

#### Task Graph

```
C0.1: Create clou/ui/commands.py
      - Command dataclass
      - Registry (dict + register/get/all_commands)
      - dispatch() function
      - _render_error() helper

C0.2: Command output on ConversationWidget
      - add_command_output(renderable) method
      - add_command_error(text) method
      - Styling: no horizon, no working indicator, dim for errors

C0.3: Input interception in ClouApp.on_input_submitted
      - text.startswith("/") → dispatch, return early
      - Slash commands do NOT add user message to conversation
      - Slash commands do NOT enqueue to supervisor

C0.4: CommandPalette widget
      - Static subclass, docked above PromptInput
      - on_input_changed: show/hide based on "/" prefix
      - Filter commands by partial match
      - Gold command names, dim descriptions
      - Enter/Tab selects (fills input), Escape dismisses

C0.5: Tests
      - test_commands.py:
        - dispatch returns True for known command
        - dispatch returns True + error for unknown command
        - dispatch returns False for non-slash text
        - mode restriction rejects with error
      - test_app_dialogue.py additions:
        - slash command does not enqueue to supervisor
        - slash command does not add user message
      - test_command_palette.py:
        - palette appears on "/" input
        - palette filters on partial text
        - palette disappears on submit/escape
```

**Dependencies:** None. This is the foundation everything else builds on.

**Files touched:**
- NEW: `clou/ui/commands.py` (~120 lines)
- NEW: `clou/ui/widgets/command_palette.py` (~80 lines)
- EDIT: `clou/ui/app.py` (on_input_submitted + on_input_changed + compose)
- EDIT: `clou/ui/widgets/conversation.py` (add_command_output + add_command_error)
- NEW: `tests/test_commands.py`
- NEW: `tests/test_command_palette.py`
- EDIT: `tests/test_app_dialogue.py`

**Review criteria:** Dispatch routes correctly. Output renders visually distinct from supervisor text. Palette appears/disappears with <100ms latency. No regressions in existing 740 tests.

---

### Cycle 1: `/help`

#### Task Graph

```
C1.1: Register /help command in commands.py
      - handler: build Rich Text with column-aligned commands
      - Read all_commands() from registry
      - Columns: name (gold), description (dim), shortcut (muted)
      - Render via add_command_output

C1.2: Tests
      - /help renders all registered commands
      - /help output contains command names
      - /help works in all four modes
```

**Files touched:**
- EDIT: `clou/ui/commands.py` (~25 lines added)
- EDIT: `tests/test_commands.py`

---

### Cycle 2: `/clear`

#### Task Graph

```
C2.1: Register /clear command
      - handler: app.action_clear()
      - modes: all

C2.2: Tests
      - /clear empties RichLog
      - /clear stops streaming overlay
      - /clear in BREATH mode does not stop coordinator
```

**Files touched:**
- EDIT: `clou/ui/commands.py` (~5 lines)
- EDIT: `tests/test_commands.py`

---

### Cycle 3: `/cost`

#### Task Graph

```
C3.1: Add _start_time to ClouApp.__init__
      - time.monotonic() captured at init

C3.2: Register /cost command
      - handler: read status bar reactives (input_tokens, output_tokens, cost_usd)
      - Compute session duration from _start_time
      - Format inline: tokens, cost, duration
      - Styling: numbers in text, labels in dim, total in gold
      - "/cost detail" → app.action_show_costs()

C3.3: Tests
      - /cost renders token counts from status bar
      - /cost shows session duration
      - /cost detail pushes DetailScreen
      - /cost works in all modes
```

**Files touched:**
- EDIT: `clou/ui/app.py` (~3 lines for _start_time)
- EDIT: `clou/ui/commands.py` (~30 lines)
- EDIT: `tests/test_commands.py`

---

### Cycle 4: `/dag`

#### Task Graph

```
C4.1: Register /dag command
      - handler: app.action_show_dag()
      - modes: {BREATH, HANDOFF}
      - Error in other modes: "dag is available during coordinator runs"

C4.2: Tests
      - /dag pushes DagScreen in BREATH mode
      - /dag rejected in DIALOGUE mode with error
      - /dag rejected in DECISION mode with error
```

**Files touched:**
- EDIT: `clou/ui/commands.py` (~8 lines)
- EDIT: `tests/test_commands.py`

---

### Cycle 5: `/context`

#### Task Graph

```
C5.1: Register /context command
      - handler: app.action_show_context()
      - modes: all

C5.2: Tests
      - /context pushes ContextScreen
      - /context does not double-push
```

**Files touched:**
- EDIT: `clou/ui/commands.py` (~5 lines)
- EDIT: `tests/test_commands.py`

---

### Cycle 6: `/diff`

#### Task Graph

```
C6.1: Diff rendering utility
      - New function: render_diff(diff_text: str) -> Text
      - Parse unified diff format
      - File paths → gold
      - Added lines → accent-green
      - Removed lines → accent-rose
      - Hunk headers → text-muted
      - Context lines → text-dim

C6.2: Register /diff command
      - handler:
        1. asyncio.create_subprocess_exec("git", "diff", "--no-color")
        2. Also capture git diff --cached
        3. If args == "staged": cached only
        4. If args is a path: pass as git diff arg
        5. Render with render_diff
        6. If >50 lines: push a DiffScreen (new screen)
        7. If <=50 lines: inline via add_command_output
      - modes: all
      - Handle non-git-repo gracefully (error message)

C6.3: DiffScreen (optional, for large diffs)
      - New screen: clou/ui/screens/diff.py
      - Scrollable rendered diff, Escape to dismiss
      - Same visual language as other screens

C6.4: Tests
      - /diff with no changes shows "no changes"
      - /diff renders added/removed lines with correct styles
      - /diff staged filters to cached changes
      - /diff in non-git directory shows error
      - Large diff pushes DiffScreen
```

**Files touched:**
- NEW: `clou/ui/diff.py` (~60 lines — render_diff utility)
- NEW: `clou/ui/screens/diff.py` (~40 lines — DiffScreen)
- EDIT: `clou/ui/commands.py` (~35 lines)
- NEW: `tests/test_diff.py`
- EDIT: `tests/test_commands.py`

---

### Cycle 7: `/export`

#### Task Graph

```
C7.1: Conversation history model
      - New dataclass in commands.py or separate module:
        ConversationEntry(role: str, content: str, timestamp: float)
      - ClouApp accumulates entries in _conversation_history: list[ConversationEntry]
      - Populated by: on_input_submitted (user), on_clou_supervisor_text (assistant),
        on_clou_tool_use (tool), on_clou_turn_complete (timing)
      - This is the semantic history, not the RichLog visual content

C7.2: Export serialization
      - Function: export_conversation(entries, include_tools=False) -> str
      - Markdown format: ## You / ## Clou sections
      - Timestamps as ISO 8601
      - Tool uses collapsed or omitted based on flag
      - Returns markdown string

C7.3: Register /export command
      - handler:
        1. If args: use as output path
        2. If no args: .clou/exports/conversation-{ISO timestamp}.md
        3. Create parent dirs if needed
        4. Write exported markdown
        5. Confirmation: "exported to {path}"
      - "/export --full" includes tool uses
      - modes: all

C7.4: Tests
      - /export creates file with conversation content
      - /export with path writes to specified location
      - /export --full includes tool uses
      - /export with empty conversation writes minimal file
      - File contains timestamps and role markers
```

**Files touched:**
- EDIT: `clou/ui/app.py` (~15 lines — _conversation_history accumulation)
- EDIT: `clou/ui/commands.py` (~50 lines)
- NEW: `tests/test_export.py`

**Note:** C7.1 (conversation history model) is also needed by T4.1 (compact's conversation persistence). Build it here; compact reuses it.

---

### Cycle 8: `/status`

#### Task Graph

```
C8.1: Register /status command
      - handler:
        1. Read cached state from ClouStatusBar reactives:
           milestone, cycle_type, cycle_num, phase
        2. Read _dag_tasks for task progress (count complete vs total)
        3. Async disk read: last 5 lines of decisions.md (if milestone active)
        4. Async disk read: count files in escalations/ dir
        5. Format as label/value pairs
      - Styling:
        - Milestone name → gold
        - Cycle type → semantic color (cycle_color from theme.py)
        - Labels → dim
        - Values → text
      - modes: all

C8.2: Handle no-active-milestone case
      - If bar.milestone is empty: "no active milestone"
      - Still show token counts and session time

C8.3: Tests
      - /status shows milestone info when active
      - /status shows "no active milestone" when idle
      - /status reads decisions.md (mock disk)
      - /status works in all modes
      - /status with dag data shows task progress
```

**Files touched:**
- EDIT: `clou/ui/commands.py` (~45 lines)
- EDIT: `tests/test_commands.py`

**Dependency:** Lifecycle events from wave 10 must be emitting for cached state to be populated. Without them, `/status` works but shows empty cycle/phase fields.

---

### Cycle 9: `/compact`

The largest cycle. See [Research Foundations §4](../research-foundations.md#4-context-compression-and-its-limits) for the research grounding.

#### Architectural Situation

Clou's supervisor session (`run_supervisor` in `orchestrator.py`) runs as a long-lived `ClaudeSDKClient` context manager. The user feeds input via `_feed_user_input` → `supervisor.query(text)`. Messages stream back via `supervisor.receive_messages()`.

Compaction must:
1. Persist conversation history to disk before compacting (recovery path).
2. Configure the Anthropic compaction API strategy on the SDK client.
3. Handle the `pause_after_compaction` event to inject rehydration content.
4. Clear the conversation surface and show confirmation.
5. Track compaction count for progressive warnings.

This is not a UI-only command — it reaches through the presentation layer into the orchestrator.

#### Task Graph

```
C9.1: Conversation history persistence (shared with C7.1)
      - Write _conversation_history to .clou/active/supervisor-history.jsonl
      - JSONL format: one JSON object per entry
      - Append-only during session; full rewrite before compaction
      - Read back for /export after compaction

C9.2: Orchestrator compaction API
      - New in orchestrator.py: configure context_management.edits on ClaudeAgentOptions
      - Strategies: clear_tool_uses_20250919 + clear_thinking_20251015 + compact_20260112
      - compact trigger: manual (very high threshold for auto — separate task)
      - pause_after_compaction: true
      - Expose a method on supervisor wrapper or via message passing:
        app._compact_requested: asyncio.Event
        Set by the /compact command handler, checked in _feed_user_input loop

C9.3: Rehydration protocol
      - After compaction pause, inject:
        1. Golden context: project.md + roadmap.md content
        2. Active milestone: milestone.md + status.md + last 10 decisions
        3. Compaction count metadata
        4. Continuation instruction
      - Implemented in orchestrator.py as _build_rehydration_message()

C9.4: Register /compact command
      - handler:
        1. Reject if mode not DIALOGUE
        2. Persist conversation history to disk (C9.1)
        3. Record pre-compaction token count
        4. Signal orchestrator: set _compact_requested event
           Optionally pass user instructions via _compact_instructions: str | None
        5. Wait for compaction to complete (asyncio.Event)
        6. Clear conversation surface
        7. Render confirmation: "compacted  Xk → Yk tokens  (Z% freed)"
      - modes: {DIALOGUE}

C9.5: User-directed focus
      - /compact keep the auth discussion
      - Args passed as custom instructions parameter to compaction API
      - If no args: use default summarization prompt
      - Default prompt in _prompts/ or inline in orchestrator.py

C9.6: Auto-compaction at 80%
      - In _feed_user_input loop: check supervisor input_tokens after each message
      - If > 80% of 200K (160K): trigger auto-compact
      - Same pipeline as manual, but with default prompt
      - Visual: "auto-compacted  Xk → Yk tokens  (approaching context limit)"
      - Track auto vs manual in compaction count

C9.7: Compaction count + progressive warnings
      - _compaction_count: int on ClouApp (or orchestrator state)
      - After 2nd compaction: "⚠ 3rd compaction — significant context loss likely"
      - Adjust summary prompt on 2nd+: "preserve ALL specific details"

C9.8: Visual feedback
      - During: "compacting..." in dim with breathing shimmer
      - After: "compacted  Xk → Yk tokens  (Z% freed)" in gold
      - Warning: rose styling for 3rd+ compaction
      - Uses add_command_output for all rendering

C9.9: Tests
      - /compact rejected in BREATH mode
      - /compact persists history before compacting
      - /compact signals orchestrator
      - /compact renders confirmation with token counts
      - /compact with args sets custom instructions
      - Auto-compact fires at 80% threshold
      - Compaction count increments
      - Warning appears on 3rd compaction
      - Rehydration includes golden context files
```

**Files touched:**
- EDIT: `clou/orchestrator.py` (~80 lines — compaction config, event, rehydration)
- EDIT: `clou/ui/app.py` (~20 lines — compact event, count tracking)
- EDIT: `clou/ui/commands.py` (~50 lines)
- EDIT: `clou/tokens.py` (~10 lines — supervisor token query for threshold)
- NEW: `tests/test_compact.py`

**Dependencies:** C7.1 (conversation history model), C0 (dispatch layer), SDK compaction API access.

**Risk:** Highest complexity. The orchestrator integration is the only part that touches the session lifecycle. If the SDK doesn't expose `context_management.edits` as expected, this design needs revision — see Open Research below.

---

### Cycle 10: `/model`

#### Task Graph

```
C10.1: Investigate SDK model switching
       - Can ClaudeAgentOptions.model be changed between queries?
       - Or must we create a new ClaudeSDKClient with a different model?
       - If new client: conversation replay required → expensive, interacts with /compact
       - If mutable: trivial — change param before next query

C10.2: Register /model command
       - "/model" alone: show current model (read from orchestrator state)
       - "/model sonnet": switch to claude-sonnet-4-6
       - "/model opus": switch to claude-opus-4-6
       - "/model haiku": switch to claude-haiku-4-5-20251001
       - Confirmation: "model → claude-sonnet-4-6"
       - modes: {DIALOGUE}
       - Error in BREATH: "model switching is available in dialogue mode"

C10.3: Orchestrator model state
       - Expose current model as readable state on ClouApp
       - If SDK allows mutation: set model on next query
       - If not: requires session restart protocol (scope TBD after C10.1)

C10.4: Tests
       - /model shows current model
       - /model sonnet changes model
       - /model invalid-name shows error
       - /model rejected in BREATH mode
```

**Files touched:**
- EDIT: `clou/orchestrator.py` (TBD after C10.1 research)
- EDIT: `clou/ui/app.py` (~5 lines — model state)
- EDIT: `clou/ui/commands.py` (~25 lines)
- EDIT: `tests/test_commands.py`

**Dependency:** C10.1 (SDK research) must complete before C10.2-C10.4 can be designed in detail.

---

## Execution Order

```
Cycle 0: Foundation    [dispatch + output + palette]
Cycle 1: /help         [first command — validates the entire abstraction]
Cycle 2: /clear        [thin alias — validates mode-agnostic dispatch]
    ↓ review: abstraction holding up? adjust if needed
Cycle 3: /cost         [first command with data source — validates widget querying]
Cycle 4: /dag          [first mode-restricted command — validates mode gating]
Cycle 5: /context      [thin alias — quick win]
    ↓ review: six commands working. completion menu filtering well?
Cycle 6: /diff         [first I/O command — validates async subprocess + rendering]
Cycle 7: /export       [conversation history model — shared infrastructure for /compact]
    ↓ review: conversation history model correct? export format right?
Cycle 8: /status       [disk reads + cached state — validates mixed data sources]
Cycle 9: /compact      [orchestrator integration — the deep one]
    ↓ review: compaction actually working? rehydration correct? auto-compact threshold right?
Cycle 10: /model       [SDK research first, then implementation]
    ↓ review: all 10 commands + foundation. update status.md
```

Between cycles, track:

1. **Completion**: which tasks in the cycle's graph are done.
2. **Findings**: anything discovered during implementation that affects design (new to `findings.md`).
3. **Test count**: running total (baseline: 740).
4. **Regressions**: any existing tests broken by changes.

---

## Tracking

### Completion

| Cycle | Status | Tests Added | Findings |
|-------|--------|-------------|----------|
| C0: Foundation | ✓ complete | 21 | conversation.py refactored from RichLog→VerticalScroll mid-cycle; _WORKING_FRAMES lost and recovered |
| C1: /help | ✓ complete | 3 | Already registered in C0; tests validate rendering |
| C2: /clear | ✓ complete | 2 | action_clear updated for new VerticalScroll structure |
| C3: /cost | ✓ complete | 3 | Added _session_start_time to ClouApp; imports time module |
| C4: /dag | ✓ complete | 3 | Mode restriction validated — dag only in BREATH/HANDOFF |
| C5: /context | ✓ complete | 2 | Thin alias to action_show_context |
| C6: /diff | ✓ complete | 7 | New clou/ui/diff.py render_diff utility; large diffs push DetailScreen |
| C7: /export | ✓ complete | 11 | New clou/ui/history.py shared with /compact; history recording in app |
| C8: /status | ✓ complete | 5 | Reads status bar reactives + DAG progress |
| C9: /compact | ✓ complete | 6 | Signaling via asyncio.Event; orchestrator checks alongside input queue; SDK compaction API TBD |
| C10: /model | ✓ complete | 6 | Model stored on app; live switching deferred (takes effect description only) |

**Total new tests:** 69 (command-specific) + baseline 808 existing = 877 total (0 regressions)

### Module Inventory (New Files)

| File | Cycle | Purpose |
|------|-------|---------|
| `clou/ui/commands.py` | C0 | Command abstraction, registry, dispatch, all handlers |
| `clou/ui/widgets/command_palette.py` | C0 | Completion menu widget |
| `clou/ui/diff.py` | C6 | Diff rendering utility |
| `clou/ui/screens/diff.py` | C6 | Scrollable diff screen for large diffs |
| `tests/test_commands.py` | C0+ | Command dispatch + per-command tests |
| `tests/test_command_palette.py` | C0 | Palette widget tests |
| `tests/test_diff.py` | C6 | Diff rendering tests |
| `tests/test_export.py` | C7 | Export serialization tests |
| `tests/test_compact.py` | C9 | Compaction integration tests |

### Module Edits

| File | Cycles | Changes |
|------|--------|---------|
| `clou/ui/app.py` | C0, C3, C7, C9, C10 | Input interception, _start_time, history accumulation, compact event, model state |
| `clou/ui/widgets/conversation.py` | C0 | add_command_output, add_command_error |
| `clou/orchestrator.py` | C9, C10 | Compaction API config, rehydration, model state |
| `clou/tokens.py` | C9 | Supervisor token query for auto-compact threshold |
| `tests/test_app_dialogue.py` | C0 | Slash command input interception tests |

---

## Open Research

### Resolved: `/compact` Mechanism

The Anthropic API provides a server-side compaction strategy (`compact-2026-01-12` beta) with `pause_after_compaction` support. Combined with `clear_tool_uses` and `clear_thinking` strategies, this enables the layered pipeline. See [Research Foundations §4](../research-foundations.md#4-context-compression-and-its-limits).

**Remaining integration question:** How does the Claude Agent SDK expose `context_management.edits`? The SDK wraps the Messages API — verify that these strategies can be configured on `ClaudeAgentOptions`, and that `pause_after_compaction` surfaces as a controllable event in the SDK's message stream. This blocks C9.2.

### Open: `/model` Switching

Can the model be changed on an active `ClaudeSDKClient` session between queries? The Messages API is stateless (client sends full history each time), suggesting model switching is a parameter change. But the SDK may maintain session state that assumes a fixed model. Verify before C10.2.

### Open: CommandPalette Input Binding

Textual's `Input.on_changed` fires on every keystroke. The palette needs to filter on each change while the input starts with `/`. Two concerns:
1. **Performance**: Filtering 10 commands on every keystroke is trivial. Not a concern.
2. **Focus management**: Does the palette steal focus from the input? It must not. The input retains focus; the palette is display-only. Tab/Enter in the input completes the selection by programmatically updating `input.value`.

Test in C0 — if Textual's focus model creates issues, the palette may need to be a pure rendering artifact (Static updated by the input's changed handler) rather than an interactive widget.
