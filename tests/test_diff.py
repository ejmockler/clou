"""Tests for diff rendering and the /diff command."""

from __future__ import annotations

import pytest

from clou.ui.diff import (
    compute_edit_stats,
    compute_multi_edit_stats,
    render_diff,
    render_inline_diff,
)


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


class TestComputeEditStats:
    def test_pure_addition(self) -> None:
        adds, rems = compute_edit_stats("a\n", "a\nb\n")
        assert adds == 1
        assert rems == 0

    def test_pure_removal(self) -> None:
        adds, rems = compute_edit_stats("a\nb\n", "a\n")
        assert adds == 0
        assert rems == 1

    def test_replacement(self) -> None:
        adds, rems = compute_edit_stats("old\n", "new\n")
        assert adds == 1
        assert rems == 1

    def test_no_change(self) -> None:
        adds, rems = compute_edit_stats("same\n", "same\n")
        assert adds == 0
        assert rems == 0

    def test_empty_to_content(self) -> None:
        adds, rems = compute_edit_stats("", "line1\nline2\n")
        assert adds == 2
        assert rems == 0

    def test_content_to_empty(self) -> None:
        adds, rems = compute_edit_stats("line1\nline2\n", "")
        assert adds == 0
        assert rems == 2

    def test_multi_line_replace(self) -> None:
        adds, rems = compute_edit_stats("a\nb\nc\n", "a\nx\ny\nz\nc\n")
        assert adds == 3
        assert rems == 1


class TestComputeMultiEditStats:
    def test_sums_across_edits(self) -> None:
        edits = [
            {"old_string": "a\n", "new_string": "b\nc\n"},
            {"old_string": "x\ny\n", "new_string": "z\n"},
        ]
        adds, rems = compute_multi_edit_stats(edits)
        assert adds == 3  # 2 + 1
        assert rems == 3  # 1 + 2

    def test_empty_list(self) -> None:
        adds, rems = compute_multi_edit_stats([])
        assert adds == 0
        assert rems == 0


class TestRenderInlineDiff:
    def test_shows_additions_and_removals(self) -> None:
        result = render_inline_diff("old_line\n", "new_line\n")
        text = result.plain
        assert "- old_line" in text
        assert "+ new_line" in text

    def test_empty_strings(self) -> None:
        result = render_inline_diff("", "")
        assert result.plain == ""

    def test_pure_addition(self) -> None:
        result = render_inline_diff("", "added\n")
        assert "+ added" in result.plain
        assert "- " not in result.plain

    def test_pure_removal(self) -> None:
        result = render_inline_diff("removed\n", "")
        assert "- removed" in result.plain
        assert "+ " not in result.plain


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
