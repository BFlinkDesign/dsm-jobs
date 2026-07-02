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


def test_strip_remote_decoration_leading_and_trailing():
    # Trailing decorations (the most common aggregator shape).
    assert fa.strip_remote_decoration("Data Entry Clerk - Remote") == "Data Entry Clerk"
    assert fa.strip_remote_decoration("Data Entry Clerk – Remote") == "Data Entry Clerk"
    assert fa.strip_remote_decoration("Data Entry Specialist (Remote)") == "Data Entry Specialist"
    assert fa.strip_remote_decoration("Administrative Assistant - Work From Home") == "Administrative Assistant"
    assert fa.strip_remote_decoration("Administrative Assistant (Work From Home)") == "Administrative Assistant"
    # Leading decorations.
    assert fa.strip_remote_decoration("(Remote) Admin Assistant") == "Admin Assistant"
    assert fa.strip_remote_decoration("[Remote] Admin Assistant") == "Admin Assistant"
    assert fa.strip_remote_decoration("REMOTE Customer Service") == "Customer Service"
    assert fa.strip_remote_decoration("100% Remote Administrative Assistant") == "Administrative Assistant"
    assert fa.strip_remote_decoration("Fully Remote Receptionist") == "Receptionist"
    # A bare leading "Remote " is fine to strip too — the card shows its own
    # Remote tag, so "Remote Support Technician" reads fine as the rest alone.
    assert fa.strip_remote_decoration("Remote Support Technician") == "Support Technician"
    # Stacked decorations get fully cleaned up.
    assert fa.strip_remote_decoration("Remote - Data Entry - WFH") == "Data Entry"
    # A mid-title occurrence is a genuine modifier, not a decoration — left alone.
    assert fa.strip_remote_decoration("Senior Remote-Friendly Assistant") == "Senior Remote-Friendly Assistant"
    assert fa.strip_remote_decoration("Virtual Assistant") == "Virtual Assistant"
    # Degenerate input never returns an empty string.
    assert fa.strip_remote_decoration("Remote") == "Remote"
    assert fa.strip_remote_decoration("") == ""


def test_normalize_strips_redundant_remote_title_only_for_remote_source():
    # The card shows a separate "Remote" tag only when source == "remote" — so
    # that's the only source that should get its title cleaned up.
    row = fa.normalize({**_job(None, None), "title": "Data Entry Clerk - Remote"}, "remote")
    assert row["title"] == "Data Entry Clerk"
    # A LOCAL posting whose title happens to mention "Remote" isn't shown with
    # a redundant Remote tag (its real location is shown instead) — leave it.
    local_row = fa.normalize({**_job(None, None), "title": "Data Entry Clerk - Remote"}, "local")
    assert local_row["title"] == "Data Entry Clerk - Remote"


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


def test_verdict_max_only_never_meets():
    # "up to $25/hr" has no known LOW end — it must NOT earn the $19+ badge
    # off its ceiling (invariant #1: never claim a floor we can't see).
    assert fa.salary_verdict(None, 25.0, stated=True) == "unlisted"
    assert fa.salary_verdict(None, 12.0, stated=True) == "unlisted"
    # A real low end still works in both directions.
    assert fa.salary_verdict(20.0, 25.0, stated=True) == "meets"
    assert fa.salary_verdict(12.0, 25.0, stated=True) == "below"


def test_phone_not_lifted_from_long_digit_run():
    # An order/ID number must not be sliced into a fake one-tap "Call" contact.
    assert fa.extract_contact_hints("Order #80012345551234 ships today")["contactPhone"] == ""
    # A genuine formatted number is still extracted.
    assert fa.extract_contact_hints("Call 515-244-0198 to apply")["contactPhone"] == "(515) 244-0198"


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
    assert fa.job_category("Caregiver - Evenings") == "Caregiving"
    assert fa.job_category("Quantum Engineer") == ""


def test_food_labor_and_retail_categories_removed():
    # Her requests: no food service, labor, OR retail jobs. These no longer
    # categorize, and (more importantly) the allowlist drops them entirely.
    for title in ("Janitor", "General Laborer", "Food Service Worker",
                  "Dishwasher", "Production Associate", "Housekeeper",
                  "Retail Sales Associate", "Cashier", "Stocker"):
        assert fa.job_category(title) == "", title
        assert not fa.is_admin_title(title), title


def test_remote_is_exempt_from_day_shift():
    # In-person evening jobs are dropped, but a remote evening job is kept —
    # she can fit remote work around her child.
    night = {"title": "Data Entry", "description": "Shift 3 PM to midnight."}
    assert not fa.is_day_shift(night)
    assert fa.is_remote_row({**night, "source": "remote"})       # exempt: kept
    assert not fa.is_remote_row({**night, "source": "local"})    # in-person: still gated


def test_is_day_shift():
    # Kept (no shift mention, or clearly daytime):
    assert fa.is_day_shift({"title": "Administrative Assistant", "description": ""})
    assert fa.is_day_shift({"title": "Receptionist",
                            "description": "Monday-Friday, 8am-5pm."})
    assert fa.is_day_shift({"title": "Office Clerk",
                            "description": "Hours 9 AM to 5 PM."})
    # Dropped (evening / night / overnight / late-ending):
    assert not fa.is_day_shift({"title": "Receptionist - 2nd Shift", "description": ""})
    assert not fa.is_day_shift({"title": "Data Entry (Overnight)", "description": ""})
    assert not fa.is_day_shift({"title": "Front Desk",
                                "description": "Shift is 3:00 PM to 12:00 AM."})
    assert not fa.is_day_shift({"title": "Scheduler",
                                "description": "Must be available evenings and weekends."})
    assert not fa.is_day_shift({"title": "Office Assistant",
                                "description": "11 am to 9 pm, some Saturdays."})
    assert not fa.is_day_shift({"title": "Front Desk Clerk",
                                "description": "Hours: 4pm to midnight."})


def test_excluded_category_overrides_admin_allowlist():
    # A title can match the admin allowlist AND still be dropped — she ruled out
    # food service, retail, and warehouse work even when an office word sneaks in.
    for title in ("Food Service Receptionist", "Retail Office Assistant",
                  "Warehouse Data Entry Clerk", "Front Desk Cashier",
                  "Administrative Assistant - Restaurant Group"):
        assert fa.has_excluded_category(title), title
    # Clean office / customer-service / care titles are NOT excluded.
    for title in ("Administrative Assistant", "Receptionist", "Data Entry Clerk",
                  "Customer Service Representative", "Caregiver"):
        assert not fa.has_excluded_category(title), title


def test_excluded_category_drops_in_pipeline():
    # "Food Service Receptionist" passes the admin allowlist (has "receptionist")
    # but the hard category exclude must still drop it.
    row = fa.normalize({**_job(41600, 45760),
                        "title": "Food Service Receptionist"}, "local")
    assert fa.is_admin_title(row["title"]) is True
    assert fa._passes_filters(row) is False


def test_weekend_required_dropped():
    assert fa.requires_weekend({"title": "Office Clerk",
                                "description": "Must work weekends and some holidays."})
    assert fa.requires_weekend({"title": "Scheduler",
                                "description": "Every other weekend required."})
    assert fa.requires_weekend({"title": "Receptionist",
                                "description": "Saturday and Sunday coverage needed."})
    # Good phrasings (which are a PLUS) must never trigger a drop:
    assert not fa.requires_weekend({"title": "Office Clerk",
                                    "description": "Weekends off. Monday-Friday only."})
    assert not fa.requires_weekend({"title": "Receptionist",
                                    "description": "No weekend work, daytime hours."})


def test_weekend_dropped_even_when_remote():
    # Remote is exempt from the day-shift gate, but NOT from the weekend gate —
    # she has no weekend childcare, remote or not.
    row = fa.normalize({**_job(41600, 45760),
                        "title": "Data Entry (Remote)",
                        "description": "Remote role. Must be available weekends."}, "remote")
    assert fa.is_remote_row(row) is True
    assert fa._passes_filters(row) is False


def test_friend_sort_newest_first():
    # The default feed order is newest-first; trust/pay only break same-day ties.
    rows = [
        {"created": "2026-06-10", "company": "Random Co", "verdict": "meets"},
        {"created": "2026-06-24", "company": "Unknown LLC", "verdict": "unlisted"},
        {"created": "2026-06-18", "company": "Some Co", "verdict": "below"},
    ]
    ordered = fa.friend_sort(rows)
    assert [r["created"] for r in ordered] == ["2026-06-24", "2026-06-18", "2026-06-10"]


def test_neg_date_orders_newest_first():
    # More-recent date → smaller (more negative) key → sorts first ascending.
    assert fa._neg_date("2026-06-24") < fa._neg_date("2026-06-10")
    assert fa._neg_date("2026-06-10") < fa._neg_date("2025-12-31")
    # Blank / garbage sinks below any real date (treated as oldest).
    assert fa._neg_date("") == 0
    assert fa._neg_date("2026-06-24") < fa._neg_date("")


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


def test_commute_gate_applies_to_local_rows_only():
    # The build-time gate now keeps a LOCAL job iff it's within commute range
    # (Norwalk/Warren is in range); a too-far town is dropped; remote rows are
    # never distance-bound. This mirrors the real pipeline gate (commute_minutes).
    near = _job(41600, 45760)
    near["location"] = {"display_name": "Norwalk, IA"}  # Warren Co — now in range
    far = _job(41600, 45760)
    far["location"] = {"display_name": "Marshalltown, IA"}  # beyond the commute map
    keep = lambda r: r["source"] != "local" or fa.commute_minutes(r["location"]) is not None  # noqa: E731
    assert keep(fa.normalize(near, "local")) is True  # was dropped before; now kept
    assert keep(fa.normalize(far, "local")) is False  # too far -> dropped
    assert keep(fa.normalize(far, "remote")) is True  # remote jobs are not distance-bound


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
    assert "contactPhone" in p and "contactEmail" in p and "contactName" in p


def test_extract_contact_hints_from_posting():
    text = "Questions? Call (515) 244-0198 or email hiring@johnstondental.example. Contact Jane Smith."
    hints = fa.extract_contact_hints(text)
    assert hints["contactPhone"] == "(515) 244-0198"
    assert hints["contactEmail"] == "hiring@johnstondental.example"
    assert hints["contactName"] == "Jane Smith"


def test_extract_contact_hints_ignores_noreply_email():
    hints = fa.extract_contact_hints("Reach us at noreply@scam.example or call (900) 555-0100")
    assert hints["contactEmail"] == ""
    assert hints["contactPhone"] == ""  # premium/fiction ranges never surface


def test_extract_contact_hints_empty_when_no_posting_text():
    assert fa.extract_contact_hints("") == {"contactPhone": "", "contactEmail": "", "contactName": ""}
    assert fa.extract_contact_hints(None)["contactPhone"] == ""


def test_payload_embeds_employer_stated_contact():
    row = fa.normalize(
        {
            "id": "c1",
            "title": "Receptionist",
            "company": {"display_name": "Dental Office"},
            "location": {"display_name": "Johnston, IA"},
            "salary_min": 39520,
            "salary_max": 41600,
            "salary_is_predicted": "0",
            "created": "2026-06-01T00:00:00Z",
            "redirect_url": "https://example.com/j",
            "description": "Call (515) 555-1212 or email hr@dental.example",
        },
        "local",
    )
    row["scam"] = {"level": "safe", "reasons": []}
    p = fa._jobs_payload([row])[0]
    assert p["contactPhone"] == "(515) 555-1212"
    assert p["contactEmail"] == "hr@dental.example"


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


def test_scam_remote_gmail_apply_link_is_hidden():
    r = _row(
        title="Administrative Assistant",
        company="Unknownish Co",
        source="remote",
        desc="",
    )
    r["url"] = "https://gmail.com/inbox/apply-here"
    assert fa.scam_assessment(r, {})["level"] == "scam"
    assert "apply link" in fa.scam_assessment(r, {})["reasons"][0]


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


def test_attainability_drops_supervisory_customer_service_leads():
    # The roles the end user flagged in screenshots: supervisory customer-service
    # titles that slipped through because the old list only dropped admin seniority.
    assert fa.is_attainable("Client Services Lead") is False
    assert fa.is_attainable("Member Services Team Lead") is False
    assert fa.is_attainable("Senior Client Services Lead") is False
    assert fa.is_attainable("Customer Service Manager") is False
    assert fa.is_attainable("Operations Supervisor") is False


def test_attainability_keeps_coordinators_regression():
    # Regression: the bare "coo" drop term used to substring-match "COOrdinator"
    # and silently drop every coordinator. They are valid admin roles — keep them.
    for t in ("Administrative Coordinator", "Office Coordinator",
              "Scheduling Coordinator", "Front Desk Coordinator",
              "Project Coordinator", "Program Coordinator"):
        assert fa.is_attainable(t) is True, t


def test_attainability_drops_roman_numeral_level_tiers():
    # "II"/"III"/"IV" job-level suffixes are a seniority leak the old plain-
    # English word list never caught (she has no way to know these mean a
    # higher experience bar than the plain title).
    assert fa.is_attainable("Administrative Assistant II") is False
    assert fa.is_attainable("Office Clerk III") is False
    assert fa.is_attainable("Customer Service Representative II") is False
    # Level I / no numeral at all is exactly her level — never dropped, and the
    # bare pronoun "I" must never accidentally match either.
    assert fa.is_attainable("Administrative Assistant I") is True
    assert fa.is_attainable("Administrative Assistant") is True


def test_requires_license_or_cert_drops_hard_requirement_only():
    # Admin-sounding titles that quietly require a credential she doesn't
    # hold -> dropped.
    assert fa.requires_license_or_cert({
        "title": "Accounting Assistant",
        "description": "Active CPA license required to review client accounts.",
    }) is True
    assert fa.requires_license_or_cert({
        "title": "Medical Receptionist",
        "description": "Must have an active CNA certification and reliable transportation.",
    }) is True
    assert fa.requires_license_or_cert({
        "title": "Insurance Office Assistant",
        "description": "Must hold an active insurance producer license to service client policies.",
    }) is True
    assert fa.requires_license_or_cert({
        "title": "Real Estate Administrative Assistant",
        "description": "Licensed real estate agent required to assist with closings.",
    }) is True
    # A credential mentioned but softened (preferred / will sponsor / not
    # required) must NOT drop a genuinely entry-level lead.
    assert fa.requires_license_or_cert({
        "title": "Accounting Assistant",
        "description": "CPA license preferred but not required; entry-level bookkeeping only.",
    }) is False
    assert fa.requires_license_or_cert({
        "title": "Medical Receptionist",
        "description": "CNA certification a plus but not required; we will train.",
    }) is False
    assert fa.requires_license_or_cert({
        "title": "Real Estate Administrative Assistant",
        "description": "Real estate license preferred; we will sponsor licensing during training.",
    }) is False
    # A plain admin role that just happens to touch insurance paperwork, with
    # no license mentioned at all, is a genuine entry-level lead.
    assert fa.requires_license_or_cert({
        "title": "Insurance Verification Clerk",
        "description": "Process insurance claims and verify coverage. No license needed.",
    }) is False


def test_requires_heavy_experience_uses_conservative_threshold():
    # A real high bar in disguise -> dropped.
    assert fa.requires_heavy_experience({
        "title": "Administrative Coordinator",
        "description": "This role requires 5+ years of related office experience.",
    }) is True
    assert fa.requires_heavy_experience({
        "title": "Office Manager",
        "description": "7 years of experience is required for this position.",
    }) is True
    # A modest, realistic range for her background must never be dropped —
    # even when it says "required", the bar itself is under the threshold.
    assert fa.requires_heavy_experience({
        "title": "Office Assistant",
        "description": "1-2 years of experience preferred.",
    }) is False
    assert fa.requires_heavy_experience({
        "title": "Office Assistant",
        "description": "3-5 years of experience required.",
    }) is False
    # Any softened mention, even a high number, must not drop it.
    assert fa.requires_heavy_experience({
        "title": "Office Assistant",
        "description": "Prefer 5+ years of experience but will train the right candidate.",
    }) is False
    assert fa.requires_heavy_experience({
        "title": "Receptionist",
        "description": "Answer phones, schedule meetings.",
    }) is False


def test_title_excluded_catches_lpn_cna_hybrids():
    # Long, unambiguous occupation names — dropped even when paired with an
    # admin word, since she holds neither credential.
    assert fa.title_excluded("Licensed Practical Nurse - Office") is True
    assert fa.title_excluded("Certified Nursing Assistant / Receptionist") is True
    # A plain medical front-desk role with no credential in the title is fine.
    assert fa.title_excluded("Medical Receptionist") is False
    assert fa.title_excluded("Patient Access Representative") is False


def test_scam_sign_on_bonus_in_title_is_hidden():
    # All-caps promo/sign-on-bonus advertised IN THE TITLE is spam/scam-shaped,
    # even for an otherwise-recognized employer name. (Screenshot example.)
    def lvl(title, desc=""):
        row = {"title": title, "company": "Businessolver", "description": desc,
               "url": "", "source": "remote", "hourly_min": None, "hourly_max": None}
        return fa.scam_assessment(row, {})["level"]
    assert lvl("Customer Service Rep - $10K Sign-On Bonus") == "scam"
    assert lvl("Receptionist (Signing Bonus!)") == "scam"
    # A body that merely mentions a bonus must NOT be hidden — title-only signal.
    assert lvl("Administrative Assistant", desc="benefits and a small bonus") == "safe"


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


def test_trusted_match_is_word_bounded_not_substring():
    # Audit fix: a hint must match as a WORD, not a substring. Junk names that
    # merely contain a trusted token ('ups' in 'Startups', 'marsh' in
    # 'Marshalling', 'target' in 'Targeted') must NOT be trusted, or they get
    # rescued from scam signals and floated to the top for a scam-targeted user.
    for bogus in ("Quick Startups Staffing", "Backups Remote Jobs", "Cloud Meetups LLC",
                  "Marshalling Logistics", "Targeted Leads LLC", "U.S. Bankruptcy Court"):
        assert not fa.employer_is_trusted(bogus), bogus
        assert fa.trusted_reason(bogus) == "", bogus
    # Real employers still match (including punctuated / multi-word names).
    for legit in ("CVS Health", "UnityPoint Health", "State of Iowa - DOT", "Hy-Vee",
                  "Robert Half", "U.S. Bank"):
        assert fa.employer_is_trusted(legit), legit
        assert fa.trusted_reason(legit) != "", legit


def test_substring_lookalike_remote_is_not_rescued():
    # The lookalike name ('ups' inside 'Meetups') no longer earns the trusted
    # rescue, so a remote posting from it is hidden, not shown as safe.
    r = _row(title="Administrative Assistant", company="Cloud Meetups LLC", source="remote")
    assert fa.scam_assessment(r, {})["level"] in ("scam", "suspect")


def test_remote_too_good_pay_scam_even_for_trusted_name():
    # Audit fix: a $30+/hr REMOTE 'admin' role is bait even when it names a
    # trusted employer — a trusted name is trivially spoofed, and a real trusted
    # employer's entry-admin role isn't a $30+/hr remote gig. (Trusted + remote
    # at ordinary pay stays safe — see below.)
    r = _row(title="Data Entry", company="UnityPoint Health", source="remote", hmin=35.0, hmax=40.0)
    assert fa.scam_assessment(r, {})["level"] == "scam"
    ok = _row(title="Scheduling Assistant", company="UnityPoint Health", source="remote", hmin=20.0, hmax=23.0)
    assert fa.scam_assessment(ok, {})["level"] == "safe"


def test_remote_trusted_name_with_structural_tell_is_not_rescued():
    # A REMOTE posting that names a trusted brand AND shows a structural tell
    # (same role spammed across cities) is the spoofed-brand shape — the trusted
    # rescue must not launder it. A LOCAL trusted posting with the same tell is
    # still downgraded to safe (a real employer hiring the role in many offices).
    rows = [_row(title="Administrative Assistant", company="UnityPoint Health",
                 source="remote", desc="") for _ in range(3)]
    for i, r in enumerate(rows):
        r["location"] = f"City{i}, IA"
    idx = fa.build_spam_index(rows)
    assert fa.scam_assessment(rows[0], idx)["level"] == "scam"
    local = {**rows[0], "source": "local"}
    assert fa.scam_assessment(local, idx)["level"] == "safe"


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


def test_native_platform_affordances_preserved():
    """End-user actions use OS-native handlers — AI owns vetting, not the dialer."""
    t = fa.APP_TEMPLATE
    assert "tel:" in t and "mailto:" in t
    assert "navigator.share" in t
    assert "Notification" in t
    assert "beforeinstallprompt" in t


def test_ai_automation_first_documented():
    """Load-bearing: AI replaces human-in-the-loop for operator judgment."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    claude = open(os.path.join(root, "CLAUDE.md"), encoding="utf-8").read().lower()
    assert "ai replaces human-in-the-loop" in claude
    assert "auto-fix" in claude or "autonomously" in claude
    rabbit = open(os.path.join(root, ".coderabbit.yaml"), encoding="utf-8").read().lower()
    assert "human-in-the-loop" in rabbit


def test_resume_tailor_paste_description_field_present():
    """The tailor flow uses scanner-pulled full text when available; paste is only
    needed when the listing gave a short preview."""
    t = fa.APP_TEMPLATE
    assert 'id="tailorjd"' in t                      # paste fallback when descFull is short
    assert 'data-act="runtailor"' in t               # manual go when paste is required
    assert "function runTailor(" in t
    assert "function tailorJobText(" in t
    assert "descFull" in t                           # full posting rides in the job payload
    assert "full.length>=200" in t                   # one-tap tailor when we have enough text
    assert "pasted.length>=40" in t                  # her paste still wins when she adds more
    assert "isPlausiblePhone" in t                   # follow-up never dials fiction/premium lines


def test_resume_tailor_edge_function_never_invents():
    """The server-side tailor is load-bearing: critic must flag fabrications."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "supabase/functions/resume-tailor/index.ts"),
               encoding="utf-8").read()
    assert "never invent" in src.lower()
    assert "fabrications" in src
    assert "CRITIC_SYSTEM" in src
    assert "MAX_REVISIONS" in src


def test_resume_tailor_copy_both_and_download_present():
    """She can get BOTH the résumé and cover note in one clipboard write, plus a
    .txt download — without losing her place (per-section copy stays too)."""
    t = fa.APP_TEMPLATE
    # "both" joins résumé + a separator + cover note into ONE clipboard write
    assert 'which==="both"' in t and "=== COVER NOTE ===" in t
    assert "var both = d.cover_note ? 'both' : 'resume';" in t  # the combined copy/download button
    assert 'data-act="dltailor"' in t                # download affordance
    assert "function downloadTailor(" in t
    # the existing per-section copy buttons are preserved
    assert 'data-copy="resume"' in t and 'data-copy="cover"' in t


def test_account_teaser_gating_present():
    """No freebies without an account: signed-out users browse jobs but the
    card actions are CSS-hidden behind .app:not(.authed), a per-card lock CTA
    replaces them, and a benefits screen (#lockwrap) exists for locked tabs."""
    t = fa.APP_TEMPLATE
    assert ".app:not(.authed) .card .apply" in t
    assert ".app:not(.authed) .card .actions" in t
    assert '.app.authed .lockcta{display:none}' in t
    assert 'data-act="signup"' in t          # the create-account CTAs
    assert 'id="lockwrap"' in t              # the benefits screen
    assert "LOCKED_VIEWS" in t               # today/apps/corner gate in setView
    # Crisis lines stay reachable on the locked screen (never gate a hotline).
    assert "988" in t and "lockcrisis" in t


def test_ruby_companion_markup_and_voice_present():
    """Ruby the emotional-support cow: full-screen overlay, a designed cow
    avatar, browser-native voice (mic + read-aloud), still signed-in only."""
    t = fa.APP_TEMPLATE
    # Identity + full-screen overlay (not a tiny card).
    assert "Ruby" in t
    assert "emotional support cow" in t.lower()
    assert 'id="rubyov"' in t                  # the full-screen overlay container
    assert 'id="rubyopen"' in t                # the "Talk to Ruby" launcher
    # Designed mascot, not emoji-only: an SVG cow face with spots.
    assert "rb-head" in t and "rb-spot" in t and "rb-horn" in t
    # Voice chat: mic input (Web Speech) + read-aloud (SpeechSynthesis), both
    # feature-detected via typeof so unsupported browsers hide them cleanly.
    assert "SpeechRecognition" in t and "webkitSpeechRecognition" in t
    assert "SpeechSynthesisUtterance" in t and "speechSynthesis" in t
    assert 'id="rubymic"' in t and 'id="rubyspk"' in t
    # Still gated to signed-in users; replies still come from the edge function.
    assert ".app:not(.authed) #chatcard{display:none}" in t
    assert 'invoke("companion"' in t
    # De-cheesed: no glassmorphism blur surface on Ruby's overlay.
    assert "backdrop-filter" not in t[t.index("rubyov"):t.index("rubyov") + 1200]


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


# ── commute radius (let-her-choose distance chooser) ──────────────────


def test_commute_minutes_warren_and_story_now_in_range():
    # Warren/Story towns used to be dropped by the Polk/Dallas-only filter; they
    # now resolve to a drive time so the in-app radius chooser can include them.
    assert fa.commute_minutes("Norwalk, IA") == 28  # Warren County
    assert fa.commute_minutes("Indianola, IA") == 38  # Warren County
    assert fa.commute_minutes("Ames, IA") == 38  # Story County
    assert fa.commute_minutes("West Des Moines, IA") == 18  # unchanged Polk value


def test_commute_minutes_city_beats_county_fallback():
    # A named city wins over its county; the county is only a fallback.
    assert fa.commute_minutes("Grimes, Polk County") == 5
    assert fa.commute_minutes("Polk County, IA") == 20  # no city -> county fallback


def test_commute_minutes_token_match_not_substring():
    # "ames" inside "James St" must NOT match Ames (token match, not substring).
    assert fa.commute_minutes("123 James Street, Des Moines, IA") == 20
    assert fa.commute_minutes("Chicago, IL") is None
    assert fa.commute_minutes("") is None
    assert fa.commute_minutes(None) is None


def test_commute_gate_keeps_nearby_county_drops_far():
    # The build-time gate keeps a local job iff it has a known drive time.
    def commutable(loc):
        return fa.commute_minutes(loc) is not None

    assert commutable("Norwalk, IA")  # Warren — now kept
    assert commutable("Ames, IA")  # Story — now kept
    assert commutable("Des Moines, IA")  # Polk — still kept
    assert not commutable("Marshalltown, IA")  # too far — dropped (not in map)
    assert not commutable("Chicago, IL")  # out of region — dropped


def test_jobs_payload_carries_commute_min():
    base = {
        "title": "Receptionist",
        "company": "Dallas County",
        "location": "Adel, IA",
        "hourly_min": 18.0,
        "hourly_max": None,
        "predicted": False,
        "verdict": fa.salary_verdict(18.0, None, stated=True),
        "created": "2026-06-10",
        "url": "https://example.gov/1",
        "description": "front desk",
    }
    local = {**base, "id": "1", "source": "local"}
    assert fa._jobs_payload([local])[0]["commuteMin"] == 15  # Adel
    # Remote jobs carry commuteMin None so the radius filter always shows them.
    remote = {**base, "id": "2", "source": "remote", "location": "Remote, US"}
    assert fa._jobs_payload([remote])[0]["commuteMin"] is None


# ── gig-bait scam hardening (pre-release review 2026-06-16) ────────────


def test_scam_shield_flags_gig_panel_bait_title():
    # "Paid Focus Group Panelist" is gig bait — hidden even as a LOCAL posting
    # (so it doesn't rely on the remote+unknown rule). Without the title flag this
    # local/unknown/no-tell row would score 'safe'; the flag makes it 'scam'.
    r = _row(
        title="Paid Focus Group Panelist - Des Moines",
        company="Generic Studio",
        source="local",
        desc="Share your opinions and get paid.",
    )
    assert fa.scam_assessment(r, {})["level"] == "scam"


def test_scam_shield_does_not_false_hide_market_research_coordinator():
    # A legit "Market Research Coordinator" must NOT be hidden by the gig-bait
    # flags — they're distinctive ("research panel"/"paid focus group"), not the
    # broad term "market research".
    r = _row(
        title="Market Research Coordinator",
        company="Wellmark",
        source="local",
        desc="Coordinate research projects, scheduling, reports.",
    )
    assert fa.scam_assessment(r, {})["level"] == "safe"
