"""Mode transitions and breathing state machine.

Defines the legal mode transitions with timing metadata, the breathing
state machine that drives ambient animation, and the global timing
constants from the visual-language specification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto

# ---------------------------------------------------------------------------
# Mode enum
# ---------------------------------------------------------------------------


class Mode(Enum):
    """The four atmospheric modes of the Clou interface."""

    DIALOGUE = auto()
    BREATH = auto()
    DECISION = auto()
    HANDOFF = auto()

# ---------------------------------------------------------------------------
# Mode transition metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionMeta:
    """Timing and easing for a single mode transition."""

    duration_ms: int
    easing: str


#: Legal mode transitions.  Any (from, to) pair not present here
#: is illegal and ``get_transition`` returns ``None``.
TRANSITIONS: dict[tuple[Mode, Mode], TransitionMeta] = {
    (Mode.DIALOGUE, Mode.BREATH): TransitionMeta(duration_ms=1500, easing="out_cubic"),
    (Mode.BREATH, Mode.DIALOGUE): TransitionMeta(duration_ms=500, easing="out_cubic"),
    (Mode.BREATH, Mode.DECISION): TransitionMeta(duration_ms=800, easing="in_cubic"),
    (Mode.DECISION, Mode.BREATH): TransitionMeta(duration_ms=1500, easing="out_cubic"),
    (Mode.DIALOGUE, Mode.DECISION): TransitionMeta(duration_ms=300, easing="ease-out"),
    (Mode.DECISION, Mode.DIALOGUE): TransitionMeta(duration_ms=300, easing="ease-out"),
    (Mode.DECISION, Mode.HANDOFF): TransitionMeta(duration_ms=500, easing="ease-out"),
    (Mode.BREATH, Mode.HANDOFF): TransitionMeta(duration_ms=3000, easing="out_cubic"),
    (Mode.HANDOFF, Mode.DIALOGUE): TransitionMeta(duration_ms=500, easing="out_cubic"),
    (Mode.HANDOFF, Mode.BREATH): TransitionMeta(duration_ms=1500, easing="out_cubic"),
    (Mode.HANDOFF, Mode.DECISION): TransitionMeta(duration_ms=300, easing="ease-out"),
    (Mode.DIALOGUE, Mode.HANDOFF): TransitionMeta(duration_ms=500, easing="out_cubic"),
}


def get_transition(from_mode: Mode, to_mode: Mode) -> TransitionMeta | None:
    """Return the transition metadata if the transition is legal, else ``None``."""
    return TRANSITIONS.get((from_mode, to_mode))


# ---------------------------------------------------------------------------
# Breathing state machine
# ---------------------------------------------------------------------------


class BreathState(Enum):
    """States of the breathing animation."""

    IDLE = auto()
    BREATHING = auto()
    HOLDING = auto()
    RELEASING = auto()
    SETTLING = auto()


#: Legal breath-state transitions.  Presence in this dict means the
#: transition is allowed.
BREATH_TRANSITIONS: dict[tuple[BreathState, BreathState], bool] = {
    (BreathState.IDLE, BreathState.BREATHING): True,
    (BreathState.BREATHING, BreathState.HOLDING): True,
    (BreathState.HOLDING, BreathState.BREATHING): True,
    (BreathState.BREATHING, BreathState.SETTLING): True,
    (BreathState.SETTLING, BreathState.IDLE): True,
    (BreathState.HOLDING, BreathState.IDLE): True,  # interrupt
    (BreathState.BREATHING, BreathState.RELEASING): True,
    (BreathState.HOLDING, BreathState.RELEASING): True,  # graceful exit from hold
    (BreathState.RELEASING, BreathState.SETTLING): True,  # continue fade-out
    (BreathState.RELEASING, BreathState.IDLE): True,  # interrupt during release
}


class BreathStateMachine:
    """Drives the breathing animation through its legal states."""

    def __init__(self) -> None:
        self._state: BreathState = BreathState.IDLE

    @property
    def state(self) -> BreathState:
        """The current breathing state."""
        return self._state

    def transition(self, to: BreathState) -> bool:
        """Attempt a state transition.

        Returns ``True`` and updates the state if the transition is legal,
        ``False`` otherwise.
        """
        if BREATH_TRANSITIONS.get((self._state, to), False):
            self._state = to
            return True
        return False

    def reset(self) -> None:
        """Force-return to IDLE (unconditional)."""
        self._state = BreathState.IDLE

    @staticmethod
    def compute_breath(t: float, period: float = 4.5) -> float:
        """Compute the normalised breathing value at time *t*.

        Uses the ``exp(sin(t))`` waveform that matches respiratory
        kinematics and corrects for Weber-Fechner perception.

        Parameters
        ----------
        t:
            Elapsed time in seconds.
        period:
            Full respiratory cycle length in seconds (default 4.5 s).

        Returns
        -------
        float:
            A value in [0, 1] where 1 is peak (inhale hold) and
            0 is trough (exhale-to-inhale transition).
        """
        e_pos = math.e  # e^1
        e_neg = 1.0 / math.e  # e^(-1)
        raw = math.exp(math.sin(t * 2.0 * math.pi / period))
        return (raw - e_neg) / (e_pos - e_neg)


# ---------------------------------------------------------------------------
# Global timing constants (ms unless noted)
# ---------------------------------------------------------------------------

#: Named durations from the visual-language specification §6.
TIMING: dict[str, int] = {
    # Interaction response
    "instant": 0,
    "snap": 100,
    "transition": 300,
    # Atmospheric shifts
    "atmosphere_in": 1500,
    "atmosphere_out": 500,
    "gather": 800,
    "release": 1500,
    "releasing": 500,
    "settle": 3000,
    # Breathing
    "breath_period": 4500,
    "shimmer_speed": 800,
    # Content
    "event_linger": 2000,
    "cycle_announce": 1000,
}
