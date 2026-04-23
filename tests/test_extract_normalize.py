"""Tests for the extract-text post-processing pipeline.

Covers the pure-logic `_normalize_extracted` helper: dedup behavior,
blank-line handling, and size cap. End-to-end integration with the
/extract-text route is verified manually against real PPTX/PDF samples
(see tools/validate_extract_normalize.py).
"""
from __future__ import annotations


class TestNormalizeExtracted:
    def _fn(self):
        from app.api.ai import _normalize_extracted
        return _normalize_extracted

    # ── Dedup behavior ──────────────────────────────────────────────

    def test_exact_duplicate_lines_dropped(self):
        fn = self._fn()
        raw = "line A\nfooter repeating\nline B\nfooter repeating\nline C\nfooter repeating"
        out = fn(raw, max_chars=1000)
        assert out.count("footer repeating") == 1
        # All unique content preserved
        assert "line A" in out and "line B" in out and "line C" in out

    def test_first_occurrence_preserves_order(self):
        fn = self._fn()
        raw = "A\nB\nA\nC\nA\nD"
        out = fn(raw, max_chars=1000)
        # First A preserved, order ABCD
        assert out == "A\nB\nC\nD"

    def test_whitespace_only_difference_still_deduped(self):
        """`footer` and `  footer  ` are the same content — key is stripped."""
        fn = self._fn()
        raw = "footer\n  footer  \nfooter\t"
        out = fn(raw, max_chars=1000)
        assert out.count("footer") == 1

    def test_case_difference_not_deduped(self):
        """PPT slides sometimes capitalize/lowercase the same phrase
        for design reasons — these are legitimately different tokens."""
        fn = self._fn()
        raw = "Feature\nfeature\nFEATURE"
        out = fn(raw, max_chars=1000)
        # All three variants kept (case-sensitive key)
        assert "Feature" in out and "feature" in out and "FEATURE" in out

    # ── Blank-line handling ─────────────────────────────────────────

    def test_blank_lines_preserved_for_structure(self):
        """Section boundaries are load-bearing for the LLM — keep one
        blank line between groups."""
        fn = self._fn()
        raw = "title\n\nparagraph 1\n\nparagraph 2"
        out = fn(raw, max_chars=1000)
        assert out == "title\n\nparagraph 1\n\nparagraph 2"

    def test_runs_of_three_or_more_blanks_collapsed(self):
        fn = self._fn()
        raw = "A\n\n\n\n\nB"
        out = fn(raw, max_chars=1000)
        assert out == "A\n\nB"

    def test_leading_trailing_whitespace_stripped(self):
        fn = self._fn()
        assert self._fn()("\n\n  hello  \n\n", max_chars=100) == "hello"

    # ── PPT-style real-world pattern ────────────────────────────────

    def test_ppt_style_master_footer_repeats(self):
        """Simulates a 5-slide deck where every slide has the same
        footer and page number. After normalization only one copy of
        each master element should remain."""
        fn = self._fn()
        parts = []
        for i in range(1, 6):
            parts.append(f"[Slide {i}]")
            parts.append(f"Slide {i} title: unique content")
            parts.append(f"Slide {i} body: details here")
            parts.append("© Acme Corp 2026")       # master footer
            parts.append("Confidential — do not share")  # master disclaimer
            parts.append("www.acme.com")           # master URL
        raw = "\n".join(parts)
        out = fn(raw, max_chars=10000)
        # Master text appears exactly once
        assert out.count("© Acme Corp 2026") == 1
        assert out.count("Confidential — do not share") == 1
        assert out.count("www.acme.com") == 1
        # Every slide's unique content preserved
        for i in range(1, 6):
            assert f"[Slide {i}]" in out
            assert f"Slide {i} title" in out
            assert f"Slide {i} body" in out

    # ── Size cap ────────────────────────────────────────────────────

    def test_cap_applies_strictly(self):
        fn = self._fn()
        raw = "X" * 100000
        out = fn(raw, max_chars=50000)
        assert len(out) <= 50000

    def test_cap_does_not_truncate_under_limit(self):
        fn = self._fn()
        raw = "hello\nworld"
        out = fn(raw, max_chars=1000)
        assert out == "hello\nworld"

    def test_empty_input(self):
        fn = self._fn()
        assert fn("", max_chars=100) == ""
        assert fn("   \n\n   ", max_chars=100) == ""

    def test_single_line_no_op(self):
        fn = self._fn()
        assert fn("the only line", max_chars=100) == "the only line"

    # ── Regression: original `[Slide N]` markers are all unique ─────
    # Without this guard, a naive dedup would strip them if they ever
    # collided. [Slide 1], [Slide 2], ... each differ by number so
    # this is just a positive sanity check.

    def test_slide_markers_all_preserved(self):
        fn = self._fn()
        raw = "\n".join(f"[Slide {i}]" for i in range(1, 31))
        out = fn(raw, max_chars=10000)
        for i in range(1, 31):
            assert f"[Slide {i}]" in out
