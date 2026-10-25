"""The Checker component: validates a parsed Receipt and reports discrepancies.

Pure deterministic Python. No network, no model.

Two arithmetic checks (Spec §10, §11):
    1. items_sum  — sum(quantity × unit_price for each item) vs subtotal
    2. total_check — subtotal − discount + tax vs total

Strict by default (abs_tol=0.005 ≈ half a cent, accounting for float repr).
±$0.01 tolerance is applied ONLY when at least one item has a non-cent-exact
product (n × x ≠ round(n × x, 2)) — the documented quantity-rounding case
from the intent (e.g., 3 × $4.333 = $12.999 rounded to $13.00 on the receipt).

Field-presence checks (Spec §5):
    - merchant, items, subtotal, total are REQUIRED; missing → FAIL.
    - discount and tax are OPTIONAL; null / missing → treated as 0.0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------- types ----------

@dataclass(frozen=True)
class Item:
    name: str
    quantity: int
    unit_price: float


@dataclass(frozen=True)
class Receipt:
    merchant: Optional[str]
    date: Optional[str]
    items: list[Item]
    subtotal: Optional[float]
    total: Optional[float]
    discount: float = 0.0
    tax: float = 0.0


@dataclass(frozen=True)
class CheckResult:
    name: str                       # e.g., "merchant", "items_sum", "total_check"
    status: str                     # PASS | FAIL | SKIPPED
    expected: Optional[float] = None
    calculated: Optional[float] = None
    delta: Optional[float] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class Report:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(r.status == "FAIL" for r in self.results)

    @property
    def failed(self) -> bool:
        return any(r.status == "FAIL" for r in self.results)


# ---------- helpers ----------

def _has_rounding_case(items: list[Item]) -> bool:
    """True iff any item's quantity × unit_price is not representable exactly in cents.

    The intent's example: 3 × $4.333 = $12.999, which the receipt rounds to $13.00.
    That $0.001 per-item rounding artifact is what unlocks the ±$0.01 tolerance.
    """
    for item in items:
        raw = item.quantity * item.unit_price
        if abs(raw - round(raw, 2)) > 1e-9:
            return True
    return False


# ---------- main entry point ----------

def check(receipt: Receipt) -> Report:
    """Run all checks against `receipt`. Returns a Report with one row per check."""
    results: list[CheckResult] = []

    # 1. Required-field presence
    results.append(CheckResult(
        name="merchant",
        status="PASS" if receipt.merchant else "FAIL",
        detail=None if receipt.merchant else "Missing required field: merchant",
    ))
    items_present = bool(receipt.items)
    results.append(CheckResult(
        name="items_present",
        status="PASS" if items_present else "FAIL",
        detail=None if items_present else "Missing required field: items",
    ))
    subtotal_present = receipt.subtotal is not None
    results.append(CheckResult(
        name="subtotal",
        status="PASS" if subtotal_present else "FAIL",
        detail=None if subtotal_present else "Missing required field: subtotal",
    ))
    total_present = receipt.total is not None
    results.append(CheckResult(
        name="total",
        status="PASS" if total_present else "FAIL",
        detail=None if total_present else "Missing required field: total",
    ))

    # 2. items_sum check
    results.append(_check_items_sum(receipt, items_present, subtotal_present))

    # 3. total check
    results.append(_check_total(receipt, subtotal_present, total_present, items_present))

    return Report(results=results)


def _check_items_sum(receipt: Receipt, items_present: bool, subtotal_present: bool) -> CheckResult:
    if not items_present:
        return CheckResult(name="items_sum", status="SKIPPED", detail="requires items")
    if not subtotal_present:
        return CheckResult(name="items_sum", status="SKIPPED", detail="requires subtotal")

    calculated = sum(item.quantity * item.unit_price for item in receipt.items)
    delta = calculated - (receipt.subtotal or 0.0)
    abs_delta = abs(delta)

    if abs_delta < 0.005:
        return CheckResult(
            name="items_sum", status="PASS",
            expected=receipt.subtotal, calculated=calculated, delta=round(delta, 4),
        )
    if abs_delta <= 0.01 and _has_rounding_case(receipt.items):
        return CheckResult(
            name="items_sum", status="PASS",
            expected=receipt.subtotal, calculated=calculated, delta=round(delta, 4),
            detail="rounding tolerance applied (±$0.01, per-line product not cent-exact)",
        )
    return CheckResult(
        name="items_sum", status="FAIL",
        expected=receipt.subtotal, calculated=calculated, delta=round(delta, 4),
        detail=(
            f"Items sum mismatch: calculated ${calculated:.2f} vs stated "
            f"${receipt.subtotal:.2f}, delta ${delta:.2f}"
        ),
    )


def _check_total(receipt: Receipt, subtotal_present: bool, total_present: bool, items_present: bool) -> CheckResult:
    if not subtotal_present:
        return CheckResult(name="total_check", status="SKIPPED", detail="requires subtotal")
    if not total_present:
        return CheckResult(name="total_check", status="SKIPPED", detail="requires total")

    calculated = (receipt.subtotal or 0.0) - (receipt.discount or 0.0) + (receipt.tax or 0.0)
    delta = calculated - (receipt.total or 0.0)
    abs_delta = abs(delta)

    if abs_delta < 0.005:
        return CheckResult(
            name="total_check", status="PASS",
            expected=receipt.total, calculated=calculated, delta=round(delta, 4),
        )
    if abs_delta <= 0.01 and items_present and _has_rounding_case(receipt.items):
        return CheckResult(
            name="total_check", status="PASS",
            expected=receipt.total, calculated=calculated, delta=round(delta, 4),
            detail="rounding tolerance applied (±$0.01, per-line product not cent-exact)",
        )
    return CheckResult(
        name="total_check", status="FAIL",
        expected=receipt.total, calculated=calculated, delta=round(delta, 4),
        detail=(
            f"Total mismatch: calculated ${calculated:.2f} vs stated "
            f"${receipt.total:.2f}, delta ${delta:.2f}"
        ),
    )
