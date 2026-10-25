# Spec

## Intent
Implements `intent/receipts.md`.

## Components

### Reader
- **What it does:** Encodes a receipt image as a base64 data URL, POSTs to the OpenRouter chat-completions endpoint with a vision prompt, and parses the model's reply into a typed `Receipt` (or a typed `ReadError`). The only component that talks to the network or to a model.
- **Language:** Python 3.10+. **Why:** the entire reader — base64, JSON, HTTPS POST, env-var lookup — is expressible in Python's standard library, which the intent requires. Java would require Jackson for JSON plus a third-party HTTP client; the build friction outweighs the benefit for one endpoint.
- **Model:** Two class-mandated models compared:
  - `minimax/minimax-m3` — primary/default model; requires `reasoning: {"enabled": true}` (or thinking enabled) to read faded dates (intent, Constraints).
  - `xiaomi/mimo-v2.6-flash` — secondary comparison model; requires `reasoning: {"enabled": false}` to avoid exhausting output tokens.
  **Comparison and default.** `minimax/minimax-m3` is the default: `CHAT_MODEL` env var, falling back to `minimax/minimax-m3`. `xiaomi/mimo-v2.6-flash` is the fallback when the default fails any of the five `images/` receipts in the eval. Both must produce the same JSON shape (Behavior §1). The actual pass/fail comparison is re-run at the eval phase of `plan.md`; whichever passes the five-receipt sweep at lower cost wins.
  **Reasoning toggle.** A small per-model lookup table maps model slug → reasoning flag (`minimax/minimax-m3` → enabled, `xiaomi/mimo-v2.6-flash` → disabled). The `CHAT_REASONING` env var can flip the table to disabled (it suppresses reasoning on `minimax` for cost; it cannot enable reasoning on `xiaomi`, which would break that model). Resolves intent open question #4: the table lives in code as a constant; this spec documents its contents.
- **Interfaces:**
  - Input: image file path (string, PNG or JPEG).
  - Output: either a `Receipt(merchant, date, items, subtotal, discount, tax, total)` or a `ReadError(reason, detail?)`. Reasons: `no_api_key`, `network`, `http`, `empty`, `not_json`, `missing_field`, `refusal`.
  - Files: reads `OPENROUTER_API_KEY`, `CHAT_MODEL`, `CHAT_REASONING` from env. Writes nothing.
  - Endpoints: `POST https://openrouter.ai/api/v1/chat/completions`.
- **Dependencies:**
  - `base64` (stdlib) — image-bytes to data URL.
  - `urllib.request` (stdlib) — HTTPS POST without an SDK.
  - `json` (stdlib) — parse the model reply.
  - `os` (stdlib) — env-var lookup.
  - `dataclasses` (stdlib) — `Receipt`, `Item`, `ReadError` shapes.
  - `typing` (stdlib) — type hints only.

### Checker
- **What it does:** Takes a parsed `Receipt`, runs the two arithmetic checks (items-sum vs subtotal; subtotal − discount + tax vs total) and the field-presence checks (merchant, items, subtotal, total required; discount, tax optional), and produces a `Report` of `CheckResult` rows. No network, no model.
- **Language:** Python 3.10+. **Why:** float arithmetic and dict-shape checks are trivial in Python. The intent's stdlib-only rule covers this — `math.isclose` handles the ±$0.01 tolerance and `dataclasses` handles the report rows. `numpy` is unnecessary for two sums and a subtraction.
- **Model:** none. Deterministic Python only.
- **Interfaces:**
  - Input: a `Receipt` from the reader, or a hand-written JSON fixture (offline tests).
  - Output: a `Report` — list of `CheckResult(check_name, status, expected?, calculated?, delta?)` where `status ∈ {PASS, FAIL, SKIPPED}`.
- **Dependencies:**
  - `math` (stdlib) — `math.isclose` for ±$0.01 tolerance.
  - `dataclasses` (stdlib) — `CheckResult`, `Report` shapes.

## Behavior

Numbered requirements the pytest suite checks. Each maps to one or more test cases.

1. **Schema.** A successful read returns a `Receipt` with: `merchant: str`, `date: str | None` (ISO `YYYY-MM-DD` or `None`), `items: list[Item]` where each `Item` has `name: str`, `quantity: int ≥ 1`, `unit_price: float`, `subtotal: float`, `discount: float` (default `0.0`), `tax: float` (default `0.0`), `total: float`.

2. **CLI.** `python receipts.py <image_path>` runs one image, prints a single report block, exits 0 on `PASS` and exit 1 on `FAIL`. (Resolves intent open question #1.)

3. **PASS report.** A single `PASS` line followed by a clean summary block:
   - `Merchant: <merchant>`
   - `Date: YYYY-MM-DD` (or `Date: (none)` when the model returned `null`)
   - `Items: <count>`
   - `Total: $<total>`

4. **FAIL report — arithmetic.** A `FAIL` line followed by, for each failing check:
   - The check name (`Items sum mismatch` or `Total mismatch`).
   - The calculated value (what the receipt's own numbers produce).
   - The stated value (what the model extracted).
   - The exact delta to two decimals.

   A receipt whose numbers do not add up is reported with the discrepancy spelled out (check name, calculated, stated, delta). Example: `FAIL — Total mismatch: calculated $13.69 vs stated $13.66, delta $0.03`.

5. **FAIL report — missing field.** `FAIL — missing required field: <name>` for any absent `merchant`, `items`, `subtotal`, or `total`. Arithmetic checks that depend on the missing field are listed as `SKIPPED` with the dependency name. Optional fields (`discount`, `tax`) missing or `null` default to `0.0` and do not trigger FAIL.

6. **FAIL report — parse failure.** A reply that is not JSON is a failed read, not a crash. The reader returns `ReadError(reason="not_json", detail=<first 200 chars>)`; the CLI prints `FAIL — could not parse model response: not JSON (<excerpt>)` and exits 1. The program never raises an uncaught exception on bad model output.

7. **FAIL report — model refusal.** A reply whose `choices[0].message.content` is empty or starts with a refusal marker (`"I can't"`, `"I'm sorry"`, etc.) is a `ReadError(reason="refusal")`. The CLI prints `FAIL — model refused (<excerpt>)` and exits 1.

8. **FAIL report — network/HTTP.** A non-200 status, a timeout, or a connection error is a `ReadError(reason="network" | "http", detail=<status or message>)`. The CLI prints `FAIL — <kind> error: <detail>` and exits 1.

9. **FAIL report — missing API key.** A missing `OPENROUTER_API_KEY` is a `ReadError(reason="no_api_key")`. The CLI prints `FAIL — OPENROUTER_API_KEY not set` and exits 1. The pytest layer treats this as the skip condition for `@pytest.mark.eval` tests.

10. **Items-sum check.** `sum(item.quantity * item.unit_price for item in items)` is compared to `subtotal`. Strict by default (`math.isclose` with `abs_tol=0.0`); ±$0.01 tolerance is permitted **only** when the discrepancy is explained by per-line rounding (`n × x ≠ round(n*x, 2)` for some item), in which case `abs_tol=0.01` is used. Deltas above `0.01` are reported with the exact difference to the cent.

11. **Total check.** `subtotal - discount + tax` is compared to `total`. Strict by default; same ±$0.01 tolerance rule as §10.

12. **Date-as-null rule.** The date is `null` when the year is not printed. The reader's prompt instructs the model to return `date: null` whenever the receipt does not show a year (matches `images/receipt-05.png`). The reader accepts `null` and the CLI prints `Date: (none)`. The reader does not attempt to guess or default the year.

13. **Offline tests.** The pytest suite runs cleanly without `OPENROUTER_API_KEY`. Model-required tests are decorated `@pytest.mark.skipif(not os.getenv("OPENROUTER_API_KEY"), reason="model eval")`. Pure-code tests (parser, arithmetic, tolerance, report formatting) use hand-written JSON fixtures and always run.

14. **Reasoning toggle.** The reader consults the per-model reasoning table (Reader, Model). `CHAT_REASONING=off` flips the table value to disabled where doing so does not break the model.

15. **Single-image CLI.** The CLI takes exactly one path. No glob, no `--all`, no directory sweep — that is pytest's job. (Defers intent open question #2 to `plan.md`.)

## Failure handling

| Failure | Reader returns | CLI prints | Exit |
|---|---|---|---|
| `OPENROUTER_API_KEY` unset | `ReadError("no_api_key")` | `FAIL — OPENROUTER_API_KEY not set` | 1 |
| Network error / timeout | `ReadError("network", detail)` | `FAIL — network error: <detail>` | 1 |
| Non-200 HTTP status | `ReadError("http", status, body[:200])` | `FAIL — HTTP <status>: <excerpt>` | 1 |
| Empty `choices[0].message.content` | `ReadError("empty")` | `FAIL — empty model response` | 1 |
| Response is not valid JSON | `ReadError("not_json", excerpt[:200])` | `FAIL — could not parse model response: not JSON (<excerpt>)` | 1 |
| JSON missing `merchant` / `items` / `subtotal` / `total` | `ReadError("missing_field", name)` | `FAIL — missing required field: <name>` | 1 |
| Model refusal / safety block | `ReadError("refusal", excerpt[:200])` | `FAIL — model refused: <excerpt>` | 1 |
| Items-sum delta > $0.01 | (checker) `CheckResult(FAIL, "Items sum mismatch", …)` | `FAIL — Items sum mismatch: calculated $X.XX vs stated $Y.YY, delta $Z.ZZ` | 1 |
| Total delta > $0.01 | (checker) `CheckResult(FAIL, "Total mismatch", …)` | `FAIL — Total mismatch: …` | 1 |
| All checks pass | (checker) all `CheckResult(PASS, …)` | `PASS` + summary block | 0 |

The program never raises an uncaught exception for any row above. Every error path is structured.

## Cost estimate

Per call: ~700–800 output tokens (the model emits only JSON, no prose). Image-input tokens are small (single receipt at typical resolution).

Per sweep (5 receipts):
- `xiaomi/mimo-v2.6-flash` at roughly $0.10 / M output tokens: ≈ $0.0004.
- `minimax/minimax-m3` at roughly $0.50 / M output tokens: ≈ $0.002.

Per semester (≈ 50 sweeps during dev + grading): `xiaomi` ≈ $0.02, `minimax` ≈ $0.10. Both well under the $5 ceiling.

Default to `minimax/minimax-m3` (class default). In the eval step of `plan.md`, compare both models across the five receipts to observe differences in token usage and pass rates, and confirm whether `xiaomi/mimo-v2.6-flash` passes at lower cost with reasoning disabled.

## Out of scope

Carried from the intent:
- Auto-correcting any discrepancy.
- GUI, web UI, persistent storage.
- Batch mode in the CLI.
- Multi-model runs in a single invocation.
- Non-USD currencies and non-US-style total layouts (e.g., VAT-inclusive prices).
- A general-purpose vision-LLM client.

Added by the design:
- Retry-on-failure. A failed read is reported as-is; the user re-runs the CLI.
- Streaming responses. The reader waits for the full reply and parses once.
- Caching model responses. Every CLI run is a fresh call.
- Custom prompts or schemas. The JSON shape is hard-coded; per-receipt prompts are not supported.
- PDF / non-PNG-or-JPEG inputs.
