"""Offline tests for the Supabase pre-publish gate's reachability fallback.

No network: urlopen is monkeypatched. Loads the script by path (it lives in
scripts/, not an importable package).
"""

from __future__ import annotations

import importlib.util
import pathlib
import urllib.error

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "verify_supabase_schema.py"
_spec = importlib.util.spec_from_file_location("verify_supabase_schema", _SCRIPT)
assert _spec is not None and _spec.loader is not None
vss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vss)

_URL = "https://tcclohxvhmwgjrtdkkuw.supabase.co"


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
    def __enter__(self) -> "_Resp":
        return self
    def __exit__(self, *a: object) -> None:
        return None


def _set_anon(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", _URL)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "pub_test_key")


def test_reachable_2xx(monkeypatch) -> None:
    _set_anon(monkeypatch)
    monkeypatch.setattr(vss.urllib.request, "urlopen", lambda *a, **k: _Resp(200))
    assert vss.verify_reachable() is True


def test_reachable_on_http_error_is_up(monkeypatch) -> None:
    # 401 from RLS still proves the host is up and answering.
    _set_anon(monkeypatch)
    def _raise(*a, **k):
        raise urllib.error.HTTPError(_URL, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(vss.urllib.request, "urlopen", _raise)
    assert vss.verify_reachable() is True


def test_unreachable_connection_error(monkeypatch) -> None:
    _set_anon(monkeypatch)
    def _raise(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(vss.urllib.request, "urlopen", _raise)
    assert vss.verify_reachable() is False


def test_server_error_is_not_reachable(monkeypatch) -> None:
    _set_anon(monkeypatch)
    def _raise(*a, **k):
        raise urllib.error.HTTPError(_URL, 503, "Service Unavailable", {}, None)
    monkeypatch.setattr(vss.urllib.request, "urlopen", _raise)
    assert vss.verify_reachable() is False


def test_reachable_needs_creds(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert vss.verify_reachable() is False


def test_main_falls_back_to_reachability_on_push(monkeypatch) -> None:
    # Push-event shape: publishable key + URL only (no service key / access token).
    monkeypatch.setattr(vss, "load_env", lambda *a, **k: None)  # ignore any real .env
    for k in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_SERVICE_KEY",
              "SUPABASE_DB_PASSWORD", "SUPABASE_POOLER_HOST"):
        monkeypatch.delenv(k, raising=False)
    _set_anon(monkeypatch)
    monkeypatch.setattr(vss, "verify_reachable", lambda: True)
    assert vss.main([]) == 0


def test_main_no_go_without_any_creds(monkeypatch) -> None:
    monkeypatch.setattr(vss, "load_env", lambda *a, **k: None)
    for k in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_PASSWORD",
              "SUPABASE_POOLER_HOST", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert vss.main([]) == 1
