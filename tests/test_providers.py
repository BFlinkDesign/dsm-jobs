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
                        {
                            "MinimumRange": "41600",
                            "MaximumRange": "54080",
                            "RateIntervalCode": "PA",
                            "Description": "Per Year",
                        },
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
                        {
                            "MinimumRange": "17.50",
                            "MaximumRange": "21.00",
                            "RateIntervalCode": "PH",
                            "Description": "Per Hour",
                        },
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
    assert r["hourly_min"] == 20.0  # 41600 / 2080
    assert r["predicted"] is False
    assert r["verdict"] == "meets"  # floor 20.0 >= 19, employer-stated
    assert r["url"].startswith("https://www.usajobs.gov/")


def test_usajobs_ph_used_directly_floor_tests_low_end():
    rows = providers._usajobs_rows(USAJOBS_PAYLOAD, "local", fa.salary_verdict)
    r = rows[1]
    assert r["hourly_min"] == 17.50
    assert r["verdict"] == "below"  # low end fails the $19 floor


def test_usajobs_unknown_interval_code_is_unlisted():
    payload = {
        "SearchResult": {
            "SearchResultItems": [
                {
                    "MatchedObjectId": "x",
                    "MatchedObjectDescriptor": {
                        "PositionTitle": "Clerk",
                        "OrganizationName": "GSA",
                        "PositionLocationDisplay": "Des Moines, Iowa",
                        "PublicationStartDate": "2026-06-01",
                        "PositionURI": "https://www.usajobs.gov/job/x",
                        "PositionRemuneration": [
                            {
                                "MinimumRange": "1200",
                                "MaximumRange": "1500",
                                "RateIntervalCode": "BW",
                                "Description": "Bi-weekly",
                            },
                        ],
                    },
                }
            ]
        }
    }
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
    assert "<b>" not in r["description"]  # snippet HTML stripped


def test_empty_payloads_yield_no_rows():
    assert providers._usajobs_rows({}, "local", fa.salary_verdict) == []
    assert providers._jooble_rows({}, fa.salary_verdict) == []


def test_providers_disabled_without_keys(monkeypatch):
    for var in ("USAJOBS_API_KEY", "USAJOBS_EMAIL", "JOOBLE_API_KEY", "JSEARCH_API_KEY", "CAREERJET_AFFID"):
        monkeypatch.delenv(var, raising=False)
    # The keyless always-on providers are neutralized so this stays offline.
    monkeypatch.setattr(providers, "ATS_BOARDS", {})
    monkeypatch.setattr(providers, "NEOGOV_AGENCIES", [])
    monkeypatch.setattr(providers, "WORKDAY_BOARDS", [])
    monkeypatch.setattr(providers, "SMARTRECRUITERS_COMPANIES", [])
    assert providers.usajobs_enabled() is False
    assert providers.jooble_enabled() is False
    assert providers.ats_enabled() is False
    assert providers.collect_extra(["x"], "Des Moines, Iowa", fa.salary_verdict, log=lambda *_: None) == []


def test_one_failing_provider_does_not_kill_collection(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")

    def boom(*a, **k):
        raise RuntimeError("provider HTTP 500: down")

    monkeypatch.setattr(providers, "fetch_jooble", boom)
    monkeypatch.setitem(providers.__dict__, "PROVIDERS", [("jooble", providers.jooble_enabled, boom)])
    out = providers.collect_extra(["x"], "Des Moines, Iowa", fa.salary_verdict, log=lambda *_: None)
    assert out == []  # failure isolated, not raised


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
                {
                    "publisher": "BigBoard",
                    "apply_link": "https://aggregator.example.com/redirect/1",
                    "is_direct": False,
                },
                {
                    "publisher": "Employer",
                    "apply_link": "https://careers.united.com/job/123",
                    "is_direct": True,
                },
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
    assert r["url"] == "https://careers.united.com/job/123"  # direct ATS preferred


def test_jsearch_null_salary_is_unlisted_not_a_number():
    r = providers._jsearch_rows(JSEARCH_PAYLOAD, "local", fa.salary_verdict)[1]
    assert r["hourly_min"] is None and r["verdict"] == "unlisted"
    assert r["predicted"] is True
    assert r["location"] == "Urbandale, Iowa"  # city+state fallback


def test_jsearch_week_period_is_skipped_not_misconverted():
    payload = {
        "data": [
            dict(JSEARCH_PAYLOAD["data"][0], job_salary_period="WEEK", job_min_salary=800, job_max_salary=900)
        ]
    }
    r = providers._jsearch_rows(payload, "local", fa.salary_verdict)[0]
    assert r["hourly_min"] is None and r["verdict"] == "unlisted"


# --- ATS (Greenhouse/Lever) + Careerjet, verified shapes 2026-06-10 ---

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 5842476,
            "title": "Administrative Assistant",
            "company_name": "Businessolver",
            "location": {"name": "West Des Moines, IA"},
            "pay_input_ranges": [],
            "absolute_url": "https://www.businessolver.com/careers/openings/?gh_jid=5842476",
            "updated_at": "2026-06-08T15:49:22-04:00",
        },
        {
            "id": 99,
            "title": "AVP Client Services",
            "company_name": "Businessolver",
            "location": {"name": "Work Remotely Anywhere in the U.S."},
            "pay_input_ranges": [],
            "absolute_url": "https://www.businessolver.com/careers/openings/?gh_jid=99",
            "updated_at": "2026-06-08T00:00:00-04:00",
        },
    ]
}

LEVER_PAYLOAD = [
    {
        "id": "abc",
        "text": "Office Coordinator",
        "categories": {"location": "West Des Moines, IA", "commitment": "Full-Time"},
        "salaryRange": {"min": 40000, "max": 52000, "currency": "USD", "interval": "per-year-salary"},
        "applyUrl": "https://jobs.lever.co/telligen/abc/apply",
        "hostedUrl": "https://jobs.lever.co/telligen/abc",
        "descriptionPlain": "Front desk support.",
    },
    {
        "id": "def",
        "text": "Clerk",
        "categories": {"location": "Illinois"},
        "salaryRange": {"min": 18, "max": 22, "currency": "USD", "interval": "per-hour-wage"},
        "applyUrl": "https://jobs.lever.co/telligen/def/apply",
        "descriptionPlain": "Filing.",
    },
]

CAREERJET_PAYLOAD = {
    "jobs": [
        {
            "title": "Receptionist",
            "company": "Acme Clinic",
            "locations": "Des Moines, IA",
            "description": "Greet patients.",
            "url": "https://www.careerjet.com/jobview/abc123.html",
            "date": "2026-06-09",
            "salary_min": "20",
            "salary_max": "24",
            "salary_type": "H",
        },
        {
            "title": "Admin Specialist",
            "company": "Beta Co",
            "locations": "Ankeny, IA",
            "description": "Data entry.",
            "url": "https://www.careerjet.com/jobview/def456.html",
            "date": "2026-06-08",
            "salary_min": "41600",
            "salary_max": "52000",
            "salary_type": "Y",
        },
    ]
}


def test_greenhouse_real_apply_url_pay_unlisted_and_source_split():
    rows = providers._greenhouse_rows(GREENHOUSE_PAYLOAD, fa.salary_verdict)
    local, remote = rows[0], rows[1]
    assert local["source"] == "local" and local["url"].startswith("https://www.businessolver.com/")
    assert local["verdict"] == "unlisted" and local["hourly_min"] is None  # no reliable period
    assert remote["source"] == "remote"  # 'Work Remotely Anywhere'


def test_lever_yearly_and_hourly_intervals():
    rows = providers._lever_rows(LEVER_PAYLOAD, fa.salary_verdict)
    yearly, hourly = rows[0], rows[1]
    assert yearly["hourly_min"] == round(40000 / 2080, 2) and yearly["verdict"] == "meets"
    assert yearly["url"].startswith("https://jobs.lever.co/")  # real ATS apply link
    assert hourly["hourly_min"] == 18 and hourly["verdict"] == "below"  # low end < 19


def test_lever_unknown_interval_is_unlisted():
    payload = [dict(LEVER_PAYLOAD[0], salaryRange={"min": 800, "max": 900, "interval": "per-week-salary"})]
    r = providers._lever_rows(payload, fa.salary_verdict)[0]
    assert r["hourly_min"] is None and r["verdict"] == "unlisted"


def test_careerjet_hourly_and_yearly_stated():
    rows = providers._careerjet_rows(CAREERJET_PAYLOAD, fa.salary_verdict)
    assert rows[0]["hourly_min"] == 20 and rows[0]["verdict"] == "meets"
    assert rows[1]["hourly_min"] == round(41600 / 2080, 2) and rows[1]["verdict"] == "meets"
    assert rows[0]["predicted"] is False


def test_ats_enabled_by_default_no_key():
    assert providers.ats_enabled() is True  # ATS_BOARDS seeded, no key needed


def test_careerjet_disabled_without_affid(monkeypatch):
    monkeypatch.delenv("CAREERJET_AFFID", raising=False)
    assert providers.careerjet_enabled() is False


def test_careeronestop_is_an_honest_stub():
    import pytest

    monkeypatch_env = {"CAREERONESTOP_TOKEN": "t", "CAREERONESTOP_USERID": "u"}
    for k, v in monkeypatch_env.items():
        os.environ[k] = v
    try:
        assert providers.careeronestop_enabled() is True
        with pytest.raises(NotImplementedError):
            providers.fetch_careeronestop(["x"], "y", fa.salary_verdict, lambda *_: None)
    finally:
        for k in monkeypatch_env:
            os.environ.pop(k, None)


# ── NEOGOV / GovernmentJobs.com feed (keyless RSS) ──────────────────────────

import pytest  # noqa: E402

_NEOGOV_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:joblisting="http://www.neogov.com/namespaces/JobListing">
  <channel>
    <title>State of Iowa, IA</title>
    <item>
      <title>Administrative Assistant 2</title>
      <link>https://www.governmentjobs.com/careers/iowa/jobs/5376469</link>
      <pubDate>Fri, 12 Jun 2026 22:11:53 GMT</pubDate>
      <joblisting:jobId>5376469</joblisting:jobId>
      <joblisting:minimumSalary>22.50</joblisting:minimumSalary>
      <joblisting:maximumSalary>33.00</joblisting:maximumSalary>
      <joblisting:salaryCurrency>USD</joblisting:salaryCurrency>
      <joblisting:salaryInterval>Hour</joblisting:salaryInterval>
      <joblisting:location>Des Moines - 50319 - Polk County</joblisting:location>
      <joblisting:qualifications>Graduation from high school.</joblisting:qualifications>
    </item>
    <item>
      <title>Office Coordinator</title>
      <link>https://www.governmentjobs.com/careers/iowa/jobs/5376470</link>
      <pubDate>Thu, 11 Jun 2026 10:00:00 GMT</pubDate>
      <joblisting:jobId>5376470</joblisting:jobId>
      <joblisting:minimumSalary>50000</joblisting:minimumSalary>
      <joblisting:maximumSalary>62400</joblisting:maximumSalary>
      <joblisting:salaryCurrency>USD</joblisting:salaryCurrency>
      <joblisting:salaryInterval>Year</joblisting:salaryInterval>
      <joblisting:location>Ankeny - 50023 - Polk County</joblisting:location>
    </item>
    <item>
      <title>Records Clerk (EU office)</title>
      <link>https://www.governmentjobs.com/careers/iowa/jobs/5376471</link>
      <joblisting:jobId>5376471</joblisting:jobId>
      <joblisting:minimumSalary>30000</joblisting:minimumSalary>
      <joblisting:salaryCurrency>EUR</joblisting:salaryCurrency>
      <joblisting:salaryInterval>Year</joblisting:salaryInterval>
      <joblisting:location>Dublin - IE</joblisting:location>
    </item>
  </channel>
</rss>"""


def test_neogov_parses_stated_usd_salary_and_fields():
    rows = providers._neogov_rows(_NEOGOV_FIXTURE, "State of Iowa", fa.salary_verdict)
    assert len(rows) == 3
    a = rows[0]
    assert a["title"] == "Administrative Assistant 2"
    assert a["company"] == "State of Iowa"
    assert a["url"].endswith("/jobs/5376469")
    assert a["id"] == "gov-5376469"
    assert a["hourly_min"] == 22.5 and a["hourly_max"] == 33.0
    assert a["predicted"] is False  # employer-stated -> real number
    assert a["verdict"] == "meets"  # $22.50 floor >= $19
    assert a["location"] == "Des Moines, 50319, Polk County"  # normalized for metro filter
    assert a["created"] == "2026-06-12"
    assert a["source"] == "local"


def test_neogov_year_salary_converted_to_hourly():
    rows = providers._neogov_rows(_NEOGOV_FIXTURE, "State of Iowa", fa.salary_verdict)
    office = rows[1]
    assert office["hourly_min"] == round(50000 / 2080, 2)  # ~24.04
    assert office["verdict"] == "meets"


def test_neogov_non_usd_salary_suppressed():
    """invariant #1: a non-USD employer salary must NOT become a number."""
    rows = providers._neogov_rows(_NEOGOV_FIXTURE, "State of Iowa", fa.salary_verdict)
    eu = rows[2]
    assert eu["hourly_min"] is None and eu["hourly_max"] is None
    assert eu["predicted"] is True
    assert eu["verdict"] == "unlisted"


def test_neogov_hourly_helper_units():
    assert providers._neogov_hourly("20", "30", "Hour", "USD") == (20.0, 30.0)
    assert providers._neogov_hourly("41600", None, "Year", "USD")[0] == 20.0
    assert providers._neogov_hourly("20", "30", "Hour", "EUR") == (None, None)
    assert providers._neogov_hourly("20", "30", "Week", "USD") == (None, None)  # unknown interval
    assert providers._neogov_hourly("", "", "Hour", "USD") == (None, None)


def test_neogov_rejects_dtd_entity_bomb():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "boom">]><rss></rss>'
    with pytest.raises(RuntimeError, match="DTD/entity"):
        providers._neogov_rows(bomb, "X", fa.salary_verdict)


def test_neogov_skips_items_without_title_or_url():
    feed = (
        '<?xml version="1.0"?><rss xmlns:joblisting="http://www.neogov.com/namespaces/JobListing">'
        "<channel><item><joblisting:jobId>1</joblisting:jobId></item></channel></rss>"
    )
    assert providers._neogov_rows(feed, "X", fa.salary_verdict) == []


# ── Workday CxS + SmartRecruiters row builders (offline fixtures) ────────────

_WORKDAY_PAYLOAD = {
    "total": 2,
    "jobPostings": [
        {"title": "Sr Executive Assistant", "externalPath": "/job/West-Des-Moines-Iowa/Sr-Exec-Asst_R1",
         "locationsText": "West Des Moines, Iowa", "bulletFields": ["R253169"]},
        {"title": "BlackLine System Administrator", "externalPath": "/job/Remote/Sysadmin_R2",
         "locationsText": "2 Locations", "bulletFields": ["R253917"]},
    ],
}


def test_workday_rows_url_and_no_salary():
    base = "https://athene.wd5.myworkdayjobs.com/athene_careers"
    rows = providers._workday_rows(
        _WORKDAY_PAYLOAD, base, "Athene", fa.salary_verdict,
        tenant="athene", dc="wd5", site="athene_careers",
    )
    assert len(rows) == 2
    a = rows[0]
    assert a["id"] == "wd-R253169"
    assert a["title"] == "Sr Executive Assistant"
    assert a["company"] == "Athene"
    assert a["url"] == base + "/job/West-Des-Moines-Iowa/Sr-Exec-Asst_R1"
    assert a["hourly_min"] is None and a["predicted"] is True   # no salary on CxS list
    assert a["verdict"] == "unlisted"
    assert a["location"] == "West Des Moines, Iowa"             # passes commute gate downstream
    assert a["_cxs"] == ("athene", "wd5", "athene_careers", "/job/West-Des-Moines-Iowa/Sr-Exec-Asst_R1")


def test_workday_rows_skip_missing_path():
    payload = {"jobPostings": [{"title": "No Path Job", "bulletFields": ["X"]}]}
    assert providers._workday_rows(
        payload, "https://x.wd5.myworkdayjobs.com/s", "X", fa.salary_verdict,
        tenant="x", dc="wd5", site="s",
    ) == []


_SR_PAYLOAD = {
    "totalFound": 2,
    "content": [
        {"id": "abc123", "name": "Administrative Assistant III",
         "company": {"identifier": "WellmarkInc"},
         "location": {"city": "Des Moines", "region": "IA", "country": "us", "remote": False},
         "releasedDate": "2026-06-10T00:00:00.000Z"},
        {"id": "def456", "name": "Remote Coordinator",
         "company": {"identifier": "WellmarkInc"},
         "location": {"city": "", "region": "", "country": "us", "remote": True,
                      "fullLocation": "Remote, US"},
         "releasedDate": "2026-06-09T00:00:00.000Z"},
    ],
}


def test_smartrecruiters_rows_url_location_and_remote():
    rows = providers._smartrecruiters_rows(_SR_PAYLOAD, "Wellmark", fa.salary_verdict)
    assert len(rows) == 2
    a = rows[0]
    assert a["id"] == "sr-abc123"
    assert a["title"] == "Administrative Assistant III"
    assert a["company"] == "Wellmark"
    assert a["url"] == "https://jobs.smartrecruiters.com/WellmarkInc/abc123"
    assert a["location"] == "Des Moines, IA"      # passes the metro filter
    assert a["source"] == "local"
    assert a["verdict"] == "unlisted"             # no salary on the list endpoint
    assert rows[1]["source"] == "remote"          # remote flag honored


def test_smartrecruiters_skips_incomplete():
    payload = {"content": [{"name": "No Id", "company": {"identifier": "X"}}]}  # no id
    assert providers._smartrecruiters_rows(payload, "X", fa.salary_verdict) == []
