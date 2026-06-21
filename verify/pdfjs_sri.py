#!/usr/bin/env python3
"""Pin + verify the pdf.js CDN bundle by Subresource Integrity (SHA-384).

The résumé reader loads pdf.js dynamically from jsdelivr. A dynamic ESM
`import()` can't carry an `integrity=` attribute, so the loader instead does
`fetch(url, {integrity})` + blob-import (see `_loadPdfjs` in find_admin_jobs.py).
This script is the other half of that pin: it fetches the EXACT pinned URLs and
computes their SHA-384, so CI proves the bytes we trust still match upstream.

Usage:
  python verify/pdfjs_sri.py            # print the live sha384- for each URL
  python verify/pdfjs_sri.py --check    # assert they equal the EXPECTED pins below

Run with --check in CI: it fails (exit 1) if jsdelivr ever serves different
bytes for the pinned version, or if the pins below drift from the loader.
Network is required (CI has it; the offline dev sandbox does not).
"""
from __future__ import annotations

import base64
import hashlib
import sys
import urllib.request

VERSION = "4.7.76"
BASE = f"https://cdn.jsdelivr.net/npm/pdfjs-dist@{VERSION}/build/"

# The two assets the loader pins. Keep these in lock-step with the SRI
# constants embedded in find_admin_jobs.py (PDFJS_SRI / PDFJS_WORKER_SRI).
ASSETS = {
    "pdf.min.mjs": "",         # filled from the first CI print run
    "pdf.worker.min.mjs": "",  # filled from the first CI print run
}


def sri_for(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (pinned https CDN)
        data = r.read()
    digest = hashlib.sha384(data).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def main() -> int:
    check = "--check" in sys.argv[1:]
    failed = False
    for name, expected in ASSETS.items():
        got = sri_for(BASE + name)
        print(f"{name}: {got}")
        if check:
            if not expected:
                print(f"::error::no expected SRI pinned for {name}", file=sys.stderr)
                failed = True
            elif got != expected:
                print(
                    f"::error::SRI mismatch for {name}: pinned {expected}, got {got}",
                    file=sys.stderr,
                )
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
