# UI Elevation Plan: Minimal-Chrome Atmospheric TUI

**Date:** 2026-03-22
**Status:** Complete

## Design Direction

Dissolve all structural borders and surface differentials from the main app shell. Create one continuous `$surface-deep` plane where typography, color dimness, and whitespace alone provide hierarchy. Reference: Codex CLI's approach — prompt character, ambient status, no boxed widgets.

## Task Graph

```
Phase 1 (CSS Foundation)          Phase 2 (Prompt Input)        Phase 4 (Typography)
  1.1  Strip input border  ──┐     2.1  PromptInput widget       4.2  User msg color fix
  1.2  Melt status bar    ──┐│     2.2  Integrate in app.py
  1.3  Verify tool-use  ←──┘│     2.3  PromptInput CSS
                             │        │
                             └────→ Phase 3 (Mode Tuning)
                                    3.1  Breath findability
                                    3.2  Decision verify
                                    3.3  Handoff verify
                                         │
                                    Phase 5 (Tests)
                                    5.1  test_app_dialogue fix
                                    5.2  Full suite verify
                                    5.3  New regression tests
                                         │
                                    Phase 6 (Validation)
                                    6.1  pytest pass
                                    6.2  Visual smoke + brutalist
                                         │
                                    Phase 7 (Brutalist Findings)
                                    7.1  Whitespace input bug fix
                                    7.2  Error color palette consolidation
                                    7.3  Dead hex cleanup
                                    7.4  Test assertion strengthening
```

## Phase 1: CSS Foundation — Dissolve Structural Chrome

### Task 1.1: Strip input border, flatten to surface-deep
- **File:** `clou/ui/clou.tcss`
- **Change:** `#user-input` — remove `border-top`, change `background: $surface` → `$surface-deep`, set `height: auto`
- **Status:** [x] Done

### Task 1.2: Melt status bar into surface-deep
- **File:** `clou/ui/clou.tcss`
- **Change:** `#status-bar` — change `background: $surface` → `$surface-deep`, add `padding: 0 1`
- **Status:** [x] Done

### Task 1.3: Verify tool-use blocks as only raised elements
- **File:** `clou/ui/clou.tcss`
- **Change:** Confirm `.tool-use { background: $surface }` creates correct contrast now that everything else is `$surface-deep`. No code change expected.
- **Status:** [x] Done — verified in TCSS lines 112-115

## Phase 2: Prompt Input Widget

### Task 2.1: Create PromptInput composite widget
- **File:** `clou/ui/widgets/prompt_input.py` (NEW)
- **Design:** `Horizontal` container: `Static("› ")` (gold prompt char) + `Input(placeholder="")` (borderless)
- **Status:** [x] Done

### Task 2.2: Integrate PromptInput into ClouApp
- **File:** `clou/ui/app.py`
- **Change:** Replace `yield Input(...)` with `yield PromptInput(id="user-input")`. Input.Submitted bubbles up — handler stays the same.
- **Risk:** Code querying `#user-input` as Input directly will break — must query nested Input.
- **Status:** [x] Done — app.py line 88

### Task 2.3: CSS for PromptInput
- **File:** `clou/ui/clou.tcss`
- **Change:** `#user-input` rules target PromptInput container. Add `#user-input .prompt-char { color: $accent-gold }`.
- **Status:** [x] Done — TCSS lines 65-67

## Phase 3: Mode-Specific Tuning

### Task 3.1: Breath mode — input findability
- **File:** `clou/ui/clou.tcss`
- **Change:** Add `ClouApp.breath #user-input { opacity: 1.0; }` — keep prompt char visible when conversation recedes at 0.4 opacity.
- **Critical:** Without borders, gold prompt char + cursor blink are the only input anchors.
- **Status:** [x] Done — TCSS lines 133-135

### Task 3.2: Decision mode — verify input visible
- **File:** `clou/ui/clou.tcss`
- **Change:** Verify EscalationModal overlay doesn't hide input. Current rules only affect `#conversation` and `#breath-widget`. No change needed.
- **Status:** [x] Done — verified, TCSS lines 138-147

### Task 3.3: Handoff mode — consistent surface
- **File:** `clou/ui/clou.tcss`
- **Change:** Verify `#handoff-widget` on `$surface-deep` is visually consistent with melted status bar. No change needed.
- **Status:** [x] Done — TCSS lines 160-166

## Phase 4: Typography Refinement

### Task 4.2: Fix hardcoded user message color
- **File:** `clou/ui/widgets/conversation.py`
- **Change:** `add_user_message` uses hardcoded `#b59a54` — changed to palette `accent-gold` via `_GOLD_HEX = PALETTE["accent-gold"].to_hex()`.
- **Status:** [x] Done — conversation.py lines 33, 80

## Phase 5: Test Updates

### Task 5.1: Fix test_app_dialogue.py
- **File:** `tests/test_app_dialogue.py`
- **Change:** Changed `query_one("#user-input")` → `query_one("#user-input Input")` in 3 tests.
- **Status:** [x] Done

### Task 5.2: Full suite verification
- **Files:** All `tests/`
- **Change:** 836 tests passing after Phase 1-4 changes.
- **Status:** [x] Done

### Task 5.3: New regression tests
- **File:** `tests/test_app_dialogue.py`
- **Change:** Added `test_prompt_input_mounts_with_prompt_char` (line 88).
- **Status:** [x] Done

## Phase 6: Validation Gate

### Task 6.1: Full pytest pass
- **Result:** 836 passed
- **Status:** [x] Done

### Task 6.2: Visual smoke + brutalist review
- Brutalist review (claude+gemini) confirmed elevation is clean
- No regressions from border dissolution or PromptInput integration
- Pre-existing architectural findings (ClouApp size, private coupling) documented but out of scope
- **Status:** [x] Done

## Phase 7: Brutalist Findings — Cycle 2

Findings from the Phase 6 brutalist review that were actionable within the elevation scope.

### Task 7.1: Fix whitespace input not clearing (bug)
- **File:** `clou/ui/app.py`
- **Change:** Moved `event.input.clear()` before the early return in `on_input_submitted`, so whitespace-only submissions still clear the field.
- **Status:** [x] Done

### Task 7.2: Consolidate error colors to palette
- **File:** `clou/ui/widgets/conversation.py`
- **Change:** Added `_ROSE_HEX = PALETTE["accent-rose"].to_hex()`. Changed `add_error_message` from `"bold #ef7d88"` → `f"bold {_ROSE_HEX}"`. Changed `on_clou_tool_result` error from `"bold red"` → `f"bold {_ROSE_HEX}"`.
- **Status:** [x] Done

### Task 7.3: Clean up dead hex in PromptInput DEFAULT_CSS
- **File:** `clou/ui/widgets/prompt_input.py`
- **Change:** Removed `color: #d09945` from DEFAULT_CSS — TCSS `$accent-gold` rule is the single source of truth.
- **Status:** [x] Done

### Task 7.4: Strengthen test assertions
- **File:** `tests/test_app_dialogue.py`
- **Changes:**
  - `test_input_calls_add_user_message` — now verifies actual message content via `line.text`
  - `test_empty_input_ignored` — added assertion that `inp.value == ""` after whitespace submission
  - Added `test_input_during_decision_stays_in_decision` — verifies DECISION mode behavior
- **Result:** 837 tests passing
- **Status:** [x] Done

## Risks

| Risk | Severity | Outcome |
|------|----------|---------|
| Input invisible in breath mode | Medium | Mitigated — gold prompt char at `opacity: 1.0` |
| PromptInput message routing | Low | Non-issue — Input.Submitted bubbles correctly |
| test_app_dialogue breakage | High certainty | Fixed — mechanical query update |
| Textual Input compact mode quirks | Medium | Non-issue — standard Input works fine |
| Whitespace input not clearing | Low | Fixed in Phase 7 |
| Hardcoded error colors | Low | Fixed in Phase 7 |

## Files Changed

| File | Phase | Type |
|------|-------|------|
| `clou/ui/clou.tcss` | 1, 2, 3 | Modified |
| `clou/ui/widgets/prompt_input.py` | 2, 7 | New |
| `clou/ui/app.py` | 2, 7 | Modified |
| `clou/ui/widgets/conversation.py` | 4, 7 | Modified |
| `tests/test_app_dialogue.py` | 5, 7 | Modified |
| `tests/test_mode_transitions.py` | 5 | Modified |

## Brutalist Convergence

Two brutalist review cycles run (claude+gemini each cycle):
1. **Cycle 1:** Found PromptInput hardcoded hex, error color inconsistencies, whitespace bug, weak assertions. All fixed in Phase 7.
2. **Cycle 2:** Converged. No new findings within elevation scope. Pre-existing architectural items (ClouApp god object, HandoffWidget markdown, BreathWidget perf) documented but deliberately out of scope.

**Final state:** 837 tests passing, zero hardcoded colors in elevation-touched files, palette fully centralized.
