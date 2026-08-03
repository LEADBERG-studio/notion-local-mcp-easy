"""Batch, patch and replace helpers (2.4.4).

The patch tool is the risky one: a half-applied diff leaves a file in a state
nobody described, so the all-or-nothing rule is tested from several angles.
"""

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bulk_tools import (  # noqa: E402
    PatchError,
    apply_unified_diff,
    first_change_preview,
    parse_unified_diff,
    render_batch,
    replace_in_text,
    tail_lines,
)

FILE = "alpha\nbravo\ncharlie\ndelta\n"


class ParseTests(unittest.TestCase):
    def test_hunks_are_parsed(self):
        hunks = parse_unified_diff("@@ -1,2 +1,2 @@\n alpha\n-bravo\n+BRAVO\n")
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].old_lines, ["alpha", "bravo"])
        self.assertEqual(hunks[0].new_lines, ["alpha", "BRAVO"])

    def test_file_headers_are_ignored(self):
        diff = "--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-alpha\n+ALPHA\n"
        self.assertEqual(len(parse_unified_diff(diff)), 1)

    def test_a_diff_without_hunks_is_refused(self):
        with self.assertRaises(PatchError):
            parse_unified_diff("just some text")

    def test_no_newline_marker_is_tolerated(self):
        hunks = parse_unified_diff("@@ -1,1 +1,1 @@\n-alpha\n+ALPHA\n\\ No newline at end of file\n")
        self.assertEqual(hunks[0].new_lines, ["ALPHA"])


class ApplyTests(unittest.TestCase):
    def test_a_simple_replacement(self):
        result, hunks = apply_unified_diff(FILE, "@@ -1,3 +1,3 @@\n alpha\n-bravo\n+BRAVO\n charlie\n")
        self.assertEqual(result, "alpha\nBRAVO\ncharlie\ndelta\n")
        self.assertEqual(hunks, 1)

    def test_stale_line_numbers_still_apply(self):
        """Models produce slightly stale numbers; the content is what matters."""
        result, _ = apply_unified_diff(FILE, "@@ -87,3 +87,3 @@\n alpha\n-bravo\n+BRAVO\n charlie\n")
        self.assertIn("BRAVO", result)

    def test_wrong_context_is_refused_and_nothing_changes(self):
        with self.assertRaises(PatchError):
            apply_unified_diff(FILE, "@@ -1,2 +1,2 @@\n nothing\n-like this\n+x\n")

    def test_several_hunks_apply_together(self):
        diff = (
            "@@ -1,2 +1,2 @@\n-alpha\n+ALPHA\n bravo\n"
            "@@ -3,2 +3,2 @@\n charlie\n-delta\n+DELTA\n"
        )
        result, hunks = apply_unified_diff(FILE, diff)
        self.assertEqual(hunks, 2)
        self.assertEqual(result, "ALPHA\nbravo\ncharlie\nDELTA\n")

    def test_one_bad_hunk_rejects_the_whole_patch(self):
        diff = (
            "@@ -1,2 +1,2 @@\n-alpha\n+ALPHA\n bravo\n"
            "@@ -3,2 +3,2 @@\n missing\n-context\n+x\n"
        )
        with self.assertRaises(PatchError):
            apply_unified_diff(FILE, diff)

    def test_insertion_only(self):
        result, _ = apply_unified_diff(FILE, "@@ -2,1 +2,2 @@\n bravo\n+inserted\n")
        self.assertEqual(result, "alpha\nbravo\ninserted\ncharlie\ndelta\n")

    def test_deletion_only(self):
        result, _ = apply_unified_diff(FILE, "@@ -2,1 +2,0 @@\n-bravo\n")
        self.assertEqual(result, "alpha\ncharlie\ndelta\n")

    def test_crlf_files_keep_their_line_endings(self):
        crlf = "alpha\r\nbravo\r\n"
        result, _ = apply_unified_diff(crlf, "@@ -1,2 +1,2 @@\n alpha\n-bravo\n+BRAVO\n")
        self.assertEqual(result, "alpha\r\nBRAVO\r\n")

    def test_a_file_without_a_trailing_newline_stays_that_way(self):
        result, _ = apply_unified_diff("alpha\nbravo", "@@ -1,2 +1,2 @@\n alpha\n-bravo\n+BRAVO\n")
        self.assertEqual(result, "alpha\nBRAVO")


class ReplaceTests(unittest.TestCase):
    def test_plain_replacement_counts(self):
        updated, count = replace_in_text("a b a", "a", "x")
        self.assertEqual((updated, count), ("x b x", 2))

    def test_no_match_leaves_content_alone(self):
        updated, count = replace_in_text("abc", "zzz", "x")
        self.assertEqual((updated, count), ("abc", 0))

    def test_regex_mode(self):
        updated, count = replace_in_text("v1 v2", r"v(\d)", r"version\1", regex=True)
        self.assertEqual((updated, count), ("version1 version2", 2))

    def test_a_broken_regex_is_reported(self):
        with self.assertRaises(ValueError):
            replace_in_text("x", "(unclosed", "y", regex=True)

    def test_case_insensitive(self):
        updated, count = replace_in_text("Alpha alpha", "alpha", "beta", ignore_case=True)
        self.assertEqual(count, 2)
        self.assertEqual(updated, "beta beta")

    def test_an_empty_search_is_refused(self):
        with self.assertRaises(ValueError):
            replace_in_text("x", "", "y")


class PreviewTests(unittest.TestCase):
    def test_preview_points_at_the_first_change(self):
        preview = first_change_preview("a\nb\nc\n", "a\nB\nc\n")
        self.assertIn("line 2", preview)
        self.assertIn("->", preview)

    def test_identical_text_has_no_preview(self):
        self.assertEqual(first_change_preview("a\n", "a\n"), "")

    def test_line_count_change_is_reported(self):
        self.assertIn("line count", first_change_preview("a\n", "a\nb\n"))


class TailTests(unittest.TestCase):
    def test_tail_returns_the_end_and_the_total(self):
        lines, total = tail_lines("\n".join(str(index) for index in range(100)), 5)
        self.assertEqual(total, 100)
        self.assertEqual(lines, ["95", "96", "97", "98", "99"])

    def test_a_short_file_is_returned_whole(self):
        lines, total = tail_lines("only\n", 50)
        self.assertEqual((lines, total), (["only"], 1))

    def test_a_zero_limit_is_clamped(self):
        lines, _ = tail_lines("a\nb\n", 0)
        self.assertEqual(len(lines), 1)


class BatchTests(unittest.TestCase):
    def test_files_are_labelled(self):
        rendered = render_batch([("a.txt", "one"), ("b.txt", "two")], per_file_chars=100, total_chars=1000)
        self.assertIn("===== a.txt =====", rendered)
        self.assertIn("two", rendered)

    def test_a_long_file_is_truncated_per_file(self):
        rendered = render_batch([("big.txt", "x" * 500)], per_file_chars=50, total_chars=1000)
        self.assertIn("truncated", rendered)
        self.assertLess(len(rendered), 300)

    def test_the_budget_is_shared_not_multiplied(self):
        """Batching must not multiply the per-file limit by the file count."""
        entries = [(f"f{index}.txt", "y" * 400) for index in range(20)]
        rendered = render_batch(entries, per_file_chars=400, total_chars=1000)
        self.assertLess(len(rendered), 2000)
        self.assertIn("not shown", rendered)

    def test_nothing_to_render_is_explicit(self):
        self.assertEqual(render_batch([], per_file_chars=10, total_chars=10), "(no files matched)")


if __name__ == "__main__":
    unittest.main()
