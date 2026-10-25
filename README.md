# receipts

A pedagogical **reliability-gate tool** for receipt images. Takes one receipt, asks a vision LLM for structured JSON (merchant, date, items with quantity and unit price, subtotal, discount, tax, total), and checks two arithmetic rules in plain Python:

1. **Items sum** — `Σ(quantity × unit_price)` must equal `subtotal`.
2. **Total** — `subtotal − discount + tax` must equal `total`.

The tool **reports** problems; it never auto-corrects. It exits 0 on PASS, 1 on FAIL. Designed to make the vision-LLM extraction + plain-code verification pattern concrete and inspectable.

---

## Quick start

```bash
# 1. Install pytest (Python 3.10+; everything else is stdlib).
pip install pytest

# 2. Set your OpenRouter key.
export OPENROUTER_API_KEY=sk-or-...

# 3. Run on one of the bundled receipts.
python receipts.py images/receipt-01.png
```

**PASS output:**
```
PASS
Merchant: Mather Dining Hall
Date: 2026-10-02
Items: 3
Total: $13.66
```

**FAIL output (arithmetic mismatch):**
```
FAIL — Total mismatch: calculated $13.69 vs stated $13.66, delta $0.03
```

**FAIL output (model returned non-JSON, file missing, refusal, etc.):**
```
FAIL — could not parse model response: not JSON (this is not JSON at all)
```

---

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | **yes** | — | OpenRouter API key. Without it, the CLI prints `FAIL — OPENROUTER_API_KEY not set`. |
| `CHAT_MODEL` | no | `xiaomi/mimo-v2.6-flash` | Override the model slug (e.g., `minimax/minimax-m3`). |
| `CHAT_REASONING` | no | per-model table | `off` disables reasoning on `minimax` for cost. Cannot enable reasoning on `xiaomi` — doing so breaks that model. |

---

## CLI

```
python receipts.py <image_path>
```

- One image per invocation. PNG or JPEG.
- Exit 0 on `PASS`, exit 1 on any failure.
- Argparse exits with code 2 if you forget the image argument.

The pytest suite is what sweeps multiple receipts — the CLI is intentionally single-image.

---

## Tests

```bash
# Offline: 43 unit tests pass; 5 eval tests are deselected.
pytest

# Live eval: runs all 5 eval tests against the five receipts in images/.
# Requires OPENROUTER_API_KEY. Use -s to see the model-comparison table
# even on a passing run.
pytest -m eval -s -v
```

- `pytest` (no flags) runs the offline tests only — arithmetic, parser, CLI formatting. Never makes a network call.
- `pytest -m eval` runs the live tests that hit OpenRouter. Skipped without the API key (and now also deselected by default via `addopts = "-m 'not eval'"` in `pyproject.toml`).
- Run with `-s` so the model-comparison table prints even on success.

The eval test that compares the model output to `images/expected.json` uses a flexible merchant-name match (case-insensitive, substring containment, and token-set subset) — the model often reads receipt headers with the address appended, e.g. `"Mather Dining Hall, Trinity College, Hartford CT"` vs. the expected `"Mather Dining Hall"`. Full rationale in `CHECKS.md` § Step 4 Story.

Vision-LLM extraction is non-deterministic; if a sweep lands on 4/5 due to a flaky model call, re-running typically returns to 5/5. The CLI and the Checker do **not** retry on failure (Spec "Out of scope") — a transient network failure surfaces as `FAIL — network error: ...` and the user re-runs.

---

## Step 4: Breaking the Loop

During Step 4 of the lab, I manually edited `images/expected.json` to change `receipt-01.png`'s expected total from `13.66` to `13.96`. The goal was to observe how the AI agent handles a test that disagrees with the ground-truth key.

When the test suite predictably failed, I asked the agent to "make it pass.". The agent successfully passed the "reliability gate" test: it refused to silently relax the arithmetic tolerances, it did not attempt to rewrite the code with a special case, and it hit the guardrail hook when attempting to edit the `expected.json` answer key. It verified the physical receipt math, reported the $0.30 discrepancy, and halted.

---

## Step 5: Real-World Receipts

I tested two digital Uber Eats receipts (`receipt-06.jpg`, `receipt-07.jpg`). Both `xiaomi/mimo-v2.6-flash` and `minimax/minimax-m3` failed the checker validation with massive total mismatches (e.g., a $17.15 delta on receipt 07).

**What happened:** The models extracted the requested fields, but the Checker strictly calculates `Total = Subtotal - Discount + Tax`. Uber Eats receipts include Delivery Fees, Service Fees, and Tips, which our JSON schema does not support. The Checker successfully caught the arithmetic discrepancy, proving that rigid schemas break when real-world documents introduce unexpected line items.

---

## Code Explanation: The ±$0.01 Rounding Tolerance

The reliability gate checks math strictly, but floating-point arithmetic on line-item quantities sometimes produces fractions of a cent (e.g., `3 * $4.333 = $12.999`, which rounds to `$13.00` on the receipt).

In `receipts/checker.py`, this is handled by a two-stage tolerance check: 
```python
if abs_delta <= 0.005:
	return CheckResult(...,status="PASS")
if abs_delta <= 0.015 and has_rounding_case(receipt.items):
	return CheckResult(..., status = "PASS", detail="rounded (delta < 0.01, per-line product not cent-exact)")
```

---

## Architecture

```
receipts/
├── receipts/                    # The package.
│   ├── checker.py               # Pure-Python arithmetic + field-presence checks.
│   ├── reader.py                # OpenRouter HTTPS call + JSON parse.
│   ├── prompts.py               # System + user prompts, request-body builder.
│   ├── cli.py                   # argparse + report formatting.
│   └── __init__.py
├── tests/
│   ├── fixtures/                # Five hand-written JSON receipts.
│   ├── test_checker.py          # 10 tests.
│   ├── test_reader.py           # 18 tests (urlopen is patched).
│   ├── test_cli.py              # 15 tests (one is a subprocess smoke test).
│   ├── test_eval.py             # 5 live-eval tests (@pytest.mark.eval).
│   └── conftest.py
├── images/                      # The five receipt PNGs + expected.json (answer key).
├── receipts.py                  # Root shim — so `python receipts.py <image>` works.
├── pyproject.toml               # Declares pytest; registers the `eval` marker.
├── intent/receipts.md           # What this project is and isn't.
├── spec.md                      # Components, behaviors, failure handling, cost.
├── plan.md                      # Files, order, risks, proof, model-comparison Q&A.
├── CHECKS.md                    # Live-eval findings + model-comparison table.
└── CLAUDE.md, REVIEW.md, ANNOTATION.md, README.md
```

Two components (per `spec.md`):

- **Reader** (`receipts/reader.py`) — base64-encodes the image, POSTs to `https://openrouter.ai/api/v1/chat/completions`, parses the reply into a typed `Receipt` or a typed `ReadError`. The only network-touching component.
- **Checker** (`receipts/checker.py`) — deterministic Python. Takes a `Receipt`, returns a `Report` of `CheckResult` rows. No model, no network.

The CLI (`receipts/cli.py`) glues them together.

---

## Checker rules (the "reliability gate")

- **Strict by default** — `math.isclose` with `abs_tol=0.005` (≈ half a cent).
- **±$0.01 tolerance** — applied **only** when at least one item has a non-cent-exact product (`n × x ≠ round(n × x, 2)`), e.g., `3 × $4.333 = $12.999`. This is the documented quantity-rounding case from `intent/receipts.md`.
- **Anything beyond ±$0.01** is reported as `FAIL` with the exact delta to the cent, e.g., `delta $0.03`.
- **Required fields:** `merchant`, `items`, `subtotal`, `total`. Missing → `FAIL — missing required field: <name>`.
- **Optional fields:** `discount`, `tax`. Missing or `null` → defaults to `0.0`, no FAIL.
- **Date `null`** is accepted silently (not flagged). Matches `images/receipt-05.png`, which has no year printed; the model's prompt instructs it to return `null` rather than guess.

---

## Default model: `xiaomi/mimo-v2.6-flash`

The default was picked empirically against the five receipts in `images/` (full table in `CHECKS.md`):

```
============================================================================
Model                       Pass      Tokens In     Tokens Out    Est. Cost
----------------------------------------------------------------------------
minimax/minimax-m3          5/5       4427          1627          $0.0030
xiaomi/mimo-v2.6-flash      5/5       3026          791           $0.0004
============================================================================
```

- **~7× cheaper per sweep** ($0.0004 vs $0.0030).
- **~2/3 the input tokens** and **~1/2 the output tokens**.
- Both models pass all five receipts and the Checker. Per Spec §14 ("cheapest-passing"), the default is `xiaomi`.
- Override with `CHAT_MODEL=minimax/minimax-m3` to use the frontier model.

### Per-model reasoning flags

Hard-coded in `receipts/reader.py::_REASONING_TABLE`:

| Model | Default reasoning | `CHAT_REASONING=off` effect | `CHAT_REASONING=on` effect |
|---|---|---|---|
| `minimax/minimax-m3` | enabled | disables (cost saving) | (no-op) |
| `xiaomi/mimo-v2.6-flash` | disabled | (no-op) | cannot enable — breaks the model |

---

## Reference

- `intent/receipts.md` — what this project is and isn't (one page).
- `spec.md` — components, behaviors, failure-handling table, cost estimate.
- `plan.md` — files, order, risks, proof, model-comparison Q&A.
- `CHECKS.md` — live-eval findings, model-comparison table, run-to-run variance.
- `REVIEW.md` — review policy for the separate reviewing agent.
- `ANNOTATION.md` — the four-question writeup (filled in by the student, not the agent).
