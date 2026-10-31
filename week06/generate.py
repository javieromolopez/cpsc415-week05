"""The Generator CLI (Spec §Generator).

Usage:
    python -m week06.generate --model <slug> --out <dir> <prompts.json>

For each entry in `<prompts.json>`:
    1. Call `generate_image(prompt, seed, model, api_key)` to get a
       `GeneratedImage` (PNG bytes + media type + cost). This is the
       only function that talks to the network; tests stub it so the
       suite runs without `OPENROUTER_API_KEY`.
    2. Write the PNG to `<out>/images/<id>.png`.
    3. Accumulate the row into the manifest.

After all entries:
    Write the manifest to `<out>/images/manifest.json` with one row per
    image: `{id, prompt, seed, model, image_path, media_type, cost_usd}`.
    `image_path` is relative to `<out>` (e.g., `images/plant-cell.png`).

Exit codes:
    0  — every image written + manifest written.
    1  — refused `--out`, missing API key, or a usage error.

`--out` overwrite guard: the script aborts *before* any API call if
`<out>/images/` already exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ============================================================================
# Module-level type + function (stubbed by tests)
# ============================================================================

@dataclass(frozen=True)
class GeneratedImage:
    """The result of a single image-generation call.

    `data` is the raw image bytes (e.g., PNG). `media_type` is the MIME
    type the API returned (e.g., `image/png`). `cost_usd` is the
    per-image cost reported by the API (None if unknown).
    """
    data: bytes
    media_type: str
    cost_usd: float | None


def generate_image(
    prompt: str,
    seed: int,
    model: str,
    api_key: str,
) -> GeneratedImage:
    """Call the OpenRouter Image API and return the image data + metadata.

    The only function that touches the network. Tests stub it via
    `monkeypatch.setattr(week06.generate, "generate_image", ...)` so
    the CLI can be exercised offline. The real implementation lands in
    a future commit; for now it raises `NotImplementedError`, which
    `main` surfaces as a structured FAIL row rather than a crash.
    """
    raise NotImplementedError(
        "generate_image is not implemented in this commit; "
        "use a stub for offline tests."
    )


# ============================================================================
# IO helpers
# ============================================================================

def load_prompts(path: str) -> list[dict]:
    """Read `prompts.json` and return its list of entries."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise TypeError(f"prompts.json must be a list, got {type(data).__name__}")
    return data


def write_manifest(path: Path, manifest: list[dict]) -> None:
    """Write the manifest JSON to `path`. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))


# ============================================================================
# Per-entry pipeline
# ============================================================================

def process_entry(entry: dict, model: str, api_key: str) -> tuple[GeneratedImage, dict]:
    """Generate one image; return `(image, manifest_row)`.

    The manifest row carries `id`, `prompt`, `seed`, `model`, and a
    relative `image_path`. `media_type` and `cost_usd` come from the
    `GeneratedImage`.
    """
    entry_id = entry["id"]
    prompt = entry["prompt"]
    seed = entry["seed"]

    result = generate_image(prompt, seed, model, api_key)

    manifest_row = {
        "id": entry_id,
        "prompt": prompt,
        "seed": seed,
        "model": model,
        "image_path": f"images/{entry_id}.png",
        "media_type": result.media_type,
        "cost_usd": result.cost_usd,
    }
    return result, manifest_row


# ============================================================================
# CLI
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="week06.generate",
        description=(
            "Generate Week 6 diagrams from a prompt file. Writes one PNG "
            "per entry to <out>/images/<id>.png and a manifest to "
            "<out>/images/manifest.json. Refuses to overwrite an existing "
            "<out>/images/."
        ),
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Image model slug (e.g., black-forest-labs/flux.2-klein-4b).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory; manifest + images go under <out>/images/.",
    )
    parser.add_argument(
        "input",
        help="Path to prompts.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (0 or 1).

    Order of checks:
        1. argparse — handles `--help` and missing required args.
        2. `--out` overwrite guard — refuse before any API call.
        3. `OPENROUTER_API_KEY` — refuse before any API call.
        4. Read `prompts.json`.
        5. For each entry: generate image, write PNG, accumulate row.
        6. Write the manifest.
        7. Return 0 if every image was written.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse calls sys.exit() on --help (0) or on error (2).
        return 0 if e.code == 0 else 1

    out_path = Path(args.out)
    images_dir = out_path / "images"

    # --out overwrite guard (before any API call).
    if images_dir.exists():
        print(
            f"FAIL — refusing to overwrite existing --out/images: {images_dir}",
            file=sys.stderr,
        )
        return 1

    # API key.
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("FAIL — OPENROUTER_API_KEY not set", file=sys.stderr)
        return 1

    # Read prompts.json.
    try:
        entries = load_prompts(args.input)
    except (OSError, TypeError) as e:
        print(f"FAIL — could not read {args.input}: {e}", file=sys.stderr)
        return 1

    # Generate each image.
    manifest: list[dict] = []
    for entry in entries:
        image, manifest_row = process_entry(entry, args.model, api_key)
        # Write the PNG.
        png_path = images_dir / f"{entry['id']}.png"
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(image.data)
        manifest.append(manifest_row)
        print(
            f"OK  — {manifest_row['id']} ({manifest_row['model']}) "
            f"cost={manifest_row['cost_usd']}"
        )

    # Write the manifest.
    try:
        write_manifest(images_dir / "manifest.json", manifest)
    except OSError as e:
        print(f"FAIL — could not write manifest: {e}", file=sys.stderr)
        return 1

    print(f"PASS — wrote {len(manifest)} images + manifest to {images_dir}")
    return 0
