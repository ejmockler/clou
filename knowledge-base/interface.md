# Clou Interface Design

The presentation layer for clou's orchestrator. This document applies perceptual engineering to the specific problem of surfacing a three-tier agent hierarchy to a human through a terminal interface. It is grounded in the research foundations (see [Research Foundations](./research-foundations.md)) and in a systematic study of the current CLI landscape.

Last updated: 2026-03-19

---

## 1. GROUND: The Felt Situation

Before layout, before technology — what does it feel like to be the person using clou?

### The Emotional Core

You have delegated something important to an agent hierarchy. You are not doing the work. The fundamental felt situation is **trust under uncertainty**: hope that it's working, anxiety that it isn't, the specific tension of having granted authority to something whose reasoning you can't fully observe.

This is not one situation. It is four qualitatively distinct modes of inhabitation, each with its own emotional ground-tone, temporal rhythm, and perceptual demands.

### Mode 1: Dialogue

**Felt quality:** Creative collaboration. Warmth. Shaping what gets built.

The user is talking to the supervisor about their project. This is conversational, interactive, human-paced. The emotional register is collaborative — you're working with a capable colleague to define scope, articulate requirements, decompose milestones. Engagement is focal and sustained.

**Perceptual demands:** Full-width text for reading/writing. Beautiful markdown streaming. Generous typography. The interface should feel like the best conversational terminal experience that exists — unhurried, spacious, alive to your words.

**Temporal rhythm:** Human conversational tempo. Streaming output that feels like thinking-made-visible. No artificial delays, no abrupt transitions.

### Mode 2: Breath

**Felt quality:** Vigilance softened by ambient confidence. Patience. Background awareness.

The supervisor has spawned a coordinator. Work is happening autonomously. The user has let go. They are no longer participating — they are witnessing. The emotional register shifts completely: from active collaboration to receptive monitoring.

**Perceptual demands:** The conversation surface should not freeze with a stale last-message. It should **pare back and breathe with the activity of the agents**. The space transforms — text recedes, ambient indicators emerge that reflect the living system beneath. Not a dashboard. A breathing surface that communicates "work is happening with purpose" through rhythm and subtle presence, not through data density.

The user must be able to re-engage at any time — scroll up to review the supervisor's prior context, or address the orchestrator directly. Breath mode is the default during autonomous work, not a locked state.

**Temporal rhythm:** Slow, measured. Cycle transitions surface gently. Phase completions arrive as quiet resolutions. Token counters tick steadily. The rhythm should feel purposeful and alive, not anxious or opaque. Thirty minutes of autonomous work should feel like watching a skilled team work from a calm vantage point — not like staring at a loading screen.

### Mode 3: Decision

**Felt quality:** Sudden focal engagement. Stakes. Deliberation.

An escalation has arrived. The coordinator has encountered something beyond its authority — a requirement conflict, a service that needs credentials, a quality judgment it can't make alone. The user's attention is needed right now, on this specific thing.

**Perceptual demands:** The escalation must announce itself — not as an alarm, but with the weight and presence that a real decision deserves. The ambient breath gathers focus. The escalation renders as a structured element: classification, context, options with tradeoffs, a recommendation. Not a chat message. A decision card. The user deliberates, decides, and the breath resumes.

**Temporal rhythm:** The arrival of an escalation is a punctuation in the breath — a moment where the ambient pace pauses and the space gathers around something that matters. After resolution, the space releases back into breath.

### Mode 4: Handoff

**Felt quality:** Resolution. Satisfaction. Evaluation. The work is done.

The coordinator has completed verification. A running environment exists. The handoff document is ready. The user is being guided through what was built.

**Perceptual demands:** The conversation surface becomes the handoff — running URLs, walk-through steps, what to look for, what the agent verified, known limitations. This should feel like a prepared room, not a construction site. The atmosphere shifts from the ambient monitoring of breath to the warm, resolved quality of completion.

**Temporal rhythm:** Unhurried. The user explores at their own pace. The handoff is a guided tour, not a report.

---

## 2. MAP: Experiential Qualities

### Atmosphere

The dominant atmospheric quality across all modes: **calm confidence**. Clou should feel like a well-designed mission control room — most of the time, things are proceeding and you're at ease. When something needs your attention, it becomes unmissable without being alarming.

The atmosphere modulates across modes:
- **Dialogue:** Warm, collaborative, spacious. The interface invites creative conversation.
- **Breath:** Calm, alive, ambient. The interface communicates purposeful activity without demanding attention.
- **Decision:** Focused, weighted, clear. The interface gathers around a decision point.
- **Handoff:** Resolved, satisfied, open. The interface presents completed work for exploration.

### Resonance

Clou's deep structure is a **hierarchy of delegation and accountability**. Supervisor delegates to coordinator delegates to agent teams. Each tier has ownership, authority, and boundaries. The interface should make this hierarchy feelable — not as an org chart, but as nested levels of activity where you can sense the depth of work happening beneath the surface.

The golden context (`.clou/`) IS the nervous system of the project. When visible (on demand), it should feel like a living structure — milestone status glowing, active phases pulsing, the compose.py DAG visible as a dependency graph. When not visible, its health should be ambient — something you sense rather than read.

### Presence

The system must feel alive during long coordinator runs. Not just a spinner — a sense that work is happening with purpose. This is the single most important experiential quality to get right. Token counters ticking, phases completing, decisions being logged — the presence of the system should be felt even when you're not looking directly at it.

### Tension and Resolution

Escalations are the primary tension points — a decision needs you. Resolution comes when you decide and the coordinator continues. Milestone completion is the ultimate resolution — handoff, running environment, guided tour. The interface should honor these arcs: don't flatten escalations into chat messages (kills tension), don't skip the handoff ritual (kills resolution).

### Invitation

During breath mode, the interface should invite patience, not demand it. The ambient indicators should be interesting enough to glance at, not so demanding that they create anxiety. The breathing quality — pare back, show activity rhythm — creates a space where waiting feels like witnessing, not like being abandoned.

---

## 3. Perceptual Primitives and Constraints

### Channel Allocation by Mode

| Mode | Focal Channel | Peripheral Channel | Motion |
|------|--------------|-------------------|--------|
| **Dialogue** | Conversation text (reading, composing) | Status line (tokens, time) | Streaming text arrival |
| **Breath** | Available but unoccupied | Cycle indicators, phase state, token counters | Rhythmic breathing (primary signal) |
| **Decision** | Escalation card (reading, evaluating options) | Breath pauses in periphery | Escalation surfaces with weight |
| **Handoff** | Walk-through content | Running service indicators | Unhurried scroll |

### Working Memory Budget

During any mode, the user should never need to hold more than 4 chunks:
- **Dialogue:** Current topic + project context + what the supervisor just said + what you're saying
- **Breath:** Current milestone + current phase + cycle count + general health
- **Decision:** The question + the options + the tradeoffs + your inclination
- **Handoff:** What was built + what to test + where it's running + known limits

The status line externalizes metrics (tokens, cost, time, cycle number) so they don't consume working memory slots.

### Temporal Thresholds

| Threshold | Application in Clou |
|-----------|-------------------|
| <100ms | Keypress → character on screen. Input echo. Mode switch initiation. |
| <300ms | Atmosphere shift begins (color temperature, layout change). |
| <1s | Breath indicator update. Streaming markdown chunk rendered. |
| 1-5s | Cycle transition announcement (surfaces and settles). |
| 5-30s | Typical agent team dispatch/return during EXECUTE. |
| 1-30min | Full coordinator milestone run. The breath mode must sustain this. |

### Spatial Memory

The interface should support stable spatial memory:
- **Status line:** Always at the bottom. Never moves. The user's body learns to glance down for metrics.
- **Conversation surface:** Always the primary area. Content scrolls within it; the surface itself is stable.
- **Input:** Always at the bottom of the conversation surface. Same position regardless of mode.
- **Escalation cards:** Always surface in the same location within the conversation surface — consistent position relative to input.
- **On-demand panels** (golden context tree, DAG view): Always invoked the same way, always appear in the same spatial relationship to the conversation.

---

## 4. DESIGN: The Breathing Conversation

### The Core Concept

**One surface. Multiple atmospheres. The transitions ARE the design.**

Clou's interface is not a multi-panel dashboard. It is a single conversation surface that modulates its atmosphere based on the current mode of inhabitation. This is fundamentally different from every existing AI CLI, all of which are fixed-layout chat interfaces or fixed-layout dashboards.

The conversation surface is the primary and only persistent visual element (besides the status line). It changes character:

- In **dialogue mode**, it is a full-width conversational interface — markdown streaming, code blocks, diffs, rich text. Clean window. The interface disappears; you see the supervisor.
- In **breath mode**, the conversation text recedes (dims, compresses upward) and the surface fills with ambient indicators of agent activity. The breathing is the interface — rhythmic, alive, purposeful. Key events (phase completions, cycle transitions, decisions logged) surface briefly as ephemeral elements that appear and settle into a minimal persistent state.
- In **decision mode**, the breath gathers focus. An escalation card surfaces with weight and presence — structured, clear, decision-ready. The ambient indicators pause or dim. The space is organized around the decision.
- In **handoff mode**, the surface becomes the handoff document — a guided, scrollable walk-through with running URLs, verification results, and exploration prompts.

### The Breathing Mechanism

During breath mode, the conversation surface should communicate agent activity through rhythm, not data density.

**What "breathing" means concretely:**

1. **Textual rhythm.** Brief, curated status lines that surface as coordinator cycles progress. Not a log stream — a haiku of activity. Examples:
   - `PLAN  compose.py written  7 tasks across 3 phases`
   - `EXECUTE  phase:foundation  deploying 3 agents`
   - `task:setup_database  complete`
   - `ASSESS  invoking brutalist  3 domains`
   - `ASSESS  2 findings accepted  1 overridden  → rework`

2. **Temporal pacing.** These lines appear at the rhythm of actual work — not buffered, not artificially delayed. When agents are active, lines arrive more frequently. During model inference, the pace slows. The breathing matches the real tempo of work.

3. **Visual weight.** Breath-mode text is lighter than dialogue-mode text — dimmer, more compact, less demanding. It's meant to be glanced at, not read closely. The cycle type (PLAN, EXECUTE, ASSESS, VERIFY) carries semantic color. Phase names are visible. Task names appear as they complete.

4. **Receding conversation.** The prior conversation (dialogue with the supervisor before the coordinator was spawned) dims and compresses upward. It's still there — scroll up to read it — but it recedes from focal awareness to make room for the breath.

5. **Ambient metrics.** The status line shows: current milestone, current cycle (number and type), current phase, tokens consumed this milestone, elapsed time. These update live and provide the ambient confidence layer.

### Mode Transitions

Transitions between modes are experiential arcs, not instant switches.

**Dialogue → Breath:** When the supervisor calls `clou_spawn_coordinator`, the conversation surface begins its atmospheric shift. The supervisor's last message (e.g., "Starting milestone: authentication system") remains visible but begins to recede. The breath indicators emerge. The status line updates with milestone information. This transition should take ~1-2 seconds — long enough to feel deliberate, short enough to not feel slow. The felt quality: releasing, letting go, handing off.

**Breath → Decision:** When an escalation arrives, the ambient rhythm pauses. The escalation card surfaces in the conversation area with more visual weight than the breath-mode text — brighter, bordered, structured. The status line may shift to show escalation context. The felt quality: gathering, focusing, something needs you.

**Decision → Breath:** After the user resolves the escalation (selects an option, provides input), the card resolves (visual confirmation of the choice), then recedes. The breath resumes. The felt quality: releasing again, the system continues.

**Breath → Dialogue (user-initiated):** The user can break out of breath mode at any time by typing (engaging the orchestrator directly) or scrolling up (reviewing supervisor conversation history). The breath indicators dim; the conversation surface returns to dialogue weight. If the coordinator is still running, the breath continues in the status line only — compressed to a single line of metrics.

**Breath → Handoff:** When the coordinator completes and the verification agent produces `handoff.md`, the breath resolves into stillness. The handoff document renders in the conversation surface. The status line shows completion state. The felt quality: resolution, arrival, the work is done.

### On-Demand Panels

Not all information belongs in the conversation surface. Some is available on demand — invoked by a keypress, dismissed by another. These are not persistent panels; they are modal or overlay views that appear within or beside the conversation surface.

**Golden context tree:** A navigable view of `.clou/` showing milestone status, phase state, file existence, recent modification times. Invoked when the user wants to understand the project's nervous system. Renders as a Textual Tree widget overlaying or replacing the conversation surface.

**Compose.py DAG:** A visual dependency graph of the current milestone's task structure. Shows which tasks are complete, in-progress, pending, failed. Invoked during breath mode when the user wants to understand what the coordinator is working on. Renders as an ASCII/box-drawing DAG.

**Decision log:** Scrollable view of `decisions.md` — the coordinator's judgment history. Invoked when the user wants to understand why the coordinator made specific choices.

**Token/cost detail:** Expanded view of per-milestone, per-session token usage and estimated cost. The status line shows the summary; this panel shows the breakdown.

---

## 5. Visual Language

### Color System

The full design system is specified in [Visual Language](./visual-language.md), including OKLCH color definitions, design tokens, breathing animation formulas, gradient/shimmer techniques, and attentional salience mapping.

The palette's governing metaphor is **twilight** — the deep blue hour after sunset. Cool blue-gray surfaces, warm gold as the primary (human) accent, equal-luminance accents for semantic roles. Every color means something. No decorative color.

**Key decisions:**
- **OKLCH as design space.** Equal L values guarantee equal perceived brightness across hues. All 8 accent colors sit at L:0.72.
- **Gold as primary accent.** Warm (human) against cool (system) — the thread of warmth that makes twilight beautiful rather than bleak.
- **4 cycle colors:** Blue (PLAN), Teal (EXECUTE), Amber (ASSESS), Violet (VERIFY) — well-separated on the OKLCH hue wheel.
- **Graceful degradation:** TrueColor → 256-color → 16 ANSI. Textual handles standard downsampling; `render_line` gradients need explicit fallback.

### Typography, Borders, Whitespace, Motion

See [Visual Language](./visual-language.md) §8 (typography and whitespace), §9 (borders and structural elements), §3 (breathing animation), §4 (gradients and shimmer), §6 (motion design) for the full specifications.

Key principles:
- **Whitespace is the primary structural element** — the void in breath mode is a positive presence, not empty screen.
- **Alignment on the monospace grid** — cycle types left-aligned, descriptions aligned at a consistent column.
- **Breathing animation** uses `exp(sin(t))` at 4.5s period — matches respiratory kinematics and corrects for Weber-Fechner.
- **Shimmer** is a sub-threshold traveling luminance wave on the breath widget — alive but never demanding.

---

## 6. Technology Stack

### Decision: Textual + Rich (Python)

**Textual** (by Will McGugan, Textualize) for the TUI framework. **Rich** (same author) for text rendering within widgets.

### Why This Stack

**1. Same process as the orchestrator.** The orchestrator is Python. The Claude Agent SDK is Python. Textual runs in the same async event loop. `AssistantMessage` objects flow directly from the SDK into Textual widgets via message passing — no IPC, no serialization, no subprocess coordination. This is the decisive architectural advantage.

**2. The visual ceiling matches the ambition.** Textual's CSS system supports: selector specificity, pseudo-classes (`:hover`, `:focus`, `:dark`), CSS variables, nesting, grid layout with `fr` units, runtime class toggling. ~120 FPS rendering via segment-tree compositor with delta updates. 40+ built-in widgets. The research confirms Python can match Charm's aesthetic — the gap is cultural defaults, not capability.

**3. Streaming markdown is solved.** Will McGugan's block-finalization approach: O(1) per-token rendering cost regardless of document length. Block-level immutability (only the last block re-renders). Sub-1ms incremental parsing. Token buffering between producer (SDK) and consumer (widget). This is state-of-the-art.

**4. CSS-driven atmosphere modulation.** Mode transitions can be implemented as CSS class changes on the root widget (`.dialogue`, `.breath`, `.decision`, `.handoff`). Colors, spacing, opacity, and weight shift through CSS — declarative, hot-reloadable, and maintainable as a design artifact separate from logic.

**5. Textual Web.** The same app can serve over WebSocket to a browser. If clou ever needs a web dashboard, the rendering layer is already built.

**6. Live CSS reload.** `textual run --dev` reloads styling in milliseconds. The visual language can be iterated at the speed of design intuition — critical for getting the atmosphere right.

### What Textual Provides

| Need | Textual Capability |
|------|-------------------|
| Conversation rendering | RichLog widget (append-only, streaming-friendly) |
| Streaming markdown | Rich Markdown + block-finalization pattern |
| Mode-driven styling | CSS class toggling (`.dialogue`, `.breath`, etc.) |
| Status line | Footer widget with live-updating content |
| Escalation cards | Custom widget with Panel/Border styling |
| Input handling | Input widget with multi-line support |
| On-demand panels | Overlay/modal patterns, Screen push/pop |
| Golden context tree | Tree widget (built-in, expandable/collapsible) |
| DAG visualization | Custom widget using Rich Text with box-drawing |
| Keyboard navigation | Built-in key binding system |
| Theme system | 11 base colors + 6 shades per color + semantic variables |
| Terminal adaptation | Automatic color downsampling, resize handling |

### What Must Be Built

| Component | Purpose |
|-----------|---------|
| **Bridge** | Convert SDK messages (`AssistantMessage`, `TaskProgressMessage`, `ResultMessage`) into Textual messages that drive widget updates |
| **Breath widget** | Custom widget for breath-mode ambient display — curated status lines with temporal pacing |
| **Escalation widget** | Structured card rendering with options, tradeoffs, recommendation, user input |
| **Handoff widget** | Guided walk-through renderer with running URLs, verification results |
| **DAG widget** | ASCII dependency graph renderer for compose.py |
| **Mode controller** | State machine managing atmospheric transitions between modes |
| **Clou theme** | Custom Textual theme with the semantic color palette |
| **Clou stylesheet** | `.tcss` file encoding the visual language across all modes |

---

## 7. Architecture

### Component Map

```
clou/
├── orchestrator.py          # Session lifecycle management (existing design)
├── graph.py                 # Compose.py validation (exists)
├── ui/
│   ├── app.py               # Textual App — root, mode state machine
│   ├── clou.tcss            # The visual language as CSS
│   ├── theme.py             # Clou color palette, semantic tokens
│   ├── bridge.py            # SDK message → Textual message adapter
│   ├── mode.py              # Mode enum + transition logic
│   ├── widgets/
│   │   ├── conversation.py  # RichLog-based streaming conversation
│   │   ├── breath.py        # Ambient activity display with temporal pacing
│   │   ├── escalation.py    # Structured decision card
│   │   ├── handoff.py       # Guided walk-through renderer
│   │   ├── dag.py           # Compose.py dependency graph (ASCII)
│   │   ├── context_tree.py  # Golden context navigable tree
│   │   ├── status.py        # Footer: metrics, milestone, cycle, tokens
│   │   └── input.py         # User input with mode-aware behavior
│   └── screens/
│       ├── main.py          # Primary screen (conversation + status)
│       ├── context.py       # On-demand golden context panel
│       └── detail.py        # On-demand detail views (decisions, costs)
```

### Data Flow

```
Claude Agent SDK
    │
    │ AssistantMessage, TaskProgressMessage, ResultMessage
    ↓
bridge.py
    │
    │ Textual Messages (ClouConversation, ClouBreathEvent,
    │                    ClouEscalation, ClouHandoff, ClouMetrics)
    ↓
app.py (mode state machine)
    │
    ├── Mode.DIALOGUE → conversation.py (full-width streaming markdown)
    ├── Mode.BREATH   → breath.py (ambient indicators) + conversation.py (receded)
    ├── Mode.DECISION → escalation.py (structured card) + breath.py (paused)
    └── Mode.HANDOFF  → handoff.py (guided walk-through)
    │
    ↓ (always)
status.py (footer: milestone, cycle, phase, tokens, time)
```

### Mode State Machine

```
                    user types / scrolls up
              ┌──────────────────────────────────┐
              ↓                                  │
         ┌─────────┐   spawn_coordinator   ┌─────────┐
         │ DIALOGUE │ ──────────────────→  │  BREATH  │
         └─────────┘                       └─────────┘
              ↑                              │      ↑
              │ milestone complete           │      │ user resolves
              │                  escalation  │      │
              │                   arrives    ↓      │
         ┌─────────┐                    ┌──────────┐
         │ HANDOFF  │                   │ DECISION  │
         └─────────┘                    └──────────┘
```

Dialogue → Breath: Triggered by `clou_spawn_coordinator` tool call.
Breath → Decision: Triggered by escalation file written to golden context.
Decision → Breath: Triggered by user resolution of escalation.
Breath → Dialogue: Triggered by user input (typing) or scroll-up.
Breath → Handoff: Triggered by coordinator completion + handoff.md existence.
Handoff → Dialogue: Triggered by user dismissal or new conversation.

### Bridge Design

The bridge (`bridge.py`) translates between the Claude Agent SDK's message types and Textual's message system. It runs as an async task within the Textual app's event loop.

```python
# Conceptual shape — not implementation
class ClouBridge:
    """Translates SDK messages into Textual widget updates."""

    async def process_supervisor_message(self, msg: AssistantMessage):
        """Supervisor text → conversation widget."""
        self.app.post_message(ClouConversation(content=msg.content))

    async def process_coordinator_progress(self, msg: TaskProgressMessage):
        """Coordinator cycle events → breath widget."""
        event = parse_coordinator_event(msg)
        self.app.post_message(ClouBreathEvent(event=event))

    async def process_result(self, msg: ResultMessage):
        """Token usage → status widget."""
        self.app.post_message(ClouMetrics(usage=msg.usage))

    async def process_escalation(self, path: Path):
        """Escalation file detected → decision card."""
        escalation = parse_escalation(path)
        self.app.post_message(ClouEscalation(escalation=escalation))
```

---

## 8. Research Grounding

### Landscape Analysis

The interface design is informed by systematic analysis of the current AI CLI landscape (conducted 2026-03-19). Key findings:

**What exists and how it falls short:**

| Tool | Stack | Strength | Fundamental Failure |
|------|-------|----------|-------------------|
| Claude Code | TypeScript + React/Ink + Yoga | Progressive disclosure via collapsible groups | Scrollback death after 5-6 pages; conversation is the only interface |
| Codex CLI | Rust + Ratatui | Transparent shell execution, approval rhythm | Visually flat; buffered thinking creates dead time; opaque long-running operations |
| Gemini CLI | Custom alternate-buffer | Smooth onboarding, mouse support | Verbose output loops; layout corruption; "Finalizing the response" meaninglessness |
| Aider | Python + Rich + Prompt Toolkit | Diff-centric honesty, git integration | No progressive disclosure; overwhelming on large changes; no spatial memory |
| OpenCode | Go + Bubble Tea | Split-pane conversation + diffs | Width-constrained; less polished |
| Warp | Rust + GPU | Multi-agent parallelism, native rendering | Replaces terminal entirely; platform lock-in |

**Universal failures no tool has solved:**
1. **Scrollback is a graveyard.** Conversations longer than a few pages become unnavigable. Critical decisions scroll into oblivion.
2. **The verbosity/opacity tension.** Show too much agent work → overwhelm. Show too little → anxiety. No tool adapts dynamically.
3. **Thinking display.** No one knows what to do with model reasoning.
4. **No spatial memory.** Everything is temporal (a stream). Nothing is spatial (a place).
5. **Token cost opacity.** Users are blindsided by costs; third-party tools fill the gap.

**Every one of them is a chat interface.** Clou's breathing conversation model is a departure from this universal assumption.

### Perceptual Science of Terminal Interfaces

Findings from research synthesis (CHI 2026, cognitive science, Charm design philosophy):

**The monospace grid is a perceptual scaffold.** Every character occupies the same width, creating an implicit coordinate system. The brain processes aligned information faster than scattered information. The terminal's grid provides visual structure for free — columns align automatically, patterns become visible through positional regularity.

**Constraint activates resourcefulness.** Research across 145 empirical studies confirms that constraints consistently improve creative output by reducing the option space. In terminal design, the limited palette (16 ANSI colors, box-drawing characters, whitespace) means every design choice carries higher signal. Each color must earn its place.

**Tufte's data-ink ratio is the terminal's native mode.** No chrome buttons, no decorative frames. Every character is either content or structural whitespace. This is why expert users prefer terminal interfaces for dense information work: the ratio of meaningful signal to decorative noise is inherently higher.

**Whitespace as architecture.** The padding between panels, the blank lines between sections, the margins in a well-styled interface — these create grouping (Gestalt proximity), breathing room (reducing cognitive overload), and scannability. In terminals where every character cell matters, the decision to leave a cell empty is a strong design signal.

**Temporal honesty creates trust.** Motion in terminals should be informational, not decorative. A spinner that correlates with actual throughput provides genuine status feedback. Consistent pacing creates trust. Streaming token output creates a sense of "thinking happening" that is both informational and emotionally engaging.

**The difference between tool and environment:** Persistent state and spatial memory. Tools like vim, lazygit, and k9s maintain state across interactions — panels remember positions, cursor is where you left it. This persistence creates the sense of a "place" rather than a "function."

**Progressive information revelation.** Show the most important information at the highest level, with detail available on demand. This respects both expert pattern-scanning ability and terminal screen real estate constraints.

### Connections to Clou Research Foundations

The interface design connects to the existing research foundations:

**Context is adversarial at scale (§1)** — applies to the user, not just the model. Information density in the UI should be minimal and purposeful. Breath mode exists because showing the user everything the coordinator is doing would be context overload for humans.

**Attention sinks (§2)** — the status line occupies the bottom of the screen, a stable spatial anchor. The most recent breath event occupies the bottom of the breath area — newest-first, like `decisions.md`, aligning the attention position with the most relevant content.

**Decomposition outperforms monolithic (§7)** — the mode system decomposes the interface into focused, single-purpose atmospheric states rather than a monolithic dashboard that tries to serve all needs simultaneously.

**The planning gap (§9)** — the breathing conversation makes the coordinator's non-linear progress (PLAN → EXECUTE → ASSESS → rework → EXECUTE again) visible without overwhelming the user with its mechanics. The user sees rhythm, not sausage-making.

**What cognitive science says AI ignores (§11)** — working memory limits (4±1 chunks) directly inform the budget per mode. The status line externalizes metrics so they don't consume working memory slots. Mode transitions are event segmentation — explicit boundaries that help the user form structured memory of the session.

### Terminal Framework Analysis

**Charm (Go):** The aesthetic benchmark. Key innovations: generous whitespace, rounded borders, curated color palettes (charmtone), physics-based animation (Harmonica), cell-level diffing (no flicker), declarative rendering. No Python bindings. Charm team recommends Textual/Rich for Python.

**Textual (Python):** CSS-like styling system (arguably more expressive than Charm's Lip Gloss), ~120 FPS via segment-tree compositor with delta updates, 40+ built-in widgets, live CSS reload, web deployment via WebSocket, 11-color theme system with automatic shade generation. Can match Charm's visual quality; requires more intentional design work for cohesion (no equivalent of charmtone providing a ready-made palette — clou must build its own).

**Rich (Python):** Rendering engine beneath Textual. Streaming markdown, syntax highlighting, tables, trees, progress bars, panels. Console Protocol enables custom renderables. `Live` class for dynamic content. McGugan's block-finalization approach for O(1) LLM streaming.

**Textual's performance characteristics:** Segment-based rendering (not character grid) naturally handles variable-width characters. Spatial map optimization: rendering time stays constant regardless of widget count. Delta updates: only dirty regions repaint. These properties ensure breath-mode updates and mode transitions remain smooth regardless of conversation history length.

---

## 9. Living Structure Assessment

Validating the design against Alexander's properties of wholeness:

**Strong centers.** Each mode has a clear center: dialogue mode centers on the conversation; breath mode centers on the breathing rhythm; decision mode centers on the escalation card; handoff mode centers on the walk-through. The status line is a secondary center that persists across all modes.

**Levels of scale.** The interface has three levels: the whole screen (mode atmosphere), the primary content area (conversation / breath / escalation / handoff), and individual elements within it (messages, status lines, card fields, URLs). Each level is perceivable as a whole at its own scale.

**Boundaries as active zones.** The transition between breath-mode content and the status line is an active boundary — the most recent breath event and the status metrics live in proximity, creating a zone where "what just happened" meets "where we are." Mode transitions are temporal boundaries — the atmospheric shift between dialogue and breath is a thick, active moment, not an instant switch.

**Good shape.** The void — the space that opens as conversation text recedes in breath mode — is a positive presence, not empty screen. It communicates "room to breathe." The space between breath events communicates "time between actions." Figure and ground are both designed.

**Not-separateness.** The conversation surface is one continuous element across all modes. It changes character, but it doesn't break into separate panels. The status line flows from the conversation surface without a hard border. Escalation cards surface within the conversation, not in a separate pane. The whole should feel like one space with shifting atmosphere, not multiple components assembled.

**Roughness.** Breath-mode events arrive at the rhythm of actual work — irregular, not metronomic. This organic timing creates the quality of a living system rather than a mechanical display. Phase durations vary. Cycle lengths vary. The breathing is alive, not clockwork.

---

## 10. Open Questions

### Questions that require prototyping to answer:

1. **How much breath-mode information is right?** Too sparse → the user feels abandoned. Too dense → it's a log, not a breath. The right density can only be found by dwelling in prototypes.

2. **What exactly does the atmospheric shift look like in CSS?** Color temperature? Opacity? Font weight? Spacing changes? The mode transition must feel deliberate and alive — this requires visual iteration, not specification.

3. **Does the receding conversation feel right?** When dialogue text dims and compresses upward in breath mode, does the user feel like the conversation is "safely stored" or "disappearing"? The former is the goal; the latter would create anxiety.

4. **Escalation card design.** How much of the escalation schema (classification, context, issue, evidence, options, recommendation) should be visible at once? Progressive disclosure within the card? Or full display?

5. **Keyboard vocabulary.** What keys invoke on-demand panels? What's the gesture for "I want to talk to the orchestrator" during breath mode? These need to feel as natural as vim keybindings — the body should learn them.

6. **Sound and notification.** Should escalation arrival produce a terminal bell? A system notification? During a 30-minute coordinator run, the user may not be looking at the terminal. How does the decision-needing escalation reach them?

7. **The `clou status` question.** Is a standalone `clou status` command needed (reads `.clou/` from another terminal), or does the on-demand golden context panel within the TUI suffice? The former is useful when the TUI isn't visible; the latter avoids building a second interface.

### Questions that require user testing to answer:

8. **Expert vs. novice breath density.** Expert users may want more granular breath events; novice users may want less. Is this configurable, or does one density serve both?

9. **Full-screen vs. inline.** Should clou take over the alternate screen buffer (like vim, lazygit) or render inline in the existing terminal? Full-screen gives more control; inline preserves terminal context. Textual supports both.

10. **Multi-terminal workflow.** Some users will have clou in one terminal pane and their editor in another. How does the breath mode work at narrow widths? Does the visual language degrade gracefully?
