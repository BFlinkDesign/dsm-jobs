"""Supabase operating-rule guards.

These are static by design: PR CI cannot depend on private Supabase secrets, but
it can enforce that the trusted live verifier exists and stays safe to run.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_supabase_verifier_has_no_legacy_local_fallback():
    verifier = _read("scripts/verify_supabase_schema.py")
    assert "Desktop" not in verifier
    assert "admin-job-finder" not in verifier
    assert "LEGACY_ENV" not in verifier
    assert "DSM_JOBS_SUPABASE_ENV_FILE" in verifier


def test_supabase_verifier_never_prints_secret_values():
    verifier = _read("scripts/verify_supabase_schema.py")
    assert "values are never printed" in verifier
    assert "Env keys present" in verifier
    assert "present_keys()" in verifier
    assert "--require-full" in verifier
    assert "SUPABASE_SERVICE_KEY" in verifier
    assert "SUPABASE_ACCESS_TOKEN" in verifier
    assert "SUPABASE_DB_PASSWORD" in verifier
    assert "SUPABASE_POOLER_HOST" in verifier
    assert "postgres_query" in verifier
    assert "aws-0-us-east-2.pooler.supabase.com" not in verifier
    assert "do not guess" in verifier.lower()
    assert "print(os.environ" not in verifier


def test_supabase_live_schema_check_runs_before_cd_publish():
    scan = _read(".github/workflows/scan.yml")
    verify_pos = scan.index("Verify Supabase schema")
    publish_pos = scan.index("Publish to gh-pages")
    assert verify_pos < publish_pos
    assert "SUPABASE_ACCESS_TOKEN" in scan
    assert "python scripts/verify_supabase_schema.py --require-full" in scan


def test_supabase_snapshot_script_preserves_data_and_settings():
    snapshot = _read("scripts/snapshot_supabase.py")
    assert "auth_users.json" in snapshot
    assert "auth_settings_public.json" in snapshot
    assert "PRIMARY_KEYS" in snapshot
    for table in ("user_profile", "chat_messages", "job_notes", "user_job_status", "ai_usage", "jobs"):
        assert table in snapshot
    assert "SUPABASE_SERVICE_KEY" in snapshot
    assert "sha256" in snapshot


def test_supabase_preservation_runbook_requires_seeded_cutover():
    runbook = _read("docs/SUPABASE-PRESERVATION-RUNBOOK.md")
    assert "Do not cut over" in runbook
    assert "latest production snapshot" in runbook
    assert "Do not accept count-only validation" in runbook
    for table in ("user_profile", "chat_messages", "job_notes", "user_job_status", "ai_usage", "jobs"):
        assert table in runbook


def test_edge_checks_are_not_path_limited():
    edge = _read(".github/workflows/edge-checks.yml")
    assert "pull_request:" in edge
    assert "paths:" not in edge
