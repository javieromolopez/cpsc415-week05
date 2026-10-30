# Plan

<!-- Drives the build and test stages. Approved before any code is written. -->

## Files to create or change

### Production code (new)
| File | Purpose |
|---|---|
| `receipts/__init__.py` | Marks `receipts` as a package; re-exports the public surface. |
| `receipts/checker.py` | The Checker component. `Receipt`, `Item`, `CheckResult`, `Report` dataclasses; `check(receipt) -> Report`. Pure Python; no model, no network. |
| `receipts/reader.py` | The Reader component. `ReadError` dataclass; `read(image_path) -> Receipt \| ReadError`. base64, env-var lookup, HTTPS POST via `urllib.request`, JSON parse, error mapping per the spec's Failure-handling table. |
| `receipts/prompts.py` | The system + user prompts sent to the model. Centralized so the date-as-null rule and the JSON schema are easy to find. |
| `receipts/cli.py` | Glue layer. Arg parser, calls Reader, runs Checker, prints the report, sets the exit code. |
| `receipts.py` | Thin shim at the project root: `from receipts.cli import main; main()` so `python receipts.py <image>` works. |

### Test code (new)
| File | Purpose |
|---|---|
| `pyproject.toml` | Declares `pytest` as the only test dependency. Non-test code stays stdlib-only per the intent. |
| `tests/conftest.py` | Shared fixtures: loads `images/expected.json`, exposes the five hand-written JSON receipts as a dict, provides a stubbed Reader for CLI tests. |
| `tests/fixtures/receipt-01.json` … `receipt-05.json` | Five hand-written JSON receipts that match `images/expected.json` (merchant, date-or-null, items with quantity and unit_price, subtotal, discount, tax, total). Some are deliberately arithmetic-tight; one or two have small mismatches to exercise FAIL paths. |
| `tests/test_checker.py` | Pure-code tests for the Checker: items-sum (strict + ±$0.01), total check, missing-field → SKIPPED. |
| `tests/test_reader.py` | Pure-code tests for the Reader: synthetic responses for non-JSON, refusal, HTTP error, missing key. `urllib.request.urlopen` is patched. |
| `tests/test_cli.py` | Tests for the report shape and exit codes. Uses `subprocess.run` for one happy-path end-to-end against a stubbed Reader; the rest invoke `cli.main()` directly with a fake argv. |
| `tests/test_eval.py` | Model-dependent tests, all `@pytest.mark.eval` and skipped without `OPENROUTER_API_KEY`. Covers the schema on real images, the null-date rule, the reasoning toggle, and a comparison run for both models. |

### Config (new)
| File | Purpose |
|---|---|
| `.env.example` | Template for `OPENROUTER_API_KEY`, `CHAT_MODEL`, `CHAT_REASONING`. No secrets — values are placeholders. |

### Files not changed
`images/`, `images/expected.json`, `intent/receipts.md`, `spec.md`, `CLAUDE.md`, `ANNOTATION.md`, `REVIEW.md`, `README.md`. `ANNOTATION.md` is filled in by the user, not the agent.

## Order of work

### Phase 1 — Production code, in dependency order
1. **`receipts/checker.py`** first. No model, no network — easiest to reason about and to test in isolation.
2. **`receipts/prompts.py`** next. The system prompt encodes the JSON schema and the date-as-null rule; getting this right unblocks the Reader.
3. **`receipts/reader.py`** after that. Depends on `prompts.py` for the request body and on `checker.py` types only for shape hints.
4. **`receipts/cli.py`** last in the package. Glues Reader + Checker.
5. **`receipts.py`** root shim so `python receipts.py <image>` runs.

### Phase 2 — Tests, in dependency order
6. **`pyproject.toml`** with `pytest` as the only declared dep.
7. **`tests/fixtures/receipt-0[1-5].json`** — five hand-written JSON files. These are also the proof artifact for the offline suite.
8. **`tests/conftest.py`** — `expected`, `hand_written_receipts`, `stub_reader`.
9. **`tests/test_checker.py`** — uses only fixtures; runs first when something breaks.
10. **`tests/test_reader.py`** — patches `urllib.request.urlopen`; no real network.
11. **`tests/test_cli.py`** — `cli.main()` invoked with a stubbed Reader plus one subprocess smoke test.
12. **`tests/test_eval.py`** — model-dependent, marked and skipped without a key.

### Phase 3 — Run, verify, decide
13. `pytest` with no API key set. Every offline test passes; every eval test skips cleanly with the documented reason.
14. `pytest -m eval` with `OPENROUTER_API_KEY` and `CHAT_MODEL=minimax/minimax-m3` (the class default). Record pass count and per-call tokens from OpenRouter's response.
15. `pytest -m eval` again with `CHAT_MODEL=xiaomi/mimo-v2.6-flash` and `CHAT_REASONING=off` (forced off for the comparison). Record the same numbers.
16. Compare. If `xiaomi` passes all five receipts at lower cost, switch the default to `xiaomi/mimo-v2.6-flash` and update `ANNOTATION.md` and `spec.md`'s Cost-estimate note. If it fails any receipt, keep `minimax` as default and note why in `ANNOTATION.md`.

## Risks

| Risk | Assumption | Blast radius if wrong |
|---|---|---|
| Model wraps JSON in markdown fences (`` ```json … ``` ``) | The Reader strips fences before parsing | Every receipt returns `not_json`; eval is unusable |
| Float arithmetic on dollar amounts | `math.isclose` plus the per-line-rounding tolerance covers real receipts | False `FAIL` on clean receipts; the tolerance rule fires too aggressively |
| OpenRouter rate-limits during eval | Sweep is small (5 receipts × 2 models = 10 calls) | Latency, not correctness; can rerun |
| `CHAT_REASONING=off` on `minimax` reduces accuracy | The class default keeps reasoning on for `minimax` | Reduced pass rate; eval surfaces it |
| Receipt-05 (CS Club Bake Sale) — model guesses a year | Spec §12 says the prompt must instruct `date: null` when no year is printed | The model returns a wrong date; the eval catches it and we tighten the prompt |
| OpenRouter timeout on a slow call | Default timeout is 30 s | One receipt fails the eval; we re-run |
| Image-input tokens higher than estimated for large receipts | Spec assumes small receipts | Per-call cost grows; still under the $5 ceiling |
| Subprocess tests (`tests/test_cli.py`) behave differently on Windows vs Unix | `python receipts.py` resolves the same in both | Edge case in path quoting — Windows-specific assertion if needed |
| We forget to mark an eval test with `@pytest.mark.eval` | Conftest + a strict `addopts = "-m eval"` not used; skip is per-test | A model-dependent test runs offline and either errors or skips for the wrong reason; the offline run is no longer a clean green |
| A hand-written fixture has a typo that the test does not catch | Fixtures are themselves validated against `images/expected.json` in `conftest.py` | Silent drift between fixture and expected; conftest validation closes this |

## Proof

### Offline (no `OPENROUTER_API_KEY` — must always pass)

| Command | Proves |
|---|---|
| `pytest tests/test_checker.py` | Spec §10 (items-sum, strict and ±$0.01 for the quantity-rounding case), §11 (total check), §5 (missing-field → `SKIPPED`, optional-field defaults to 0). Pure deterministic Python — proves the arithmetic and the report shape are correct independent of any model. |
| `pytest tests/test_reader.py` | Spec §6 (`not_json` → `ReadError`, never a crash), §7 (refusal-marker detection), §8 (`http` / `network` mapping), §9 (`no_api_key` → `ReadError`). `urllib.request.urlopen` is patched with synthetic responses — proves the Reader's error mapping is complete without making a real network call. |
| `pytest tests/test_cli.py` | Spec §2 (exit 0 on PASS, exit 1 on FAIL), §3 (PASS report shape — `PASS`, Merchant, Date, Items, Total), §4 (FAIL report shape — check name, calculated, stated, delta), §12 (date-as-null renders as `Date: (none)`). One subprocess smoke test asserts `python receipts.py` works as the spec requires. |
| `tests/conftest.py` validation | Each hand-written fixture is cross-checked against `images/expected.json` at import time; a typo in a fixture fails the suite. Proves the fixtures stay honest. |

A green `pytest` with no key set is the offline proof: the suite passes, the eval tests skip, and the codebase is known-good without spending any tokens.

### Eval (needs `OPENROUTER_API_KEY` — marked `@pytest.mark.eval`)

| Test | Proves |
|---|---|
| `test_eval.py::test_schema_on_real_receipts` | Spec §1: the model returns JSON matching the schema (correct fields, types, `Item` shape) for all five `images/receipt-0[1-5].png`. Catches missing fields, wrong types, or shape drift. |
| `test_eval.py::test_arithmetic_passes_on_clean_receipts` | Spec §10, §11, **plus comparison against `images/expected.json`** for the four fields it lists (`merchant`, `date`, `items` count, `total`). Passes when (a) the model output agrees with `expected.json` for those four fields, **and** (b) the Checker's arithmetic holds on the model output. Proves the end-to-end pipeline (image → model → JSON → `expected.json` + arithmetic) is internally consistent. |
| `test_eval.py::test_null_date_on_receipt_05` | Spec §12: the model returns `date: null` for `receipt-05.png` (no year printed). Catches the model guessing a year, which would silently corrupt the report. |
| `test_eval.py::test_reasoning_toggle` | Spec §14: with `CHAT_REASONING=off`, the Reader still emits a parseable request and the model still returns valid JSON — proves the toggle doesn't break the call. |
| `test_eval.py::test_model_comparison` | Runs the same five receipts against both `minimax/minimax-m3` and `xiaomi/mimo-v2.6-flash`, prints a small comparison table (pass count + OpenRouter-reported token usage), and is the artifact that drives the "pick the default" decision in Phase 3, step 16. |

The split between offline and eval means: a fresh clone runs `pytest` and gets a clean green (offline pass + eval skip). Setting `OPENROUTER_API_KEY` adds the model layer without disturbing the offline layer — graders can run the offline suite even without a key.

## Subagents and parallel work

None. This is a single-developer lab with two small components; the suite is small enough that sequential work is faster than delegation. Both models are tested from one process because OpenRouter serializes requests per key anyway.

## Interrogation

Two questions the lab requires the plan to answer in writing. Both are about what the plan does **not** cover, and what to do when the plan meets reality.

### Q1. What does the eval compare against, and what if the model is right and `expected.json` is wrong?

The eval compares the model's extracted JSON against two ground-truth sources:

1. **`images/expected.json`** for the four fields it lists: `merchant`, `date`, `items` (count), `total`. If the model disagrees with `expected.json` on any of these, the eval test fails and prints the specific field-level diff.
2. **The Checker's own arithmetic** for the per-item lines and the total. If the model says `subtotal=15.00` but `sum(quantity × unit_price) = 14.97`, that's a Checker-driven FAIL regardless of what `expected.json` says.

If the model is right and `expected.json` is wrong, the policy per `CLAUDE.md` is **report and stop, never edit**:

> "images/ and images/expected.json are the answer key. Never edit them. If a test disagrees with the key, say so and stop."

The failing eval test is left as-is. The agent does not "fix" the test, does not edit `expected.json`, and does not silently switch to a different value. This is also why `test_model_comparison` prints the diff for human review rather than auto-deciding which side is correct.

A concrete example: if the model extracts `merchant: "Mather Hall"` and `expected.json` has `"Mather Dining Hall"`, the eval test prints both strings and stops. The grader (looking at the original receipt image) decides whether the model truncated the name or `expected.json` is wrong. The agent surfaces the conflict; the human resolves it.

The eval-test contract is therefore: **the model is judged against `expected.json` + the Checker's arithmetic, not against the agent's opinion of what the receipt says.** If the two sources of truth disagree, the eval halts.

### Q2. Why pytest as a dependency, and what would we write by hand without it?

Pytest is the only declared test dependency (everything else in the project is stdlib per the intent). We use it because it gives us, out of the box, what we would otherwise have to hand-write:

| What pytest gives us | What we'd write by hand without it |
|---|---|
| Test discovery (`test_*.py` glob) | A registry of test functions and an `importlib` walk over `tests/` |
| `@pytest.fixture` injection | A `FIXTURES = {}` global dict and explicit `setup()` / `teardown()` calls in each test |
| `@pytest.mark.skipif` for the no-key offline path | A try/except around the test body and a manual skip counter |
| `@pytest.mark.eval` for the model-dependent tests | A marker registry plus a CLI flag that filters the test list |
| `assert` rewriting (clear diff on failure) | Custom assertion helpers or `unittest.TestCase.assertEqual` boilerplate |
| `pytest.raises` (asserting `ReadError`) | `try/except` blocks plus an attribute check on the exception |
| `subprocess.run` capture in `tests/test_cli.py` | Manual `subprocess.Popen` plumbing with `stdout=PIPE` |
| Exit codes and pass/fail/skip summary output | Hand-built counters and a `sys.exit` based on the failure count |

Pytest is also industry-standard — graders, CI tooling, and IDEs all recognize its syntax. Hand-rolling a ~200-line test runner to replace it for a codebase with ~10 tests and one optional network call would add a maintenance burden larger than the production code itself, with no benefit beyond "one fewer dependency."

The intent already names `pytest` as the only test dependency (Constraints, §Language), and `spec.md` lists it in Dependencies. The trade-off — one third-party test dependency in exchange for skipping an entire test-framework rewrite — is the right one for the size of this project.

## Week 6

Implements the Week 6 section of `spec.md` (which implements `intent/image-set.md`). Two scripts, two critics, one prompt family of five educational science diagrams. Mirrors the Week 5 reader/checker split: the Generator only talks to the image model, the Critic-checker only talks to the vision model and runs the deterministic comparison.

### Files under `week06/`

#### Production code (new)
| File | Purpose |
|---|---|
| `week06/prompts.json` | Rich, self-contained prompt file. Five entries, each `{id, prompt, seed, answer_key}` where `answer_key` is a map of part-name → expected presence (boolean; string for any expected-text case). Hand-coded from the prompt text — the only oracle the lab trusts. |
| `week06/generate.py` | The Generator. Calls the OpenRouter Image API through the `openrouter` SDK at `--model` for each entry, writes the PNGs and the manifest. `--out` is required and the script aborts instead of overwriting an existing run. |
| `week06/critique.py` | The Critic-checker. Reads `manifest.json` (or `prompts.json` directly), sends each image + checklist to the vision model at `--model`, parses the JSON, runs the deterministic comparison against `answer_key`, prints a per-image `PASS`/`FAIL` + per-part table, writes a per-critic report JSON. The same script serves both the primary (`minimax/minimax-m3`) and the comparison (`xiaomi/mimo-v2.6-flash`) critic runs. |

#### Test code (new)
| File | Purpose |
|---|---|
| `week06/tests/conftest.py` | Shared fixtures: loads `week06/prompts.json`; exposes per-prompt hand-written critique-reply JSON fixtures (one fully-correct reply, one with a missing key, one with an invented key, one malformed) plus a stubbed `openrouter` client. |
| `week06/tests/test_checker.py` | Pure-code tests for the checker: expected-vs-critiqued ✓/✗ rule, un-answered → FAIL, invented → FAIL, per-image PASS rule, per-part table includes required + invented + un-answered rows. No model calls. |
| `week06/tests/test_critique_parser.py` | Tests for JSON extraction: valid object parses; not-JSON → structured FAIL with `<200-char excerpt>` (no crash); single optional markdown fence is stripped; non-object top-level is rejected. |
| `week06/tests/test_text_compare.py` | Tests for expected-text comparison: character-for-character equality on string-valued `answer_key` entries; strict bool equality (`True` ≠ `"true"` ≠ `"yes"`); no fuzzy matching. |
| `week06/tests/test_inter_critic.py` | Tests for inter-critic comparison: given two report JSONs, flags every row where the two critics' `critiqued` values differ on the same `part`; inter-critic disagreement is reported alongside the key check, never instead of it. |
| `week06/tests/test_cli.py` | Tests for CLI exit codes (0 on every-image-pass, 1 on any FAIL or refused `--out`) and the `--out` overwrite guard. Uses a stubbed `openrouter` client. |
| `week06/tests/test_eval.py` | Billed eval tests, marked `@pytest.mark.eval` and skipped without `OPENROUTER_API_KEY`. One generation per prompt, one critique per image, fixed seeds. |

#### Outputs (gitignored; not committed)
| Path | Purpose |
|---|---|
| `week06/out/flux/images/<id>.png` | Five FLUX images, one per prompt, fixed seed. |
| `week06/out/flux/images/manifest.json` | Manifest with `id`, `prompt`, `seed`, `model`, `image_path`, `media_type`, `cost_usd` per entry. |
| `week06/out/flux/report.minimax-m3.json` | Primary critic's per-image report. |
| `week06/out/flux/report.xiaomi-mimo.json` | Comparison critic's per-image report. |
| `week06/out/gemini/images/<id>.png` + `manifest.json` | The single comparison-image-model run. |

#### Files changed (not new)
| File | Change |
|---|---|
| `pyproject.toml` | Adds the `openrouter` runtime dependency (the SDK used by both scripts). Declared as a Week-6 extras block (`[project.optional-dependencies].week06 = ["openrouter"]`) so the Week 5 stdlib-only rule is not weakened for graders who only run the receipts suite. |

#### Files not changed
`receipts/`, top-level `tests/`, `images/`, `images/expected.json`, `intent/receipts.md`, `spec.md` (Week 5 section), `CLAUDE.md`, `ANNOTATION.md`, `REVIEW.md`, `README.md`. Per CLAUDE.md: `images/` and the answer key remain untouched — they belong to the Week 5 receipts suite.

### Order of work

#### Phase 1 — Data first
1. **`week06/prompts.json`** — author the five entries. Each `answer_key` is hand-coded from the prompt text, not derived from a model. The file is the only oracle; correctness here unblocks every downstream step.

#### Phase 2 — Offline tests (no API key, no spend)
2. **`week06/tests/conftest.py`** — load `prompts.json`; build the per-prompt critique-reply fixtures (clean, missing-key, invented-key, malformed).
3. **`week06/tests/test_checker.py`** — ✓/✗ rule, un-answered FAIL, invented FAIL, per-image PASS rule.
4. **`week06/tests/test_critique_parser.py`** — valid object parses; not-JSON → structured FAIL (no crash); fence-stripping; non-object rejection.
5. **`week06/tests/test_text_compare.py`** — character-for-character equality on strings; strict bool equality; no fuzzy match.
6. **`week06/tests/test_inter_critic.py`** — flags every per-row disagreement between two report JSONs; never overrides the key check.
7. **`week06/tests/test_cli.py`** — exit codes and `--out` overwrite guard against a stubbed client.

Run `pytest week06/tests/` after each step. By the end of Phase 2 the offline suite is fully green with no API key.

#### Phase 3 — Production code
8. **`pyproject.toml`** — declare the `openrouter` extras block before importing it anywhere.
9. **`week06/generate.py`** — uses the `openrouter` SDK; writes images + manifest; honors the `--out` overwrite guard. We do not call the API yet — write the script, then move on.
10. **`week06/critique.py`** — uses the `openrouter` SDK for chat-completions; reads manifest; calls the vision model; parses JSON; runs the checker; prints the per-image report; writes the per-critic report JSON. Both the primary and comparison critic paths share the script via `--model`.

#### Phase 4 — Billed eval
11. **`week06/tests/test_eval.py`** — model-dependent tests, marked `@pytest.mark.eval`, skipped without `OPENROUTER_API_KEY`.
12. `pytest -m eval week06/tests/` with `OPENROUTER_API_KEY` set. Five generations (FLUX, fixed seeds) + five critiques (minimax-m3). One generation per prompt, one critique per image, **fixed seeds** — the same `(prompt, seed)` yields a visually identical diagram across re-runs.
13. Run the comparison passes: `generate.py --model google/gemini-3.1-flash-lite-image` on one prompt (records `cost_usd`); `critique.py --model xiaomi/mimo-v2.6-flash` on the same FLUX manifest; run `test_inter_critic.py` to flag every critic-vs-critic disagreement.
14. Record the eval outcome in `ANNOTATION.md`: per-image PASS/FAIL, per-part disagreements, inter-critic disagreements, total spend. Per intent, **report and stop** on any disagreement — do not edit `prompts.json` or `answer_key` to "fix" a FAIL.

### Risks

| Risk | Assumption | Blast radius if wrong |
|---|---|---|
| The `openrouter` PyPI package is unavailable or its image-generation API differs from the spec | The SDK exposes an Image-generation call returning typed media + per-call `cost_usd` | The Generator falls back to `urllib` (mirroring `receipts/reader.py`), `cost_usd` is left `null` in the manifest, the spec's stated dependency is revised |
| `flux.2-klein-4b` produces visually distinct diagrams across runs despite a fixed seed | "Fixed seed" means visually identical even if bytes shift (per intent) | The eval artifacts are not byte-comparable across runs; reproducibility is judged visually + by critique output, not by image hash |
| `gemini-3.1-flash-lite-image` costs more than the rough $0.02 estimate | Recorded `cost_usd` is the source of truth; the manifest captures it for every entry | The single comparison run still fits the $1 per-run ceiling unless the cost is >$0.91 |
| The primary critic returns a non-JSON reply for a clean prompt | Spec §17 mandates a structured FAIL with `<200-char excerpt>`, no crash | The eval test surfaces it; we tighten the system prompt in `critique.py` and re-run |
| The critic invents requirement names | Spec §19 mandates a structured ✗ row with `(invented)` tag | Same as above; visible in the per-part table, not silent |
| Inter-critic disagreement is high (most rows differ) | Both critics use the same checklist; agreement should be common | The "critic uncertainty" surface becomes noisy; the per-row check against `answer_key` stays authoritative regardless |
| `OPENROUTER_API_KEY` rate-limits during the eval | The eval is small (≤16 billed calls); sequential | Latency, not correctness; re-run |
| `prompts.json` `answer_key` drifts from the prompt text | The file is hand-coded once; the eval re-validates it implicitly (a clean diagram whose critique misses a real part surfaces a key drift) | Drift becomes visible in the per-part table; we fix `answer_key` (not the prompt — the prompt is the spec) |
| A hand-written critique fixture drifts from real-world critique shape | Fixtures are written against the expected schema; the eval re-validates against real output | A new schema field from the model goes unnoticed in offline tests; the eval catches it as a parse failure |
| The `--out` overwrite guard is too aggressive (refuses writes that should be allowed) | The guard checks for an existing `images/` directory or `manifest.json` | A second run on the same `--out` fails; users learn to use `--out week06/out/run-2`; acceptable |
| Adding `openrouter` to `pyproject.toml` weakens the Week 5 stdlib-only rule | Declared under a Week-6 extras block (`[project.optional-dependencies].week06 = ["openrouter"]`); default `pip install -e .` does not pull it in | Graders who only run the receipts suite are unaffected; Week 6 users run `pip install -e ".[week06]"` |
| The `test_eval.py` marker is forgotten on a model-dependent test | `@pytest.mark.eval` mirrors Week 5's pattern; the suite's `addopts` is `-m 'not eval'` so unmarked tests still skip without a key | A model-dependent test runs offline and either errors or skips for the wrong reason; the offline run is no longer a clean green — caught at first offline run |
| A user loops the eval by mistake (e.g., a shell `for` loop over the same script) | The `--out` overwrite guard fires before any API call on the second iteration; `@pytest.mark.eval` skips by default | Each iteration that bypasses the guard (e.g., a fresh `--out` each time) bills another ≈$0.10; ~50 sweeps hit the $5 semester ceiling. See **Interrogation Q2**. |

### Proof

The lab is proven in two layers that map onto the spec's behaviors. The **offline** layer exercises every comparison rule with hand-written fixtures — no API key, no spend, always green. The **billed eval** exercises the end-to-end pipeline with real models — five generations and five critiques, fixed seeds. The split mirrors Week 5: graders can run the offline suite without a key and trust the comparison logic; setting the key adds the model layer without disturbing the offline layer.

#### Offline (no `OPENROUTER_API_KEY` — must always pass)

| Test | Proves |
|---|---|
| `week06/tests/test_checker.py` | Spec §20 (`expected == critiqued` in Python, ✓/✗), §18 (un-answered key → `critiqued = (none)`, ✗, image FAIL), §19 (invented key → ✗ with `(invented)` tag, image FAIL), §21 (per-image PASS iff every row ✓), §22 (per-part table includes required + invented + un-answered rows). **Proves the deterministic comparison logic is correct without a model.** |
| `week06/tests/test_critique_parser.py` | Spec §16 (valid object parses), §17 (not-JSON → structured FAIL with `<200-char excerpt>`, **no crash**), §16 (single optional markdown fence is stripped), §16 (non-object top-level → structured FAIL). **Proves JSON extraction is complete and every malformed-reply path is a structured FAIL.** |
| `week06/tests/test_text_compare.py` | Spec §20 (character-for-character equality on string-valued `answer_key` entries), §20 (strict bool equality, no normalization: `True` ≠ `"true"` ≠ `"yes"`), §20 (no fuzzy matching). **Proves expected text is compared by the Python code, never by the critic.** |
| `week06/tests/test_inter_critic.py` | Spec §23 (given two report JSONs, flags every row where the two critics' `critiqued` values differ on the same `part`), §23 (inter-critic disagreement is reported *alongside*, never *instead of*, the key check), §23 (a critic-vs-key disagreement is never overridden by critic-vs-critic agreement). **Proves the comparison of two critiques is correct.** |
| `week06/tests/test_cli.py` (offline parts) | Spec §24 (exit 0 on every-image-pass, exit 1 on any FAIL or refused `--out`), `--out` overwrite guard. Stubbed `openrouter` client — **proves the CLI surface without a real model**. |

A green `pytest week06/tests/` with no key set is the offline proof: every comparison rule is exercised, the eval tests skip with the documented reason, and the codebase is known-good without spending any tokens.

#### Billed eval (needs `OPENROUTER_API_KEY` — marked `@pytest.mark.eval`)

The billed eval is the **five generations and five critiques** of the primary path, each run with fixed seeds so a re-run is reproducible. Each step proves one piece of the end-to-end pipeline and carries a per-step cost; the subtotal is the full Week 6 primary-path spend.

| Step | Proves | Cost |
|---|---|---|
| **5 generations** — `week06/generate.py --model black-forest-labs/flux.2-klein-4b --out week06/out/flux week06/prompts.json`, run once, fixed seeds. | (a) The `openrouter` SDK reaches the OpenRouter Image endpoint and returns typed media + `cost_usd`. (b) Each `(prompt, seed)` pair yields a visually reproducible diagram across re-runs (visually identical even if bytes shift). (c) The manifest is written with `prompt`, `seed`, `model`, `image_path`, `media_type`, `cost_usd` for every entry. (d) The `--out` overwrite guard refuses to overwrite an existing run. | 5 × ≈$0.014 = **≈$0.07**. |
| **5 critiques** — `week06/critique.py --model minimax/minimax-m3 --out week06/out/flux/report.minimax-m3.json week06/out/flux/images/manifest.json`, run once after the generation step. | (a) The vision model returns a JSON object matching Spec §16 for each of the five FLUX diagrams. (b) Every entry in `answer_key` is either answered correctly (✓) or surfaced as ✗ with the correct reason (un-answered, invented, mismatch). (c) The per-image PASS/FAIL rule (§21) classifies the image correctly. (d) The per-part table on stdout matches the JSON report row-for-row. (e) The CLI exits 0 on every-pass and 1 on any FAIL. | 5 × ≈$0.002 = **≈$0.01**. |
| **Subtotal — primary path (5+5)** | End-to-end pipeline (image → manifest → critique → comparison → PASS/FAIL + table + report) is correct against real models with real prompts. The 5+5 matrix covers all five prompt families without looping the model inside a test (per intent). | **≈$0.08** per sweep — well under the $1.00 per-run ceiling from intent. |

**Comparison runs** are billed but run **once per sweep**, not as part of the 5+5 matrix above:

| Step | Proves | Cost |
|---|---|---|
| **Comparison critic** — `critique.py --model xiaomi/mimo-v2.6-flash --out week06/out/flux/report.xiaomi-mimo.json week06/out/flux/images/manifest.json`. | (a) The second vision model produces a report JSON in the same shape as the primary (§16). (b) `test_inter_critic.py` flags every row where the two critics disagree — surfaces places where the primary critic may have been confidently wrong. (c) A critic-vs-critic disagreement never overrides a critic-vs-key disagreement (§23). | 5 × ≈$0.0004 = **≈$0.002**. |
| **Comparison image model** — `generate.py --model google/gemini-3.1-flash-lite-image --out week06/out/gemini <single-prompt-file>` for one prompt. | (a) A different image model can produce a comparable diagram for the same prompt family. (b) `critique.py --model minimax-m3` against the resulting manifest produces a normal PASS/FAIL report. (c) The manifest records the per-image `cost_usd` for auditability. | 1 × ≈$0.02 (estimate; the manifest captures actual cost). |
| **Sweep total** | Full Week 6 matrix (primary 5+5 + comparison 1+5). | **≈$0.10** per sweep — semester ceiling $5 holds for ~50 sweeps. |

The eval is intentionally **not looped**: each billed step runs exactly once per sweep (one generation per prompt, one critique per image), per intent and per CLAUDE.md. A re-run is a fresh billed sweep — useful for confirming the fixed-seed reproducibility of the FLUX images, not for retrying failures. Per CLAUDE.md, an unexpected disagreement is **reported, not fixed**: the eval test fails with a clear ✗ row, and the agent stops.

### Interrogation (Week 6)

Three questions the lab requires the plan to answer in writing. All three are about what the plan does **not** cover, and what to do when the plan meets reality.

#### Q1. If the critic says a requirement is unmet and it is actually met, which test notices?

This is the central failure mode the lab is designed to surface: the image has the part; the critic returns `false`; the answer key says `true`. Four layers notice, in order of how directly they point at the wrongness, plus a fifth (the offline suite) that explicitly does **not** catch it.

1. **The per-part table** (deterministic) — the comparison surfaces it as a ✗ row with `expected = true`, `critiqued = false`, `match = ✗`. The per-image verdict is FAIL, the JSON report carries the disagreement, and pytest prints the table on failure.
2. **The billed eval test** (`test_eval.py`) — runs the real critic against the real image and asserts the per-image PASS rule (§21). On a critic miss, the assertion fails and pytest prints the per-part table. The test **surfaces** the disagreement but does **not adjudicate** it — it does not know whether the critic is wrong, the image is missing the part, or `answer_key` is wrong.
3. **The inter-critic comparison** (`test_inter_critic.py`) — when the second critic (`xiaomi/mimo-v2.6-flash`) runs on the same FLUX images, every row where the two critics disagree is flagged as "critic uncertainty." If the comparison critic says `true` and the primary says `false` on a row where `answer_key` is `true`, the wrongness is sandwiched between two agreeing sources (answer_key + comparison critic). This is the strongest **in-test** signal.
4. **The human** is the final oracle. Per CLAUDE.md and the intent's "report, don't fix" rule, the agent does not edit `answer_key` to "fix" a FAIL. The user opens the image, sees the part is present, and confirms the critic was wrong. The lab's purpose is to make that wrongness visible; it does not auto-correct it.

The **offline suite** (`test_checker.py`, `test_critique_parser.py`, `test_text_compare.py`, `test_inter_critic.py`) does **not** catch this kind of error. The fixtures are hand-written critique-reply JSON files that are self-consistent by construction; they prove the **comparison logic** is correct, not that the **critic** is accurate. Critic accuracy is the billed eval's job, and it is bounded by what the model can see in the image.

The single most direct automated signal is the **per-image FAIL row in the per-part table** (Spec §21 + §22). It surfaces every wrongness regardless of which side is wrong.

#### Q2. What does one run of the eval cost, and what happens if I run it in a loop by mistake?

One sweep of the billed eval (the 5+5 matrix plus the comparison passes) costs **≈$0.10**, broken down as:

- 5 × `flux.2-klein-4b` image generations: ≈$0.07.
- 1 × `gemini-3.1-flash-lite-image` image generation: ≈$0.02.
- 5 × `minimax/minimax-m3` critiques: ≈$0.01.
- 5 × `xiaomi/mimo-v2.6-flash` critiques: ≈$0.002.

That is well under the **$1.00 per-run ceiling** from intent, and the **$5 semester ceiling** from CLAUDE.md holds for ≈50 sweeps.

A runaway loop multiplies this linearly:

- 10 sweeps → ≈$1.00 (at the per-run ceiling).
- 50 sweeps → ≈$5.00 (at the semester ceiling).
- 100 sweeps → ≈$10.00 (over the semester ceiling).

The protection against accidental loops is layered, with each layer covering a different class of mistake:

1. **The `--out` overwrite guard** is the primary defense. Re-running `generate.py` or `critique.py` with the same `--out` path **aborts before any API call** on the second iteration. The most common accident — running the same command twice in a row — produces zero additional spend.
2. **The `@pytest.mark.eval` marker** is the second defense. Eval tests are skipped by default (`addopts = "-m 'not eval'"`); they only run when the user explicitly opts in with `pytest -m eval`. There is no `--count` or loop flag; each test makes exactly one API call.
3. **The intent's "one generation per prompt, one critique per image" rule** prevents tests from looping internally. A test does not call the API inside a `for` loop; it asserts on a single call's result.
4. **No test re-runs on FAIL.** A failed eval is left as-is; the user inspects the per-part table, decides what is wrong, and re-runs the whole sweep manually (with a fresh `--out` to bypass the guard).

The OpenRouter SDK / API does **not** enforce a hard spend cap at the call site — the user sets their own cap in the OpenRouter dashboard. The semester ceiling is therefore a self-imposed budget, checked manually. The plan's job is to make the spend **visible and bounded**, not to make overspend impossible — that is what the OpenRouter dashboard cap is for. A user-side hardening step (out of scope for this lab) would be to set a hard monthly cap in the OpenRouter dashboard before running the eval for the first time.

#### Q3. What does the same seed guarantee, and how would I check that?

The same seed guarantees **visual reproducibility** for the same `(prompt, seed, model)` triple: re-running `generate.py` with the same prompt, the same seed, and the same model produces a visually identical diagram — same composition, same parts, same layout, same colors. It does **not** guarantee byte-identical PNG output — the model may add small numerical noise or use a slightly different compression, so the raw bytes can shift while the picture does not. Per intent: "a 'fixed seed' means the generated image should look visually identical for the same prompt and seed even if the raw output bytes differ slightly."

What the same seed does **not** guarantee:

- That text rendered inside the image (e.g., part labels like "nucleus", "chloroplast") is spelled the same way across runs. Text-in-image is a known weak point for diffusion models and FLUX is no exception; a label may appear as "nucleus" once and "nucleeus" the next. This is **not** a seed failure — it is a model characteristic.
- That the per-call `cost_usd` is byte-identical across runs. OpenRouter's pricing can fluctuate at sub-cent precision; the manifest records whatever the API returned. A 10× jump is a real signal; a sub-cent drift is not.
- That the critique is identical across runs. The vision model is not seeded at the same level as the image model; the temperature and top-p defaults can introduce variation even with `minimax/minimax-m3` or `xiaomi/mimo-v2.6-flash`. The per-part table is therefore **approximately** reproducible, not exactly.

The plan checks reproducibility in three ways, cheapest to most thorough:

1. **Cost audit.** The manifest's `cost_usd` per entry should be in the same ballpark across re-runs (within the per-image pricing tolerance). A 10× jump means the wrong model was used.
2. **Per-part table** — the most useful functional check. Run `critique.py` against two manifests produced by two `generate.py` runs at the same `(prompt, seed, model)`. The per-part tables (Spec §22) should be **identical** for a fully-reproducible run: same ✓/✗ per part, same `verdict` per image. A divergence in the table means either the seed did not bite (the image actually changed) or the critic varied on the same image. This check uses no extra API calls beyond the two generation sweeps.
3. **Visual eyeball.** Open both PNGs side-by-side. They should look the same composition. Labels may spell slightly differently; parts may sit at slightly different pixel positions; everything else should match. This is the human's check.

The eval re-run is **useful as a reproducibility check, not as a retry mechanism.** Per intent: a failed critique is reported as-is; the user does not loop to "try again." A reproducibility re-run is a deliberate second sweep (with a fresh `--out`, e.g., `out/run-1` and `out/run-2`), intended to confirm the seed held, **not** to mask a FAIL on the first run.

**Approved by:** Javier Romo, October 23, 2026

**Approved by (Week 6):** Javier Romo, October 23, 2026
