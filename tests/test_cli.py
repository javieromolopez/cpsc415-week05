"""CLI tests (receipts.cli + receipts.py root shim).

Substantive behavior (pass/fail reporting, exit codes, ReadError mapping)
is tested through cli.main() with a stubbed reader. One subprocess smoke
test verifies that `python receipts.py` works as the spec requires (Spec §2).
"""
import subprocess
import sys
from pathlib import Path

import pytest

from receipts import cli
from receipts.checker import Item, Receipt
from receipts.reader import ReadError


# ---------- helpers ----------

PROJECT_ROOT = Path(__file__).parent.parent


def make_receipt(merchant="Test Cafe", date="2026-10-01", total=5.00,
                 items=None, subtotal=None, discount=0.0, tax=0.0) -> Receipt:
    if items is None:
        items = [Item(name="x", quantity=1, unit_price=total)]
    if subtotal is None:
        subtotal = total
    return Receipt(
        merchant=merchant, date=date, items=items,
        subtotal=subtotal, discount=discount, tax=tax, total=total,
    )


def stub_reader(monkeypatch, return_value):
    """Replace receipts.cli.read with a stub that returns `return_value`."""
    monkeypatch.setattr("receipts.cli.read", lambda path: return_value)


# ---------- happy paths ----------

def test_cli_passes_for_clean_receipt(monkeypatch, capsys):
    """Stubbed reader returns a clean Receipt → main() prints PASS, exits 0."""
    receipt = make_receipt(merchant="Test Cafe", date="2026-10-01", total=5.00)
    stub_reader(monkeypatch, receipt)

    exit_code = cli.main(["fake.png"])

    captured = capsys.readouterr()
    assert exit_code == 0
    out = captured.out
    assert "PASS" in out
    assert "Merchant: Test Cafe" in out
    assert "Date: 2026-10-01" in out
    assert "Items: 1" in out
    assert "Total: $5.00" in out


def test_cli_passes_for_null_date(monkeypatch, capsys):
    """date: null → 'Date: (none)' in the report (Spec §12)."""
    receipt = make_receipt(merchant="CS Club Bake Sale", date=None, total=6.50)
    stub_reader(monkeypatch, receipt)

    exit_code = cli.main(["fake.png"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Date: (none)" in captured.out


# ---------- arithmetic failures ----------

def test_cli_fails_for_arithmetic_mismatch(monkeypatch, capsys):
    """Receipt whose items sum != subtotal → FAIL with check name + delta (Spec §4)."""
    receipt = Receipt(
        merchant="Test", date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=10.00)],
        subtotal=9.95, discount=0.0, tax=0.0, total=9.95,
    )
    stub_reader(monkeypatch, receipt)

    exit_code = cli.main(["fake.png"])

    captured = capsys.readouterr()
    assert exit_code == 1
    out = captured.out
    assert "FAIL" in out
    assert "Items sum mismatch" in out
    assert "$10.00" in out   # calculated
    assert "$9.95" in out    # stated
    assert "$0.05" in out    # delta


def test_cli_reports_multiple_failing_checks(monkeypatch, capsys):
    """When both items_sum AND total_check fail, both are printed."""
    receipt = Receipt(
        merchant="Test", date="2026-10-01",
        items=[Item(name="x", quantity=1, unit_price=10.00)],
        subtotal=9.95, discount=0.0, tax=0.0, total=11.00,  # both off
    )
    stub_reader(monkeypatch, receipt)

    exit_code = cli.main(["fake.png"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Items sum mismatch" in captured.out
    assert "Total mismatch" in captured.out


# ---------- ReadError mapping (one parametrized test per reason) ----------

@pytest.mark.parametrize("error,expected_substr", [
    (ReadError("no_api_key"), "OPENROUTER_API_KEY not set"),
    (ReadError("network", "connection refused"), "network error"),
    (ReadError("http", "status 401: Unauthorized"), "status 401"),
    (ReadError("empty"), "empty model response"),
    (ReadError("not_json", "this is not JSON at all"), "not JSON"),
    (ReadError("refusal", "I can't read that image"), "model refused"),
    (ReadError("missing_field", "merchant"), "missing required field: merchant"),
])
def test_cli_fails_for_read_error(monkeypatch, capsys, error, expected_substr):
    """Each ReadError reason maps to a specific FAIL message (Spec Failure-handling table)."""
    stub_reader(monkeypatch, error)

    exit_code = cli.main(["fake.png"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "FAIL" in captured.out
    assert expected_substr in captured.out


# ---------- exit code semantics ----------

def test_cli_exits_zero_on_pass(monkeypatch, capsys):
    stub_reader(monkeypatch, make_receipt())
    assert cli.main(["fake.png"]) == 0


def test_cli_exits_nonzero_on_failure(monkeypatch, capsys):
    stub_reader(monkeypatch, ReadError("no_api_key"))
    assert cli.main(["fake.png"]) == 1


def test_cli_requires_image_path():
    """No image argument → argparse exits with code 2 (Spec §2)."""
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


# ---------- subprocess smoke test (Spec §2) ----------

def test_receipts_py_shim_runs():
    """`python receipts.py` (no args) executes the shim and exits with argparse code 2."""
    result = subprocess.run(
        [sys.executable, "receipts.py"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 2
    assert "image" in (result.stderr + result.stdout).lower()
