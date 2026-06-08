"""Unit + smoke tests for find_admin_jobs. No network calls."""

import os
import subprocess
import sys

# Import the module under test (repo root is one level up from tests/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import find_admin_jobs as fa  # noqa: E402


# ── pure helpers ──────────────────────────────────────────────────────


def test_to_hourly_basic():
    assert fa.to_hourly(41600) == 20.0  # 41600 / 2080
    assert fa.to_hourly(39520) == 19.0  # exactly the floor


def test_to_hourly_handles_none_and_garbage():
    assert fa.to_hourly(None) is None
    assert fa.to_hourly("not a number") is None


def test_title_excluded_blocks_it_roles():
    assert fa.title_excluded("Network Administrator") is True
    assert fa.title_excluded("Senior Software Engineer") is True
    assert fa.title_excluded("Administrative Assistant") is False
    assert fa.title_excluded("Receptionist") is False


def test_requires_degree_detection():
    assert (
        fa.requires_degree(
            {"title": "Admin", "description": "Bachelor's degree required."}
        )
        is True
    )
    assert (
        fa.requires_degree(
            {"title": "Admin", "description": "Associate's preferred, HS diploma ok."}
        )
        is False
    )
    assert (
        fa.requires_degree({"title": "Receptionist", "description": "Answer phones."})
        is False
    )


def test_looks_remote():
    assert fa.looks_remote({"title": "Data Entry (Remote)", "description": ""}) is True
    assert (
        fa.looks_remote({"title": "Clerk", "description": "Work from home OK"}) is True
    )
    assert fa.looks_remote({"title": "Receptionist", "description": "On site"}) is False


# ── salary verdict classification ─────────────────────────────────────


def _job(smin, smax, predicted="0"):
    return {
        "id": "x",
        "title": "Administrative Assistant",
        "company": {"display_name": "Co"},
        "location": {"display_name": "Des Moines, IA"},
        "salary_min": smin,
        "salary_max": smax,
        "salary_is_predicted": predicted,
        "created": "2026-06-01T00:00:00Z",
        "redirect_url": "https://x",
        "description": "",
    }


def test_verdict_meets_when_listed_above_floor():
    assert fa.normalize(_job(41600, 45760), "local")["verdict"] == "meets"


def test_verdict_estimated_when_predicted_above_floor():
    assert (
        fa.normalize(_job(39520, 39520, predicted="1"), "local")["verdict"]
        == "estimated_ok"
    )


def test_verdict_unlisted_when_no_salary():
    assert fa.normalize(_job(None, None), "local")["verdict"] == "unlisted"


def test_verdict_below_when_under_floor():
    assert fa.normalize(_job(31200, 33280), "local")["verdict"] == "below"


# ── formatting + sorting ──────────────────────────────────────────────


def test_salary_text_range_and_estimated():
    row = fa.normalize(_job(41600, 45760), "local")
    assert fa.salary_text(row) == "$20-$22/hr"
    est = fa.normalize(_job(39520, 39520, predicted="1"), "local")
    assert "(estimated)" in fa.salary_text(est)


def test_sort_puts_paying_jobs_first():
    rows = [
        fa.normalize(_job(31200, 33280), "local"),  # below
        fa.normalize(_job(41600, 45760), "local"),
    ]  # meets
    ordered = fa.sort_rows(rows)
    assert ordered[0]["verdict"] == "meets"
    assert ordered[-1]["verdict"] == "below"


# ── pipeline (mock) ───────────────────────────────────────────────────


def test_collect_mock_drops_it_role():
    rows = fa.collect_mock()
    titles = [r["title"] for r in rows]
    assert "Network Administrator" not in titles
    assert any("Administrative Assistant" == t for t in titles)
    assert len(rows) == 5


def test_cli_mock_runs_and_writes_files(tmp_path):
    """End-to-end: the --mock CLI exits 0 and emits an HTML + CSV."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, os.path.join(root, "find_admin_jobs.py"), "--mock"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    assert "Total jobs:" in result.stdout
