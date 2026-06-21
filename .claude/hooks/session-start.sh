#!/bin/bash
# SessionStart hook for Claude Code on the web: install the dev tooling so ruff,
# pytest, and mypy work immediately in a fresh remote container. Runtime is
# stdlib-only, so this is dev/CI tooling only. No-op outside a remote session.
# Synchronous + idempotent + non-interactive.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"
python -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt
echo "dev tooling ready: ruff, pytest, pytest-timeout, mypy"
