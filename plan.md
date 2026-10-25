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

**Approved by:** Javier Romo, Octoer 23, 2026
