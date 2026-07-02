"""Read-only Supabase production snapshot.

Use this before any Supabase setting, schema, function, or data operation. The
script exports the auth users plus all application tables and writes a manifest
with row counts and SHA-256 hashes. Secret values are loaded from environment,
repo `.env`, or `~/Secrets/dsm-jobs/supabase-admin.env`, but never printed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_PATH = ROOT / "scripts" / "verify_supabase_schema.py"
TABLES = (
    "user_profile",
    "chat_messages",
    "job_notes",
    "user_job_status",
    "ai_usage",
    "jobs",
)
PRIMARY_KEYS = {
    "auth_users": ("id",),
    "user_profile": ("user_id",),
    "chat_messages": ("id",),
    "job_notes": ("id",),
    "user_job_status": ("user_id", "job_id"),
    "ai_usage": ("id",),
    "jobs": ("id",),
}
URL_RE = re.compile(r"^https://[a-z0-9-]+\.supabase\.co$")


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_supabase_schema", VERIFY_PATH)
    if not spec or not spec.loader:
        raise RuntimeError("could not load verify_supabase_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_env() -> None:
    verifier = _load_verifier()
    verifier.load_standard_env(ROOT)


# Same Cloudflare user-agent ban as verify_supabase_schema.py: the default
# Python-urllib/3.x signature draws a 403/1010 at the edge.
_USER_AGENT = "dsm-jobs-snapshot/1.0 (+https://github.com/BFlinkDesign/dsm-jobs)"


def _json_request(url: str, headers: dict[str, str]) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else None


def _fetch_table(base_url: str, service_key: str, table: str) -> list[dict]:
    headers = {
        "User-Agent": _USER_AGENT,
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }
    rows: list[dict] = []
    start = 0
    page_size = 1000
    while True:
        endpoint = f"{base_url}/rest/v1/{urllib.parse.quote(table, safe='')}?select=*"
        req = urllib.request.Request(
            endpoint,
            headers={**headers, "Range": f"{start}-{start + page_size - 1}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read().decode("utf-8"))
        if not isinstance(page, list):
            raise RuntimeError(f"unexpected {table} response type: {type(page).__name__}")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _snapshot_root() -> Path:
    base = os.environ.get("DSM_JOBS_SNAPSHOT_ROOT")
    if base:
        return Path(base)
    return Path(tempfile.gettempdir()) / "dsm-jobs-production-snapshots"


def main() -> int:
    _load_env()
    base_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not URL_RE.match(base_url):
        print("NO-GO: SUPABASE_URL missing or invalid")
        return 1
    if not service_key:
        print("NO-GO: SUPABASE_SERVICE_KEY missing")
        return 1

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _snapshot_root() / stamp
    out_dir.mkdir(parents=True, exist_ok=False)
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }

    auth = _json_request(f"{base_url}/auth/v1/admin/users?per_page=1000&page=1", headers)
    users = auth.get("users", []) if isinstance(auth, dict) else []
    _write_json(out_dir / "auth_users.json", users)

    public_settings = _json_request(f"{base_url}/auth/v1/settings", headers)
    _write_json(out_dir / "auth_settings_public.json", public_settings)

    counts = {"auth_users": len(users)}
    for table in TABLES:
        rows = _fetch_table(base_url, service_key, table)
        counts[table] = len(rows)
        _write_json(out_dir / f"{table}.json", rows)

    files = {}
    for path in sorted(out_dir.glob("*.json")):
        files[path.name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}

    manifest = {
        "created_utc": stamp,
        "source_url": base_url,
        "counts": counts,
        "primary_keys": PRIMARY_KEYS,
        "files": files,
    }
    _write_json(out_dir / "manifest.json", manifest)
    print(f"Snapshot written: {out_dir}")
    print(json.dumps({"counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
