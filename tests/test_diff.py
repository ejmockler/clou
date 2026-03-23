"""Tests for diff rendering and the /diff command."""

from __future__ import annotations

import pytest

from clou.ui.diff import render_diff


_SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index abc123..def456 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 import os
+import sys

-old_line = True
+new_line = False
"""


class TestRenderDiff:
    def test_empty_input(self) -> None:
        result = render_diff("")
        assert str(result) == ""

    def test_added_lines_present(self) -> None:
        result = render_diff(_SAMPLE_DIFF)
        text = str(result)
        assert "+import sys" in text
        assert "+new_line = False" in text

    def test_removed_lines_present(self) -> None:
        result = render_diff(_SAMPLE_DIFF)
        text = str(result)
        assert "-old_line = True" in text

    def test_file_header_present(self) -> None:
        result = render_diff(_SAMPLE_DIFF)
        text = str(result)
        assert "--- a/foo.py" in text
        assert "+++ b/foo.py" in text

    def test_hunk_header_present(self) -> None:
        result = render_diff(_SAMPLE_DIFF)
        text = str(result)
        assert "@@" in text


class TestDiffCommand:
    @pytest.mark.asyncio
    async def test_diff_registered(self) -> None:
        from clou.ui.commands import get

        assert get("diff") is not None

    @pytest.mark.asyncio
    async def test_diff_no_changes_in_non_git(self, tmp_path, monkeypatch) -> None:
        """In a non-git directory, /diff shows an error."""
        from clou.ui.app import ClouApp
        from clou.ui.commands import dispatch
        from clou.ui.widgets.conversation import ConversationWidget

        monkeypatch.chdir(tmp_path)
        async with ClouApp().run_test() as pilot:
            await dispatch(pilot.app, "/diff")
            conv = pilot.app.query_one(ConversationWidget)
            all_text = "".join(
                str(w.render()) for w in conv.query(".msg")
            )
            assert "not a git repository" in all_text or "no changes" in all_text
