"""The Checker component: parse a critic's reply, compare against answer_key.

Pure Python — no model, no network. Maps every spec failure mode
(Spec §16, §17, §18, §19, §20, §21, §22) to a structured result:

- §16 — a successful parse returns a dict whose keys are part names and
  whose values are booleans (or strings, for expected-text cases).
- §17 — a non-JSON reply returns a structured `ParseResult` with
  `failed=True, reason="not_json", excerpt=<first 200 chars>`. Never a
  crash. Single optional ```json ... ``` fence is stripped first.
- §18 — a required part the critic did not answer → ✗ row.
- §19 — a part the critic invented (in critique but not in answer_key)
  → ✗ (invented) row.
- §20 — value comparison is strict equality in Python: bool vs bool,
  string vs string character-for-character. `True` ≠ `"true"` ≠ `"yes"`,
  `"nucleus"` ≠ `"Nucleus"` ≠ `"nucleeus"`. No fuzzy matching, no LLM-
  as-judge.
- §21 — per-image PASS iff every row is ✓; otherwise FAIL.
- §22 — the per-part table includes required + un-answered + invented
  rows, all with the same shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# ============================================================================
# JSON extraction (Spec §16, §17)
# ============================================================================

@dataclass(frozen=True)
class ParseResult:
    """Result of `parse_critique`. Either ok with a parsed dict, or failed.

    `failed=False`  → `value` is the parsed dict, `reason` and `excerpt`
                     are None.
    `failed=True`   → `value` is None, `reason` names the failure
                     (currently always "not_json"), `excerpt` is the
                     first 200 chars of the model's literal reply for
                     debugging.
    """
    value: dict | None = None
    failed: bool = False
    reason: str | None = None
    excerpt: str | None = None

    @classmethod
    def ok(cls, value: dict) -> ParseResult:
        return cls(value=value, failed=False)

    @classmethod
    def fail(cls, reason: str, excerpt: str = "") -> ParseResult:
        return cls(failed=True, reason=reason, excerpt=excerpt[:200])


def _strip_fence(text: str) -> str:
    """If the model wraps JSON in ```json ... ```, return the inner content.

    Single optional fence only. Mirrors `receipts/reader.py` behavior so
    the two labs have the same parsing surface.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    if first_newline == -1:
        return text
    end = text.rfind("```")
    if end <= first_newline:
        return text
    return text[first_newline + 1:end].strip()


def parse_critique(text: str) -> ParseResult:
    """Parse the critic's reply. Returns `ParseResult.ok(dict)` or `ParseResult.fail(...)`.

    A non-empty, non-JSON, or non-object reply returns a structured
    failure — never raises. An empty string also returns a structured
    failure (Spec §17).
    """
    if not text or not text.strip():
        return ParseResult.fail("not_json", excerpt="<empty model reply>")

    stripped = _strip_fence(text)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return ParseResult.fail("not_json", excerpt=stripped)

    if not isinstance(value, dict):
        return ParseResult.fail(
            "not_json",
            excerpt=f"expected object, got {type(value).__name__}",
        )
    return ParseResult.ok(value)


# ============================================================================
# Per-part comparison (Spec §18, §19, §20)
# ============================================================================

@dataclass(frozen=True)
class CheckRow:
    """One row of the per-part table (Spec §22).

    `expected` and `critiqued` are the values being compared. `match`
    is one of:
        "✓"           — values agree (Spec §20)
        "✗"           — values disagree, or critiqued is None for a
                        required part (Spec §18 un-answered)
        "✗ (invented)"— critiqued has a key not in answer_key
                        (Spec §19 invented)
    """
    part: str
    expected: object  # bool | str | None
    critiqued: object  # bool | str | None
    match: str

    @property
    def is_ok(self) -> bool:
        return self.match == "✓"


def compare_part(
    part: str,
    expected,
    critiqued,
    *,
    invented: bool = False,
) -> CheckRow:
    """Compare one part's expected vs critiqued value in pure Python.

    Order of checks (matches the priority of the spec's failure modes):
        1. If the part was invented by the critic (not in answer_key) →
           ✗ (invented).
        2. If the critic did not answer (critiqued is None) → ✗
           (un-answered).
        3. If the types differ (e.g. bool vs str) → ✗. Spec §20 forbids
           normalization, so `True` ≠ `"true"` ≠ `"yes"`.
        4. If the values differ → ✗.
        5. Otherwise → ✓.
    """
    if invented:
        return CheckRow(
            part=part, expected=None, critiqued=critiqued, match="✗ (invented)"
        )
    if critiqued is None:
        return CheckRow(
            part=part, expected=expected, critiqued=None, match="✗"
        )
    if type(expected) is not type(critiqued):
        return CheckRow(
            part=part, expected=expected, critiqued=critiqued, match="✗"
        )
    if expected != critiqued:
        return CheckRow(
            part=part, expected=expected, critiqued=critiqued, match="✗"
        )
    return CheckRow(
        part=part, expected=expected, critiqued=critiqued, match="✓"
    )


# ============================================================================
# Report building (Spec §21, §22)
# ============================================================================

@dataclass(frozen=True)
class Report:
    """Per-image report (Spec §21, §22).

    `verdict` is "PASS" iff every row in `rows` is ✓ (Spec §21).
    `rows` covers every required part, every un-answered part, and every
    invented part (Spec §22) — the same set the CLI prints to stdout.
    """
    verdict: str  # "PASS" | "FAIL"
    rows: list[CheckRow] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return self.verdict == "PASS"


def build_report(answer_key: dict, critique: dict) -> Report:
    """Build a per-image `Report` from the hand-coded `answer_key` and the parsed critique.

    Three row sources (Spec §22):
        - Required parts in `answer_key` that the critic answered: a
          `compare_part` row.
        - Required parts in `answer_key` that the critic did not answer:
          a ✗ row with `critiqued=None` (Spec §18).
        - Parts in the critique that are not in `answer_key`: a
          ✗ (invented) row (Spec §19).
    """
    rows: list[CheckRow] = []

    # Required parts: from answer_key.
    for part, expected in answer_key.items():
        if part in critique:
            rows.append(compare_part(part, expected, critique[part]))
        else:
            rows.append(compare_part(part, expected, None))

    # Invented parts: keys in critique but not in answer_key.
    for part, critiqued in critique.items():
        if part not in answer_key:
            rows.append(compare_part(part, None, critiqued, invented=True))

    verdict = "PASS" if all(r.is_ok for r in rows) else "FAIL"
    return Report(verdict=verdict, rows=rows)


# ============================================================================
# Inter-critic comparison (Spec §23)
# ============================================================================

@dataclass(frozen=True)
class Disagreement:
    """A part where two critics gave different critiqued values (Spec §23).

    `value_a` and `value_b` are the critiqued values from each critic.
    If a part is present in one report but not the other, the missing
    side's value is `None` — the structural difference is itself a
    disagreement (one critic invented; the other un-answered, or
    coverage differed).
    """
    part: str
    value_a: object | None  # bool | str | None
    value_b: object | None  # bool | str | None


def compare_critics(report_a: Report, report_b: Report) -> list[Disagreement]:
    """Find every part where the two critics gave different critiqued values.

    Spec §23: a disagreement between critics is reported as "critic
    uncertainty" *alongside* the hand-coded key check, never *instead of*
    it. This function does not modify either report's per-image verdict;
    it returns a list of `Disagreement` rows that the CLI prints under
    the per-part table.

    The union of both reports' part sets is checked — a part present in
    only one report is a disagreement (`None` on the missing side).
    """
    parts_a = {row.part: row.critiqued for row in report_a.rows}
    parts_b = {row.part: row.critiqued for row in report_b.rows}
    all_parts = sorted(set(parts_a) | set(parts_b))

    disagreements: list[Disagreement] = []
    for part in all_parts:
        value_a = parts_a.get(part)
        value_b = parts_b.get(part)
        if value_a != value_b:
            disagreements.append(
                Disagreement(part=part, value_a=value_a, value_b=value_b)
            )

    return disagreements
