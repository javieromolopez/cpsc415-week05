"""Offline tests for the Week 6 Generator (Spec §Generator).

Tests cover the CLI surface (no model calls, no API key required):
- One PNG written per entry at `<out>/images/<id>.png`.
- Manifest written at `<out>/images/manifest.json` with the required
  fields: id, prompt, seed, model, image_path, media_type, cost_usd.
- `--out` overwrite guard: refuses if `<out>/images/` already exists.
- Missing `OPENROUTER_API_KEY` → exit 1; no images written, no manifest.
- Usage errors: missing required args exit 1; `--help` exits 0.

Tests use `monkeypatch` to stub `week06.generate.generate_image` so no
network call is made. The CLI is exercised via its `main(argv)` entry
point.

CLI shape:
    python -m week06.generate --model <slug> --out <dir> <prompts.json>
"""
from __future__ import annotations

import json

from week06.generate import GeneratedImage


def _write_prompts(path) -> None:
    """Write a minimal `prompts.json` with two entries."""
    path.write_text(json.dumps([
        {
            "id": "plant-cell",
            "prompt": "Diagram of a plant cell.",
            "seed": 42,
            "answer_key": {"nucleus": True, "cell_wall": True},
        },
        {
            "id": "flower",
            "prompt": "Diagram of a flower.",
            "seed": 43,
            "answer_key": {"petal": True, "stem": True},
        },
    ]))


# ============================================================================
# Manifest + image writes (Spec §Generator Output)
# ============================================================================

class TestGeneratorOutput:
    def test_writes_one_png_per_entry(self, tmp_path, monkeypatch):
        """Each entry → one PNG in `<out>/images/<id>.png`."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"

        def fake_generate_image(prompt, seed, model, api_key):
            return GeneratedImage(
                data=b"PNG_" + prompt.encode(),
                media_type="image/png",
                cost_usd=0.014,
            )

        monkeypatch.setattr(generate, "generate_image", fake_generate_image)

        exit_code = generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 0
        assert (out / "images" / "plant-cell.png").exists()
        assert (out / "images" / "flower.png").exists()

    def test_png_contents_match_generate_image_output(self, tmp_path, monkeypatch):
        """The PNG bytes on disk are exactly what `generate_image` returned."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"

        plant_bytes = b"PNG_plant-cell-data"
        flower_bytes = b"PNG_flower-data"

        def fake_generate_image(prompt, seed, model, api_key):
            if "plant" in prompt:
                return GeneratedImage(
                    data=plant_bytes, media_type="image/png", cost_usd=0.014
                )
            return GeneratedImage(
                data=flower_bytes, media_type="image/png", cost_usd=0.014
            )

        monkeypatch.setattr(generate, "generate_image", fake_generate_image)

        generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
            str(prompts),
        ])

        assert (out / "images" / "plant-cell.png").read_bytes() == plant_bytes
        assert (out / "images" / "flower.png").read_bytes() == flower_bytes

    def test_writes_manifest_with_required_fields(self, tmp_path, monkeypatch):
        """Manifest includes id, prompt, seed, model, image_path, media_type, cost_usd."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"
        manifest_path = out / "images" / "manifest.json"

        def fake_generate_image(prompt, seed, model, api_key):
            return GeneratedImage(
                data=b"PNG", media_type="image/png", cost_usd=0.014
            )

        monkeypatch.setattr(generate, "generate_image", fake_generate_image)

        exit_code = generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 0
        manifest = json.loads(manifest_path.read_text())
        assert isinstance(manifest, list)
        assert len(manifest) == 2

        for entry in manifest:
            assert "id" in entry
            assert "prompt" in entry
            assert "seed" in entry
            assert "model" in entry
            assert "image_path" in entry
            assert "media_type" in entry
            assert "cost_usd" in entry
            assert entry["model"] == "black-forest-labs/flux.2-klein-4b"
            assert entry["media_type"] == "image/png"
            assert entry["cost_usd"] == 0.014
            # image_path is relative to --out (per spec).
            assert entry["image_path"].startswith("images/")

    def test_manifest_preserves_prompt_id_seed_order(self, tmp_path, monkeypatch):
        """Manifest preserves the order of entries from prompts.json."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"

        def fake_generate_image(prompt, seed, model, api_key):
            return GeneratedImage(
                data=b"PNG", media_type="image/png", cost_usd=0.014
            )

        monkeypatch.setattr(generate, "generate_image", fake_generate_image)

        generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
            str(prompts),
        ])

        manifest = json.loads((out / "images" / "manifest.json").read_text())
        assert [e["id"] for e in manifest] == ["plant-cell", "flower"]
        assert [e["seed"] for e in manifest] == [42, 43]
        assert manifest[0]["prompt"] == "Diagram of a plant cell."
        assert manifest[1]["prompt"] == "Diagram of a flower."


# ============================================================================
# --out overwrite guard (Spec §Generator Interfaces)
# ============================================================================

class TestOutOverwriteGuard:
    def test_refuses_when_images_dir_exists(self, tmp_path, monkeypatch):
        """If `<out>/images/` exists, the CLI aborts BEFORE any API call."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"
        (out / "images").mkdir(parents=True)

        call_count = 0
        def fake_generate_image(prompt, seed, model, api_key):
            nonlocal call_count
            call_count += 1
            return GeneratedImage(data=b"PNG", media_type="image/png", cost_usd=0.014)

        monkeypatch.setattr(generate, "generate_image", fake_generate_image)

        exit_code = generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 1
        assert call_count == 0  # guard fires before any generate_image call


# ============================================================================
# Missing API key (Spec §9)
# ============================================================================

class TestMissingApiKey:
    def test_exit_1_with_no_api_key(self, tmp_path, monkeypatch):
        """Missing `OPENROUTER_API_KEY` → exit 1; no images, no manifest."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from week06 import generate

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"

        call_count = 0
        def fake_generate_image(prompt, seed, model, api_key):
            nonlocal call_count
            call_count += 1
            return GeneratedImage(data=b"PNG", media_type="image/png", cost_usd=0.014)

        monkeypatch.setattr(generate, "generate_image", fake_generate_image)

        exit_code = generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 1
        assert call_count == 0
        assert not (out / "images").exists()


# ============================================================================
# Usage errors (argparse)
# ============================================================================

class TestUsageErrors:
    def test_exit_1_when_missing_model(self, tmp_path, monkeypatch):
        """--model is required → exit 1."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "out"

        exit_code = generate.main([
            "--out", str(out),
            str(prompts),
        ])
        assert exit_code == 1

    def test_exit_1_when_missing_out(self, tmp_path, monkeypatch):
        """--out is required → exit 1."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)

        exit_code = generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            str(prompts),
        ])
        assert exit_code == 1

    def test_exit_1_when_missing_input(self, tmp_path, monkeypatch):
        """Positional input is required → exit 1."""
        from week06 import generate
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        out = tmp_path / "out"

        exit_code = generate.main([
            "--model", "black-forest-labs/flux.2-klein-4b",
            "--out", str(out),
        ])
        assert exit_code == 1

    def test_exit_0_for_help(self):
        """--help → exit 0."""
        from week06 import generate

        exit_code = generate.main(["--help"])
        assert exit_code == 0
