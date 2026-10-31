"""Offline CLI tests for the Week 6 Critic-checker (Spec §24).

Tests cover the CLI surface (no model calls, no API key required):
- Exit codes (Spec §24): 0 on every-image-pass, 1 on any FAIL or
  refused `--out`, 1 on missing API key, 1 on usage errors.
- `--out` overwrite guard: refuses to write over an existing file
  *before* any API call is made.
- Missing API key (Spec §9): exits 1 with a no_api_key message;
  never a crash.
- Usage errors: missing required args exit 1.

Tests use `monkeypatch` to stub `week06.critique.critique_image` so no
network call is made. The CLI is exercised via its `main(argv)` entry
point, not via subprocess.

These tests assume the CLI shape:
    python -m week06.critique --model <slug> --out <report.json> <prompts.json>

`prompts.json` entries carry `id, prompt, seed, image_path, answer_key`.
"""
from __future__ import annotations

import json

from week06.checker import ParseResult


def _write_prompts(path) -> None:
    """Write a minimal `prompts.json` with two entries (plant-cell + flower).

    Each entry gets a distinct `image_path` so stubs that route on path
    (e.g., returning one critique for "plant-cell" and another for
    "flower") can disambiguate. `image_path` is unused by the stubbed
    `critique_image`; the strings just need to be distinct.
    """
    path.write_text(json.dumps([
        {
            "id": "plant-cell",
            "prompt": "Diagram of a plant cell.",
            "seed": 42,
            "image_path": "/tmp/plant-cell.png",
            "answer_key": {"nucleus": True, "cell_wall": True},
        },
        {
            "id": "flower",
            "prompt": "Diagram of a flower.",
            "seed": 43,
            "image_path": "/tmp/flower.png",
            "answer_key": {"petal": True, "stem": True},
        },
    ]))


# ============================================================================
# Exit codes (Spec §24)
# ============================================================================

class TestExitCodes:
    def test_exit_0_when_all_images_pass(self, tmp_path, monkeypatch):
        """Every image PASS → exit 0; report is written."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "report.json"

        def fake_critique(image_path, prompt, model, api_key):
            if "plant-cell" in image_path:
                return ParseResult.ok({"nucleus": True, "cell_wall": True})
            return ParseResult.ok({"petal": True, "stem": True})

        monkeypatch.setattr(critique, "critique_image", fake_critique)

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 0
        assert out.exists()

    def test_exit_1_when_any_image_fails(self, tmp_path, monkeypatch):
        """At least one image FAIL → exit 1."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "report.json"

        def fake_critique(image_path, prompt, model, api_key):
            if "plant-cell" in image_path:
                return ParseResult.ok({"nucleus": True, "cell_wall": True})
            # Flower: critique says False but key says True → FAIL.
            return ParseResult.ok({"petal": False, "stem": False})

        monkeypatch.setattr(critique, "critique_image", fake_critique)

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 1

    def test_exit_1_when_critique_is_not_json(self, tmp_path, monkeypatch):
        """A non-JSON critique → image FAIL → exit 1 (never a crash)."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "report.json"

        def fake_critique(image_path, prompt, model, api_key):
            return ParseResult.fail("not_json", excerpt="this is not json")

        monkeypatch.setattr(critique, "critique_image", fake_critique)

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 1
        # Report is still written so the user can inspect the failure.
        assert out.exists()


# ============================================================================
# --out overwrite guard (Spec §24)
# ============================================================================

class TestOutOverwriteGuard:
    def test_refuses_to_overwrite_existing_out(self, tmp_path, monkeypatch):
        """If `--out` exists, the CLI aborts BEFORE making any API call."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "report.json"
        out.write_text('{"existing": true}')

        call_count = 0
        def fake_critique(image_path, prompt, model, api_key):
            nonlocal call_count
            call_count += 1
            return ParseResult.ok({})

        monkeypatch.setattr(critique, "critique_image", fake_critique)

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 1
        assert call_count == 0  # guard fires before any critique_image call
        # Existing file is unchanged.
        assert json.loads(out.read_text()) == {"existing": True}


# ============================================================================
# Missing API key (Spec §9)
# ============================================================================

class TestMissingApiKey:
    def test_exit_1_with_no_api_key(self, tmp_path, monkeypatch):
        """Missing `OPENROUTER_API_KEY` → exit 1; no report, no API calls."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from week06 import critique

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "report.json"

        call_count = 0
        def fake_critique(image_path, prompt, model, api_key):
            nonlocal call_count
            call_count += 1
            return ParseResult.ok({})

        monkeypatch.setattr(critique, "critique_image", fake_critique)

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            "--out", str(out),
            str(prompts),
        ])

        assert exit_code == 1
        assert call_count == 0
        assert not out.exists()


# ============================================================================
# Usage errors (argparse)
# ============================================================================

class TestUsageErrors:
    def test_exit_1_when_missing_model(self, tmp_path, monkeypatch):
        """--model is required → exit 1."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)
        out = tmp_path / "report.json"

        exit_code = critique.main([
            "--out", str(out),
            str(prompts),
        ])
        assert exit_code == 1

    def test_exit_1_when_missing_out(self, tmp_path, monkeypatch):
        """--out is required → exit 1."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        prompts = tmp_path / "prompts.json"
        _write_prompts(prompts)

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            str(prompts),
        ])
        assert exit_code == 1

    def test_exit_1_when_missing_input(self, tmp_path, monkeypatch):
        """Positional input is required → exit 1."""
        from week06 import critique
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        out = tmp_path / "report.json"

        exit_code = critique.main([
            "--model", "minimax/minimax-m3",
            "--out", str(out),
        ])
        assert exit_code == 1

    def test_exit_0_for_help(self):
        """--help → exit 0."""
        from week06 import critique

        exit_code = critique.main(["--help"])
        assert exit_code == 0
