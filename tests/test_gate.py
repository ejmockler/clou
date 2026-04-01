"""Tests for clou.gate — UserGate synchronization primitive."""

from __future__ import annotations

import asyncio

import pytest

from clou.gate import UserGate


@pytest.fixture
def gate() -> UserGate:
    return UserGate()


class TestUserGate:
    def test_initially_closed(self, gate: UserGate) -> None:
        assert not gate.is_open

    @pytest.mark.asyncio
    async def test_open_sets_is_open(self, gate: UserGate) -> None:
        gate.open()
        assert gate.is_open

    @pytest.mark.asyncio
    async def test_open_respond_wait_cycle(self, gate: UserGate) -> None:
        gate.open()
        gate.respond("hello")
        result = await gate.wait()
        assert result == "hello"
        assert not gate.is_open

    @pytest.mark.asyncio
    async def test_respond_before_wait(self, gate: UserGate) -> None:
        """respond() can be called before await — future resolves immediately."""
        gate.open()
        gate.respond("early")
        result = await gate.wait()
        assert result == "early"

    @pytest.mark.asyncio
    async def test_wait_then_respond(self, gate: UserGate) -> None:
        """wait() blocks until respond() delivers."""
        gate.open()
        answer: str | None = None

        async def waiter() -> None:
            nonlocal answer
            answer = await gate.wait()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)  # let waiter start
        assert answer is None
        gate.respond("late")
        await task
        assert answer == "late"

    def test_respond_when_closed_is_noop(self, gate: UserGate) -> None:
        """respond() on a closed gate does nothing — no error."""
        gate.respond("ignored")  # should not raise

    @pytest.mark.asyncio
    async def test_respond_after_done_is_noop(self, gate: UserGate) -> None:
        """respond() after the future is already resolved is a no-op."""
        gate.open()
        gate.respond("first")
        gate.respond("second")  # should not raise

    @pytest.mark.asyncio
    async def test_double_open_cancels_previous(self, gate: UserGate) -> None:
        """Opening twice cancels the first future."""
        gate.open()
        first_future = gate._future

        gate.open()
        assert first_future is not None
        assert first_future.cancelled()

        gate.respond("second")
        result = await gate.wait()
        assert result == "second"

    @pytest.mark.asyncio
    async def test_wait_without_open_raises(self, gate: UserGate) -> None:
        with pytest.raises(RuntimeError, match="before open"):
            await gate.wait()


class TestUserGateChoices:
    """Tests for the question and choices parameters on UserGate."""

    @pytest.mark.asyncio
    async def test_open_with_question_and_choices(self, gate: UserGate) -> None:
        gate.open(question="Pick one", choices=["A", "B"])
        assert gate.question == "Pick one"
        assert gate.choices == ["A", "B"]

    @pytest.mark.asyncio
    async def test_open_with_choices_stores_them(self, gate: UserGate) -> None:
        gate.open(choices=["A", "B"])
        assert gate.choices == ["A", "B"]

    @pytest.mark.asyncio
    async def test_open_without_choices_returns_none(self, gate: UserGate) -> None:
        gate.open()
        assert gate.question is None
        assert gate.choices is None

    @pytest.mark.asyncio
    async def test_respond_clears_question_and_choices(self, gate: UserGate) -> None:
        gate.open(question="Q?", choices=["A", "B"])
        gate.respond("A")
        assert gate.question is None
        assert gate.choices is None

    def test_choices_none_when_closed(self, gate: UserGate) -> None:
        """choices returns None when the gate has never been opened."""
        assert gate.choices is None

    @pytest.mark.asyncio
    async def test_choices_none_after_close(self, gate: UserGate) -> None:
        """choices returns None after the gate closes (future done)."""
        gate.open(choices=["X"])
        gate.respond("X")
        # Future is now done → gate.is_open is False
        assert gate.choices is None

    @pytest.mark.asyncio
    async def test_double_open_replaces_choices(self, gate: UserGate) -> None:
        """Re-opening the gate replaces the previous choices."""
        gate.open(choices=["A"])
        gate.open(choices=["B", "C"])
        assert gate.choices == ["B", "C"]

    @pytest.mark.asyncio
    async def test_double_open_clears_choices(self, gate: UserGate) -> None:
        """Re-opening without choices clears previous choices."""
        gate.open(choices=["A"])
        gate.open()
        assert gate.choices is None
