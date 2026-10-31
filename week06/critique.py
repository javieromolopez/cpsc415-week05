"""The Critic-checker CLI (Spec §24).

Usage:
    python -m week06.critique --model <slug> --out <report.json> <prompts.json>

For each entry in `<prompts.json>`:
    1. Call `critique_image(image_path, prompt, model, api_key)` to get a
       `ParseResult`. This is the only function that talks to the network
       (tests stub it so the suite runs without `OPENROUTER_API_KEY`).
    2. Build a per-image `Report` from the parsed critique + the
       hand-coded `answer_key`.
    3. Print one PASS/FAIL line + a per-part table on stdout.
    4. Accumulate the row into the on-disk report.

Exit codes (Spec §24):
    0  — every image passed.
    1  — at least one FAIL, refused `--out`, missing `OPENROUTER_API_KEY`,
         or a usage error (argparse).

`--out` overwrite guard: the script aborts *before* any API call if the
file already exists (Spec §24). The per-image verdict is FAIL on a
non-JSON critique (Spec §17), with the reason surfaced in the report.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import httpx

from week06.checker import ParseResult, build_report, parse_critique

# ============================================================================
# Module-level function (stubbed by tests; real impl below)
# ============================================================================

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def build_critique_prompt(diagram_prompt: str, answer_key: dict) -> str:
    """Build the prompt sent to the vision model.

    Combines the diagram description with a per-part checklist and a
    JSON-only output instruction. The model is asked to return a JSON
    object whose keys are the part names from `answer_key` and whose
    values are booleans (true = present, false = absent).
    """
    parts = list(answer_key.keys())
    parts_list = "\n".join(f"- {p}" for p in parts)
    example_json = "{" + ", ".join(f'"{p}": true' for p in parts) + "}"
    return (
        f"{diagram_prompt}\n\n"
        "Look carefully at the image. For each part below, decide "
        "whether it is present (true) or absent (false) in the diagram.\n\n"
        f"Parts to check:\n{parts_list}\n\n"
        "Return your answer as JSON only — no prose, no markdown fences. "
        f"One boolean key per part, exactly matching these names. "
        f"Example format:\n{example_json}"
    )


def critique_image(image_path: str, prompt: str, model: str, api_key: str) -> ParseResult:
    """Send an image + critique prompt to the vision model and parse the JSON reply.

    The only function that touches the network for critique. Tests stub
    it via `monkeypatch.setattr(week06.critique, "critique_image", ...)`
    so the CLI can be exercised offline.

    `prompt` is the full critique prompt — the caller is responsible
    for building it (use `build_critique_prompt` for the standard
    checklist shape). The function encodes the image as a base64 data
    URL and posts to the chat-completions endpoint with the OpenAI-
    compatible multimodal `content` list.

    Implementation note: uses `httpx` directly. The `openrouter` SDK's
    `chat.send(...)` does the same thing with a more verbose typed
    signature; `httpx` is already a transitive dep of `openrouter`.
    """
    image_bytes = Path(image_path).read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                        },
                    },
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        payload = resp.json()

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ParseResult.fail("empty", excerpt="<unexpected response shape>")

    if not content or not str(content).strip():
        return ParseResult.fail("empty", excerpt="<empty model reply>")

    return parse_critique(str(content))


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


def write_report(path: str, report_data: dict) -> None:
    """Write the per-critic report JSON to `path`.

    Raises `FileExistsError` if `path` already exists (the `--out` guard).
    Creates parent directories as needed.
    """
    p = Path(path)
    if p.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)


# ============================================================================
# Per-image pipeline
# ============================================================================

def process_entry(entry: dict, model: str, api_key: str) -> dict:
    """Critique one entry, build a Report, return the row that goes into the JSON report.

    Shape:
        {
            "id": <str>,
            "model": <str>,
            "verdict": "PASS" | "FAIL",
            "rows": [{"part", "expected", "critiqued", "match"}, ...],
            "critique_failed": bool,
            "critique_excerpt": str | None,
        }

    When the critique itself is not parseable (Spec §17), a single
    `"(critique)"` row is recorded with `critique_failed=True` and the
    excerpt attached for debugging.
    """
    image_path = entry["image_path"]
    diagram_prompt = entry["prompt"]
    answer_key = entry["answer_key"]
    entry_id = entry.get("id", "<unknown>")

    critique_prompt = build_critique_prompt(diagram_prompt, answer_key)
    parse_result = critique_image(image_path, critique_prompt, model, api_key)

    if parse_result.failed:
        return {
            "id": entry_id,
            "model": model,
            "verdict": "FAIL",
            "rows": [{
                "part": "(critique)",
                "expected": "(parseable JSON)",
                "critiqued": parse_result.reason or "unknown",
                "match": "✗",
            }],
            "critique_failed": True,
            "critique_excerpt": parse_result.excerpt,
        }

    report = build_report(answer_key, parse_result.value)
    return {
        "id": entry_id,
        "model": model,
        "verdict": report.verdict,
        "rows": [
            {
                "part": row.part,
                "expected": row.expected,
                "critiqued": row.critiqued,
                "match": row.match,
            }
            for row in report.rows
        ],
        "critique_failed": False,
        "critique_excerpt": None,
    }


def print_report(report_data: dict) -> None:
    """Print one PASS/FAIL line + per-part table for an image on stdout."""
    print(f"{report_data['verdict']} — {report_data['id']} ({report_data['model']})")
    for row in report_data["rows"]:
        print(
            f"  {row['match']}  {row['part']}: "
            f"expected={row['expected']!r}, critiqued={row['critiqued']!r}"
        )


# ============================================================================
# CLI
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="week06.critique",
        description=(
            "Critique Week 6 diagrams against the hand-coded answer key. "
            "Prints per-image PASS/FAIL + per-part table; writes a per-critic "
            "report JSON to --out."
        ),
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Vision model slug (e.g., minimax/minimax-m3).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Path to the per-critic report JSON. Refuses to overwrite.",
    )
    parser.add_argument(
        "input",
        help="Path to prompts.json (manifest.json support is in a future commit).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (0 or 1).

    Order of checks (Spec §24 + Spec §9):
        1. argparse — handles `--help` and missing required args.
        2. `--out` overwrite guard — refuse before any API call.
        3. `OPENROUTER_API_KEY` — refuse before any API call.
        4. Read `prompts.json`.
        5. For each entry: critique, build report, print.
        6. Write the on-disk report.
        7. Return 0 if every image PASS, 1 otherwise.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse calls sys.exit() on --help (0) or on error (2). Map
        # to the CLI's 0/1 contract.
        return 0 if e.code == 0 else 1

    # --out overwrite guard (before any API call).
    if Path(args.out).exists():
        print(
            f"FAIL — refusing to overwrite existing --out file: {args.out}",
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

    # Process each entry.
    report_entries: list[dict] = []
    any_failed = False
    for entry in entries:
        row = process_entry(entry, args.model, api_key)
        report_entries.append(row)
        if row["verdict"] != "PASS":
            any_failed = True
        print_report(row)

    # Write the on-disk report.
    report_data = {
        "model": args.model,
        "verdict": "FAIL" if any_failed else "PASS",
        "entries": report_entries,
    }
    try:
        write_report(args.out, report_data)
    except FileExistsError as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1

    return 0 if not any_failed else 1
