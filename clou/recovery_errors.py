"""Error classification for worker task failures.

Maps worker errors to hold (wait until reset, no retry-bounded), transient
(auto-retry with cooldown, bounded), or terminal (escalate immediately).
Used by the coordinator loop to decide whether to suspend, retry, or abort
a failed task.

Rate-limit is HOLD, not TRANSIENT: the remote server has published a reset
time and retrying before it burns the very capacity that's rate-limited.
Under the "zero escalations" principle, rate-limit must never become
terminal and never fire an escalation.

Internal module -- import from clou.recovery for public API.
"""

from __future__ import annotations

import logging
import re
from enum import Enum

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class ErrorKind(str, Enum):
    """Classification of a worker error.

    HOLD: Rate limit -- wait for reset; never counts against retry budget,
        never converts to terminal.
    TRANSIENT: Network, sleep, timeout -- auto-retry with cooldown, bounded.
    TERMINAL: Config, code bug, missing dependency, repeated failure -- escalate.
    """

    HOLD = "hold"
    TRANSIENT = "transient"
    TERMINAL = "terminal"


#: Patterns that indicate a rate-limit / quota HOLD condition.
#: The remote server has scheduled-capacity; retrying burns the same
#: capacity that's rate-limited.  Matched case-insensitively against the
#: error message.
_HOLD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"rate[\s_-]*limit",
        r"\b429\b",
        r"\bquota\b",
        r"throttl",
    )
)


#: Patterns that indicate a transient (retriable) error.
#: Matched case-insensitively against the error message.
_TRANSIENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"timeout",
        r"timed?\s*out",
        r"connection",
        r"\b503\b",
        r"\bECONNRESET\b",
        r"network",
        r"\bsleep\b",
        r"unavailable",
        r"temporarily",
        r"retry",
    )
)

#: Patterns that indicate a terminal (non-retriable) error.
#: Matched case-insensitively against the error message.
#: Terminal patterns take precedence over transient when both match.
_TERMINAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"SyntaxError",
        r"ImportError",
        r"ModuleNotFoundError",
        r"FileNotFoundError",
        r"PermissionError",
        r"\bconfig\b",
        r"\bschema\b",
        r"TypeError",
        r"ValueError",
        r"KeyError",
        r"AttributeError",
        r"NameError",
    )
)

#: Default cooldown between transient retries (seconds).
DEFAULT_RETRY_COOLDOWN: float = 30.0

#: Default cooldown when holding on a rate limit without a known reset time.
#: Used as a fallback when ``resets_at`` is not available from the typed
#: SDK ``RateLimitEvent``.  Longer than transient cooldown: the remote
#: server's reset window is typically minutes, not seconds.
DEFAULT_HOLD_COOLDOWN: float = 60.0

#: Default maximum retries before a transient error becomes terminal.
#: Does NOT apply to HOLD -- rate-limit waits indefinitely; retries don't
#: accumulate against a budget because retries don't help.
DEFAULT_MAX_RETRIES: int = 2


def compute_hold_wait(
    resets_at: int | float | None,
    now: float,
    default: float = DEFAULT_HOLD_COOLDOWN,
    floor: float = 1.0,
    ceiling: float = 3600.0,
) -> float:
    """Return seconds to sleep before retrying a HOLD error.

    If *resets_at* is a unix timestamp in the future, wait until then
    (plus a small buffer handled by the caller).  If *resets_at* is in
    the past, missing, or malformed, fall back to *default*.  The
    returned value is clamped to ``[floor, ceiling]`` -- a rate-limit
    reset time in the distant future is almost certainly a clock skew
    or stale header, not a real hour-long hold.
    """
    if resets_at is None:
        return default
    try:
        wait = float(resets_at) - float(now)
    except (TypeError, ValueError):
        return default
    if wait <= 0:
        # Reset time in the past -- treat as "just happened", wait briefly.
        return floor
    return max(floor, min(ceiling, wait))


def classify_error(
    error_message: str,
    task_name: str,
    retry_count: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[ErrorKind, str]:
    """Classify a worker error as hold, transient, or terminal.

    Returns ``(kind, reason)`` where *reason* is human-readable evidence
    for the classification decision.

    Classification rules (evaluated in order):
    1. Terminal patterns in the error message -> TERMINAL.
    2. Hold patterns (rate-limit, 429, throttl, quota) -> HOLD.
       HOLD is never converted to TERMINAL by retry count -- retries
       don't help with scheduled capacity, and escalating to the user
       violates the zero-escalations principle.
    3. Transient errors that have exceeded *max_retries* become terminal
       (prevents infinite retry loops on genuinely-failing tasks).
    4. Transient patterns in the error message -> TRANSIENT.
    5. Default: TERMINAL (conservative -- unknown errors escalate).

    The decision is logged at INFO level with the task name, error kind,
    and classification reason.
    """
    # Rule 1: Terminal patterns take precedence.  A task that fails with
    # both "ImportError" and "rate limit" text is a coding bug that
    # happens to mention rate-limit in its traceback -- don't HOLD on
    # a structural failure.
    for pat in _TERMINAL_PATTERNS:
        if pat.search(error_message):
            reason = f"terminal pattern matched: {pat.pattern!r}"
            _log.info(
                "Error classification for task %r: %s -- %s",
                task_name, ErrorKind.TERMINAL.value, reason,
            )
            return ErrorKind.TERMINAL, reason

    # Rule 2: Hold patterns -> wait for reset.  Not bounded by retries;
    # not convertible to terminal.
    for pat in _HOLD_PATTERNS:
        if pat.search(error_message):
            reason = f"hold pattern matched: {pat.pattern!r}"
            _log.info(
                "Error classification for task %r: %s -- %s",
                task_name, ErrorKind.HOLD.value, reason,
            )
            return ErrorKind.HOLD, reason

    # Rule 3: Transient errors that recurred beyond max_retries -> terminal.
    if retry_count >= max_retries:
        for pat in _TRANSIENT_PATTERNS:
            if pat.search(error_message):
                reason = (
                    f"transient pattern matched ({pat.pattern!r}) but "
                    f"retry count {retry_count} >= max_retries {max_retries}"
                )
                _log.info(
                    "Error classification for task %r: %s -- %s",
                    task_name, ErrorKind.TERMINAL.value, reason,
                )
                return ErrorKind.TERMINAL, reason

    # Rule 4: Transient patterns -> retry.
    for pat in _TRANSIENT_PATTERNS:
        if pat.search(error_message):
            reason = f"transient pattern matched: {pat.pattern!r}"
            _log.info(
                "Error classification for task %r: %s -- %s",
                task_name, ErrorKind.TRANSIENT.value, reason,
            )
            return ErrorKind.TRANSIENT, reason

    # Rule 5: Default to terminal (conservative).
    reason = "no known pattern matched -- defaulting to terminal"
    _log.info(
        "Error classification for task %r: %s -- %s",
        task_name, ErrorKind.TERMINAL.value, reason,
    )
    return ErrorKind.TERMINAL, reason
