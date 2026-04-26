"""Tests for the ``_communicate_or_timeout`` helper and structural
invariants in :mod:`clou.recovery_git`.

The legacy tests in ``test_recovery.py`` (``test_git_revert_timeout``,
``test_git_commit_phase_timeout``) exercise the timeout contract
**transitively** through the public ``git_revert_golden_context`` and
``git_commit_phase`` entry points.  They assert that a hung subprocess
surfaces as ``RuntimeError``, and they assert that ``proc.kill()`` is
called — but they do NOT:

    * directly test the helper's behaviour (timeout path, happy path,
      message shape)
    * pin the production timeout *value* (any positive number satisfies
      the regex match)
    * enforce the structural invariant that EVERY ``proc.communicate()``
      call in ``recovery_git.py`` routes through the helper

Those three gaps are closed here.  The direct helper tests use a real
short timeout (0.05s) rather than monkeypatching ``asyncio.wait_for``,
so they also avoid the module-global-patch fragility the legacy tests
inherit.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from clou.recovery_git import (
    _GIT_SUBPROCESS_TIMEOUT,
    _communicate_or_timeout,
)


# ---------------------------------------------------------------------------
# Mocks — minimal asyncio.subprocess.Process-like objects
# ---------------------------------------------------------------------------


class _HangingProc:
    """Mock whose ``communicate()`` never returns in the test window."""

    def __init__(self) -> None:
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        # Longer than any plausible test-level timeout; the helper's
        # asyncio.wait_for() kicks in first and raises TimeoutError.
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _HappyProc:
    """Mock whose ``communicate()`` returns immediately with stubbed bytes."""

    def __init__(
        self,
        stdout: bytes = b"out",
        stderr: bytes = b"err",
    ) -> None:
        self.killed = False
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


# ---------------------------------------------------------------------------
# _communicate_or_timeout — direct helper tests
# ---------------------------------------------------------------------------


class TestCommunicateOrTimeoutHelper:
    """Direct tests of the timeout-wrapped communicate() helper."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_stdout_stderr_unchanged(
        self,
    ) -> None:
        """No timeout => helper is a pass-through.  Bytes flow through
        verbatim so callers can treat the helper as a drop-in
        replacement for ``await proc.communicate()``."""
        proc = _HappyProc(stdout=b"hello", stderr=b"world")
        out, err = await _communicate_or_timeout(
            proc, operation="test op", timeout=1.0,
        )
        assert out == b"hello"
        assert err == b"world"
        assert proc.killed is False

    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self) -> None:
        """A hung subprocess => RuntimeError, not a TimeoutError leak."""
        proc = _HangingProc()
        with pytest.raises(RuntimeError):
            await _communicate_or_timeout(
                proc, operation="test op", timeout=0.05,
            )

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess_before_raising(self) -> None:
        """The hung process MUST be killed — otherwise the helper
        leaks an OS process every time the timeout fires.  The legacy
        tests check this transitively via a captured flag; we pin it
        directly here."""
        proc = _HangingProc()
        with pytest.raises(RuntimeError):
            await _communicate_or_timeout(
                proc, operation="test op", timeout=0.05,
            )
        assert proc.killed is True

    @pytest.mark.asyncio
    async def test_timeout_message_pins_exact_format(self) -> None:
        """The message shape is load-bearing — operators triage from
        it, and callers pattern-match on substrings like 'timed out'.
        Pin the full shape so a refactor that moves the operation
        label or drops the 'after Ns' tail surfaces in CI, not prod."""
        proc = _HangingProc()
        with pytest.raises(
            RuntimeError,
            match=r"^my operation timed out after 0\.05s$",
        ):
            await _communicate_or_timeout(
                proc, operation="my operation", timeout=0.05,
            )

    @pytest.mark.asyncio
    async def test_timeout_message_embeds_operation_label(self) -> None:
        """Different operations produce different messages — ensures
        operator-facing labels propagate and are not swallowed by a
        generic 'git operation timed out' handler."""
        labels_under_test = [
            "git revert",
            "git commit phase (add)",
            "git archive (rm)",
            "git clean",
        ]
        for label in labels_under_test:
            proc = _HangingProc()
            with pytest.raises(RuntimeError) as exc_info:
                await _communicate_or_timeout(
                    proc, operation=label, timeout=0.05,
                )
            assert label in str(exc_info.value), (
                f"operation label {label!r} not found in error "
                f"message: {exc_info.value!s}"
            )

    @pytest.mark.asyncio
    async def test_timeout_chained_exception_suppressed(self) -> None:
        """``raise ... from None`` suppresses the underlying
        ``TimeoutError`` chain so callers see a clean ``RuntimeError``
        without asyncio internals in the traceback.  Pin it: if a
        refactor drops ``from None``, downstream callers that rely on
        the RuntimeError being terminal may start seeing unexpected
        ``__cause__`` chains."""
        proc = _HangingProc()
        with pytest.raises(RuntimeError) as exc_info:
            await _communicate_or_timeout(
                proc, operation="test", timeout=0.05,
            )
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True


# ---------------------------------------------------------------------------
# _GIT_SUBPROCESS_TIMEOUT — constant value pin
# ---------------------------------------------------------------------------


class TestGitSubprocessTimeoutConstant:
    """Pin the timeout value is within a sensible operating band."""

    def test_timeout_is_positive(self) -> None:
        assert _GIT_SUBPROCESS_TIMEOUT > 0

    def test_timeout_is_sensible_for_git_operations(self) -> None:
        """Under 5 seconds: real git operations (pre-commit hooks,
        large ``git clean -fd``, credential helper fetch on cold
        keychain) can legitimately take that long, and would trip the
        guard on every run.
        Over 300 seconds: the coordinator's cycle loop would stall
        for >5 minutes on a hung git, which is longer than the SDK's
        own watchdog interval.  Either bound says the constant has
        drifted out of its useful range — reconsider the design."""
        assert 5 <= _GIT_SUBPROCESS_TIMEOUT <= 300, (
            f"_GIT_SUBPROCESS_TIMEOUT={_GIT_SUBPROCESS_TIMEOUT} is "
            f"outside the sensible [5, 300] band for git operations"
        )


# ---------------------------------------------------------------------------
# Structural invariant: no raw communicate() calls outside the helper
# ---------------------------------------------------------------------------


class _CommunicateCallFinder(ast.NodeVisitor):
    """Walk a parsed module and collect every ``await <expr>.communicate()``
    call, tagging each with the enclosing function name.  The helper
    function itself is allowed to call ``proc.communicate()``; every
    other function must delegate through the helper."""

    HELPER_NAME = "_communicate_or_timeout"

    def __init__(self) -> None:
        self._func_stack: list[str] = []
        self.raw_calls: list[tuple[str, int]] = []

    def _enter(self, node: ast.AST) -> None:
        self._func_stack.append(getattr(node, "name", "<unknown>"))

    def _leave(self) -> None:
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)
        self.generic_visit(node)
        self._leave()

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef,
    ) -> None:
        self._enter(node)
        self.generic_visit(node)
        self._leave()

    def visit_Await(self, node: ast.Await) -> None:
        if isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "communicate"
            ):
                enclosing = (
                    self._func_stack[-1] if self._func_stack else "<module>"
                )
                if enclosing != self.HELPER_NAME:
                    self.raw_calls.append((enclosing, node.lineno))
        self.generic_visit(node)


def test_all_communicate_calls_in_recovery_git_go_through_helper() -> None:
    """Structural invariant: every ``await <proc>.communicate()`` in
    :mod:`clou.recovery_git` must be inside ``_communicate_or_timeout``.
    All other callers must delegate through the helper so that the
    timeout guard applies uniformly.

    The legacy tests cover exactly ONE of the 8 production call sites
    (the first subprocess in each of ``git_revert_golden_context`` and
    ``git_commit_phase``).  The other 6 sites — including every call
    in ``archive_milestone_episodic`` — are structurally correct today
    but would silently regress if a future edit reintroduced a raw
    ``await proc.communicate()``.  This test catches that class of
    regression without requiring per-site behavioural coverage.
    """
    src_path = (
        Path(__file__).resolve().parent.parent
        / "clou" / "recovery_git.py"
    )
    assert src_path.is_file(), f"source file missing: {src_path}"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    finder = _CommunicateCallFinder()
    finder.visit(tree)

    assert not finder.raw_calls, (
        "Raw `await <proc>.communicate()` calls found in "
        f"clou/recovery_git.py outside `_communicate_or_timeout`. "
        "All subprocess communications must route through the helper "
        "so the 30s timeout guard applies uniformly. Violations:\n"
        + "\n".join(
            f"  - {enclosing} (line {lineno})"
            for enclosing, lineno in finder.raw_calls
        )
    )


def test_structural_scanner_catches_planted_violation() -> None:
    """Meta-test: if the AST scanner in this module regresses, the
    invariant above gives false confidence.  Synthesize a tiny module
    with a planted raw communicate() outside the helper and verify
    the scanner flags it."""
    fake_module = """
import asyncio

async def _communicate_or_timeout(proc, *, operation, timeout=30):
    return await proc.communicate()  # allowed: inside the helper

async def bad_function(proc):
    return await proc.communicate()  # VIOLATION: outside the helper

async def another_bad_one(proc):
    data = await proc.communicate()  # VIOLATION: outside the helper
    return data
"""
    tree = ast.parse(fake_module)
    finder = _CommunicateCallFinder()
    finder.visit(tree)
    names = {enclosing for enclosing, _ in finder.raw_calls}
    assert names == {"bad_function", "another_bad_one"}, (
        f"scanner regressed — expected to find 2 violations in "
        f"bad_function + another_bad_one, got {finder.raw_calls!r}"
    )
