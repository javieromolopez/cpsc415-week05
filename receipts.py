#!/usr/bin/env python3
"""Root shim so `python receipts.py <image>` works as Spec §2 requires.

The real CLI lives in receipts/cli.py; this file is just a one-line entry
point at the project root.
"""
import sys

from receipts.cli import main

if __name__ == "__main__":
    sys.exit(main())
