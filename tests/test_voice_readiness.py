from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_voice_readiness.py"


def run_checker(secrets_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DSM_JOBS_SECRETS_DIR"] = str(secrets_dir)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_voice_readiness_fails_closed_without_admin_snapshot_keys(tmp_path: Path):
    (tmp_path / "edge-voice.env").write_text(
        "REPLICATE_API_TOKEN=secret-token-never-print\n"
        "VOICE_TTS=chatterbox\n"
        "CHATTERBOX_MODEL=resemble-ai/chatterbox\n",
        encoding="utf-8",
    )

    result = run_checker(tmp_path)
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["status"] == "NO-GO"
    assert payload["voice_env"]["present_keys"] == [
        "CHATTERBOX_MODEL",
        "REPLICATE_API_TOKEN",
        "VOICE_TTS",
    ]
    assert payload["supabase_admin_env"]["exists"] is False
    assert "secret-token-never-print" not in result.stdout
    assert "secret-token-never-print" not in result.stderr


def test_voice_readiness_passes_with_value_blind_local_secret_files(tmp_path: Path):
    (tmp_path / "edge-voice.env").write_text(
        "REPLICATE_API_TOKEN=secret-token-never-print\n"
        "VOICE_TTS=chatterbox\n"
        "CHATTERBOX_MODEL=resemble-ai/chatterbox\n",
        encoding="utf-8",
    )
    (tmp_path / "supabase-admin.env").write_text(
        "SUPABASE_URL=https://tcclohxvhmwgjrtdkkuw.supabase.co\n"
        "SUPABASE_SERVICE_KEY=service-secret-never-print\n"
        "SUPABASE_ACCESS_TOKEN=access-secret-never-print\n",
        encoding="utf-8",
    )

    result = run_checker(tmp_path)
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["status"] == "GO"
    assert payload["source"]["ok"] is True
    assert payload["voice_env"]["missing_keys"] == []
    assert payload["supabase_admin_env"]["missing_keys"] == []
    assert "secret-token-never-print" not in result.stdout
    assert "service-secret-never-print" not in result.stdout
    assert "access-secret-never-print" not in result.stdout
