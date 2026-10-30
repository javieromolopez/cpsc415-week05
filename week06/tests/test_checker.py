"""Offline (no-API-key) tests for the Week 6 checker.

Consolidated per Step 3 of `plan.md`. Three spec-driven categories in one
file (the plan separates them into three files; for Step 3 we collapse them
into `test_checker.py` so we can prove RED → GREEN on a single pytest run):

- JSON extraction (Spec §16, §17): valid object parses; non-JSON returns a
  structured FAIL with an excerpt; markdown fence stripped; non-object
  top-level rejected; empty reply rejected — **never a crash**.
- Pure-code checker logic (Spec §18–§22): expected vs critiqued, ✓/✗,
  un-answered FAIL, invented FAIL, per-image PASS rule, per-part rows
  include required + un-answered + invented.
- Text comparison (Spec §20): character-for-character equality on strings,
  strict type equality on bools (`True` ≠ `"true"` ≠ `"yes"`), no fuzzy
  matching.

All tests use hand-written fixtures — no model calls, no API key.
"""
from __future__ import annotations

from week06.checker import (
    CheckRow,
    ParseResult,
    Report,
    build_report,
    compare_part,
    parse_critique,
)

# ============================================================================
# 1. JSON extraction (Spec §16, §17)
# ============================================================================

class TestParseCritique:
    def test_valid_object_parses(self):
        """Spec §16: a valid JSON object returns the parsed dict."""
        result = parse_critique('{"nucleus": true, "cell wall": true}')
        assert isinstance(result, ParseResult)
        assert result.failed is False
        assert result.value == {"nucleus": True, "cell wall": True}

    def test_not_json_returns_structured_fail(self):
        """Spec §17: non-JSON reply is a structured FAIL, never a crash."""
        result = parse_critique("this is not json at all")
        assert result.failed is True
        assert result.reason == "not_json"
        assert result.excerpt  # non-empty excerpt
        assert result.value is None

    def test_fenced_json_strips_fence(self):
        """Spec §16: a single optional ```json ... ``` fence is stripped."""
        fenced = '```json\n{"nucleus": true, "chloroplast": true}\n```'
        result = parse_critique(fenced)
        assert result.failed is False
        assert result.value == {"nucleus": True, "chloroplast": True}

    def test_non_object_top_level_rejected(self):
        """Spec §16: top-level must be a JSON object."""
        result = parse_critique("[1, 2, 3]")
        assert result.failed is True
        assert result.reason == "not_json"
        # Excerpt names what was actually found so the human can debug.
        excerpt = (result.excerpt or "").lower()
        assert "list" in excerpt or "object" in excerpt

    def test_top_level_string_rejected(self):
        """Spec §16: a top-level string is rejected as not_json."""
        result = parse_critique('"just a string"')
        assert result.failed is True
        assert result.reason == "not_json"

    def test_empty_string_rejected(self):
        """Spec §17: empty model content is a structured FAIL, not a crash."""
        result = parse_critique("")
        assert result.failed is True
        assert result.reason == "not_json"


# ============================================================================
# 2. Per-part comparison (Spec §18, §19, §20)
# ============================================================================

class TestComparePart:
    def test_match_true(self):
        """Spec §20: expected True == critiqued True → ✓."""
        row = compare_part("nucleus", expected=True, critiqued=True)
        assert isinstance(row, CheckRow)
        assert row.part == "nucleus"
        assert row.expected is True
        assert row.critiqued is True
        assert row.match == "✓"

    def test_mismatch_true_vs_false(self):
        """Spec §20: expected True != critiqued False → ✗."""
        row = compare_part("nucleus", expected=True, critiqued=False)
        assert row.match == "✗"
        assert row.expected is True
        assert row.critiqued is False

    def test_un_answered_required(self):
        """Spec §18: critiqued is None → ✗ (un-answered)."""
        row = compare_part("nucleus", expected=True, critiqued=None)
        assert row.match == "✗"
        assert row.expected is True
        assert row.critiqued is None

    def test_invented_part(self):
        """Spec §19: a part the critic invented → ✗ (invented)."""
        row = compare_part("chloroplast", expected=None, critiqued=True, invented=True)
        assert row.match == "✗ (invented)"
        assert row.expected is None
        assert row.critiqued is True


# ============================================================================
# 3. Report building (Spec §21, §22)
# ============================================================================

class TestBuildReport:
    def test_per_image_pass_when_all_match(self):
        """Spec §21: image passes iff every row is ✓."""
        report = build_report(
            answer_key={"nucleus": True, "cell wall": True},
            critique={"nucleus": True, "cell wall": True},
        )
        assert isinstance(report, Report)
        assert report.verdict == "PASS"

    def test_per_image_fail_on_un_answered(self):
        """Spec §18: a single un-answered requirement fails the image."""
        report = build_report(
            answer_key={"nucleus": True, "cell wall": True},
            critique={"nucleus": True},  # cell wall un-answered
        )
        assert report.verdict == "FAIL"

    def test_per_image_fail_on_invented(self):
        """Spec §19: a single invented requirement fails the image."""
        report = build_report(
            answer_key={"nucleus": True},
            critique={"nucleus": True, "chloroplast": True},  # chloroplast invented
        )
        assert report.verdict == "FAIL"

    def test_per_image_fail_on_mismatch(self):
        """Spec §20: a value mismatch fails the image."""
        report = build_report(
            answer_key={"nucleus": True},
            critique={"nucleus": False},
        )
        assert report.verdict == "FAIL"

    def test_rows_include_required_unanswered_and_invented(self):
        """Spec §22: rows include required + un-answered + invented."""
        report = build_report(
            answer_key={"nucleus": True, "cell wall": True},
            critique={"cell wall": True, "chloroplast": True},
            # nucleus un-answered, cell wall matched, chloroplast invented
        )
        parts = [row.part for row in report.rows]
        assert "nucleus" in parts
        assert "cell wall" in parts
        assert "chloroplast" in parts
        assert len(report.rows) == 3


# ============================================================================
# 4. Text comparison (Spec §20)
# ============================================================================

class TestTextCompare:
    def test_string_character_for_character_match(self):
        """Spec §20: string-valued entries compared character-for-character."""
        row = compare_part("label", expected="nucleus", critiqued="nucleus")
        assert row.match == "✓"

    def test_string_case_sensitive_mismatch(self):
        """Spec §20: 'nucleus' != 'Nucleus' (case-sensitive)."""
        row = compare_part("label", expected="nucleus", critiqued="Nucleus")
        assert row.match == "✗"

    def test_string_no_fuzzy_match(self):
        """Spec §20: no fuzzy match. 'nucleus' != 'nucleeus'."""
        row = compare_part("label", expected="nucleus", critiqued="nucleeus")
        assert row.match == "✗"

    def test_bool_strict_type_mismatch(self):
        """Spec §20: True != 'true' (strict type equality, no normalization)."""
        row = compare_part("present", expected=True, critiqued="true")
        assert row.match == "✗"

    def test_bool_yes_string_not_equal_true(self):
        """Spec §20: True != 'yes'."""
        row = compare_part("present", expected=True, critiqued="yes")
        assert row.match == "✗"
