"""The CLI: arg parsing, glue between Reader and Checker, formatted report.

`python receipts.py <image>` runs one image through the Reader, runs the
Checker on the result, prints a structured PASS/FAIL report, and exits
with 0 (PASS) or 1 (FAIL). argparse handles its own error exits (code 2).

Report shape (Spec §3, §4, §12):
    PASS
    Merchant: <merchant>
    Date: <YYYY-MM-DD | (none)>
    Items: <count>
    Total: $<total>

    FAIL — <reason>            (ReadError case, single line)
    FAIL — <check detail>      (arithmetic case, one line per failing check)
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from receipts.checker import Report, check
from receipts.reader import ReadError, read


# ---------- formatters ----------

def _format_pass(receipt) -> str:
    date_str = receipt.date if receipt.date is not None else "(none)"
    return "\n".join([
        "PASS",
        f"Merchant: {receipt.merchant}",
        f"Date: {date_str}",
        f"Items: {len(receipt.items)}",
        f"Total: ${receipt.total:.2f}",
    ])


def _format_read_error(error: ReadError) -> str:
    if error.reason == "no_api_key":
        return "OPENROUTER_API_KEY not set"
    if error.reason == "network":
        return f"network error: {error.detail or ''}".rstrip(": ")
    if error.reason == "http":
        return error.detail or "HTTP error"
    if error.reason == "empty":
        return "empty model response"
    if error.reason == "not_json":
        return f"could not parse model response: not JSON ({error.detail or ''})"
    if error.reason == "refusal":
        return f"model refused ({error.detail or ''})"
    if error.reason == "missing_field":
        return f"missing required field: {error.detail or ''}"
    return error.detail or error.reason


# ---------- main entry point ----------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="receipts",
        description="Read a receipt image, check its arithmetic, print a report.",
    )
    parser.add_argument("image", help="Path to a receipt image (PNG or JPEG).")
    args = parser.parse_args(argv)

    result = read(args.image)

    if isinstance(result, ReadError):
        print(f"FAIL — {_format_read_error(result)}")
        return 1

    report: Report = check(result)

    if report.passed:
        print(_format_pass(result))
        return 0

    # One FAIL line per failing check.
    for r in report.results:
        if r.status == "FAIL":
            print(f"FAIL — {r.detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
