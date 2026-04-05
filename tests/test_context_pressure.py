"""Tests for context pressure modulation — the session's embodied memory.

Covers the cross-cutting concern of supervisor context pressure:
breath period modulation, status bar glyph, and app handler propagation.
"""

from __future__ import annotations

import pytest

from clou.ui.app import ClouApp, _breath_period_for_pressure
from clou.ui.messages import ClouContextPressure
from clou.ui.widgets.status_bar import ClouStatusBar


class TestBreathPeriodForPressure:
    """Breath period deepens as supervisor memory accumulates."""

    def test_none_is_baseline(self) -> None:
        assert _breath_period_for_pressure("none") == 4.5

    def test_warn_is_subliminally_deeper(self) -> None:
        """~11% increase — at the threshold of conscious perception."""
        period = _breath_period_for_pressure("warn")
        assert period == 5.0
        assert period > 4.5

    def test_compact_is_noticeable(self) -> None:
        """~22% increase — noticeable deepening."""
        period = _breath_period_for_pressure("compact")
        assert period == 5.5
        assert period > _breath_period_for_pressure("warn")

    def test_block_is_heavy(self) -> None:
        """~33% increase — clearly labored breathing."""
        period = _breath_period_for_pressure("block")
        assert period == 6.0
        assert period > _breath_period_for_pressure("compact")

    def test_monotonic_ordering(self) -> None:
        """Deeper pressure → longer period, always."""
        levels = ("none", "warn", "compact", "block")
        periods = [_breath_period_for_pressure(lv) for lv in levels]
        assert periods == sorted(periods)

    def test_unknown_falls_back_to_baseline(self) -> None:
        """Safety: unexpected pressure level doesn't break breathing."""
        assert _breath_period_for_pressure("invalid") == 4.5
        assert _breath_period_for_pressure("") == 4.5


class TestContextPressureHandler:
    """ClouApp handler propagates pressure to reactive + status bar."""

    @pytest.mark.asyncio
    async def test_handler_updates_app_reactive(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.context_pressure == "none"

            app.post_message(ClouContextPressure(
                level="warn", estimate=161_000, threshold=160_000,
            ))
            await pilot.pause()

            assert app.context_pressure == "warn"

    @pytest.mark.asyncio
    async def test_handler_propagates_to_status_bar(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            bar = app.query_one(ClouStatusBar)
            assert bar.context_pressure == "none"

            app.post_message(ClouContextPressure(
                level="compact", estimate=168_000, threshold=167_000,
            ))
            await pilot.pause()

            assert bar.context_pressure == "compact"

    @pytest.mark.asyncio
    async def test_handler_transitions_through_levels(self) -> None:
        """Pressure can move through the full range, including back to none."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            bar = app.query_one(ClouStatusBar)

            for level in ("warn", "compact", "block", "none"):
                app.post_message(ClouContextPressure(
                    level=level, estimate=160_000, threshold=160_000,
                ))
                await pilot.pause()
                assert app.context_pressure == level
                assert bar.context_pressure == level

    @pytest.mark.asyncio
    async def test_reactive_is_initially_none(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert app.context_pressure == "none"


class TestPressureCssClasses:
    """CSS class management for the dialogue aura — warm edge at pressure."""

    @pytest.mark.asyncio
    async def test_none_has_no_pressure_class(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            assert not app.has_class("pressure-warn")
            assert not app.has_class("pressure-compact")
            assert not app.has_class("pressure-block")

    @pytest.mark.asyncio
    async def test_warn_adds_amber_class(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouContextPressure(
                level="warn", estimate=161_000, threshold=160_000,
            ))
            await pilot.pause()
            assert app.has_class("pressure-warn")
            assert not app.has_class("pressure-compact")
            assert not app.has_class("pressure-block")

    @pytest.mark.asyncio
    async def test_compact_adds_gold_class(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouContextPressure(
                level="compact", estimate=168_000, threshold=167_000,
            ))
            await pilot.pause()
            assert app.has_class("pressure-compact")
            assert not app.has_class("pressure-warn")
            assert not app.has_class("pressure-block")

    @pytest.mark.asyncio
    async def test_block_adds_rose_class(self) -> None:
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouContextPressure(
                level="block", estimate=178_000, threshold=177_000,
            ))
            await pilot.pause()
            assert app.has_class("pressure-block")
            assert not app.has_class("pressure-warn")
            assert not app.has_class("pressure-compact")

    @pytest.mark.asyncio
    async def test_transition_warn_to_compact_swaps_class(self) -> None:
        """Moving from WARN to COMPACT removes amber, adds gold."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouContextPressure(
                level="warn", estimate=161_000, threshold=160_000,
            ))
            await pilot.pause()
            assert app.has_class("pressure-warn")

            app.post_message(ClouContextPressure(
                level="compact", estimate=168_000, threshold=167_000,
            ))
            await pilot.pause()
            assert not app.has_class("pressure-warn")
            assert app.has_class("pressure-compact")

    @pytest.mark.asyncio
    async def test_transition_back_to_none_clears_class(self) -> None:
        """Post-compaction: pressure returns to NONE, all classes clear."""
        async with ClouApp().run_test() as pilot:
            app: ClouApp = pilot.app  # type: ignore[assignment]
            app.post_message(ClouContextPressure(
                level="compact", estimate=168_000, threshold=167_000,
            ))
            await pilot.pause()
            assert app.has_class("pressure-compact")

            app.post_message(ClouContextPressure(
                level="none", estimate=50_000, threshold=160_000,
            ))
            await pilot.pause()
            assert not app.has_class("pressure-warn")
            assert not app.has_class("pressure-compact")
            assert not app.has_class("pressure-block")


class TestContextPressureMessage:
    """ClouContextPressure message carries level + estimate + threshold."""

    def test_construction(self) -> None:
        msg = ClouContextPressure(
            level="warn", estimate=161_234, threshold=160_000,
        )
        assert msg.level == "warn"
        assert msg.estimate == 161_234
        assert msg.threshold == 160_000

    def test_all_levels_constructible(self) -> None:
        for level in ("none", "warn", "compact", "block"):
            msg = ClouContextPressure(
                level=level, estimate=100_000, threshold=160_000,
            )
            assert msg.level == level
