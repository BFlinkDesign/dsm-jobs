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
    assert fa.requires_degree({"title": "Admin", "description": "Bachelor's degree required."}) is True
    assert (
        fa.requires_degree({"title": "Admin", "description": "Associate's preferred, HS diploma ok."})
        is False
    )
    assert fa.requires_degree({"title": "Receptionist", "description": "Answer phones."}) is False


def test_looks_remote():
    assert fa.looks_remote({"title": "Data Entry (Remote)", "description": ""}) is True
    assert fa.looks_remote({"title": "Clerk", "description": "Work from home OK"}) is True
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


def test_predicted_salary_is_never_promised():
    # Adzuna-predicted pay must NOT earn a $19+ verdict; treated as unlisted.
    assert fa.normalize(_job(39520, 39520, predicted="1"), "local")["verdict"] == "unlisted"


def test_payload_hides_predicted_number():
    # Even a high predicted salary shows "Pay not listed" with no number / no $19+ flag.
    row = fa.normalize(_job(41600, 45760, predicted="1"), "local")
    row["scam"] = {"level": "safe", "reasons": []}
    p = fa._jobs_payload([row])[0]
    assert p["pay"] == "Pay not listed"
    assert p["good"] is False
    assert p["payNum"] == 0.0


def test_payload_shows_employer_stated_number():
    row = fa.normalize(_job(41600, 45760, predicted="0"), "local")
    row["scam"] = {"level": "safe", "reasons": []}
    p = fa._jobs_payload([row])[0]
    assert "$20" in p["pay"] and p["good"] is True


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
    assert "Total jobs found:" in result.stdout
    assert "Hidden as scams:" in result.stdout


# ── categories, dedup, enrichment ─────────────────────────────────────


def test_warehouse_jobs_removed():
    assert fa.is_admin_title("Warehouse Associate") is False
    assert "warehouse associate" not in fa.TITLES
    assert not any("warehouse" in t for t in fa.ADMIN_TITLE_TERMS)
    assert not any("packer" in t or "picker" in t for t in fa.ADMIN_TITLE_TERMS)


def test_job_category():
    assert fa.job_category("Administrative Assistant") == "Office"
    assert fa.job_category("Call Center Representative") == "Customer service"
    assert fa.job_category("Retail Sales Associate") == "Store & retail"
    assert fa.job_category("Caregiver - Evenings") == "Caregiving"
    assert fa.job_category("Janitor") == "Food & cleaning"
    assert fa.job_category("General Laborer") == "Production & labor"
    assert fa.job_category("Quantum Engineer") == ""


def test_in_polk_or_dallas_counties_only():
    # In: Polk + Dallas County cities, and the counties by name.
    assert fa.in_polk_or_dallas("Waukee, IA") is True
    assert fa.in_polk_or_dallas("West Des Moines, IA") is True
    assert fa.in_polk_or_dallas("Urbandale, Polk County") is True
    assert fa.in_polk_or_dallas("Adel, Dallas County") is True
    # Out: inside the search radius but in Warren/Story/etc. counties.
    assert fa.in_polk_or_dallas("Norwalk, IA") is False
    assert fa.in_polk_or_dallas("Indianola, IA") is False
    assert fa.in_polk_or_dallas("Ames, IA") is False
    # Unknown / blank locations are not in-county.
    assert fa.in_polk_or_dallas("") is False
    assert fa.in_polk_or_dallas("Iowa, US") is False


def test_county_filter_applies_to_local_rows_only():
    out_of_county = _job(41600, 45760)
    out_of_county["location"] = {"display_name": "Norwalk, IA"}
    local = fa.normalize(out_of_county, "local")
    remote = fa.normalize(out_of_county, "remote")
    keep = lambda r: r["source"] != "local" or fa.in_polk_or_dallas(r["location"])  # noqa: E731
    assert keep(local) is False
    assert keep(remote) is True  # remote jobs are not county-bound


def test_trusted_reason_labels():
    assert fa.trusted_reason("State of Iowa - DOT") == "Government"
    assert fa.trusted_reason("UnityPoint Health") == "Healthcare"
    assert fa.trusted_reason("Robert Half") == "Staffing agency"
    assert fa.trusted_reason("Unknown LLC") == ""


def test_commute_text_prefers_longest_match():
    assert fa.commute_text("West Des Moines, IA") == "~18 min drive"
    assert fa.commute_text("Des Moines, IA") == "~20 min drive"
    assert fa.commute_text("Grimes, Polk County") == "~5 min drive"
    assert fa.commute_text("Remote, US") == ""


def test_snippet_truncates_at_word_boundary():
    long = "word " * 100
    s = fa.snippet(long)
    assert len(s) <= 241 and s.endswith("…")
    assert fa.snippet("  Short   description. ") == "Short description."
    assert fa.snippet(None) == ""


def test_dedupe_collapses_same_job_from_two_boards():
    j1, j2 = _job(41600, 45760), _job(41600, 45760)
    j2["id"], j2["created"] = "y", "2026-06-05T00:00:00Z"
    rows = [fa.normalize(j1, "local"), fa.normalize(j2, "local")]
    deduped, n = fa.dedupe_rows(rows)
    assert n == 1 and len(deduped) == 1
    assert deduped[0]["id"] == "y"  # newest posting wins


def test_payload_includes_enrichment_fields():
    row = fa.normalize(_job(41600, 45760), "local")
    row["scam"] = {"level": "safe", "reasons": []}
    p = fa._jobs_payload([row])[0]
    assert p["category"] == "Office"
    assert p["commute"] == "~20 min drive"  # Des Moines, IA from Grimes
    assert "trustLabel" in p and "about" in p


def test_app_keeps_server_sort_order():
    # Invariant #2: the page must NOT re-sort by pay, which would bury
    # "Pay not listed" jobs under every stated-pay job.
    assert "payNum-a.payNum" not in fa.APP_TEMPLATE


# ── scam shield + attainability ───────────────────────────────────────


def _row(title="Receptionist", company="Some LLC", source="local", desc="", hmin=20.0, hmax=24.0):
    return {
        "title": title,
        "company": company,
        "location": "Des Moines, IA",
        "description": desc,
        "source": source,
        "hourly_min": hmin,
        "hourly_max": hmax,
    }


def test_scam_hard_phrase_is_scam_even_for_known_employer():
    r = _row(company="State of Iowa", desc="You must cash a check and wire transfer the balance.")
    out = fa.scam_assessment(r, {})
    assert out["level"] == "scam"


def test_scam_remote_unknown_employer_is_hidden():
    r = _row(title="Administrative Assistant", company="Unknownish Co", source="remote", desc="")
    out = fa.scam_assessment(r, {})
    assert out["level"] in ("scam", "suspect")  # never 'safe'


def test_scam_remote_too_good_pay_is_scam():
    r = _row(title="Data Entry", company="Mystery Co", source="remote", hmin=35.0, hmax=40.0)
    assert fa.scam_assessment(r, {})["level"] == "scam"


def test_trusted_local_employer_is_safe():
    r = _row(title="Receptionist", company="UnityPoint Health", source="local")
    assert fa.scam_assessment(r, {})["level"] == "safe"


def test_spam_across_cities_flagged():
    company, title = "Turbo Co", "remote administrative assistant"
    idx = {(fa._norm_company(company), title[:25]): {"a", "b", "c", "d"}}
    r = _row(title=title, company=company, source="remote")
    assert fa.scam_assessment(r, idx)["level"] == "scam"


def test_blocklist_hides_even_trusted(monkeypatch):
    monkeypatch.setattr(fa, "BLOCKLIST", ["maxion corp"])
    r = _row(title="Administrative Assistant", company="Maxion Corp LLC", source="remote")
    assert fa.scam_assessment(r, {})["level"] == "scam"
    monkeypatch.setattr(fa, "BLOCKLIST", [])


def test_attainability_drops_senior_roles():
    assert fa.is_attainable("Administrative Assistant") is True
    assert fa.is_attainable("Senior Administrative Assistant") is False
    assert fa.is_attainable("Office Manager") is False
    assert fa.is_attainable("Receptionist") is True
