# Conversation-Integrated Input: Codex-Style Layout Rearchitecture

**Date:** 2026-03-23
**Status:** Complete

## Design Direction

Move PromptInput from a pinned-bottom sibling of ConversationWidget into the conversation flow itself. The input becomes the "growing tip" of the dialogue — spatially adjacent to the last message, riding the conversation's growing edge downward.

Reference: Codex CLI — the `>` prompt sits right below the last message, not at the bottom of the terminal.

## Current Architecture

```
ClouApp
  ConversationWidget [1fr]     ← scrolls internally via RichLog
    RichLog#history [1fr]
    Static#stream-overlay [auto]
  BreathWidget [hidden]
  HandoffWidget [hidden]
  PromptInput#user-input [auto] ← pinned at bottom, spatially divorced
  ClouStatusBar [auto]
```

## Target Architecture

```
ClouApp
  ConversationWidget [1fr]
    RichLog#history [1fr]
    Static#stream-overlay [auto]
    PromptInput#user-input [auto] ← inside conversation flow
  BreathWidget [hidden]
  HandoffWidget [hidden]
  ClouStatusBar [auto]
```

## Task Graph

```
Phase 1 (Structural Move)          Phase 2 (Mode Fixes)           Phase 3 (Polish)
  1.1 Remove from ClouApp  ──┐     2.1 Breath opacity fix  ──┐    3.1 Border/spacing
  1.2 Add to ConvWidget    ──┤     2.2 Decision opacity fix  │    3.2 Visual verification
  1.3 Update CSS           ──┘     2.3 Handoff display fix ──┘
                                          │
                                   Phase 4 (Tests)
                                    4.1 Verify existing pass
                                    4.2 Add breath input visibility test
                                    4.3 Full suite
                                          │
                                   Phase 5 (Validation)
                                    5.1 pytest 837+
                                    5.2 Brutalist convergence
```

## Phase 1: Structural Move

### Task 1.1: Remove PromptInput yield from ClouApp.compose()
- **File:** `clou/ui/app.py`
- **Change:** Remove `yield PromptInput(id="user-input")` from compose(). Keep import for type reference.
- **Status:** [ ]

### Task 1.2: Add PromptInput to ConversationWidget.compose()
- **File:** `clou/ui/widgets/conversation.py`
- **Change:** Import PromptInput. Add `yield PromptInput(id="user-input")` after stream-overlay.
- **Event bubbling:** Input.Submitted bubbles through ConversationWidget to ClouApp — no handler change needed.
- **Status:** [ ]

### Task 1.3: Update CSS for relocated PromptInput
- **File:** `clou/ui/clou.tcss`
- **Change:** Remove `border-top` and `background: $surface` from `#user-input` (now flows naturally inside conversation). Keep `#user-input .prompt-char` color rule.
- **Status:** [ ]

## Phase 2: Mode-Scoped Fixes (Critical)

The hardest part. Moving PromptInput inside `#conversation` means mode-scoped opacity/display rules now affect it.

### Task 2.1: Breath mode — selective opacity
- **File:** `clou/ui/clou.tcss`
- **Problem:** `ClouApp.breath #conversation { opacity: 0.4 }` would dim the input too.
- **Fix:** Apply opacity to `#history` and `#stream-overlay` individually, not `#conversation`:
  ```css
  ClouApp.breath #conversation #history { opacity: 0.4; }
  ClouApp.breath #conversation #stream-overlay { opacity: 0.4; }
  ClouApp.breath #user-input { opacity: 1.0; }
  ```
  Remove `overflow-y: hidden` from `#conversation` in breath mode (would clip input).
- **Status:** [ ]

### Task 2.2: Decision mode — selective opacity
- **File:** `clou/ui/clou.tcss`
- **Problem:** Same pattern — `ClouApp.decision #conversation { opacity: 0.4 }`.
- **Fix:** Same selective approach as Task 2.1.
- **Status:** [ ]

### Task 2.3: Handoff mode — selective hiding
- **File:** `clou/ui/clou.tcss`
- **Problem:** `ClouApp.handoff #conversation { display: none }` hides input entirely.
- **Fix:** Hide only children, keep ConversationWidget mounted for the input:
  ```css
  ClouApp.handoff #conversation #history { display: none; }
  ClouApp.handoff #conversation #stream-overlay { display: none; }
  ClouApp.handoff #conversation { height: auto; }
  ```
- **Status:** [ ]

## Phase 3: Visual Polish

### Task 3.1: Spacing and separator
- **File:** `clou/ui/clou.tcss`
- **Change:** Add subtle margin or padding between stream-overlay and input. Remove old border-top.
- **Status:** [ ]

### Task 3.2: Visual verification
- Launch clou, verify input sits below conversation content naturally.
- **Status:** [ ]

## Phase 4: Test Updates

### Task 4.1: Verify existing tests pass
- All `query_one("#user-input Input")` calls resolve through full DOM traversal — nesting depth doesn't matter.
- `test_conversation.py` standalone ConversationApp will now include PromptInput internally — shouldn't break existing assertions.
- **Status:** [ ]

### Task 4.2: Add breath-mode input visibility test
- **File:** `tests/test_mode_transitions.py`
- **Change:** Verify input remains interactable during breath mode.
- **Status:** [ ]

### Task 4.3: Full suite pass
- 837+ tests must pass.
- **Status:** [ ]

## Phase 5: Validation Gate

### Task 5.1: Full pytest pass
- **Status:** [ ]

### Task 5.2: Brutalist convergence
- **Status:** [ ]

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Breath opacity inheritance dims input | HIGH | Selective opacity on children, not container |
| Handoff hides entire ConversationWidget | HIGH | Hide only #history/#stream-overlay children |
| overflow-y:hidden in breath clips input | MEDIUM | Remove overflow from container, apply to #history only |
| RichLog cannot be height:auto | MEDIUM | Keep 1fr; accept RichLog fills space |
| Input.Submitted stops bubbling | LOW | Standard Textual bubbling; ConversationWidget has no interceptor |
| Test queries break | LOW | query_one is full-DOM traversal |

## Files Changed

| File | Phase | Type |
|------|-------|------|
| `clou/ui/app.py` | 1 | Modified |
| `clou/ui/widgets/conversation.py` | 1 | Modified |
| `clou/ui/clou.tcss` | 1, 2, 3 | Modified |
| `tests/test_mode_transitions.py` | 4 | Modified |
