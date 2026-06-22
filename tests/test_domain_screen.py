"""domain_screen — apply URL helpers + WHOIS age (mocked in tests)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import domain_screen as ds
import find_admin_jobs as fa


def test_apply_host_strips_www():
    assert ds.apply_host("https://www.jobs.example.com/apply/1") == "jobs.example.com"


def test_normalize_apply_url_ignores_query():
    a = ds.normalize_apply_url("https://jobs.example.com/Apply/42?utm=1")
    b = ds.normalize_apply_url("https://www.jobs.example.com/apply/42")
    assert a == b == "jobs.example.com/apply/42"


def test_trusted_apply_host_greenhouse():
    assert ds.is_trusted_apply_host("boards.greenhouse.io")
    assert ds.is_skipped_apply_host("www.adzuna.com")


def test_plausible_phone_rejects_fiction_range():
    hints = fa.extract_contact_hints("Call (515) 555-0100 or (515) 244-1212")
    assert hints["contactPhone"] == "(515) 244-1212"


def test_scam_hides_young_remote_domain(monkeypatch):
    monkeypatch.setattr(ds, "domain_age_days", lambda host, cache=None: 10)
    row = {
        "title": "Administrative Assistant",
        "company": "Mystery LLC",
        "location": "Remote",
        "description": "",
        "url": "https://brand-new-scam.example/apply",
        "source": "remote",
        "hourly_min": None,
        "hourly_max": None,
        "_apply_host": "brand-new-scam.example",
        "_domain_age_days": 10,
    }
    out = fa.scam_assessment(row, {})
    assert out["level"] == "scam"
    assert any("registered 10 days ago" in r for r in out["reasons"])


def test_dedupe_collapses_same_apply_url():
    a = {
        "id": "1", "title": "Admin Assistant", "company": "Co A", "location": "Des Moines, IA",
        "created": "2026-06-01", "url": "https://boards.greenhouse.io/co/jobs/123",
    }
    b = {
        "id": "2", "title": "Admin Asst", "company": "Co A", "location": "DSM",
        "created": "2026-06-10", "url": "https://www.boards.greenhouse.io/co/jobs/123?gh_src=adzuna",
    }
    rows, collapsed = fa.dedupe_rows([a, b])
    assert collapsed == 1 and len(rows) == 1
    assert rows[0]["id"] == "2"


def test_enrich_blocklist_autogen_idempotent(tmp_path):
    path = tmp_path / "autogen.txt"
    path.write_text("# header\n", encoding="utf-8")
    hidden = [{
        "url": "https://fresh-scam.example/job",
        "_apply_host": "fresh-scam.example",
        "_domain_age_days": 12,
        "scam": {"level": "scam", "reasons": ["test"]},
    }]
    added = ds.enrich_blocklist_autogen(hidden, str(path))
    assert added == ["fresh-scam.example"]
    assert ds.enrich_blocklist_autogen(hidden, str(path)) == []


def test_load_blocklist_includes_autogen(tmp_path, monkeypatch):
    main = tmp_path / "scam_blocklist.txt"
    auto = tmp_path / "scam_blocklist_autogen.txt"
    main.write_text("manual corp\n", encoding="utf-8")
    auto.write_text("auto-domain.example\n", encoding="utf-8")
    items = fa.load_blocklist(str(main))
    assert "manual corp" in items and "auto-domain.example" in items
