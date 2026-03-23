# Implementation Status

## Current State
phase: active
wave: 12 (session persistence) ✓
next_action: none — all waves complete
blockers: none
tests: 929 passed + 3 skipped (SDK-dependent, skip when SDK absent)

## Completed

### Wave 1 — Call Graph Validator ✓
completed: 2026-03-19

| Module | Lines | Tests | Quality Gates |
|--------|-------|-------|---------------|
| `clou/graph.py` | 311 | 17 | ruff ✓ mypy ✓ pytest ✓ |
| `tests/test_graph.py` | 317 | — | — |

**What it does:** Validates compose.py call graphs via AST parsing. Five checks: well-formedness, completeness, acyclicity, type compatibility, convergence. Pure stdlib, zero I/O.

**Key decisions made during implementation:**
- Type-based cycle detection (not variable-flow) — sequential statements can't express data-flow cycles
- Self-dependency exclusion — `transform(x: A) -> A` doesn't self-loop
- Only `AsyncFunctionDef` nodes are task signatures; sync helpers ignored
- Only awaited calls are task dispatches; non-awaited calls (helpers) are structural, not graph edges

### Project Scaffolding ✓
- `pyproject.toml` — Python 3.12+, hatchling, ruff/mypy/pytest dev deps
- `clou/__init__.py` — package marker

### Wave 2 — Orchestrator Core ✓
completed: 2026-03-19
target: ~500-800 lines across 7 modules

**Phase 1** (parallel, no deps) ✓:
| Module | Lines | Tests | Quality Gates |
|--------|-------|-------|---------------|
| `clou/prompts.py` | 66 | 12 | ruff ✓ mypy ✓ pytest ✓ |
| `clou/tokens.py` | 89 | 14 | ruff ✓ mypy ✓ pytest ✓ |
| `clou/validation.py` | 238 | 30 | ruff ✓ mypy ✓ pytest ✓ |

**Phase 2** (parallel, light deps) ✓:
| Module | Lines | Tests | Quality Gates |
|--------|-------|-------|---------------|
| `clou/hooks.py` | 197 | 32 | ruff ✓ mypy ✓ pytest ✓ |
| `clou/tools.py` | 81 | 15 | ruff ✓ mypy ✓ pytest ✓ |
| `clou/recovery.py` | 293 | 25 | ruff ✓ mypy ✓ pytest ✓ |

Review fix: PostToolUse compose.py validation now coordinator-only (spec compliance).

**Phase 3** (integration) ✓:
| Module | Lines | Tests | Quality Gates |
|--------|-------|-------|---------------|
| `clou/orchestrator.py` | 443 | 34 | ruff ✓ mypy ✓ pytest ✓ |

**Key decisions:**
- `_to_sdk_hooks` returns `Any` — intentional type bridge between testable `HookConfig` and SDK `HookMatcher`
- `_display` uses `hasattr` guards for duck-typing SDK message types
- `milestone=` param passed to `build_hooks` for milestone-scoped write boundaries
- `SandboxSettings(enabled=True)` on coordinator sessions
- `max_budget_usd=50.0` cost circuit breaker per coordinator
- Integration tests mock at SDK boundary (ClaudeSDKClient), run our own code real

### Wave 3 — Prompt Files ✓
completed: 2026-03-19

12 prompt files in `.clou/prompts/`:

| File | Type | Tokens (est.) |
|------|------|---------------|
| `supervisor-system.xml` | System prompt | ~300 |
| `coordinator-system.xml` | System prompt | ~250 |
| `worker-system.xml` | System prompt | ~150 |
| `verifier-system.xml` | System prompt | ~150 |
| `supervisor.md` | Protocol | ~600 |
| `coordinator-plan.md` | Protocol (PLAN cycle) | ~600 |
| `coordinator-execute.md` | Protocol (EXECUTE cycle) | ~550 |
| `coordinator-assess.md` | Protocol (ASSESS cycle) | ~550 |
| `coordinator-verify.md` | Protocol (VERIFY cycle) | ~650 |
| `coordinator-exit.md` | Protocol (EXIT cycle) | ~600 |
| `worker.md` | Protocol | ~700 |
| `verifier.md` | Protocol | ~1000 |

**Design decisions:**
- Two-layer architecture (DB-04): light system_prompt XML + per-cycle protocol files agent reads
- Attention sinks (§2): identity first, not backstory
- Schemas embedded in protocol files, not separate schema files (DB-08)
- Worker writes execution.md summary FIRST for circuit breaker readability (DB-10)
- Verifier captures raw artifacts for mediated Brutalist perception (DB-09)
- handoff.md 7-section schema embedded in coordinator-exit.md

### Wave 4 — Integration Testing ✓
completed: 2026-03-19

| File | Tests | What's Verified |
|------|-------|-----------------|
| `tests/conftest.py` | — | Auth detection via `claude auth status` (method-agnostic) |
| `tests/test_integration.py` | 11 | Live SDK handshake, hooks, agents, options format |

**11 integration tests across 5 classes:**
- TestSDKHandshake (4): minimal connect, sandbox, effort, max_budget
- TestHooksIntegration (1): HookMatcher dict format accepted
- TestAgentDefinitionsIntegration (1): AgentDefinition format accepted
- TestResultMessage (2): usage dict, session_id present
- TestOrchestratorIntegration (3): load_prompt output, build_hooks output, full coordinator options

**Key decisions:**
- Auth detection is method-agnostic: `claude auth status` → `loggedIn` field. No env var coupling.
- Zero auth code in `clou/` — the SDK/CLI owns authentication, Clou owns orchestration
- `pytest.mark.integration` marker, skip gracefully when no auth
- Tests use sonnet + max_turns=1 to minimize API cost

### Waves 5-9 — Presentation Layer ✓
completed: 2026-03-20

Full Textual + Rich UI layer across five waves: OKLCH palette, breathing animation, mode transitions, conversation widget, breath widget, status bar, escalation modal, handoff renderer, DAG viewer, context tree, SDK bridge, CSS stylesheet. 12 brutalist review rounds drove convergence. See `presentation-plan.md` for full details.

### Wave 10 — The Crossing ✓
completed: 2026-03-21

Wired orchestrator lifecycle events to the Textual UI — the nervous system connecting the breathing conversation to real coordinator activity.

| Change | Module | Description |
|--------|--------|-------------|
| `ClouStatusUpdate` message | `messages.py`, `app.py` | New message type; status bar updates before each cycle |
| `ClouCycleComplete` emission | `orchestrator.py` | Posted after each successful cycle |
| `ClouDagUpdate` emission | `orchestrator.py`, `graph.py` | Compose.py parsed after PLAN cycle; `extract_dag_data()` added to graph.py |
| Escalation scanning | `orchestrator.py` | New escalation files detected at cycle boundaries |
| `ClouHandoff` emission | `orchestrator.py` | Posted before ClouCoordinatorComplete on success |
| Crash recovery | `orchestrator.py` | ClouCoordinatorComplete(result="error") always posted on crash |
| DECISION→BREATH fix | `app.py` | Animation time reset to quarter period for smooth phase alignment |
| SDK test isolation | `test_integration.py`, `test_integration_ui.py`, `test_orchestrator.py` | `pytest.importorskip` for graceful skip |
| Test coverage | `test_app_dialogue.py`, `test_escalation.py` | Handoff handler, escalation path rejection, cost_usd=None |
| Lifecycle tests | `test_lifecycle_events.py` | 6 integration tests for all lifecycle event paths |

**P0 diagnostic corrections:**
- "Double token counting" was NOT a bug — supervisor and coordinator use separate routing paths
- "Shimmer frozen" was NOT a bug — `_frame_time` already updated via `time.monotonic()` in `watch_breath_phase`
- "DAG screen empty" was real but root cause was missing lifecycle events, not missing screen args

### Wave 11 — Slash Commands ✓
completed: 2026-03-23

Full slash command system: dispatch layer, command palette, 10 commands across 11 implementation cycles. See `slash-commands.md` for full design and tracking.

| Module | Purpose | Cycle |
|--------|---------|-------|
| `clou/ui/commands.py` | Command dataclass, registry, dispatch, all handlers | C0-C10 |
| `clou/ui/widgets/command_palette.py` | Filtered completion menu widget | C0 |
| `clou/ui/diff.py` | Diff rendering (unified→Rich Text) | C6 |
| `clou/ui/history.py` | Conversation history model (shared by /export, /compact) | C7 |

**10 commands registered:**
`/help` `/clear` `/cost` `/dag` `/context` `/diff` `/export` `/status` `/compact` `/model`

**Key decisions:**
- Slash commands intercept in `on_input_submitted` before supervisor queue — no user message, no enqueue
- CommandPalette: display-only Static widget, input retains focus
- /compact uses asyncio.Event signaling between app and orchestrator
- /model stores preference on app; live switching deferred pending SDK research
- conversation.py refactored mid-implementation from RichLog to VerticalScroll+Static widgets

**69 new tests** across: `test_commands.py`, `test_command_palette.py`, `test_diff.py`, `test_export.py`, `test_compact.py`

### Wave 12 — Session Persistence ✓
completed: 2026-03-23

Append-only JSONL session transcripts with three-tier resumption context. Research-grounded design from §4b of research-foundations.md.

| Module | Purpose |
|--------|---------|
| `clou/session.py` | Session dataclass, JSONL append/read, session listing |
| `clou/resume.py` | Three-tier resumption context builder (Tier 1: verbatim tail, Tier 2: observation-masked summary, Tier 3: golden context pointer) |
| `clou/__main__.py` | `--continue` and `--resume SESSION_ID` CLI flags |
| `clou/ui/commands.py` | `/sessions` command (list and detail) |

**Architecture:**
- Every turn auto-persisted to `.clou/sessions/{uuid}.jsonl` — no explicit save needed
- Session created on `ClouApp.on_mount`, entries appended on every `ClouProcessingStarted` (user) and `ClouTurnComplete` (assistant)
- `_last_completed_content` on ConversationWidget preserves buffer before conv widget clears it (fixes race between widget handler and app handler in Textual's bubble-up model)
- `--continue` resolves latest session; `--resume` takes explicit session ID
- Resumption injects three-tier context as first supervisor query, replacing normal greeting
- Observation masking (JetBrains NeurIPS 2025): truncate tool outputs, preserve action chain

**11 commands registered:**
`/help` `/clear` `/cost` `/dag` `/context` `/diff` `/export` `/status` `/compact` `/model` `/sessions`

**48 new tests** across: `test_session.py` (23), `test_resume.py` (12), `test_main.py` (5), `test_commands.py` (5), `test_app_dialogue.py` (4)

**Bug fix:** `on_clou_turn_complete` in app.py was reading `conv._stream_buffer` after ConversationWidget's handler already cleared it (Textual messages bubble UP — widget handler fires first). Fixed by introducing `_last_completed_content` that preserves the buffer before clearing.

## Quality Gates
All modules must pass before proceeding:
- `python3 -m ruff check clou/ tests/`
- `python3 -m ruff format --check clou/ tests/`
- `python3 -m mypy clou/`
- `python3 -m pytest`
