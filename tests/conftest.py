"""Shared pytest fixtures: load the five hand-written receipt JSON files and
the ground-truth images/expected.json. Pure stdlib + pytest; no project code.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
IMAGES = ROOT / "images"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def expected() -> dict:
    """images/expected.json — the ground-truth answer key (the agent must not edit)."""
    return json.loads((IMAGES / "expected.json").read_text())


@pytest.fixture
def receipt_dicts() -> dict[str, dict]:
    """All five hand-written fixtures as raw dicts, keyed by file stem ('receipt-01')."""
    return {
        p.stem: json.loads(p.read_text())
        for p in sorted(FIXTURES.glob("receipt-*.json"))
    }
