#!/usr/bin/env bash
# Deterministic setup for the camera self-verifier (verify/camera.py).
#
# Installs the pinned Playwright (verify/requirements.txt) and its BUNDLED
# Chromium — one fixed revision per Playwright version — so the camera renders
# reproducibly on any machine that can reach the Playwright browser CDN
# (your local CLI, CI, or a web environment whose network policy permits it).
#
# Use as a Claude-Code-on-the-web environment setup script, or run by hand:
#     bash verify/setup-web.sh && python verify/camera.py
#
# If the browser download is blocked by a restrictive egress policy, this fails
# LOUDLY here (not silently at render time); use the CI camera workflow instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "ERROR: python3 or python not found on PATH" >&2
  exit 1
fi

echo "==> Installing pinned Playwright"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -r "$ROOT/verify/requirements.txt"

echo "==> Installing the pinned Chromium revision (+ system deps)"
"$PYTHON" -m playwright install --with-deps chromium

echo "==> Done. The camera will use Playwright's pinned bundled Chromium."
echo "    Run:  $PYTHON verify/camera.py"
