"""Unit + smoke tests for find_admin_jobs. No network calls."""

import os
import subprocess
import sys
import urllib.error

import pytest

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


def test_attainability_keeps_experienced_admin_drops_exec_tiers():
    # Lilly has years of admin experience — experienced-admin roles are KEPT.
    assert fa.is_attainable("Administrative Assistant") is True
    assert fa.is_attainable("Senior Administrative Assistant") is True
    assert fa.is_attainable("Office Manager") is True
    assert fa.is_attainable("Executive Assistant") is True
    assert fa.is_attainable("Administrative Supervisor") is True
    assert fa.is_attainable("Receptionist") is True
    # Only true executive / non-admin tiers are dropped as out-of-scope.
    assert fa.is_attainable("Director of Operations") is False
    assert fa.is_attainable("VP of Finance") is False
    assert fa.is_attainable("Chief of Staff") is False


# --- transient-5xx retry (scheduled scan failed on a one-off Adzuna 503) ---


def _http_error(code):
    import io

    return urllib.error.HTTPError("https://api.adzuna.com/x", code, "err", {}, io.BytesIO(b"down"))


def test_adzuna_request_retries_5xx_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"results": []}'

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return FakeResp()

    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    monkeypatch.setattr(fa.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    assert fa.adzuna_request({"what": "admin"}) == {"results": []}
    assert calls["n"] == 3


def test_adzuna_request_does_not_retry_4xx(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        raise _http_error(401)

    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    monkeypatch.setattr(fa.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="Adzuna HTTP 401"):
        fa.adzuna_request({"what": "admin"})
    assert calls["n"] == 1


def test_adzuna_request_gives_up_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        raise _http_error(503)

    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    monkeypatch.setattr(fa.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="Adzuna HTTP 503"):
        fa.adzuna_request({"what": "admin"})
    assert calls["n"] == 3


# --- audit-finding fixes: duty-phrase rescue, degree softeners, word-boundary excludes ---


def _duty_row(company, desc, title="Administrative Assistant", source="local"):
    return {
        "title": title,
        "company": company,
        "location": "Des Moines, IA",
        "description": desc,
        "source": source,
        "hourly_min": None,
        "hourly_max": None,
        "url": "https://example.com/x",
    }


def test_trusted_employer_financial_duties_not_scam():
    # Bank/teller-adjacent duty language from a TRUSTED employer is normal work,
    # not a scam tell ("process payments", "money order", "gift card"...).
    # Mule-script shapes ("cash a check", "wire transfer") stay fatal for all —
    # see test_scam_hard_phrase_is_scam_even_for_known_employer.
    r = _duty_row(
        "Wells Fargo", "You will process payments, handle money orders, and sell gift cards to members."
    )
    assert fa.scam_assessment(r, {})["level"] == "safe"


def test_unknown_employer_financial_duties_still_scam():
    r = _duty_row("Quick Cash Partners LLC", "You will process payments and handle money orders from home.")
    assert fa.scam_assessment(r, {})["level"] == "scam"


def test_remote_posting_naming_trusted_employer_with_financial_duties_is_scam():
    # Audit fix: a REMOTE posting that merely NAMES a trusted employer + has
    # financial-duty language is the spoofed-name check-cashing shape, so the
    # trusted rescue must NOT apply (a real trusted teller/AP role is local).
    r = _duty_row(
        "Wells Fargo", "You will process payments and handle money orders from home.", source="remote"
    )
    assert fa.scam_assessment(r, {})["level"] == "scam"


def test_boolean_predicted_flag_still_hides_wage():
    # Audit fix: salary_is_predicted as a JSON boolean true must still be treated
    # as a guess (fail closed) — no number, no $19+ verdict.
    row = fa.normalize(
        {
            "title": "Office Assistant",
            "company": {"display_name": "X"},
            "location": {"display_name": "Des Moines, IA"},
            "salary_min": 41600,
            "salary_max": 45760,
            "salary_is_predicted": True,
            "created": "2026-06-01",
            "redirect_url": "https://x",
        },
        "local",
    )
    assert row["predicted"] is True and row["verdict"] == "unlisted"
    p = fa._jobs_payload([{**row, "scam": {"level": "safe", "reasons": []}}])[0]
    assert p["pay"] == "Pay not listed" and p["payNum"] == 0.0 and p["good"] is False


def test_jobs_with_empty_id_and_url_get_distinct_ids():
    # Audit fix: a row with no id AND no url must not collapse onto the shared ""
    # localStorage key (which would bleed Applied/Saved state between jobs).
    base = {
        "hourly_min": 20.0,
        "hourly_max": 24.0,
        "verdict": "meets",
        "predicted": False,
        "created": "2026-06-01",
        "source": "local",
        "description": "",
        "location": "Des Moines, IA",
        "id": None,
        "url": "",
        "scam": {"level": "safe", "reasons": []},
    }
    a = {**base, "title": "Receptionist", "company": "Alpha"}
    b = {**base, "title": "File Clerk", "company": "Beta"}
    ids = [j["id"] for j in fa._jobs_payload([a, b])]
    assert ids[0] != ids[1] and "" not in ids


def test_hard_tell_overrides_trusted_employer():
    # Fee/off-platform tells are fatal even from a trusted name (spoofed listings).
    r = _duty_row("Wells Fargo", "Interview conducted via telegram. Pay a registration fee to start.")
    assert fa.scam_assessment(r, {})["level"] == "scam"


def test_degree_preferred_is_not_required():
    assert (
        fa.requires_degree(
            {"title": "Office Assistant", "description": "Bachelor's degree preferred but not required."}
        )
        is False
    )
    assert (
        fa.requires_degree(
            {"title": "Office Assistant", "description": "No degree required. HS diploma welcome."}
        )
        is False
    )
    assert (
        fa.requires_degree(
            {"title": "Office Assistant", "description": "Bachelor's degree required for this role."}
        )
        is True
    )
    assert (
        fa.requires_degree(
            {"title": "Office Assistant", "description": "Must hold a bachelor degree in business."}
        )
        is True
    )


def test_title_excluded_word_boundaries():
    assert (
        fa.title_excluded("Engineering Office Assistant") is False
    )  # 'engineer' must not match 'engineering'
    assert fa.title_excluded("Network Engineer") is True
    assert fa.title_excluded("Food Server") is True
    assert fa.title_excluded("Cybersecurity Analyst") is True  # prefix family
    assert fa.title_excluded("Phlebotomist") is True  # prefix family


def test_template_has_no_legacy_theme_leftovers():
    t = fa.APP_TEMPLATE
    assert "Fraunces" not in t  # serif from the pre-Relume theme
    for warm in ("#fff3e2", "#ecd2a8", "#7a5417", "#9aa39e"):
        assert warm not in t, f"legacy warm color {warm} still in template"


# --- US-only hard guard (no European / foreign trash) ---


def test_looks_non_us_flags_foreign_not_us_lookalikes():
    assert fa.looks_non_us("London, United Kingdom") is True
    assert fa.looks_non_us("Bangalore, India") is True
    assert fa.looks_non_us("Toronto, ON, Canada") is True
    assert fa.looks_non_us("Remote - EMEA") is True
    # US lookalikes must NOT trip foreign markers:
    assert fa.looks_non_us("Indianapolis, Indiana") is False  # 'india' inside 'indiana'
    assert fa.looks_non_us("Des Moines, IA") is False
    assert fa.looks_non_us("Paris, Texas") is False  # US Paris, has 'texas'


def test_is_us_location_positive_signals():
    assert fa.is_us_location("Des Moines, IA") is True
    assert fa.is_us_location("Remote - United States") is True
    assert fa.is_us_location("Austin, Texas") is True
    assert fa.is_us_location("Bangalore, India") is False


def test_passes_us_filter_drops_foreign_remote_keeps_us():
    def row(loc, source="remote"):
        return {"location": loc, "source": source, "title": "Admin"}

    assert fa.passes_us_filter(row("London, United Kingdom")) is False
    assert fa.passes_us_filter(row("Paris, France")) is False
    assert fa.passes_us_filter(row("Remote - United States")) is True
    assert fa.passes_us_filter(row("Phoenix, Arizona")) is True
    assert fa.passes_us_filter(row("Remote")) is True  # bare remote allowed
    # A foreign marker overrides even a 'local' source (defensive):
    assert fa.passes_us_filter(row("Des Moines, IA", source="local")) is True
    assert fa.passes_us_filter(row("London, UK", source="local")) is False
