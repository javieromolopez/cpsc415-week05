"""Offline tests for inter-critic comparison (Spec §23).

Given two per-critic `Report` objects, `compare_critics` flags every part
where the two critics gave different critiqued values — "critic
uncertainty." A disagreement between critics is reported *alongside* the
hand-coded key check, never *instead of* it. The code does not require
the critics to agree with each other; it requires every critic to match
`answer_key` (Spec §23).

All tests use hand-written Reports built via `build_report` — no model
calls, no API key.
"""
from __future__ import annotations

from week06.checker import (
    Disagreement,
    Report,
    build_report,
    compare_critics,
)


def make_report(answer_key: dict, critique: dict) -> Report:
    """Helper: build a Report from an answer_key and a critique."""
    return build_report(answer_key, critique)


class TestCompareCritics:
    def test_no_disagreement_when_identical(self):
        """Both critics agree on every part → empty disagreement list."""
        r_a = make_report({"nucleus": True}, {"nucleus": True})
        r_b = make_report({"nucleus": True}, {"nucleus": True})
        assert compare_critics(r_a, r_b) == []

    def test_one_disagreement_on_value_mismatch(self):
        """One row differs → one Disagreement entry."""
        r_a = make_report(
            {"nucleus": True, "cell_wall": True},
            {"nucleus": True, "cell_wall": True},
        )
        r_b = make_report(
            {"nucleus": True, "cell_wall": True},
            {"nucleus": True, "cell_wall": False},
        )
        disagreements = compare_critics(r_a, r_b)
        assert len(disagreements) == 1
        d = disagreements[0]
        assert isinstance(d, Disagreement)
        assert d.part == "cell_wall"
        assert d.value_a is True
        assert d.value_b is False

    def test_multiple_disagreements(self):
        """Multiple rows differ → multiple Disagreement entries, one per part."""
        r_a = make_report(
            {"a": True, "b": True, "c": True},
            {"a": True, "b": True, "c": True},
        )
        r_b = make_report(
            {"a": True, "b": True, "c": True},
            {"a": False, "b": False, "c": True},
        )
        disagreements = compare_critics(r_a, r_b)
        assert len(disagreements) == 2
        parts = {d.part for d in disagreements}
        assert parts == {"a", "b"}

    def test_disagreement_when_part_in_a_only(self):
        """A part present in A but not in B → flagged as disagreement.

        In B's report, the missing key surfaces as a ✗ (un-answered) row
        with critiqued=None. The two reports still disagree on that part.
        """
        r_a = make_report(
            {"nucleus": True, "cell_wall": True},
            {"nucleus": True, "cell_wall": True},
        )
        r_b = make_report(
            {"nucleus": True},
            {"nucleus": True},  # cell_wall un-answered in B
        )
        disagreements = compare_critics(r_a, r_b)
        assert len(disagreements) == 1
        d = disagreements[0]
        assert d.part == "cell_wall"
        assert d.value_a is True
        assert d.value_b is None

    def test_disagreement_when_part_in_b_only(self):
        """A part present in B but not in A → flagged as disagreement."""
        r_a = make_report(
            {"nucleus": True},
            {"nucleus": True},
        )
        r_b = make_report(
            {"nucleus": True, "cell_wall": True},
            {"nucleus": True, "cell_wall": True},
        )
        disagreements = compare_critics(r_a, r_b)
        assert len(disagreements) == 1
        d = disagreements[0]
        assert d.part == "cell_wall"
        assert d.value_a is None
        assert d.value_b is True

    def test_disagreement_does_not_override_per_image_verdict(self):
        """Spec §23: a critic disagreement never overrides the per-image verdict.

        Two reports disagree on cell_wall (A: True, B: False). A passes
        the key check, B fails it. `compare_critics` flags the
        disagreement, but does NOT change either report's verdict — the
        two checks coexist.
        """
        answer_key = {"nucleus": True, "cell_wall": True}
        r_a = make_report(answer_key, {"nucleus": True, "cell_wall": True})
        r_b = make_report(answer_key, {"nucleus": True, "cell_wall": False})
        disagreements = compare_critics(r_a, r_b)
        assert len(disagreements) == 1
        # Verdicts are not modified by compare_critics.
        assert r_a.verdict == "PASS"
        assert r_b.verdict == "FAIL"

    def test_no_disagreement_when_both_critics_confidently_wrong(self):
        """Spec §23: critic-vs-critic agreement does NOT silence the key check.

        Both critics confidently say cell_wall=False. The key says True.
        `compare_critics` sees NO disagreement (they agree with each
        other). The key check still fails for both critics
        independently — `compare_critics` returning [] does not make
        either verdict PASS.
        """
        answer_key = {"nucleus": True, "cell_wall": True}
        r_a = make_report(answer_key, {"nucleus": True, "cell_wall": False})
        r_b = make_report(answer_key, {"nucleus": True, "cell_wall": False})
        assert compare_critics(r_a, r_b) == []
        # Both critics still FAIL the key check independently.
        assert r_a.verdict == "FAIL"
        assert r_b.verdict == "FAIL"
