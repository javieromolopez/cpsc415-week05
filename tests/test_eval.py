"""Live evaluation suite — real OpenRouter calls on images/receipt-0[1-5].png.

All tests are marked @pytest.mark.eval and decorated with @needs_key, which
skips them cleanly when OPENROUTER_API_KEY is not set (Spec §13). Run the
full eval with:  pytest -m eval -s -v
( -s so the model-comparison table is visible even on a passing run )
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from receipts.checker import Item, Receipt, check
from receipts.prompts import build_request_body
from receipts.reader import ReadError, read


IMAGES_DIR = Path(__file__).parent.parent / "images"
EXPECTED = json.loads((IMAGES_DIR / "expected.json").read_text())
ALL_RECEIPTS = sorted(IMAGES_DIR.glob("receipt-*.png"))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Rough per-1M-token prices (USD). Verify against OpenRouter's pricing page
# before using for billing; these are the values used in spec.md Cost estimate.
_PRICES = {
    "minimax/minimax-m3": {"input": 0.50, "output": 0.50},
    "xiaomi/mimo-v2.6-flash": {"input": 0.10, "output": 0.10},
}

# Per-model reasoning defaults — must match receipts/reader.py.
_REASONING_DEFAULTS = {
    "minimax/minimax-m3": True,
    "xiaomi/mimo-v2.6-flash": False,
}


needs_key = pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY"),
    reason="model eval (set OPENROUTER_API_KEY to run)",
)


def _merchants_match(actual: str, expected: str) -> bool:
    """Compare merchant names flexibly.

    The model preserves the receipt's printed casing (often ALL CAPS), may
    include the receipt's address line in the merchant header, and may reorder
    tokens. `images/expected.json` holds a short, title-cased version.

    Pass if any of the following holds (case-insensitive, whitespace- and
    punctuation-insensitive):
    - Either string is a substring of the other.
    - The expected tokens are a subset of the actual tokens (handles the
      "address appended to merchant" case where actual has all expected
      tokens plus more).
    """
    a = actual.strip().lower()
    e = expected.strip().lower()

    # Substring containment handles receipts whose extracted name is a prefix
    # or suffix of the expected string, or vice versa.
    if e in a or a in e:
        return True

    # Token-set subset: every expected token must appear in actual.
    actual_tokens = {tok for tok in re.split(r"[^a-z0-9]+", a) if tok}
    expected_tokens = {tok for tok in re.split(r"[^a-z0-9]+", e) if tok}
    if not expected_tokens:
        return False
    return expected_tokens.issubset(actual_tokens)


# ---------- helper for test_model_comparison (needs raw usage) ----------

def _call_with_usage(image_path: Path, model: str) -> tuple[Receipt | None, dict]:
    """Call OpenRouter directly so we can capture `usage` from the response.
    The receipts.reader.read() helper discards usage; for the cost table we
    need prompt/completion tokens, so this is a one-off call path used only
    by test_model_comparison.
    """
    api_key = os.environ["OPENROUTER_API_KEY"]
    image_bytes = image_path.read_bytes()
    body = build_request_body(
        image_bytes,
        model=model,
        reasoning_enabled=_REASONING_DEFAULTS.get(model, True),
    )
    req = Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        envelope = json.loads(resp.read().decode())

    content = envelope["choices"][0]["message"]["content"]
    usage = envelope.get("usage", {})

    # Strip markdown fence if the model added one.
    text = content.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        last_fence = text.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            text = text[first_nl + 1:last_fence].strip()

    data = json.loads(text)
    receipt = Receipt(
        merchant=data["merchant"],
        date=data.get("date"),
        items=[
            Item(name=i["name"], quantity=int(i["quantity"]), unit_price=float(i["unit_price"]))
            for i in data["items"]
        ],
        subtotal=float(data["subtotal"]),
        discount=float(data.get("discount") or 0.0),
        tax=float(data.get("tax") or 0.0),
        total=float(data["total"]),
    )
    return receipt, usage


# ---------- tests ----------

@needs_key
@pytest.mark.eval
def test_schema_on_real_receipts():
    """All 5 receipts produce JSON matching the spec schema (Spec §1).

    Required fields: merchant (non-empty), items (non-empty list),
    subtotal (not None), total (not None).
    """
    failures = []
    for path in ALL_RECEIPTS:
        result = read(str(path))
        if not isinstance(result, Receipt):
            failures.append(f"{path.name}: {type(result).__name__}: {getattr(result, 'detail', '')}")
            continue
        if not result.merchant:
            failures.append(f"{path.name}: missing or empty merchant")
        if not result.items:
            failures.append(f"{path.name}: missing or empty items")
        if result.subtotal is None:
            failures.append(f"{path.name}: missing subtotal")
        if result.total is None:
            failures.append(f"{path.name}: missing total")
    assert not failures, "schema failures:\n  " + "\n  ".join(failures)


@needs_key
@pytest.mark.eval
def test_arithmetic_passes_on_clean_receipts():
    """For each receipt: model output matches expected.json's four listed
    fields (merchant, date, items count, total) AND the Checker passes
    (Spec §10, §11 + Plan Interrogation Q1)."""
    from receipts.checker import check
    failures = []
    for path in ALL_RECEIPTS:
        stem = path.stem
        expected = EXPECTED[f"{stem}.png"]

        result = read(str(path))
        if not isinstance(result, Receipt):
            failures.append(f"{stem}: {type(result).__name__}: {getattr(result, 'detail', '')}")
            continue

        # Compare against expected.json (the four fields it lists).
        # Merchant name: case- and order-insensitive (model preserves printed casing).
        if not _merchants_match(result.merchant, expected["merchant"]):
            failures.append(
                f"{stem}: merchant: model={result.merchant!r} vs expected={expected['merchant']!r}"
            )
        if expected["date"] is not None and result.date != expected["date"]:
            failures.append(
                f"{stem}: date: model={result.date!r} vs expected={expected['date']!r}"
            )
        if len(result.items) != expected["items"]:
            failures.append(
                f"{stem}: items count: model={len(result.items)} vs expected={expected['items']}"
            )
        if abs(result.total - expected["total"]) > 0.01:
            failures.append(
                f"{stem}: total: model={result.total} vs expected={expected['total']}"
            )

        # Run the Checker on the model output.
        report = check(result)
        if not report.passed:
            for r in report.results:
                if r.status == "FAIL":
                    failures.append(f"{stem}: Checker FAIL: {r.detail}")
    assert not failures, "failures:\n  " + "\n  ".join(failures)


@needs_key
@pytest.mark.eval
def test_null_date_on_receipt_05():
    """receipt-05 (CS Club Bake Sale) has no year printed → date is null (Spec §12)."""
    result = read(str(IMAGES_DIR / "receipt-05.png"))
    assert isinstance(result, Receipt), f"not a Receipt: {result!r}"
    assert result.date is None, f"expected null date, got {result.date!r}"


@needs_key
@pytest.mark.eval
def test_reasoning_toggle():
    """With CHAT_REASONING=off, the Reader still extracts a valid Receipt (Spec §14).

    The request-shape toggle is covered by mocked tests in test_reader.py;
    here we verify the end-to-end behavior: with reasoning disabled,
    minimax still returns parseable JSON.
    """
    old = os.environ.get("CHAT_REASONING")
    os.environ["CHAT_REASONING"] = "off"
    try:
        result = read(str(IMAGES_DIR / "receipt-01.png"))
    finally:
        if old is None:
            os.environ.pop("CHAT_REASONING", None)
        else:
            os.environ["CHAT_REASONING"] = old

    assert isinstance(result, Receipt), f"with reasoning off: {result!r}"
    assert result.merchant, "with reasoning off: missing merchant"


@needs_key
@pytest.mark.eval
def test_model_comparison():
    """Run the 5-receipt sweep on both models; print the comparison table
    that drives the default-model decision (Spec §14, Cost-estimate).

    The test passes if at least one model produces a valid Receipt for all
    5 receipts. The comparison table is the actual artifact — run with -s
    so the printed table is visible.
    """
    models = ["minimax/minimax-m3", "xiaomi/mimo-v2.6-flash"]
    summary: dict[str, dict] = {}

    for model in models:
        passes = 0
        tokens_in = 0
        tokens_out = 0
        cost = 0.0
        prices = _PRICES.get(model, {"input": 0, "output": 0})

        # Each model needs its own reasoning setting.
        old_reasoning = os.environ.get("CHAT_REASONING")
        os.environ["CHAT_REASONING"] = "off" if "xiaomi" in model else ""

        for path in ALL_RECEIPTS:
            try:
                receipt, usage = _call_with_usage(path, model)
                if receipt is not None:
                    passes += 1
                tokens_in += usage.get("prompt_tokens", 0)
                tokens_out += usage.get("completion_tokens", 0)
                cost += (
                    usage.get("prompt_tokens", 0) / 1_000_000 * prices["input"]
                    + usage.get("completion_tokens", 0) / 1_000_000 * prices["output"]
                )
            except Exception as e:
                pass  # recorded as a non-pass; surface in the table below

        if old_reasoning is None:
            os.environ.pop("CHAT_REASONING", None)
        else:
            os.environ["CHAT_REASONING"] = old_reasoning

        summary[model] = {
            "passes": passes,
            "total": len(ALL_RECEIPTS),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": cost,
        }

    # Print the comparison table. With -s this is visible; without -s it's
    # captured (and re-printed below via caplog).
    lines = [
        "",
        "=" * 76,
        "MODEL COMPARISON (5-receipt sweep)",
        "=" * 76,
        f"{'Model':<28}{'Pass':<10}{'Tokens In':<14}{'Tokens Out':<14}{'Est. Cost':<10}",
        "-" * 76,
    ]
    for model, s in summary.items():
        lines.append(
            f"{model:<28}"
            f"{s['passes']}/{s['total']:<8}"
            f"{s['tokens_in']:<14}"
            f"{s['tokens_out']:<14}"
            f"${s['cost']:.4f}"
        )
    lines.append("=" * 76)
    lines.append("Cost = rough estimate using placeholder per-1M-token prices.")
    lines.append("Verify against OpenRouter's actual pricing before billing.")
    lines.append("")
    print("\n".join(lines))

    # At least one model must produce all 5 receipts.
    assert any(s["passes"] == s["total"] for s in summary.values()), (
        f"Neither model produced all 5 receipts: {summary}"
    )
