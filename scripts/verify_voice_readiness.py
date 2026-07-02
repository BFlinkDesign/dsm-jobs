"""Local readiness gate for Rudy voice / Chatterbox launch.

This script is deliberately value-blind: it reads env files only to collect key
names, never prints values, and fails closed when the live Supabase snapshot
credentials are not available. It is safe to run before any Supabase mutation.
"""

from __future__ import annotations

import json
import os
import re
import sys
from argparse import ArgumentParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_REF = "tcclohxvhmwgjrtdkkuw"


def secrets_dir() -> Path:
    override = os.environ.get("DSM_JOBS_SECRETS_DIR")
    if override:
        return Path(override)
    return Path.home() / "Secrets" / "dsm-jobs"


def voice_env_path() -> Path:
    override = os.environ.get("DSM_JOBS_VOICE_ENV_FILE")
    if override:
        return Path(override)
    return secrets_dir() / "edge-voice.env"


def admin_env_path() -> Path:
    override = os.environ.get("DSM_JOBS_SUPABASE_ENV_FILE")
    if override:
        return Path(override)
    return secrets_dir() / "supabase-admin.env"


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def env_key_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names: set[str] = set()
    text = path.read_text(encoding="utf-8-sig")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, _value = line.partition("=")
        key = key.strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            names.add(key)
    return names


def source_checks() -> tuple[bool, list[str]]:
    failures: list[str] = []
    cfg = read_text("supabase/config.toml")
    voice = read_text("supabase/functions/voice/index.ts")
    app = read_text("app/src/scripts/app.ts")
    page = read_text("app/src/pages/index.astro")

    if f'project_id = "{PROJECT_REF}"' not in cfg:
        failures.append("supabase/config.toml project_id does not match production ref")
    if "[functions.voice]" not in cfg:
        failures.append("supabase/config.toml is missing [functions.voice]")
    else:
        voice_section = cfg.split("[functions.voice]", 1)[1].split("[", 1)[0]
        if "verify_jwt = true" not in voice_section:
            failures.append("voice function verify_jwt is not pinned true")
    if 'if (env("REPLICATE_API_TOKEN")) return "chatterbox";' not in voice:
        failures.append("voice function does not select Chatterbox from REPLICATE_API_TOKEN")
    if 'case "chatterbox": return await ttsChatterbox(clean, voiceId);' not in voice:
        failures.append("voice function does not dispatch to ttsChatterbox")
    if 'default: return json({ unconfigured: true });' not in voice:
        failures.append("voice function must fail open to browser fallback when unconfigured")
    if "edgeSpeak" not in app:
        failures.append("client does not call the voice Edge Function")
    if "MediaRecorder -> voice Edge Function" not in page:
        failures.append("Rudy mic copy is not tied to server-side voice")
    return not failures, failures


def env_checks() -> tuple[bool, dict[str, object]]:
    voice_path = voice_env_path()
    admin_path = admin_env_path()
    voice_names = env_key_names(voice_path)
    admin_names = env_key_names(admin_path)

    voice_missing = sorted({"REPLICATE_API_TOKEN"} - voice_names)
    admin_missing: list[str] = []
    if "SUPABASE_URL" not in admin_names:
        admin_missing.append("SUPABASE_URL")
    if "SUPABASE_SERVICE_KEY" not in admin_names:
        admin_missing.append("SUPABASE_SERVICE_KEY")
    full_verify_ready = (
        "SUPABASE_ACCESS_TOKEN" in admin_names
        or {"SUPABASE_DB_PASSWORD", "SUPABASE_POOLER_HOST"}.issubset(admin_names)
    )
    if not full_verify_ready:
        admin_missing.append("SUPABASE_ACCESS_TOKEN or SUPABASE_DB_PASSWORD+SUPABASE_POOLER_HOST")

    payload: dict[str, object] = {
        "voice_env": {
            "path": str(voice_path),
            "exists": voice_path.is_file(),
            "present_keys": sorted(voice_names),
            "missing_keys": voice_missing,
        },
        "supabase_admin_env": {
            "path": str(admin_path),
            "exists": admin_path.is_file(),
            "present_keys": sorted(admin_names),
            "missing_keys": admin_missing,
        },
    }
    return not voice_missing and not admin_missing, payload


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Value-blind Rudy voice readiness checker.")
    parser.add_argument("--source-only", action="store_true", help="Skip local secret-file readiness checks.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable status.")
    args = parser.parse_args(argv)

    source_ok, source_failures = source_checks()
    ok = source_ok
    payload: dict[str, object] = {
        "project_ref": PROJECT_REF,
        "source": {"ok": source_ok, "failures": source_failures},
    }
    if not args.source_only:
        env_ok, env_payload = env_checks()
        ok = ok and env_ok
        payload.update(env_payload)

    payload["status"] = "GO" if ok else "NO-GO"
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Voice readiness: {payload['status']}")
        print(f"Project ref: {PROJECT_REF}")
        if source_failures:
            for failure in source_failures:
                print(f"NO-GO: {failure}")
        if not args.source_only:
            for label in ("voice_env", "supabase_admin_env"):
                block = payload[label]
                assert isinstance(block, dict)
                print(f"{label}: {block['path']}")
                print(f"  exists: {'yes' if block['exists'] else 'no'}")
                print(f"  present keys: {', '.join(block['present_keys']) or '(none)'}")
                missing = block["missing_keys"]
                if missing:
                    print(f"  missing keys: {', '.join(missing)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
