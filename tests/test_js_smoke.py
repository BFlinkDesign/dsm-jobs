"""Runtime smoke test: execute the generated page's JS and drive every view.

pytest proves the Python logic; this proves the embedded app script actually
RUNS — catching runtime ReferenceError/TypeError (e.g. a function deleted in a
refactor) that text-based template assertions miss. Skips cleanly where node
isn't installed; CI (ubuntu-latest) has node, so it runs there.
"""

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import find_admin_jobs as faj

_HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verify", "js_smoke.js")
_DOCX_HARNESS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verify", "docx_roundtrip.mjs"
)


def _mk_row(**over):
    # Backend row shape that write_html() consumes (mirrors test_portal_and_features).
    row = {
        "id": "x1",
        "title": "Office Clerk",
        "company": "Hy-Vee",
        "location": "Grimes, IA",
        "hourly_min": 20.0,
        "hourly_max": 22.0,
        "predicted": False,
        "verdict": "meets",
        "created": "2026-06-10",
        "url": "https://example.com/j/1",
        "source": "local",
        "description": "Filing. Will train.",
    }
    row.update(over)
    return row


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_generated_js_runs_without_runtime_errors(tmp_path):
    out = tmp_path / "index.html"
    # A couple of rows (one stale, one fresh) so freshness/age paths execute too.
    faj.write_html(
        [_mk_row(), _mk_row(id="x2", created="2026-04-01", verdict="unlisted", predicted=True)],
        2, 3, str(out), "2026-06-18 06:00", contact="Brady", portal_cfg=None,
    )
    res = subprocess.run(
        ["node", _HARNESS, str(out)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"JS smoke failed:\n{res.stdout}\n{res.stderr}"
    assert "SMOKE OK" in res.stdout, res.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_generated_js_runs_signed_in(tmp_path):
    """Portal-configured build: the harness stubs Supabase with a signed-in
    session, so the account popover / showIn / chat-mount code actually runs."""
    out = tmp_path / "index.html"
    faj.write_html(
        [_mk_row()], 1, 2, str(out), "2026-06-18 06:00", contact="Brady",
        portal_cfg={"url": "https://abc123.supabase.co", "key": "sb_publishable_x"},
    )
    res = subprocess.run(
        ["node", _HARNESS, str(out)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"signed-in JS smoke failed:\n{res.stdout}\n{res.stderr}"
    assert "SMOKE OK" in res.stdout, res.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_docx_writer_round_trips_through_the_docx_reader():
    """The tailored-résumé Download button must produce a real .docx (the City
    of Des Moines portal, and most ATS upload widgets, reject .txt). This runs
    the zero-dependency writer (docx.ts) through the app's own hand-rolled
    reader (resume.ts's extractResumeFile) via a node type-stripping harness.

    Needs a node new enough to strip TS types natively with no flags (node
    >=22.18 is confirmed to work here); older node exits with a syntax/parse
    error on the `.ts` import instead of running the harness, so that's
    treated as a skip rather than a failure — this test isn't trying to pin
    a minimum node version, just to prove the writer/reader agree when the
    runtime supports it.
    """
    res = subprocess.run(
        ["node", _DOCX_HARNESS],
        capture_output=True, text=True, timeout=60,
    )
    if res.returncode != 0:
        stderr = res.stderr or ""
        strip_types_markers = (
            "Unknown file extension",
            "ERR_UNKNOWN_FILE_EXTENSION",
            "SyntaxError",
            "--experimental-strip-types",
            "typescript",
        )
        if any(marker in stderr for marker in strip_types_markers):
            pytest.skip(f"node build can't strip TS types natively: {stderr.strip()[:200]}")
        pytest.fail(f"docx round-trip harness failed:\n{res.stdout}\n{stderr}")
    assert "DOCX OK" in res.stdout, res.stdout
