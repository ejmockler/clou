"""UserGate — tool-to-user synchronization primitive.

Bridges the SDK's tool model (call → result) and the TUI's input model
(user types → queue → delivery).  Any MCP tool that needs to pause and
wait for user input opens a gate, awaits the response, and returns it
as the tool result.

Usage inside an MCP tool handler::

    gate.open()
    answer = await gate.wait()
    return {"content": [{"type": "text", "text": answer}]}

The input feeder checks the gate before routing to ``supervisor.query``::

    if gate.is_open:
        gate.respond(text)
    else:
        await supervisor.query(text)
"""

from __future__ import annotations

import asyncio


class UserGate:
    """One-shot synchronization: tool opens, user responds, tool completes.

    Backed by :class:`asyncio.Future` — single use per open/respond cycle.
    Thread-safe within a single event loop.
    """

    def __init__(self) -> None:
        self._future: asyncio.Future[str] | None = None

    @property
    def is_open(self) -> bool:
        """True while a tool is waiting for the user's response."""
        return self._future is not None and not self._future.done()

    def open(self) -> None:
        """Signal that a tool is waiting for user input.

        If a previous gate is still open, it is cancelled (the tool
        receives ``asyncio.CancelledError``).  This prevents zombie
        waits if the model calls the tool twice without a response.
        """
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = asyncio.get_running_loop().create_future()

    async def wait(self) -> str:
        """Block until the user responds.  Returns the response text."""
        if self._future is None:
            raise RuntimeError("UserGate.wait() called before open()")
        return await self._future

    def respond(self, text: str) -> None:
        """Deliver the user's response to the waiting tool.

        No-op if the gate is not open (e.g. tool already timed out or
        was cancelled).
        """
        if self._future is not None and not self._future.done():
            self._future.set_result(text)
