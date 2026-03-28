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
