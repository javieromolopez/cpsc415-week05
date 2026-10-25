"""Pure-code tests for the Checker (receipts.checker).

No model, no network. Builds Receipt objects inline from fixture dicts.
"""
from dataclasses import dataclass

import pytest

from receipts.checker import CheckResult, Item, Receipt, Report, check


# ---------- helpers ----------

def make_receipt(d: dict) -> Receipt:
    """Build a Receipt from a fixture-shaped dict. Used for every test."""
    return Receipt(
        merchant=d["merchant"],
        date=d.get("date"),
        items=[Item(**item) for item in d["items"]],
        subtotal=d["subtotal"],
        discount=d.get("discount") or 0.0,
        tax=d.get("tax") or 0.0,
        total=d["total"],
    )


# ---------- happy paths ----------

def test_clean_receipt_passes(receipt_dicts):
    """receipt-01: items sum exactly, total = subtotal - 0 + tax. All checks PASS."""
    r = make_receipt(receipt_dicts["receipt-01"])
    report = check(r)

    assert report.passed
    assert not report.failed
    assert {res.name for res in report.results} == {"merchant", "items_present", "subtotal", "total", "items_sum", "total_check"}


def test_receipt_with_discount_and_tax_passes(receipt_dicts):
    """receipt-02: 6 items, non-zero discount and tax, arithmetic tight. All PASS."""
    r = make_receipt(receipt_dicts["receipt-02"])
    report = check(r)

    assert report.passed
    items_sum = next(res for res in report.results if res.name == "items_sum")
    assert items_sum.status == "PASS"
    total = next(res for res in report.results if res.name == "total_check")
    assert total.status == "PASS"


def test_receipt_with_null_date_passes(receipt_dicts):
    """receipt-05: date is null (no year printed). Null date is NOT a problem."""
    r = make_receipt(receipt_dicts["receipt-05"])
    assert r.date is None

    report = check(r)
    assert report.passed
    # No "date" check result exists — null is silently accepted, not flagged.
    assert all(res.name != "date" for res in report.results)


# ---------- missing fields ----------

def test_missing_merchant_fails():
    """A receipt with no merchant is reported as missing required field."""
    r = Receipt(
        merchant=None,
        date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=1.0)],
        subtotal=1.0,
        discount=0.0,
        tax=0.0,
        total=1.0,
    )
    report = check(r)
    assert report.failed
    merchant = next(res for res in report.results if res.name == "merchant")
    assert merchant.status == "FAIL"
    assert "merchant" in (merchant.detail or "")


def test_missing_subtotal_skips_items_sum_check():
    """If subtotal is missing, the items-sum check is SKIPPED (not FAIL)."""
    r = Receipt(
        merchant="Test",
        date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=1.0)],
        subtotal=None,
        discount=0.0,
        tax=0.0,
        total=1.0,
    )
    report = check(r)
    subtotal_check = next(res for res in report.results if res.name == "subtotal")
    items_sum = next(res for res in report.results if res.name == "items_sum")
    assert subtotal_check.status == "FAIL"
    assert items_sum.status == "SKIPPED"


# ---------- arithmetic ----------

def test_items_sum_mismatch_fails():
    """items sum differs from subtotal by $0.05 — fails strictly."""
    r = Receipt(
        merchant="Test",
        date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=10.00)],
        subtotal=9.95,  # off by $0.05
        discount=0.0,
        tax=0.0,
        total=9.95,
    )
    report = check(r)
    items_sum = next(res for res in report.results if res.name == "items_sum")
    assert items_sum.status == "FAIL"
    assert items_sum.expected == 9.95
    assert items_sum.calculated == 10.00
    assert items_sum.delta == pytest.approx(0.05)


def test_total_mismatch_fails():
    """subtotal - discount + tax != total by $0.50 — fails strictly."""
    r = Receipt(
        merchant="Test",
        date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=10.00)],
        subtotal=10.00,
        discount=0.0,
        tax=0.50,
        total=11.00,  # stated, but 10.00 + 0.50 = 10.50 — off by $0.50
    )
    report = check(r)
    total = next(res for res in report.results if res.name == "total_check")
    assert total.status == "FAIL"
    assert total.expected == 11.00
    assert total.calculated == pytest.approx(10.50)
    assert abs(total.delta) == pytest.approx(0.50)


def test_items_sum_within_rounding_tolerance_passes():
    """items sum differs from subtotal by $0.009, which exceeds strict tolerance
    ($0.005) but is within the documented ±$0.01 tolerance because one item
    has a non-cent-exact product (3 × 4.333 = 12.999 ≠ 13.00). PASS via tolerance."""
    r = Receipt(
        merchant="Test",
        date="2026-10-01",
        items=[Item(name="x", quantity=3, unit_price=4.333)],
        subtotal=12.99,  # line-item sum 12.999, stated as 12.99 → delta 0.009
        discount=0.0,
        tax=0.0,
        total=12.99,
    )
    report = check(r)
    items_sum = next(res for res in report.results if res.name == "items_sum")
    assert items_sum.status == "PASS"
    assert items_sum.delta is not None
    assert 0.008 < items_sum.delta < 0.010  # ~$0.009
    assert "tolerance" in (items_sum.detail or "").lower()


# ---------- optional fields ----------

def test_null_discount_defaults_to_zero(receipt_dicts):
    """discount = null on the wire is treated as 0.0, not a FAIL."""
    d = dict(receipt_dicts["receipt-01"])
    d["discount"] = None
    d["total"] = d["subtotal"] + d["tax"]  # total stays consistent with discount=0
    r = make_receipt(d)
    report = check(r)
    assert report.passed


def test_missing_tax_key_defaults_to_zero():
    """If the tax key is absent, default to 0.0 and total check still works."""
    r = Receipt(
        merchant="Test",
        date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=5.00)],
        subtotal=5.00,
        discount=0.0,
        tax=0.0,  # absent would default to 0 via dataclass default; here we pass 0 explicitly
        total=5.00,
    )
    report = check(r)
    assert report.passed
