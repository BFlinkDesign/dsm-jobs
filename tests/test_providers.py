"""Provider framework tests. No network: fixtures mirror the officially
documented response shapes (developer.usajobs.gov / help.jooble.org)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import find_admin_jobs as fa  # noqa: E402
import providers  # noqa: E402

USAJOBS_PAYLOAD = {
    "SearchResult": {
        "SearchResultCount": 2,
        "SearchResultItems": [
            {
                "MatchedObjectId": "21947200",
                "MatchedObjectDescriptor": {
                    "PositionTitle": "Administrative Support Assistant",
                    "OrganizationName": "Veterans Health Administration",
                    "PositionLocationDisplay": "Des Moines, Iowa",
                    "PublicationStartDate": "2026-06-01T00:00:00.0000",
                    "PositionURI": "https://www.usajobs.gov/job/21947200",
                    "QualificationSummary": "Clerical duties for the medical center.",
                    "PositionRemuneration": [
                        {"MinimumRange": "41600", "MaximumRange": "54080",
                         "RateIntervalCode": "PA", "Description": "Per Year"},
                    ],
                },
            },
            {
                "MatchedObjectId": "21947201",
                "MatchedObjectDescriptor": {
                    "PositionTitle": "Office Clerk",
                    "OrganizationName": "USDA",
                    "PositionLocationDisplay": "West Des Moines, Iowa",
                    "PublicationStartDate": "2026-06-05T00:00:00.0000",
                    "PositionURI": "https://www.usajobs.gov/job/21947201",
                    "QualificationSummary": "Front desk and filing.",
                    "PositionRemuneration": [
                        {"MinimumRange": "17.50", "MaximumRange": "21.00",
                         "RateIntervalCode": "PH", "Description": "Per Hour"},
                    ],
                },
            },
        ],
    }
}

JOOBLE_PAYLOAD = {
    "totalCount": 1,
    "jobs": [
        {
            "title": "Receptionist",
            "location": "Des Moines, IA",
            "snippet": "Greet <b>visitors</b> and answer phones.",
            "salary": "17,600 UAH",
            "source": "examplejobs.com",
            "type": "Full-time",
            "link": "https://jooble.org/jdp/12345",
            "company": "Acme Dental",
            "updated": "2026-06-09T12:55:35.3870000",
            "id": 1234567890,
        }
    ],
}


def test_usajobs_pa_converts_to_hourly_and_states_pay():
    rows = providers._usajobs_rows(USAJOBS_PAYLOAD, "local", fa.salary_verdict)
    r = rows[0]
    assert r["id"] == "usaj-21947200"
    assert r["hourly_min"] == 20.0          # 41600 / 2080
    assert r["predicted"] is False
    assert r["verdict"] == "meets"          # floor 20.0 >= 19, employer-stated
    assert r["url"].startswith("https://www.usajobs.gov/")


def test_usajobs_ph_used_directly_floor_tests_low_end():
    rows = providers._usajobs_rows(USAJOBS_PAYLOAD, "local", fa.salary_verdict)
    r = rows[1]
    assert r["hourly_min"] == 17.50
    assert r["verdict"] == "below"          # low end fails the $19 floor


def test_usajobs_unknown_interval_code_is_unlisted():
    payload = {"SearchResult": {"SearchResultItems": [{
        "MatchedObjectId": "x",
        "MatchedObjectDescriptor": {
            "PositionTitle": "Clerk", "OrganizationName": "GSA",
            "PositionLocationDisplay": "Des Moines, Iowa",
            "PublicationStartDate": "2026-06-01", "PositionURI": "https://www.usajobs.gov/job/x",
            "PositionRemuneration": [
                {"MinimumRange": "1200", "MaximumRange": "1500",
                 "RateIntervalCode": "BW", "Description": "Bi-weekly"},
            ],
        },
    }]}}
    r = providers._usajobs_rows(payload, "local", fa.salary_verdict)[0]
    assert r["hourly_min"] is None and r["verdict"] == "unlisted"


def test_jooble_salary_never_becomes_a_number():
    # Jooble salary is free text with no provenance flag -> invariant #1:
    # no number, no badge, verdict 'unlisted'.
    r = providers._jooble_rows(JOOBLE_PAYLOAD, fa.salary_verdict)[0]
    assert r["hourly_min"] is None and r["hourly_max"] is None
    assert r["predicted"] is True
    assert r["verdict"] == "unlisted"
    assert r["id"] == "joob-1234567890"
    assert "<b>" not in r["description"]    # snippet HTML stripped


def test_empty_payloads_yield_no_rows():
    assert providers._usajobs_rows({}, "local", fa.salary_verdict) == []
    assert providers._jooble_rows({}, fa.salary_verdict) == []


def test_providers_disabled_without_keys(monkeypatch):
    for var in ("USAJOBS_API_KEY", "USAJOBS_EMAIL", "JOOBLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert providers.usajobs_enabled() is False
    assert providers.jooble_enabled() is False
    assert providers.collect_extra(["x"], "Des Moines, Iowa", fa.salary_verdict, log=lambda *_: None) == []


def test_one_failing_provider_does_not_kill_collection(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    def boom(*a, **k):
        raise RuntimeError("provider HTTP 500: down")
    monkeypatch.setattr(providers, "fetch_jooble", boom)
    monkeypatch.setitem(providers.__dict__, "PROVIDERS",
                        [("jooble", providers.jooble_enabled, boom)])
    out = providers.collect_extra(["x"], "Des Moines, Iowa", fa.salary_verdict, log=lambda *_: None)
    assert out == []                        # failure isolated, not raised


def test_request_json_refuses_unlisted_hosts():
    import pytest
    with pytest.raises(RuntimeError, match="refusing"):
        providers._request_json("https://evil.example.com/api")


JSEARCH_PAYLOAD = {
    "status": "OK",
    "data": [
        {
            "job_id": "woj2gE2S_6LqvmLAAAAAAA==",
            "job_title": "Administrative Assistant",
            "employer_name": "United Airlines",
            "job_city": "Des Moines",
            "job_state": "Iowa",
            "job_location": "Des Moines, IA",
            "job_description": "Front office support role.",
            "job_apply_link": "https://aggregator.example.com/redirect/1",
            "job_apply_is_direct": False,
            "apply_options": [
                {"publisher": "BigBoard", "apply_link": "https://aggregator.example.com/redirect/1", "is_direct": False},
                {"publisher": "Employer", "apply_link": "https://careers.united.com/job/123", "is_direct": True},
            ],
            "job_posted_at_datetime_utc": "2026-06-08T00:00:00.000Z",
            "job_min_salary": 20,
            "job_max_salary": 24,
            "job_salary_period": "HOUR",
        },
        {
            "job_id": "abc",
            "job_title": "Office Clerk",
            "employer_name": "Acme",
            "job_city": "Urbandale",
            "job_state": "Iowa",
            "job_location": None,
            "job_description": "Filing.",
            "job_apply_link": "https://example.com/a",
            "apply_options": [],
            "job_posted_at_datetime_utc": "2026-06-09T00:00:00.000Z",
            "job_min_salary": None,
            "job_max_salary": None,
            "job_salary_period": None,
        },
    ],
}


def test_jsearch_stated_hourly_and_direct_ats_link():
    r = providers._jsearch_rows(JSEARCH_PAYLOAD, "local", fa.salary_verdict)[0]
    assert r["hourly_min"] == 20 and r["verdict"] == "meets"
    assert r["predicted"] is False
    assert r["url"] == "https://careers.united.com/job/123"   # direct ATS preferred


def test_jsearch_null_salary_is_unlisted_not_a_number():
    r = providers._jsearch_rows(JSEARCH_PAYLOAD, "local", fa.salary_verdict)[1]
    assert r["hourly_min"] is None and r["verdict"] == "unlisted"
    assert r["predicted"] is True
    assert r["location"] == "Urbandale, Iowa"                 # city+state fallback


def test_jsearch_week_period_is_skipped_not_misconverted():
    payload = {"data": [dict(JSEARCH_PAYLOAD["data"][0],
                             job_salary_period="WEEK", job_min_salary=800, job_max_salary=900)]}
    r = providers._jsearch_rows(payload, "local", fa.salary_verdict)[0]
    assert r["hourly_min"] is None and r["verdict"] == "unlisted"
