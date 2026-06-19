"""CSS hygiene gate: the generated stylesheet must parse + transpile cleanly.

Runs verify/css/lint_css.py (Lightning CSS validate/transpile + stylelint).
Skips cleanly where node or the dev toolchain isn't installed; CI installs it.
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "verify", "css", "lint_css.py")
HAS_TOOLS = os.path.isdir(os.path.join(ROOT, "verify", "css", "node_modules"))


@pytest.mark.skipif(shutil.which("node") is None or not HAS_TOOLS,
                    reason="node / verify/css toolchain not installed")
def test_generated_css_validates_with_lightningcss():
    res = subprocess.run([sys.executable, HARNESS], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"CSS lint failed:\n{res.stdout}\n{res.stderr}"
    assert "CSS LINT OK" in res.stdout, res.stdout
