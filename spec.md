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

## Week 6: image set

Implements `intent/image-set.md`. The Week 6 lab generates five educational science diagrams from a single prompt family, critiques each with a vision model against a hand-coded answer key, and surfaces every disagreement. The split mirrors Week 5's reader/checker split: the **Generator** only talks to the image model; the **Critic-checker** only talks to the vision model and runs the deterministic comparison. Nothing in this section overrides or relaxes any Week 5 rule.

### Components

#### Generator
- **What it does:** Reads `week06/prompts.json` (an array of `{id, prompt, seed, answer_key}` entries), calls the OpenRouter Image API for each entry at the configured seed through the `openrouter` SDK, saves the resulting PNG, and writes `images/manifest.json` with one record per image carrying `prompt`, `seed`, `model`, `media_type`, and `cost_usd` (per-image cost from the API response when available, otherwise `null`). The `--model` flag selects the image model. **One generation per prompt** — never loops the model inside a test.
- **Language:** Python 3.10+. **Why:** the comparison and report logic is dict-shape and string-equality, which Python's standard library handles cleanly with `json`, `dataclasses`, and `set` operations. The vision call itself can ride the same `openrouter` SDK as the generator, so Week 6 stays on a single HTTP layer (chat-completions is a first-class endpoint in the same SDK). Java is not chosen because the lab is small, the manifest/report shapes are JSON-only, and adding Jackson plus a build tool for ~150 lines of tool code is disproportionate friction.
- **Model:** Two class-mandated image models, selected by `--model`:
  - `black-forest-labs/flux.2-klein-4b` — **default**, used for the five primary prompts. ~$0.014 per image. Fixed seed per prompt, so re-running with the same `(prompt, seed)` produces a visually identical diagram even if raw PNG bytes shift slightly.
  - `google/gemini-3.1-flash-lite-image` — **comparison** model, run for **one** prompt only as a single-prompt sanity / cost check. The manifest records which `id` was used and the per-image cost so the comparison cost is auditable.
  The `--model` flag is required (no silent default to a different model). The script does not embed a fallback chain.
- **Interfaces:**
  - Input: `--model <slug>`, `--out <directory>`, `<prompts.json>` (positional, required). Missing API key ⇒ exit 1 with a `no_api_key` message; never crash.
  - Output:
    - `--out/images/<id>.png` for each entry.
    - `--out/images/manifest.json` with shape `[{"id", "prompt", "seed", "model", "image_path", "media_type", "cost_usd"}, ...]`. `image_path` is relative to `--out`; the other fields match the user's mandated list (prompt, seed, model, media type, cost).
    - If `--out` already contains an `images/` directory or a `manifest.json`, the script **aborts** instead of overwriting (mirrors intent's `--out` rule).
  - Env: `OPENROUTER_API_KEY`.
- **Dependencies:**
  - `openrouter` (PyPI) — the OpenRouter Python SDK. **Reason:** the Image endpoint returns generated media plus per-call cost in a typed response object (URL or base64 + usage); re-implementing that with `urllib` would be ~80 lines of stdlib parsing for what the SDK already wraps. Imported once at the top of `week06/generate.py`.
  - `json` (stdlib) — read `prompts.json`; serialize the manifest.
  - `os` (stdlib) — env lookup (`OPENROUTER_API_KEY`); path joins; `--out` overwrite guard.
  - `argparse` (stdlib) — `--model`, `--out`.
  - `dataclasses` (stdlib) — typed manifest rows.

#### Critic-checker
- **What it does:** Loads `--out/images/manifest.json` (or a `prompts.json` directly), sends each image and its prompt-specific checklist to the vision model selected by `--model`, parses the model's yes/no JSON, and compares every key in the parsed reply against the hand-coded `answer_key` in `prompts.json` **in deterministic Python code**. For each image it prints a single `PASS` / `FAIL` line followed by a per-part table (rows = part names; columns = `expected`, `critiqued`, `match`). It also writes a per-critic report JSON so the two critics can be diffed in code. **The critic is not trusted; the code is the oracle.**
- **Language:** Python 3.10+. **Why:** the comparison is pure dict-shape checking plus string equality — every Behavior below (§17–§23) is expressible with `dict` lookups, `set` operations, and `==`. The vision call itself uses the same `openrouter` SDK as the generator (chat-completions endpoint, OpenAI-compatible), keeping both scripts on one HTTP layer. Java would force Jackson plus a build tool for the same JSON-only work.
- **Model:** Two class-mandated vision models, selected by `--model`:
  - `minimax/minimax-m3` — **primary critic / default**. Reads the diagram and answers the checklist (one yes/no per required part).
  - `xiaomi/mimo-v2.6-flash` — **comparison critic**, run on the same five FLUX images so the code can flag inter-critic disagreement. **Not the ground truth** — its job is to surface places where the primary critic may have been confidently wrong; the hand-coded `answer_key` remains the only oracle.
  Both critics must produce the same JSON shape (Behavior §16) so the per-critic reports are diff-able. The `--model` flag selects which critic runs; there is no embedded fallback chain.
- **Interfaces:**
  - Input: `--model <slug>`, `--out <report.json>`, `<manifest-or-prompts>` (positional). When the input is `prompts.json` the script reads `answer_key` from it directly; when it is `manifest.json` it pairs each entry with the matching `answer_key` by `id`.
  - Output:
    - Per image, on stdout: one `PASS` or `FAIL` line followed by a per-part table whose rows are the parts from `answer_key` plus any invented parts (Behavior §19). On `PASS`, a one-line summary; on `FAIL`, the table.
    - On disk: `--out` (a JSON file) with shape `[{"id", "model", "verdict", "rows": [{"part", "expected", "critiqued", "match"}], "critique_raw"?}, ...]`. The `critique_raw` field, when present, captures the model's literal reply for debugging and is never consulted by the comparison code. Aborts instead of overwriting an existing `--out`.
  - Env: `OPENROUTER_API_KEY`. Missing key ⇒ exit 1 with a `no_api_key` message; never crash.
- **Dependencies:**
  - `openrouter` (PyPI) — same SDK as the generator; chat-completions endpoint. **Reason:** keeps both Week 6 scripts on one HTTP layer and one dependency; chat-completions support in the SDK is first-class (typed response with `choices[0].message.content`).
  - `json` (stdlib) — parse the model's reply; serialize the per-critic report.
  - `os` (stdlib) — env lookup; read image bytes.
  - `argparse` (stdlib) — `--model`, `--out`.
  - `dataclasses` (stdlib) — typed rows for the per-part table and the report JSON.

### Behavior

Numbered requirements the pytest suite checks. Numbering continues from the Week 5 list (which ends at §15).

16. **Schema — critique reply.** A successful critique returns a JSON object whose keys are the part names from that prompt's `answer_key` and whose values are booleans (the critic's yes/no for each part). The reply must be valid JSON and must be an object at the top level. Extra keys are tolerated by the parser but flagged by the checker (Behavior §19). A single optional markdown fence (` ```json … ``` `) around the reply is stripped before parsing.

17. **FAIL — critique not JSON.** A reply that is not valid JSON (after stripping a single optional markdown fence) is a **failed critique**, not a crash. The critic-checker records `verdict = "FAIL"`, attaches `reason = "not_json"` and a `<200-char excerpt>` of the model's literal text, prints `FAIL — could not parse critique: not JSON (<excerpt>)`, and exits 1 (or returns the same structured error to the calling context). The program never raises an uncaught exception on bad model output.

18. **FAIL — un-answered requirement.** A requirement from `answer_key` that the critic **did not answer** (the key is missing from the parsed reply) counts as a problem. The per-part table records `expected = <from key>`, `critiqued = (none)`, `match = ✗`, and the image's overall `verdict = "FAIL"`. A single un-answered requirement fails the image.

19. **FAIL — invented requirement.** A key that appears in the critique reply but **not** in the prompt's `answer_key` counts as a problem — the critic invented a requirement. The table appends a row `part = <invented key>, expected = (not required), critiqued = <yes|no>, match = ✗ (invented)`, and the image's overall `verdict = "FAIL"`. The set of "required" parts is determined **entirely** from `answer_key` in code, never from the critic.

20. **Expected-text comparison is in code, not by the critic.** For every part that **is** in `answer_key` **and** is in the critique reply, the comparison is `(critiqued == expected)` evaluated in Python — strict equality on booleans, character-for-character equality on any string-valued entry. There is **no fuzzy matching, no normalization, and no LLM-as-judge**: the critic's job is to emit yes/no (or text); the Python code's job is to compare. A mismatch is a `✗` row.

21. **Per-image PASS/FAIL.** An image passes **iff** every row in its table has `match = ✓`. Otherwise it fails. `PASS` images print `PASS` on a single line followed by a one-line summary (`id`, `model`, parts-passed / parts-total); `FAIL` images print `FAIL` followed by the per-part table.

22. **Per-part table.** Columns are `part`, `expected`, `critiqued`, `match`. Rows include every required part from `answer_key`, every invented part from the critique (§19), and every un-answered part (§18). The table is the same on stdout and in the JSON report.

23. **Inter-critic comparison.** When two per-critic report JSONs are loaded (e.g., the primary `minimax/minimax-m3` report and the secondary `xiaomi/mimo-v2.6-flash` report on the same five FLUX images), the code flags every row where the two critics' `critiqued` values differ on the same `part` as **"critic uncertainty."** This is reported **alongside** — not instead of — the hand-coded key check (§§17–§20). The code does not require the critics to agree with each other; it requires every critic to match `answer_key`. A disagreement between critics never overrides a disagreement with the key.

24. **CLI exit codes.** The critic-checker exits **0** on every-image-pass and exits **1** on any `FAIL` (per §17/§18/§19/§20), any unrecoverable parse error, or a refused write to `--out`. The generator exits **0** on a complete manifest write and exits **1** on any API error, missing key, or refused write to `--out`.

25. **Offline tests.** Model-dependent calls are skipped without `OPENROUTER_API_KEY` via the same `@pytest.mark.skipif(not os.getenv("OPENROUTER_API_KEY"), reason="model eval")` pattern as Week 5. Pure-code behaviors (§§16–§23 above, plus manifest/report JSON shape, the `--out` overwrite guard, and inter-critic disagreement detection) are exercised by `week06/tests/` against hand-written fixtures — no API calls. The fixture for the critique reply, in particular, lives as a JSON file (e.g., `tests/fixtures/critique-plant-cell.json`) so the checker is tested without ever touching a model.

### Cost estimate (Week 6)

Per sweep (the full matrix from intent §Constraints):
- 5 × `flux.2-klein-4b` image generations @ ≈ $0.014 ≈ **$0.07**.
- 1 × `gemini-3.1-flash-lite-image` image generation @ ≈ $0.02 (estimate; recorded in `manifest.json` `cost_usd` for auditability).
- 5 × `minimax/minimax-m3` critiques @ ≈ $0.002 ≈ **$0.01**.
- 5 × `xiaomi/mimo-v2.6-flash` critiques @ ≈ $0.0004 ≈ **$0.002**.

Total per sweep ≈ **$0.10** — well under the **$1.00 per-run** ceiling from intent. Semester ceiling unchanged at **$5** (Week 5 already well under; Week 6 fits comfortably inside the same ceiling across dozens of sweeps).

### Out of scope (Week 6)

Carried verbatim from `intent/image-set.md`, restated here for the spec record:
- Auto-correcting any disagreement — the tool reports, it does not fix.
- Generating images in any style other than the fixed flat-color 2D textbook look.
- Concepts outside the educational science-diagram family.
- More than five primary prompts or more than one comparison-image run.
- More than one critique pass per image per critic. The comparison-critic pass is the only second pass, and only over the five FLUX images.
- A general-purpose image-generation or vision client.
- Branch / PR workflow in the personal repository (commit to `main`).
- Web UI, GUI, persistent storage. CLI text output plus written report files.

Added by the design (mirrors Week 5 §Out of scope):
- Retry-on-failure. A failed critique is reported as-is; the user re-runs the script.
- Streaming responses. The reader waits for the full reply and parses once.
- Caching model responses. Every CLI run is a fresh call.
- PDF / non-PNG-or-JPEG inputs.
- Looping the model inside a test. (Reinforced by intent: "Never loop over the model inside a test.")
