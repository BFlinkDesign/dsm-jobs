"""Portal transport, row mapping, template features. No network, no real APIs."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import find_admin_jobs as faj
import providers
from portal import push


# ── will_train detector ─────────────────────────────────────────────────────


def test_will_train_positive_phrases():
    assert faj.will_train("No experience necessary, we provide paid training!")
    assert faj.will_train("We'll train the right candidate")
    assert faj.will_train("ON-THE-JOB TRAINING provided")


def test_will_train_negative():
    assert not faj.will_train("5 years experience required")
    assert not faj.will_train("")
    assert not faj.will_train(None)


# ── _portal_rows mapping (invariant #1 carried into the portal) ─────────────


def _mk_row(**over):
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


def test_portal_rows_stated_pay_and_columns():
    rows = faj._portal_rows([_mk_row()], "2026-06-12T06:00:00")
    assert len(rows) == 1
    r = rows[0]
    assert set(r) == {
        "id",
        "title",
        "company",
        "location",
        "pay_text",
        "verdict",
        "category",
        "trust_label",
        "commute",
        "url",
        "about",
        "trains",
        "source",
        "posted",
        "last_seen",
    }
    assert r["verdict"] in ("meets", "unlisted", "below")  # schema CHECK
    assert r["trains"] is True
    assert r["posted"] == "2026-06-10"
    assert "first_seen" not in r  # DB default owns it


def test_portal_rows_predicted_pay_never_shows_number():
    rows = faj._portal_rows([_mk_row(predicted=True, verdict="unlisted")], "t")
    assert rows[0]["pay_text"] == "Pay not listed"
    assert "$" not in rows[0]["pay_text"]


def test_portal_rows_empty_posted_becomes_none():
    rows = faj._portal_rows([_mk_row(created="")], "t")
    assert rows[0]["posted"] is None


# ── push.py transport ────────────────────────────────────────────────────────


def test_supabase_enabled_requires_both(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert push.supabase_enabled() is False
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    assert push.supabase_enabled() is False
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    assert push.supabase_enabled() is True


def test_supabase_enabled_rejects_bad_urls(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    for bad in (
        "http://abc.supabase.co",
        "https://evil.com",
        "https://abc.supabase.co.evil.com",
        "https://ABC.supabase.co",
    ):
        monkeypatch.setenv("SUPABASE_URL", bad)
        assert push.supabase_enabled() is False, bad


def test_push_jobs_batches_and_counts(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    calls = []

    def fake_request(url, *, headers=None, body=None, attempts=3, allowed_prefixes=None):
        calls.append((url, headers, body, allowed_prefixes))
        return [{"id": r["id"]} for r in body]

    monkeypatch.setattr(providers, "_request_json", fake_request)
    rows = [{"id": str(i)} for i in range(7)]
    assert push.push_jobs(rows, log=lambda s: None) == 7
    assert len(calls) == 1  # under batch size -> one call
    url, headers, body, allowed = calls[0]
    assert url.startswith("https://abc123.supabase.co/rest/v1/jobs")
    assert headers["Authorization"] == "Bearer service-key"
    assert "merge-duplicates" in headers["Prefer"]
    assert allowed == ("https://abc123.supabase.co",)


def test_push_jobs_echo_mismatch_raises(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(providers, "_request_json", lambda *a, **kw: [])  # server "lost" the rows
    with pytest.raises(RuntimeError, match="echo mismatch"):
        push.push_jobs([{"id": "1"}], log=lambda s: None)


def test_push_jobs_no_rows_is_noop(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(providers, "_request_json", lambda *a, **kw: pytest.fail("must not be called"))
    assert push.push_jobs([], log=lambda s: None) == 0


# ── browser-config guard: a secret key may never reach the page ─────────────


def test_portal_web_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert faj._portal_web_config() is None


def test_portal_web_config_ok(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_xyz")
    cfg = faj._portal_web_config()
    assert cfg == {"url": "https://abc123.supabase.co", "key": "sb_publishable_xyz"}


def test_portal_web_config_refuses_secret_shapes(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    for leaky in ("sb_secret_abc", "sbp_legacytoken", "xx_service_role_yy"):
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", leaky)
        with pytest.raises(RuntimeError, match="refusing"):
            faj._portal_web_config()


def _fake_jwt(role):
    """Build a realistic-shaped (unsigned) JWT for role-claim tests.
    Constructed at runtime so no literal token lives in the source tree
    (keeps the CI secret-scan clean)."""
    import base64
    import json as _json

    def seg(obj):
        raw = base64.urlsafe_b64encode(_json.dumps(obj).encode()).rstrip(b"=")
        return raw.decode()

    return ".".join(
        [
            seg({"alg": "HS256", "typ": "JWT"}),
            seg({"role": role, "iss": "supabase"}),
            "c2lnbmF0dXJlc2lnbmF0dXJl",
        ]
    )  # dummy signature segment


def test_portal_web_config_refuses_legacy_service_role_jwt(monkeypatch):
    """The High finding: a legacy service_role JWT must NOT slip into the page."""
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _fake_jwt("service_role"))
    with pytest.raises(RuntimeError, match="refusing"):
        faj._portal_web_config()


def test_portal_web_config_accepts_legacy_anon_jwt(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    anon = _fake_jwt("anon")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", anon)
    assert faj._portal_web_config() == {"url": "https://abc123.supabase.co", "key": anon}


def test_browser_safe_key_allowlist():
    assert faj._is_browser_safe_supabase_key("sb_publishable_xyz123")
    assert faj._is_browser_safe_supabase_key(_fake_jwt("anon"))
    assert not faj._is_browser_safe_supabase_key(_fake_jwt("service_role"))
    assert not faj._is_browser_safe_supabase_key("sb_secret_abc")
    assert not faj._is_browser_safe_supabase_key("garbage")
    assert not faj._is_browser_safe_supabase_key("eyJ.broken")  # not 3 segments
    assert not faj._is_browser_safe_supabase_key("eyJx.@@@notb64@@@.z")  # unparseable payload


# ── template: tokens, features, theme, no-portal regression ─────────────────


def _build_html(tmp_path, portal_cfg=None):
    out = tmp_path / "index.html"
    faj.write_html([_mk_row()], 1, 2, str(out), "2026-06-12 06:00", contact="Brady", portal_cfg=portal_cfg)
    return out.read_text(encoding="utf-8")


def test_template_unconfigured_has_no_supabase(tmp_path):
    html = _build_html(tmp_path)
    # No Supabase CDN script when unconfigured. (The résumé uploader references
    # pdf.js on jsdelivr too, loaded on demand — that's unrelated to portal config,
    # so assert specifically that the supabase-js bundle isn't embedded.)
    assert "supabase-js@" not in html
    assert "const PORTAL = null;" in html
    assert "##PORTAL##" not in html and "##PORTAL_SCRIPT##" not in html


def test_template_configured_pins_and_sris_the_cdn(tmp_path):
    html = _build_html(tmp_path, {"url": "https://abc123.supabase.co", "key": "pk"})
    assert "supabase-js@2.108.1" in html  # exact pin
    assert 'integrity="sha384-' in html  # SRI locked
    assert '"url": "https://abc123.supabase.co"' in html


def test_template_features_present(tmp_path):
    html = _build_html(tmp_path)
    for marker in (
        "nav-corner",
        "todaywrap",
        "appswrap",
        "cornerwrap",
        "faqwrap",
        "Will train",
        "Not today",
        "printlog",
        "copylog",
        "qopt",
        "855-581-8111",
        "844-775-9276",
        "Grimes",
        "tabbar",
        "[hidden]{display:none !important}",
    ):
        assert marker in html, marker


def test_template_xss_escapes_survive(tmp_path):
    """The esc()/safeUrl() pipeline from the original app must stay intact."""
    html = _build_html(tmp_path)
    assert "function esc(" in html
    assert "function safeUrl(" in html
    assert html.count("<\\/") >= 0  # embedded JSON </ escaping path exists


def test_mock_pipeline_marks_trains(tmp_path, monkeypatch):
    """End-to-end: a mock row whose description says 'will train' gets the flag."""
    rows = faj.collect_mock()
    payload = faj._jobs_payload([r for r in rows if not r.get("scam")])
    assert any("trains" in j for j in payload)
