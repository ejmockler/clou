"""Error classification for worker task failures.

Maps worker errors to transient (auto-retry with cooldown) or terminal
(escalate immediately).  Used by the coordinator loop to decide whether
to retry a failed task or proceed with existing abort/escalation logic.

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

    TRANSIENT: Network, rate limit, sleep, timeout -- auto-retry with cooldown.
    TERMINAL: Config, code bug, missing dependency, repeated failure -- escalate.
    """

    TRANSIENT = "transient"
    TERMINAL = "terminal"


#: Patterns that indicate a transient (retriable) error.
#: Matched case-insensitively against the error message.
_TRANSIENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"timeout",
        r"timed?\s*out",
        r"connection",
        r"rate\s*limit",
        r"\b429\b",
        r"\b503\b",
        r"\bECONNRESET\b",
        r"network",
        r"\bsleep\b",
        r"unavailable",
        r"temporarily",
        r"retry",
        r"throttl",
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

#: Default maximum retries before a transient error becomes terminal.
DEFAULT_MAX_RETRIES: int = 2


def classify_error(
    error_message: str,
    task_name: str,
    retry_count: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[ErrorKind, str]:
    """Classify a worker error as transient or terminal.

    Returns ``(kind, reason)`` where *reason* is human-readable evidence
    for the classification decision.

    Classification rules (evaluated in order):
    1. Transient errors that have exceeded *max_retries* become terminal
       (prevents infinite retry loops).
    2. Terminal patterns in the error message -> TERMINAL.
    3. Transient patterns in the error message -> TRANSIENT.
    4. Default: TERMINAL (conservative -- unknown errors escalate).

    The decision is logged at INFO level with the task name, error kind,
    and classification reason.
    """
    # Rule 1: Transient errors that recurred beyond max_retries -> terminal.
    if retry_count >= max_retries:
        # Check if this *would* be transient before escalating.
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

    # Rule 2: Terminal patterns override transient.
    for pat in _TERMINAL_PATTERNS:
        if pat.search(error_message):
            reason = f"terminal pattern matched: {pat.pattern!r}"
            _log.info(
                "Error classification for task %r: %s -- %s",
                task_name, ErrorKind.TERMINAL.value, reason,
            )
            return ErrorKind.TERMINAL, reason

    # Rule 3: Transient patterns -> retry.
    for pat in _TRANSIENT_PATTERNS:
        if pat.search(error_message):
            reason = f"transient pattern matched: {pat.pattern!r}"
            _log.info(
                "Error classification for task %r: %s -- %s",
                task_name, ErrorKind.TRANSIENT.value, reason,
            )
            return ErrorKind.TRANSIENT, reason

    # Rule 4: Default to terminal (conservative).
    reason = "no known pattern matched -- defaulting to terminal"
    _log.info(
        "Error classification for task %r: %s -- %s",
        task_name, ErrorKind.TERMINAL.value, reason,
    )
    return ErrorKind.TERMINAL, reason
