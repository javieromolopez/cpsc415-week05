"""Billed evaluation tests for Week 6 (Spec §Phase 4).

Marked `@pytest.mark.eval`. **Skipped when `OPENROUTER_API_KEY` is unset.**
Run with: `pytest -m eval week06/tests/test_eval.py`

The eval runs the full Week 6 matrix (one generation per prompt, one
critique per image, fixed seeds):

- 5 generations with `black-forest-labs/flux.2-klein-4b` (primary image).
- 1 generation with `google/gemini-3.1-flash-lite-image` (sanity check).
- 5 critiques with `minimax/minimax-m3` (primary critic).
- 5 critiques with `xiaomi/mimo-v2.6-flash` (comparison critic).
- Inter-critic comparison across all 5 prompts.

Total: **16 model calls**, **≈$0.10 per run** (well under the $1.00 per-run
ceiling from intent, and well under the $5 semester ceiling).

Artifacts (PNGs, manifest, per-critic report JSONs) are written to
`week06/out/eval-<timestamp>/` so re-runs do not collide on the
`--out` overwrite guard.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from week06.checker import build_report, compare_critics
from week06.critique import build_critique_prompt, critique_image
from week06.generate import generate_image

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_FILE = REPO_ROOT / "week06" / "prompts.json"

PRIMARY_IMAGE_MODEL = "black-forest-labs/flux.2-klein-4b"
PRIMARY_CRITIC_MODEL = "minimax/minimax-m3"
COMPARISON_CRITIC_MODEL = "xiaomi/mimo-v2.6-flash"
COMPARISON_IMAGE_MODEL = "google/gemini-3.1-flash-lite-image"


def _has_api_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not _has_api_key(),
        reason="OPENROUTER_API_KEY not set; eval tests require a real model call",
    ),
]


# ============================================================================
# Fixtures (module-scoped so 5 generations are shared across all tests)
# ============================================================================

@pytest.fixture(scope="module")
def prompts() -> list[dict]:
    """The five entries from `week06/prompts.json`."""
    return json.loads(PROMPTS_FILE.read_text())


@pytest.fixture(scope="module")
def api_key() -> str:
    return os.environ["OPENROUTER_API_KEY"]


@pytest.fixture(scope="module")
def eval_dir() -> Path:
    """Unique output directory for this eval run (timestamped)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPO_ROOT / "week06" / "out" / f"eval-{stamp}"
    (out / "images").mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture(scope="module")
def generated_images(prompts, api_key, eval_dir) -> list[dict]:
    """Generate 5 images with the primary model and save them to disk.

    Each entry in the returned list has `id`, `image_path`, `image`
    (the `GeneratedImage` returned by the API), `prompt`, `seed`,
    `answer_key`.
    """
    rows = []
    for entry in prompts:
        gen = generate_image(
            prompt=entry["prompt"],
            seed=entry["seed"],
            model=PRIMARY_IMAGE_MODEL,
            api_key=api_key,
        )
        path = eval_dir / "images" / f"{entry['id']}.png"
        path.write_bytes(gen.data)
        rows.append({
            "id": entry["id"],
            "image_path": path,
            "image": gen,
            "prompt": entry["prompt"],
            "seed": entry["seed"],
            "answer_key": entry["answer_key"],
        })
    return rows


@pytest.fixture(scope="module")
def primary_critiques(generated_images, api_key) -> list[tuple[dict, object]]:
    """Run the primary critic (`minimax/minimax-m3`) on all 5 images."""
    return [
        (
            entry,
            critique_image(
                image_path=str(entry["image_path"]),
                prompt=build_critique_prompt(entry["prompt"], entry["answer_key"]),
                model=PRIMARY_CRITIC_MODEL,
                api_key=api_key,
            ),
        )
        for entry in generated_images
    ]


@pytest.fixture(scope="module")
def comparison_critiques(generated_images, api_key) -> list[tuple[dict, object]]:
    """Run the comparison critic (`xiaomi/mimo-v2.6-flash`) on all 5 images."""
    return [
        (
            entry,
            critique_image(
                image_path=str(entry["image_path"]),
                prompt=build_critique_prompt(entry["prompt"], entry["answer_key"]),
                model=COMPARISON_CRITIC_MODEL,
                api_key=api_key,
            ),
        )
        for entry in generated_images
    ]


# ============================================================================
# Generation eval
# ============================================================================

class TestGeneration:
    def test_primary_generates_five_images(self, generated_images):
        """5 FLUX generations return non-empty PNGs with valid media types."""
        for entry in generated_images:
            assert entry["image"].data, f"empty image data for {entry['id']}"
            assert entry["image"].media_type.startswith("image/"), (
                f"unexpected media type {entry['image'].media_type} "
                f"for {entry['id']}"
            )

    def test_comparison_image_model_sanity(self, prompts, api_key):
        """1 Gemini generation — sanity check that the model + endpoint work."""
        entry = prompts[0]
        gen = generate_image(
            prompt=entry["prompt"],
            seed=entry["seed"],
            model=COMPARISON_IMAGE_MODEL,
            api_key=api_key,
        )
        assert gen.data, "empty image data from Gemini"
        assert gen.media_type.startswith("image/"), (
            f"unexpected media type {gen.media_type} from Gemini"
        )


# ============================================================================
# Critique eval
# ============================================================================

class TestCritique:
    def test_primary_critic_returns_parseable_json(self, primary_critiques):
        """5 `minimax/minimax-m3` critiques — each reply must be parseable JSON."""
        for entry, result in primary_critiques:
            assert not result.failed, (
                f"primary critic failed for {entry['id']}: {result.excerpt}"
            )
            assert isinstance(result.value, dict)
            assert result.value, f"empty critique dict for {entry['id']}"

    def test_comparison_critic_returns_parseable_json(self, comparison_critiques):
        """5 `xiaomi/mimo-v2.6-flash` critiques — each reply must be parseable JSON."""
        for entry, result in comparison_critiques:
            assert not result.failed, (
                f"comparison critic failed for {entry['id']}: {result.excerpt}"
            )
            assert isinstance(result.value, dict)
            assert result.value, f"empty critique dict for {entry['id']}"


# ============================================================================
# Inter-critic comparison
# ============================================================================

class TestInterCritic:
    def test_inter_critic_comparison_runs(
        self, generated_images, primary_critiques, comparison_critiques
    ):
        """Build per-critic reports and run `compare_critics` for each prompt.

        For each prompt:
            1. Build the primary critic's report against the answer key.
            2. Build the comparison critic's report against the answer key.
            3. Run `compare_critics` — it must return a `list`, even if empty.
        Skips a prompt if either critic failed to parse (the test for that
        failure mode lives in `TestCritique`).
        """
        for (entry_p, p_result), (entry_c, c_result) in zip(
            primary_critiques, comparison_critiques
        ):
            assert entry_p["id"] == entry_c["id"]
            if p_result.failed or c_result.failed:
                # Skip inter-critic compare if either failed to parse.
                continue
            p_report = build_report(entry_p["answer_key"], p_result.value)
            c_report = build_report(entry_c["answer_key"], c_result.value)
            disagreements = compare_critics(p_report, c_report)
            assert isinstance(disagreements, list)
