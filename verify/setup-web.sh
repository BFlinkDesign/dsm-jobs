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

echo "==> Installing pinned Playwright"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r "$ROOT/verify/requirements.txt"

echo "==> Installing the pinned Chromium revision (+ system deps)"
python -m playwright install --with-deps chromium

echo "==> Done. The camera will use Playwright's pinned bundled Chromium."
echo "    Run:  python verify/camera.py"
