# Intent: receipts

<!-- A pedagogical reliability-gate tool. One receipt image in, structured JSON
     out from a vision model, two arithmetic checks in plain code. Reports
     problems; never auto-corrects. -->

## Goal
A command-line tool that takes one receipt image, asks a vision LLM for structured JSON (merchant, date, items with quantity and unit price, subtotal, discount, tax, total), then checks in deterministic Python code that (1) the items add up to the subtotal, and (2) subtotal − discount + tax = total. The tool **reports** discrepancies clearly; it never edits or "fixes" the data.

## Who it is for
Me (CPSC 415 student) and the course's graders. Today the workflow is: look at a receipt image, look at a model's raw output, eyeball the arithmetic. This tool composes vision-LLM extraction with plain-code arithmetic verification so the eyeballing becomes a typed check that prints the answer.

## Constraints
- **Language:** Python 3.10+. Standard library for all non-test code (`json`, `base64`, `urllib`/`http.client`, `math`, `dataclasses`); `pytest` for tests. Java was rejected because parsing JSON there requires an extra library (Jackson) and build-tool setup that adds friction disproportionate to the size of this tool.
- **Model:** OpenRouter, called directly over HTTPS (no SDK). Two class models are mandated and must be compared in `spec.md`:
  - `minimax/minimax-m3` — primary; requires `reasoning: enabled` to read faded dates.
  - `xiaomi/mimo-v2.6-flash` — second; requires `reasoning: disabled` or it exhausts its output budget.
  A `CHAT_REASONING` env var (or per-model lookup table) makes the reasoning flag configurable.
- **Budget:** Pick the cheapest model that passes the eval. Per-call cost is sub-cent on a few-hundred-token output; a full sweep of the five test receipts stays under a few cents; semester ceiling $5.
- **Offline:** When `OPENROUTER_API_KEY` is unset, model-dependent tests are skipped via `@pytest.mark.skipif`. The suite must still run cleanly offline, verifying the checker logic, the arithmetic, the tolerance rules, and the JSON parser using hand-written fixtures (not images).

## Not in scope
- Auto-correcting any discrepancy — the tool reports, it does not fix.
- GUI, web UI, or any persistent storage. CLI text output only.
- Batch mode in the CLI. One image per invocation; the pytest suite is what sweeps.
- Multi-model runs in a single invocation. One model per call, selected by env var.
- Currencies other than USD and non-US-style total layouts (e.g., VAT-inclusive prices). The arithmetic assumes US-style subtotal / discount / tax / total.
- A general-purpose vision-LLM client. This tool is shaped around receipts, not arbitrary images.

## Success looks like
- `python receipts.py images/receipt-01.png` prints a single `PASS` line followed by a clean summary block (Merchant, Date, Items Count, Total). On any discrepancy it prints `FAIL` and spells out *which* check failed, the expected vs calculated values, and the exact delta.
- `pytest` passes against all five receipts in `images/` using `images/expected.json` as the ground-truth answer key. Model-dependent tests skip without `OPENROUTER_API_KEY`; the pure-code tests (arithmetic, parser, tolerance, report formatting) always run.
- Arithmetic checks are **strict by default**, with a ±$0.01 tolerance allowed only for the documented quantity-rounding case (e.g., `3 × $4.99 = $14.97` against a stated subtotal of `$15.00`). Any delta beyond ±$0.01 is reported with the exact difference.
- The CLI never crashes on a bad model response: JSON parse failures, missing required fields, and refusal-shaped outputs all surface as a structured `FAIL` with a specific reason.

## Open questions
- The exact exit-code behavior of the CLI on `FAIL` (non-zero exit for CI vs. zero with a structured report). Resolves in `spec.md`.
- Whether `my-receipts/` (mentioned for the test sweep) lives next to `images/` from day one or is added once the layout is designed. Resolves when tests are written.
- Which of the two class models actually passes on `images/` at lower cost. Resolves empirically in `spec.md`.
- Whether the per-model reasoning table lives in code or is documented only in `spec.md`. Resolves in `spec.md`.

**Approved by:** Javier Romo, October 23, 2026
