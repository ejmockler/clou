# Clou Visual Language

The design system for clou's breathing conversation. Color, motion, and light as expressions of system state — grounded in perceptual science, realized through Textual + Rich.

This document extends the experiential direction in [Interface Design](./interface.md) §5 with specific values, formulas, and implementation patterns. It is the reference for building `clou.tcss` and `theme.py`.

Last updated: 2026-03-19

---

## 1. Design Philosophy

### The Twilight Metaphor

Clou runs for hours doing autonomous work. The user settles into ambient awareness — monitoring, not operating. The felt quality this demands has a precise natural analogue: **twilight**. The deep blue hour after sunset, before night.

- Deep, dark surfaces — the sky approaching night
- Warm gold accents — the last amber light at the horizon
- Cool ambient tones — the pervasive blue of the atmosphere
- Points of brightness — stars emerging, agent activity sparking

This isn't decoration. The metaphor organizes every design decision: why surfaces are cool blue-gray (not neutral gray), why the primary accent is warm gold (human warmth in a cool system), why breath-mode indicators glow like distant signals rather than flash like alerts.

### Perceptual Commitments

**OKLCH as the design space.** All palette colors are specified in OKLCH (Oklab Lightness, Chroma, Hue). Equal L values guarantee equal perceived brightness across hues — no color appears to "pop" more than another at the same semantic level. This is the correct perceptual space for palette design; HSL lies about brightness (yellow at 50% lightness is perceived far brighter than blue at 50% lightness).

**Chromatic contrast captures attention; luminance contrast sustains processing.** These are independent perceptual channels that stack linearly. Breath-mode events use chromatic contrast (cycle-type color on neutral surface) to attract glances. Dialogue-mode text uses luminance contrast (bright text on dark surface) to sustain reading. Escalation cards use both — chromatic for the arrival moment, luminance for the decision content.

**Every color means something.** No decorative color. The palette has ~24 named tokens and each one maps to a specific semantic role. If a color doesn't encode information, it's surface or text — chromatic silence.

### What Clou Is Not

- Not **Catppuccin** — warm pastels, cozy, approachable. Clou is cooler, more reserved, more confident.
- Not **Dracula** — high saturation, gothic boldness. Clou is quieter. Accents are moderate chroma, not screaming.
- Not **Solarized** — scientific neutrality, symmetric precision. Clou has warmth and personality.
- Not **Tokyo Night** — neon city, electric purple. Clou is natural, atmospheric, not synthetic.
- Not **Rosé Pine** — minimal, warm, soho vibes. Clou has more structure and more semantic depth.

Clou is **twilight confidence**: the calm, deep, blue-shifted quality of a system that is working with purpose while you watch from a quiet vantage point.

---

## 2. Color Palette

### Surface Scale (8 grades)

The surface scale creates depth through subtle luminance steps. All surfaces share the same hue family (H: 260, deep blue-violet) with near-zero chroma — just enough warmth to avoid the dead gray of pure neutral.

| Token | OKLCH (L, C, H) | Hex | Usage |
|-------|-----------------|-----|-------|
| `surface-deep` | 0.13, 0.015, 260 | `#171928` | App background, the void |
| `surface` | 0.17, 0.015, 260 | `#1e2030` | Default background, card fills |
| `surface-raised` | 0.21, 0.015, 260 | `#262838` | Hover state, raised elements |
| `surface-overlay` | 0.25, 0.012, 260 | `#2e3042` | Modal overlays, escalation card bg |
| `border-subtle` | 0.30, 0.010, 260 | `#383a4e` | Subtle dividers, inactive borders |
| `border` | 0.35, 0.010, 260 | `#43455a` | Standard borders, active dividers |
| `border-bright` | 0.40, 0.012, 260 | `#4f5168` | Focused borders, selection rings |
| `surface-bright` | 0.45, 0.010, 260 | `#5b5d76` | Scrollbar thumbs, muted interactive |

**Design rationale:** 8 surface grades may seem excessive, but the breathing conversation requires fine gradation to express atmospheric depth. In breath mode, elements recede through 3-4 levels simultaneously. In dialogue mode, only 2-3 levels are needed. The extra grades enable smooth atmospheric transitions without hue shifts.

### Text Hierarchy (4 levels)

Text lightness ranges from L: 0.45 (barely visible — timestamps in breath mode) to L: 0.95 (near-white — emphasis). The slight warm tint (H: 80) in brighter text prevents the clinical feel of pure white on blue.

| Token | OKLCH (L, C, H) | Hex | Usage |
|-------|-----------------|-----|-------|
| `text-muted` | 0.45, 0.008, 250 | `#5e6078` | Timestamps, metadata, breath-mode labels |
| `text-dim` | 0.60, 0.008, 250 | `#848698` | Secondary text, breath events, receded conversation |
| `text` | 0.88, 0.010, 80 | `#dbd9e3` | Primary text, dialogue content |
| `text-bright` | 0.95, 0.005, 80 | `#f0eef5` | Emphasis, headings, active elements |

### Accent Palette (8 semantic colors)

All accents share L: 0.72 — equal perceived brightness. This is the core OKLCH guarantee: when a PLAN event (blue) appears next to an EXECUTE event (teal), neither appears brighter. Semantic meaning comes from hue alone, not from accidental luminance variation.

Chroma ranges from 0.10-0.14. Moderate — visible and distinctive without the garish intensity of Dracula's 90%+ saturation or the washed-out delicacy of Rosé Pine's low-chroma accents.

| Token | OKLCH (L, C, H) | Hex | Hue Name | Semantic Role |
|-------|-----------------|-----|----------|--------------|
| `accent-gold` | 0.72, 0.12, 75 | `#c4a35d` | Gold | **Primary.** Supervisor identity, user prompts, input cursor, the human warmth |
| `accent-blue` | 0.72, 0.10, 255 | `#7d9ed5` | Slate | **PLAN.** Planning activity, compose.py, structural thinking |
| `accent-teal` | 0.72, 0.10, 180 | `#5bb8a7` | Teal | **EXECUTE.** Agent dispatch, task activity, doing |
| `accent-amber` | 0.72, 0.12, 55 | `#c99f4c` | Amber | **ASSESS.** Brutalist invocation, quality judgment |
| `accent-violet` | 0.72, 0.10, 295 | `#b38dc5` | Violet | **VERIFY.** Verification phase, handoff preparation |
| `accent-green` | 0.72, 0.12, 145 | `#5cba77` | Green | **Success.** Task complete, phase complete, milestone done |
| `accent-orange` | 0.72, 0.14, 50 | `#d1984f` | Orange | **Warning.** Non-blocking escalation, approaching limits |
| `accent-rose` | 0.72, 0.14, 15 | `#d57d71` | Rose | **Error.** Failure, blocking escalation, crash |

**Why gold as primary, not blue?** Blue is the ambient atmosphere — the twilight itself. The primary accent is the human element *within* that atmosphere. Gold is warm (human) against cool (system). It's also the color of the last light at the horizon — the thread of warmth that makes twilight beautiful rather than bleak.

### Accent Modulations

Each accent has three intensity levels derived by shifting L and C:

| Modifier | L shift | C shift | Usage |
|----------|---------|---------|-------|
| `-dim` | L: 0.55 | C × 0.6 | Inactive/background use of the color |
| (base) | L: 0.72 | C × 1.0 | Standard foreground use |
| `-bright` | L: 0.85 | C × 1.2 | Emphasis, current/active state, motion peaks |

Example: `accent-teal-dim` (L: 0.55, C: 0.06, H: 180) for a completed agent in breath mode, `accent-teal` (L: 0.72, C: 0.10, H: 180) for an active agent, `accent-teal-bright` (L: 0.85, C: 0.12, H: 180) for the dispatch flash moment.

This gives 24 accent values (8 hues × 3 intensities) plus 8 surfaces + 4 text levels = **36 total tokens**. The practical ceiling for a design system is 20-30 *primary* tokens; the modulations are derived, not independently chosen.

### Light Theme (Future)

The palette is dark-first — terminal work is predominantly dark-themed. A light theme inverts the surface scale (L: 0.13 → L: 0.97 at the top) and reduces accent chroma by ~30% (bright backgrounds make saturated accents feel garish). Accent hues remain identical — the OKLCH guarantee means the same hue relationships hold in both modes.

Light theme is designed for but not built. The `.tcss` file uses CSS variables throughout, enabling a theme swap by redefining the variable block.

### Terminal Compatibility

| Capability | Coverage | Clou Strategy |
|-----------|----------|---------------|
| TrueColor (16M) | iTerm2, Kitty, Alacritty, WezTerm, Windows Terminal, Ghostty | Full palette as specified |
| 256-color | Older terminals, tmux without `Tc` | Map each token to nearest 256-color index. Pre-computed lookup table in `theme.py` |
| 16 ANSI | Rare, but possible | Map to semantic ANSI: `accent-gold` → yellow, `accent-blue` → blue, `accent-rose` → red, surfaces → default bg. Functional but not atmospheric |

Textual handles TrueColor → 256 → 16 downsampling automatically for Rich styles. Custom `render_line` gradient rendering (§4) must implement its own fallback for 256-color — pre-compute the gradient as a 256-color palette-mapped sequence.

---

## 3. The Breathing Animation

### The Formula

Human respiratory rhythm is 4-6 seconds per cycle, 12-15 breaths per minute. The interface breathes with this rhythm during breath mode — not a heartbeat (too fast, too urgent) but a breath (calm, alive, sustaining).

```
breath(t) = normalize( exp( sin(t × 2π / period) ) )

where:
  period = 4.5 seconds (center of respiratory range)
  normalize(x) = (x - e^(-1)) / (e^1 - e^(-1))  → maps to [0, 1]
```

**Why `exp(sin(t))`?** A bare sine wave spends equal time at peaks and troughs, which reads as mechanical. `exp(sin(t))` compresses the trough and expands the peak — the waveform lingers at maximum (inhale hold) and moves quickly through minimum (exhale-to-inhale transition). This matches respiratory kinematics and, critically, **corrects for the Weber-Fechner law**: perceived brightness is logarithmic, so a linear sine modulation looks wrong. The exponential maps linear time to perceptually uniform brightness change.

### What Breathes

The breathing modulates specific visual properties during breath mode. Not everything breathes — only elements that represent the system's living state.

| Property | Min (exhale) | Max (inhale) | What It Communicates |
|----------|-------------|-------------|---------------------|
| Status line opacity | 0.6 | 1.0 | The system is alive, metrics are current |
| Active agent indicator luminance | `accent-*-dim` L | `accent-*` L | Agents are working |
| Border luminance on breath widget | `border-subtle` L | `border` L | The breath container pulses |
| Cursor glow radius (if agent idle) | 0 cells | 1 cell | Waiting for model inference |

The breathing is **subtle**. The amplitude of the luminance modulation is ~15% of the range (from dim to base, not from invisible to blinding). It should be felt more than seen — peripheral vision detects the rhythm; focal attention need not engage.

### What Does NOT Breathe

- Dialogue-mode text (stable for reading)
- Escalation cards (need sharp clarity for decision-making)
- Handoff content (stable for evaluation)
- Input field (stable for typing)
- Completed elements (finished work is still, not alive)

### Breathing State Machine

```
IDLE        → no breathing (dialogue, decision, handoff modes)
BREATHING   → standard rhythm (breath mode, agents active)
HOLDING     → peak luminance held (escalation arriving, gathering focus)
RELEASING   → slow fade from peak to idle (escalation resolved, returning to breath)
SETTLING    → breath decelerates to stillness (coordinator completing, approaching handoff)
```

The transitions between breathing states have their own timing:

| Transition | Duration | Easing | Felt Quality |
|-----------|----------|--------|--------------|
| IDLE → BREATHING | 2.0s | ease-out | The space comes alive |
| BREATHING → HOLDING | 0.8s | ease-in | Attention gathering |
| HOLDING → BREATHING | 1.5s | ease-out | Releasing back to ambient |
| BREATHING → SETTLING | 3.0s | ease-out | Work winding down |
| SETTLING → IDLE | 1.0s | linear | Stillness arrives |

---

## 4. Gradients and Shimmer

### Per-Character Color via `render_line`

Textual's `render_line(y: int) → Strip` method gives cell-level control over a widget's rendered output. A `Strip` is a list of `Segment(text, style)` tuples. By computing a unique `Style(color=...)` per cell, we can render smooth horizontal gradients, traveling waves, and shimmer effects within a single line.

```python
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style
from rich.color import Color

class BreathWidget(Widget):

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        t = self._animation_time
        segments = []

        for x in range(width):
            # Breathing luminance modulation
            base_l = 0.55  # text-dim luminance
            breath_offset = self._breath_value(t) * 0.15  # ±15% range
            l = base_l + breath_offset

            # Horizontal gradient: subtle hue shift across width
            hue = 255 + (x / width) * 15  # 255-270, blue to blue-violet
            r, g, b = oklch_to_srgb(l, 0.02, hue)

            style = Style(color=Color.from_rgb(r, g, b))
            char = self._content_at(x, y) or " "
            segments.append(Segment(char, style))

        return Strip(segments)
```

**Performance constraint:** `render_line` is called on every frame for visible lines. At 30 FPS on an 80-column terminal, that's 2,400 segments/frame for one line. Textual's segment-tree compositor handles this efficiently, but:
- Pre-compute `oklch_to_srgb` as a lookup table (256 luminance levels × hue count), not per-frame
- Cache `Strip` when no animation state has changed (skip re-render)
- Only render gradient lines for the breath widget and status bar — conversation text uses standard Rich styles

### Shimmer: The Traveling Luminance Wave

During breath mode, a subtle traveling wave of luminance crosses the breath widget — like sunlight moving across water, or the gentle glow of a distant signal.

```
shimmer(x, t) = base_l + amplitude * sin(x / wavelength - t * speed)

where:
  base_l = current breath luminance
  amplitude = 0.03 (barely perceptible, 3% luminance variation)
  wavelength = 20 cells (one wave per ~quarter of an 80-col terminal)
  speed = 0.8 (cells per second — glacial, not busy)
```

The shimmer is **additive to the breath**. The breath provides the macro rhythm (4.5s cycle, whole-widget). The shimmer provides micro texture (spatial variation across the width). Together they create the sense of a living surface — not mechanical pulsing but organic, light-on-water presence.

**When shimmer activates:**
- Breath mode, agents actively working → shimmer on
- Breath mode, waiting for model inference → shimmer off, breath only
- All other modes → no shimmer

### Gradient Applications

**Status bar gradient.** The status bar background has a horizontal gradient from `surface-deep` at the left edge to `surface` at the right, creating a subtle sense of depth — the status bar isn't flat, it has an atmospheric quality.

**Cycle-type color wash.** When a cycle transitions (PLAN → EXECUTE), the breath widget's background briefly tints with the incoming cycle's accent color at very low chroma (C: 0.02-0.03), then settles back to neutral surface. This is the atmospheric equivalent of the sky changing color — you notice it happened but can't point to the moment.

**Escalation arrival gradient.** When an escalation surfaces, a horizontal luminance gradient sweeps across the line from left to right over 0.5s — like a spotlight passing across a stage, drawing the eye to the card that's appearing. This uses the `accent-orange` or `accent-rose` hue (depending on severity) at very low chroma.

### Half-Block Sub-Cell Resolution

Unicode half-block characters (`▀` upper half, `▄` lower half) with independent foreground and background colors enable 2× vertical resolution. Each character cell becomes two independently colored pixels.

**Application in Clou:** The cycle-transition indicator in the breath widget uses half-blocks to render a thin (1-cell-height = 2-pixel-height) color bar that separates cycle sections. The top pixel is the outgoing cycle's accent-dim color; the bottom pixel is the incoming cycle's accent-dim color. This creates a seam that feels like a natural boundary between phases, not a hard rule line.

```python
# Cycle transition indicator using half-blocks
upper_color = cycle_colors[outgoing_cycle]  # dim variant
lower_color = cycle_colors[incoming_cycle]  # dim variant
segment = Segment("▀", Style(
    color=Color.from_rgb(*upper_color),
    bgcolor=Color.from_rgb(*lower_color),
))
```

---

## 5. Design Tokens

### Token Architecture

Tokens are organized in three tiers:

1. **Primitive tokens** — raw OKLCH values. Never referenced directly in `.tcss`. Defined in `theme.py`.
2. **Semantic tokens** — named roles (`$text`, `$accent-plan`, `$surface`). Referenced in `.tcss`. Map to primitives.
3. **Component tokens** — mode-specific overrides (`$breath-text`, `$escalation-border`). Referenced in scoped `.tcss` rules. Map to semantic tokens.

```
Primitive (OKLCH values)
    ↓ mapped by theme.py
Semantic ($text, $accent-plan, $surface)
    ↓ scoped by .tcss rules
Component ($breath-text = $text-dim in breath mode)
```

### Semantic Token Definitions

```tcss
/* === Surface === */
$surface-deep: #171928;
$surface: #1e2030;
$surface-raised: #262838;
$surface-overlay: #2e3042;

/* === Borders === */
$border-subtle: #383a4e;
$border: #43455a;
$border-bright: #4f5168;

/* === Text === */
$text-muted: #5e6078;
$text-dim: #848698;
$text: #dbd9e3;
$text-bright: #f0eef5;

/* === Accents (base) === */
$accent-gold: #c4a35d;
$accent-blue: #7d9ed5;
$accent-teal: #5bb8a7;
$accent-amber: #c99f4c;
$accent-violet: #b38dc5;
$accent-green: #5cba77;
$accent-orange: #d1984f;
$accent-rose: #d57d71;

/* === Accents (dim — derived L:0.55, C×0.6) === */
$accent-gold-dim: #8d7a48;
$accent-blue-dim: #607a9e;
$accent-teal-dim: #48897e;
$accent-amber-dim: #91753a;
$accent-violet-dim: #846a91;
$accent-green-dim: #468a5a;
$accent-orange-dim: #986f3d;
$accent-rose-dim: #9b5f56;

/* === Accents (bright — derived L:0.85, C×1.2) === */
$accent-gold-bright: #e6c873;
$accent-blue-bright: #a0c0f0;
$accent-teal-bright: #78dcc8;
$accent-amber-bright: #ebc05e;
$accent-violet-bright: #d4ade6;
$accent-green-bright: #76e094;
$accent-orange-bright: #f5b563;
$accent-rose-bright: #f49a8c;
```

### Component Token Mapping by Mode

```tcss
/* === Dialogue mode === */
ClouApp.dialogue {
    /* Full presence — standard tokens */
}

ClouApp.dialogue #conversation {
    background: $surface-deep;
    color: $text;
}

ClouApp.dialogue .user-message {
    color: $accent-gold;
}

ClouApp.dialogue .assistant-message {
    color: $text;
}

ClouApp.dialogue .tool-use {
    color: $text-dim;
    background: $surface;
}

/* === Breath mode === */
ClouApp.breath #conversation {
    /* Receded — conversation dims */
    color: $text-muted;
    background: $surface-deep;
    max-height: 30%;
}

ClouApp.breath #breath-widget {
    display: block;
    background: $surface-deep;
    color: $text-dim;
}

ClouApp.breath .cycle-plan {
    color: $accent-blue;
}

ClouApp.breath .cycle-execute {
    color: $accent-teal;
}

ClouApp.breath .cycle-assess {
    color: $accent-amber;
}

ClouApp.breath .cycle-verify {
    color: $accent-violet;
}

ClouApp.breath .agent-active {
    color: $accent-teal;
}

ClouApp.breath .agent-complete {
    color: $accent-green-dim;
}

ClouApp.breath .agent-failed {
    color: $accent-rose-dim;
}

/* === Decision mode === */
ClouApp.decision #breath-widget {
    /* Breath pauses, dims further */
    color: $text-muted;
}

ClouApp.decision .escalation-card {
    background: $surface-overlay;
    border: solid $accent-orange;
    color: $text;
}

ClouApp.decision .escalation-card.blocking {
    border: solid $accent-rose;
}

ClouApp.decision .escalation-option {
    color: $text-dim;
}

ClouApp.decision .escalation-option:focus {
    color: $text-bright;
    background: $surface-raised;
    border-left: thick $accent-gold;
}

ClouApp.decision .escalation-recommendation {
    color: $accent-gold;
}

/* === Handoff mode === */
ClouApp.handoff {
    background: $surface-deep;
    color: $text;
}

ClouApp.handoff .service-url {
    color: $accent-teal;
    text-style: underline;
}

ClouApp.handoff .verification-pass {
    color: $accent-green;
}

ClouApp.handoff .verification-fail {
    color: $accent-rose;
}

ClouApp.handoff .known-limitation {
    color: $accent-orange;
}
```

### Status Bar Tokens

```tcss
#status-bar {
    dock: bottom;
    height: 1;
    background: $surface;
    color: $text-dim;
}

#status-bar .label {
    color: $text-muted;
}

#status-bar .milestone {
    color: $accent-gold;
    text-style: bold;
}

#status-bar .cycle-indicator {
    /* Color set dynamically by cycle type */
}

#status-bar .tokens {
    color: $text-dim;
}

#status-bar .cost {
    color: $text-muted;
}

#status-bar .rate-limited {
    color: $accent-orange;
    text-style: bold;
}
```

---

## 6. Motion Design

### Timing Constants

```python
# In theme.py or constants.py

TIMING = {
    # Interaction response
    "instant": 0,           # Direct state changes (keypress echo)
    "snap": 100,             # UI reorganization from user action
    "transition": 300,       # View changes, mode initiation

    # Atmospheric shifts
    "atmosphere_in": 1500,   # Dialogue → Breath, breath emerging
    "atmosphere_out": 500,   # Breath → Dialogue, snapping back to focus
    "gather": 800,           # Breath → Decision, attention gathering
    "release": 1500,         # Decision → Breath, releasing
    "settle": 3000,          # Breath → Handoff, work winding down

    # Breathing
    "breath_period": 4500,   # One full respiratory cycle (ms)
    "shimmer_speed": 800,    # Shimmer wave travel speed (ms per wavelength)

    # Content
    "event_linger": 2000,    # Breath event visible before dimming
    "cycle_announce": 1000,  # Cycle transition announcement hold
}
```

### Easing Functions

| Transition Type | Easing | Rationale |
|----------------|--------|-----------|
| Mode entering (atmosphere_in) | `ease-out` (decelerate) | Arriving gently, settling in |
| Mode exiting (atmosphere_out) | `ease-out` (decelerate) | Quick departure, gentle stop |
| Attention gathering (gather) | `ease-in` (accelerate) | Building urgency, something needs you |
| Release (release) | `ease-out` (decelerate) | Tension dissolving |
| Breathing (breath cycle) | `exp(sin(t))` (custom) | Respiratory kinematics + Weber-Fechner correction |
| Shimmer | `sin(x - vt)` (linear travel) | Constant-velocity wave — no acceleration |
| Escalation card entry | `ease-out` | Surfaces with presence, settles into place |
| Completion marker | `ease-in-out` | Natural arc — appears, holds, settles |

### Animation Loop

Breath-mode animations run on a `set_interval` timer within the Textual app. The interval determines the visual frame rate.

```python
ANIMATION_FPS = 24  # 24 FPS is perceptually smooth for luminance changes
                     # (not spatial motion — that needs 30-60 FPS)
ANIMATION_INTERVAL = 1.0 / ANIMATION_FPS  # ~42ms

class ClouApp(App):

    def on_mount(self) -> None:
        self._animation_timer = None
        self._animation_time = 0.0

    def _start_breathing(self) -> None:
        """Begin the breath animation loop."""
        if self._animation_timer is None:
            self._animation_time = 0.0
            self._animation_timer = self.set_interval(
                ANIMATION_INTERVAL, self._animation_tick
            )

    def _stop_breathing(self) -> None:
        """Stop the breath animation loop."""
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None

    def _animation_tick(self) -> None:
        """Called every frame during breath mode."""
        self._animation_time += ANIMATION_INTERVAL
        breath_value = self._compute_breath(self._animation_time)
        # Post to breath widget for render_line recomputation
        self.query_one(BreathWidget).breath_phase = breath_value

    def _compute_breath(self, t: float) -> float:
        """exp(sin(t)) breathing curve, normalized to [0, 1]."""
        import math
        period = TIMING["breath_period"] / 1000.0
        raw = math.exp(math.sin(t * 2 * math.pi / period))
        # Normalize: exp(-1) to exp(1) → 0 to 1
        e = math.e
        return (raw - 1/e) / (e - 1/e)
```

### Event Lifecycle Animation

Breath events (status lines from coordinator activity) have an arrival-linger-settle lifecycle:

1. **Arrival** (0-100ms): Text appears at `text` luminance (full brightness) — a spark of visibility
2. **Linger** (100ms-2s): Holds at `text-dim` luminance — readable if you're looking
3. **Settle** (2s-4s): Fades to `text-muted` luminance — joins the ambient background
4. **Resting**: Remains at `text-muted` — part of the history, not demanding attention

The most recent event is always at the linger or arrival stage. Events stack upward, with each settling into progressively dimmer states. This creates a natural recency gradient — the freshest information is brightest, older information recedes. No explicit "latest" marker needed; luminance IS the marker.

---

## 7. Attentional Salience Map

Different interface elements compete for different perceptual channels. The salience map ensures that the right thing captures attention at the right time.

### Preattentive Capture (< 100ms, involuntary)

These elements grab attention before conscious processing. Use sparingly — only for things that genuinely need immediate awareness.

| Element | Mechanism | When |
|---------|-----------|------|
| Escalation arrival | Chromatic flash (`accent-orange-bright` or `accent-rose-bright`) + luminance sweep | BREATH → DECISION |
| Agent failure | `accent-rose` text appearing in a field of `text-dim` content | Breath mode, agent crashes |
| Rate limit warning | `accent-orange` pulsing in status bar | Any mode, rate limited |

### Attentive Processing (100ms-1s, voluntary)

These elements reward focused attention. They use luminance contrast (readable text) rather than chromatic pop.

| Element | Mechanism | When |
|---------|-----------|------|
| Conversation text | High luminance contrast (`text` on `surface-deep`) | Dialogue mode |
| Escalation content | `text` on `surface-overlay` with structured layout | Decision mode |
| Handoff document | `text` on `surface-deep`, rich markdown formatting | Handoff mode |

### Ambient Awareness (> 1s, peripheral)

These elements communicate through peripheral vision — detected without focal attention. They use rhythm and subtle chromatic signals.

| Element | Mechanism | When |
|---------|-----------|------|
| Breathing animation | Luminance oscillation at respiratory rhythm | Breath mode |
| Cycle-type color | Chromatic tint matching cycle (blue/teal/amber/violet) | Breath mode |
| Active agent count | Teal indicators in breath widget, count readable at glance | Breath mode |
| Token counter | Steady increment in status bar | All modes |
| Shimmer wave | Sub-threshold luminance variation traveling across surface | Breath mode, agents active |

### Salience Stack

When channels overlap (escalation arrives during shimmer animation), higher-salience elements suppress lower ones:

```
Preattentive (escalation flash) → suppresses shimmer, pauses breathing
Attentive (escalation content) → shimmer off, breathing held at peak
Ambient (breathing resumes) → shimmer returns when agents active
```

This is implemented by the breathing state machine (§3): BREATHING → HOLDING suppresses shimmer, HOLDING → BREATHING re-enables it.

---

## 8. Typography and Whitespace

### Typographic Hierarchy

Terminal typography has one lever: **text style** (bold, dim, italic, underline, strikethrough). Combined with the color palette, this creates a rich but bounded hierarchy.

| Level | Style | Color | Usage |
|-------|-------|-------|-------|
| **Heading 1** | bold | `text-bright` | Milestone names, handoff title |
| **Heading 2** | bold | `text` | Section headers, phase names |
| **Body** | normal | `text` | Dialogue text, handoff content |
| **Secondary** | normal | `text-dim` | Breath events, timestamps, metadata |
| **Tertiary** | normal | `text-muted` | Settled breath events, labels |
| **Emphasis** | bold | `text` | Key terms within body text |
| **Code** | normal | `accent-blue` | File paths, function names, commands |
| **Link** | underline | `accent-teal` | URLs in handoff, references |
| **Error** | bold | `accent-rose` | Error messages, failure summaries |
| **Success** | normal | `accent-green` | Completion markers |

### Whitespace Architecture

Whitespace is Clou's primary structural element — more important than borders. Following from the perceptual research: terminal whitespace creates grouping (Gestalt proximity), breathing room, and scannability.

**The Void.** In breath mode, the space between the receded conversation and the breath events is not empty screen. It is **the void** — an intentional positive presence communicating "room to breathe." The void should be at least 3-4 lines. If the breath widget content is sparse, the void expands. If content is dense, the void compresses but never disappears.

**Spacing scale:**

| Token | Lines | Usage |
|-------|-------|-------|
| `space-xs` | 0 (inline) | Between label and value on same line |
| `space-sm` | 1 | Between breath events |
| `space-md` | 2 | Between sections (conversation and breath area) |
| `space-lg` | 3 | Above/below escalation card, the void minimum |
| `space-xl` | 4 | Major mode transition breathing room |

**Internal padding for cards:**

```
╭──────────────────────────────────────────────╮
│                                              │  ← 1 line top padding
│  ⚠ Requirement Conflict                     │
│                                              │
│  The auth spec requires OAuth2 but the       │  ← 2-char left/right padding
│  existing codebase uses session tokens.      │
│                                              │
│  Options:                                    │
│  ▸ Migrate to OAuth2 (recommended)           │
│    Adds 2-3 days, resolves compliance        │
│  ▹ Wrap session tokens in OAuth2 facade      │
│    Faster, deferred migration risk           │
│                                              │  ← 1 line bottom padding
╰──────────────────────────────────────────────╯
```

### Alignment on the Monospace Grid

The monospace grid is a perceptual scaffold — alignment is free and powerful.

**Breath events align by structure:**
```
PLAN      compose.py written  7 tasks across 3 phases
EXECUTE   phase:foundation    deploying 3 agents
          task:setup_db       complete
ASSESS    invoking brutalist  3 domains
          2 findings accepted  1 overridden → rework
```

- Cycle type left-aligned at column 0, uppercase, colored by cycle
- Description left-aligned at column 10 (after the longest cycle type + padding)
- Sub-events indented to column 10 (aligned with parent description)

**Status bar alignment:**
```
clou  auth-system  EXECUTE #4  phase:foundation  tokens: 142,338↓ 28,102↑  $3.42
```

- Fixed-width fields where possible (cycle type padded, token counts right-aligned)
- Natural reading order: identity → context → activity → metrics → cost

---

## 9. Borders and Structural Elements

### Border Vocabulary

| Element | Border Style | Character Set | When |
|---------|-------------|---------------|------|
| Escalation card | Rounded heavy | `╭╮╰╯│─` | Decision mode |
| On-demand panels | Rounded light | `╭╮╰╯│─` | Overlays (context tree, DAG) |
| Cycle separator | Thin horizontal | `─` | Between cycles in breath history |
| Section divider | Thin horizontal with gap | `── ──` | Within conversation, between turns |
| Agent indicator | Bullet | `●` active, `○` complete, `✕` failed | Breath widget |
| Progress | Bar | `█▓▒░` (quarter blocks) | Phase progress in breath widget |

### Border Color Rules

- Escalation card border: `accent-orange` (non-blocking) or `accent-rose` (blocking)
- Panel borders: `border` (standard) or `border-bright` (focused)
- Cycle separators: `border-subtle`
- The status bar has **no border** — it is a natural boundary at the screen edge, not a bordered element

---

## 10. Implementation in `theme.py`

```python
"""Clou color palette and design tokens.

All colors specified in OKLCH (Lightness, Chroma, Hue) and converted to
hex for Textual consumption. The OKLCH specification is the source of truth;
hex values are derived.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OklchColor:
    """A color in OKLCH space."""
    l: float  # Lightness [0, 1]
    c: float  # Chroma [0, ~0.37]
    h: float  # Hue [0, 360]

    def to_hex(self) -> str:
        """Convert OKLCH → sRGB hex. Uses Oklab as intermediate."""
        import math
        # OKLCH → Oklab
        a = self.c * math.cos(math.radians(self.h))
        b = self.c * math.sin(math.radians(self.h))
        L = self.l

        # Oklab → linear sRGB (via LMS)
        l_ = L + 0.3963377774 * a + 0.2158037573 * b
        m_ = L - 0.1055613458 * a - 0.0638541728 * b
        s_ = L - 0.0894841775 * a - 1.2914855480 * b

        l = l_ ** 3
        m = m_ ** 3
        s = s_ ** 3

        r_lin = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
        g_lin = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
        b_lin = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

        def linear_to_srgb(c: float) -> int:
            c = max(0.0, min(1.0, c))
            if c <= 0.0031308:
                c = 12.92 * c
            else:
                c = 1.055 * (c ** (1.0 / 2.4)) - 0.055
            return max(0, min(255, round(c * 255)))

        return f"#{linear_to_srgb(r_lin):02x}{linear_to_srgb(g_lin):02x}{linear_to_srgb(b_lin):02x}"

    def with_l(self, l: float) -> "OklchColor":
        return OklchColor(l, self.c, self.h)

    def with_c(self, c: float) -> "OklchColor":
        return OklchColor(self.l, c, self.h)

    def dim(self) -> "OklchColor":
        """Dim variant: L→0.55, C×0.6"""
        return OklchColor(0.55, self.c * 0.6, self.h)

    def bright(self) -> "OklchColor":
        """Bright variant: L→0.85, C×1.2"""
        return OklchColor(0.85, min(self.c * 1.2, 0.37), self.h)


# === Primitive Palette ===
# Source of truth. All hex values derived from these.

PALETTE = {
    # Surfaces (H:260, near-achromatic)
    "surface-deep":    OklchColor(0.13, 0.015, 260),
    "surface":         OklchColor(0.17, 0.015, 260),
    "surface-raised":  OklchColor(0.21, 0.015, 260),
    "surface-overlay": OklchColor(0.25, 0.012, 260),

    # Borders
    "border-subtle":   OklchColor(0.30, 0.010, 260),
    "border":          OklchColor(0.35, 0.010, 260),
    "border-bright":   OklchColor(0.40, 0.012, 260),
    "surface-bright":  OklchColor(0.45, 0.010, 260),

    # Text
    "text-muted":      OklchColor(0.45, 0.008, 250),
    "text-dim":        OklchColor(0.60, 0.008, 250),
    "text":            OklchColor(0.88, 0.010, 80),
    "text-bright":     OklchColor(0.95, 0.005, 80),

    # Accents (all L:0.72 — equal perceived brightness)
    "accent-gold":     OklchColor(0.72, 0.12, 75),
    "accent-blue":     OklchColor(0.72, 0.10, 255),
    "accent-teal":     OklchColor(0.72, 0.10, 180),
    "accent-amber":    OklchColor(0.72, 0.12, 55),
    "accent-violet":   OklchColor(0.72, 0.10, 295),
    "accent-green":    OklchColor(0.72, 0.12, 145),
    "accent-orange":   OklchColor(0.72, 0.14, 50),
    "accent-rose":     OklchColor(0.72, 0.14, 15),
}


def build_css_variables() -> str:
    """Generate CSS variable block for clou.tcss."""
    lines = []
    for name, color in PALETTE.items():
        lines.append(f"${name}: {color.to_hex()};")
        # Generate dim/bright variants for accents
        if name.startswith("accent-"):
            lines.append(f"${name}-dim: {color.dim().to_hex()};")
            lines.append(f"${name}-bright: {color.bright().to_hex()};")
    return "\n".join(lines)
```

---

## 11. Validation Criteria

### Phenomenological

- **Twilight test.** Look at the full palette rendered on screen. Does it feel like twilight — cool, deep, alive? Or does it feel cold and sterile? If sterile, the surface chroma is too low or the accent warmth is insufficient.
- **Breath test.** Watch the breathing animation for 60 seconds without focusing on it. Does it feel like the system is alive? Or does it feel mechanical (too regular) or anxious (too fast)? The rhythm should be noticeable when you attend to it, invisible when you don't.
- **Shimmer test.** Can you see the shimmer if you look for it? Can you ignore it if you're reading conversation history? If it demands attention, the amplitude is too high. If it's completely invisible, the amplitude is too low.
- **Escalation interrupt test.** During breath mode, trigger an escalation. Does the arrival feel weighty and present without being alarming? The chromatic flash should attract, not startle.

### Analytical

- [ ] All 8 accent hues distinguishable at L:0.72 (test with color-blindness simulators — protanopia, deuteranopia)
- [ ] Surface scale produces ≥3:1 contrast ratio between adjacent levels
- [ ] Text on surface-deep meets WCAG AA contrast (≥4.5:1 for `text`, ≥3:1 for `text-dim`)
- [ ] Breathing animation does not trigger photosensitive seizure thresholds (< 3 flashes/sec, < 25% area change)
- [ ] `render_line` gradient rendering holds 24 FPS on 120-column terminal
- [ ] 256-color fallback preserves semantic distinctions (all 8 accents map to different 256-color indices)
- [ ] 16-ANSI fallback is functional (cycle types distinguishable, text hierarchy preserved)

### Color Blindness Considerations

The accent palette relies on hue for semantic meaning. For the ~8% of males with color vision deficiency:

- **Protanopia/Deuteranopia (red-green):** `accent-rose` and `accent-green` may be hard to distinguish. Mitigated by: (1) semantic context (rose = error, green = success — never ambiguous in isolation), (2) text labels always accompany color, (3) bright/dim variants still differ in luminance.
- **Tritanopia (blue-yellow):** `accent-blue` and `accent-gold` may converge. Mitigated by: blue = PLAN and gold = primary/user — they appear in different contexts, never side by side as comparable elements.

The breathing animation and shimmer are pure luminance modulations — unaffected by color deficiency. The critical ambient awareness channel (Is the system alive? Is work happening?) is color-blind accessible by design.

---

## 12. Open Questions for Prototyping

1. **Exact hex values.** The OKLCH specifications are the design intent. Exact hex rendering depends on terminal color profile (sRGB assumed). Verify against iTerm2, Kitty, Alacritty, macOS Terminal.app, and WezTerm with both default and popular themes.

2. **Breath amplitude.** 15% luminance range is the starting point. May need adjustment per-display — OLED panels render dark values differently than LCD.

3. **Shimmer perceptibility threshold.** 3% luminance amplitude is theoretical. Test in actual terminal at normal reading distance (~60cm). May need to be 5% for 256-color terminals where the luminance quantization is coarser.

4. **Animation FPS.** 24 FPS for luminance is the starting point. If `render_line` calls are too expensive on wide terminals (200+ columns), drop to 15 FPS (the shimmer and breath are slow enough to tolerate it).

5. **Gold vs. the terminal's default yellow.** Many terminal themes have a custom yellow that may clash with `accent-gold`. Test whether Clou's accent feels intentional alongside the terminal's own palette, or whether it creates a dissonant "two different yellows" effect.

6. **Surface-deep vs. terminal background.** If the user's terminal background is different from `surface-deep` (#171928), there may be a visible boundary. Option: detect terminal background color (Textual can query it) and adjust `surface-deep` to match, treating it as the one non-fixed token.
