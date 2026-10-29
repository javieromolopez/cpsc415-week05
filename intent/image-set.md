# Intent: image-set

<!-- A pedagogical reliability-gate tool for generated educational diagrams.
     One prompt family, five concepts, one critique per image, a hand-coded
     answer key per prompt, and a comparison critic that catches the primary
     critic being confidently wrong. Reports disagreements; never fixes them. -->

## Goal
A two-script CLI tool (`week06/generate.py` and `week06/critique.py`) that takes a self-contained `prompts.json` of five educational science-diagram prompts with inline answer keys, generates one image per prompt with an image model, sends each image to a vision model with a yes/no checklist derived from that prompt's requirements, and compares the model's yes/no answers to the hand-coded key in deterministic Python code. The output is a per-image PASS/FAIL line plus a per-part table whose rows are the required parts and whose columns flag every disagreement (critique said "yes" to a part the key says shouldn't be there, "no" to a part it says should be). The tool **reports** disagreements; it never edits or "fixes" them.

## Who it is for
Me (CPSC 415 student) and the course's graders, and — by alignment with Team Project 1's design — the team project reviewers. Today: I generate five diagrams, look at them, look at a vision model's raw yes/no response, eyeball whether the model got each part right. This tool composes image generation + vision-LLM critique + plain-code comparison so the eyeballing becomes a typed PASS/FAIL with every disagreement named.

## Constraints
- **Language:** Python 3.10+. Standard library plus `pytest` and the OpenRouter Python SDK already in use from Week 5 (`receipts/reader.py` shows the wire pattern). No build tools, no extra JSON libraries.
- **Path layout:** everything Week 6 lives under `week06/`. Two top-level scripts (`week06/generate.py`, `week06/critique.py`), `week06/prompts.json` (the rich per-prompt file), `week06/tests/` for the offline suite, and output paths dictated by an `--out` flag (image directory and/or report path) so nothing is silently overwritten.
- **Prompt family (fixed):** educational science diagrams. Style and scene are fixed as flat-color 2D textbook illustrations with solid color fills and clean labels. The single variable across the five prompts is the **scientific concept** being depicted. This is the only axis that varies; no stylistic drift between prompts.
- **Models (mandated, fixed for this lab):**
  - **Primary image model:** `black-forest-labs/flux.2-klein-4b` via the OpenRouter Python SDK. ~$0.014 per image. Used for all five prompt images.
  - **Comparison image model:** `google/gemini-3.1-flash-lite-image` — generates **one** image for **one** prompt, used as a single-prompt sanity / cost check.
  - **Primary vision model:** `minimax/minimax-m3` via OpenRouter API. Critiques every generated image against the matching prompt's answer key.
  - **Comparison vision model:** `xiaomi/mimo-v2.6-flash` — re-critiques the five FLUX images so the code can compare the two critics' JSON outputs and flag inter-critic disagreement.
- **Cost ceiling:** Total sweep is roughly 6 image-generation calls (≈ $0.08) plus ~11 critique calls at sub-cent per call. Per-run ceiling: **under $1.00** with realistic spend around $0.20; semester ceiling unchanged at $5 (Week 5 already well under).
- **Image generation:** one generation per prompt, fixed seed per prompt (so re-running with the same prompt+seed produces a visually identical diagram even if the raw PNG bytes shift slightly). Never loop the model inside a test.
- **Critique:** one critique call per image per critic. The critique is model output and **may be confidently wrong**; the tool makes that visible by comparing every yes/no to the hand-coded key and every pair of critics to each other. The code does not trust the critique.
- **`prompts.json` shape (rich, self-contained):** each entry carries `id`, `prompt`, `seed`, and `answer_key` (a map of part-name → expected presence, e.g. `{"nucleus": true, "cell wall": true, "chloroplast": true, ...}`). Inline so each prompt is portable and the script needs only one input file.
- **CLI flags (minimum):** `--model` selects the image model (in `generate.py`) or the critic model (in `critique.py`); `--out` dictates the output path so no run silently overwrites a previous run's images, manifest, or report.
- **Offline behavior:** model-dependent calls are skipped without `OPENROUTER_API_KEY` via the same `@pytest.mark.skipif` pattern as Week 5. The checker logic, JSON extraction, and report formatting are exercised by `week06/tests/` against hand-written fixtures (no images).

## Not in scope
- Auto-correcting any disagreement — the tool reports, it does not fix. (Aligned with Team Project 1's strict ✓/✗ contract: missing, unanswered, or invented requirements are surfaced, never silently normalized.)
- Generating images in any style other than the fixed flat-color 2D textbook look.
- Concepts outside the educational science-diagram family (no people, no landscapes, no abstract art).
- More than five primary prompts or more than one comparison-image run.
- More than one critique pass per image per critic. The comparison-critic pass is the only second pass, and only over the five FLUX images.
- A general-purpose image-generation or vision client. The scripts are shaped around this lab's prompt family and checklist format.
- Branch / PR workflow in the personal repository. Per CLAUDE.md, commit to `main`.
- Web UI, GUI, persistent storage. CLI text output plus written report files.
- The specific five scientific concepts. Those are picked in `spec.md`, not here, so the intent stays about *what* the tool checks, not *which* five diagrams it checks this semester.

## Success looks like
- `python week06/generate.py --model black-forest-labs/flux.2-klein-4b --out week06/out/flux week06/prompts.json` writes five PNGs into `week06/out/flux/images/` and a manifest JSON listing `id`, `prompt`, `seed`, `image_path`, and `model` for each entry. Re-running with the same `--out` aborts instead of overwriting.
- `python week06/critique.py --model minimax/minimax-m3 --out week06/out/flux/report.minimax-m3.json <manifest-or-prompts>` prints, per image, one `PASS` or `FAIL` line followed by a table whose rows are the part names from that prompt's answer key and whose columns show `expected` (from the key), `critiqued` (from the model), and `match` (✓/✗). Every disagreement — including a critique answer of `null`/missing — is a ✗ cell.
- Running the same `critique.py --model xiaomi/mimo-v2.6-flash` over the same FLUX manifest produces a second report. A small comparison step (script flag or a separate `compare.py` — deferred to `spec.md`) reads both reports and surfaces every row where the two critics disagree, flagged as "critic uncertainty."
- `python week06/generate.py --model google/gemini-3.1-flash-lite-image --out week06/out/gemini <single-prompt-file>` produces one comparable image; `critique.py --model minimax-m3` against that manifest produces a normal PASS/FAIL report and the manifest records the per-image cost for the comparison model.
- `pytest week06/tests/` passes offline. The suite covers: key-loading from `prompts.json`, the per-part checker (expected vs. critiqued vs. ✓/✗), report formatting, inter-critic disagreement detection, and the `--out` overwrite guard — all from hand-written fixtures, no API calls.

## Open questions
- Which five scientific concepts make the prompt set. Resolves in `spec.md`.
- Whether the inter-critic comparison is a third script (`week06/compare.py`) or a flag on `critique.py` (e.g. `--compare-with report.xiaomi.json`). Resolves in `spec.md`.
- Whether the comparison-image-model run uses the same prompt as the first FLUX image or a different one. Resolves in `spec.md`.
- The exact column set and row ordering of the per-part table (e.g., whether to add a `critique-raw` column showing the model's literal text). Resolves in `spec.md`.
- The exact JSON schemas for the manifest and the per-critic report. Resolves in `spec.md`.
- Whether `prompts.json` lives at `week06/prompts.json` or at a deeper path (e.g., `week06/prompts/v1.json`) so a future lab can ship a second prompt set side by side. Resolves in `spec.md`.

**Approved by:** Javier Romo, October 28, 2026
