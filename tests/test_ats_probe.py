"""scripts/ats_probe.py -- offline smoke tests.

No network: this is a diagnostic CLI meant to be run from GitHub Actions
(real egress) against candidate ATS boards not yet in providers.py. These
tests only prove the dispatch/formatting is callable and fails soft (never
raises) on bad input / network errors -- matching the "one bad provider
never kills the run" convention used everywhere else in providers.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import ats_probe  # noqa: E402


def test_unknown_provider_fails_soft():
    r = ats_probe.probe_one("not-a-real-provider", ["x"])
    assert r.ok is False
    assert "unknown provider type" in r.error


def test_workday_wrong_arity_fails_soft():
    r = ats_probe.probe_one("workday", ["only-one-arg"])
    assert r.ok is False
    assert "expects" in r.error


def test_greenhouse_probe_handles_network_error_gracefully(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider HTTP 404: not found")

    monkeypatch.setattr(ats_probe.p, "_request_json", boom)
    r = ats_probe.probe_greenhouse("some-nonexistent-board")
    assert r.ok is False
    assert "404" in r.error


def test_lever_probe_handles_empty_response(monkeypatch):
    monkeypatch.setattr(ats_probe.p, "_request_json", lambda url: [])
    r = ats_probe.probe_lever("empty-board")
    assert r.ok is True
    assert r.count == 0


def test_neogov_probe_counts_items(monkeypatch):
    xml = "<rss><channel><item><title>A</title></item><item><title>B</title></item></channel></rss>"
    monkeypatch.setattr(ats_probe.p, "_request_text", lambda url: xml)
    r = ats_probe.probe_neogov("someslug")
    assert r.ok is True
    assert r.count == 2


def test_smartrecruiters_probe_reads_total_found(monkeypatch):
    monkeypatch.setattr(ats_probe.p, "_request_json", lambda url: {"totalFound": 7})
    r = ats_probe.probe_smartrecruiters("someco")
    assert r.ok is True
    assert r.count == 7


def test_render_summary_is_callable_and_reports_pass_fail():
    results = [ats_probe.probe_one("bogus", ["x"])]
    out = ats_probe.render_summary(results)
    assert "0/1" in out
    assert "ATS candidate probe results" in out


def test_main_requires_two_args(capsys):
    assert ats_probe.main([]) == 1
    assert ats_probe.main(["greenhouse"]) == 1


def test_main_runs_end_to_end_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(ats_probe.p, "_request_json", lambda url: {"totalFound": 3})
    rc = ats_probe.main(["smartrecruiters", "someco"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "smartrecruiters" in out
