"""Read-only portal schema verification - no Supabase CLI login required.

Loads keys from repo-root `.env`, then an optional explicit env file named by
`DSM_JOBS_SUPABASE_ENV_FILE`. By default it also checks
`~/Secrets/dsm-jobs/supabase-admin.env` so live-operation credentials can stay
outside the repo. Key names are logged; values are never printed.
Preferred path: Supabase Management API (`SUPABASE_ACCESS_TOKEN` + project ref
from `SUPABASE_URL`). Fallback: PostgREST table probes with
`SUPABASE_SERVICE_KEY` (tables only - RLS/policy checks skipped).

Exit 0 = GO, 1 = NO-GO.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_REF = "tcclohxvhmwgjrtdkkuw"
URL_RE = re.compile(r"^https://([a-z0-9-]+)\.supabase\.co$")

EXPECTED_TABLES = (
    "jobs",
    "user_job_status",
    "job_notes",
    "user_profile",
    "chat_messages",
    "ai_usage",
)

TABLES_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'jobs', 'user_job_status', 'job_notes',
    'user_profile', 'chat_messages', 'ai_usage'
  )
ORDER BY table_name;
"""

RLS_SQL = """
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND c.relname IN (
    'jobs', 'user_job_status', 'job_notes',
    'user_profile', 'chat_messages', 'ai_usage'
  )
ORDER BY c.relname;
"""

POLICIES_SQL = """
SELECT tablename, count(*) AS policy_count
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN (
    'jobs', 'user_job_status', 'job_notes',
    'user_profile', 'chat_messages', 'ai_usage'
  )
GROUP BY tablename
ORDER BY tablename;
"""


def default_secrets_dir() -> Path:
    override = os.environ.get("DSM_JOBS_SECRETS_DIR")
    if override:
        return Path(override)
    return Path.home() / "Secrets" / "dsm-jobs"


def default_admin_env_path() -> Path:
    return default_secrets_dir() / "supabase-admin.env"


def load_env(path: Path, *, override: bool = False) -> None:
    """Read KEY=VALUE lines into os.environ.

    Defaults are first-file-wins. Explicit env-file overrides are intentional
    because operators may point at a fresh snapshot/deploy credential bundle.
    """
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as err:
        print(f"WARN: could not read {path}: {err}", file=sys.stderr)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if override:
            os.environ[key] = val
        else:
            os.environ.setdefault(key, val)


def load_standard_env(root: Path = REPO_ROOT) -> None:
    load_env(root / ".env")
    load_env(default_admin_env_path())
    extra_env = os.environ.get("DSM_JOBS_SUPABASE_ENV_FILE")
    if extra_env:
        load_env(Path(extra_env), override=True)


def present_keys() -> dict[str, bool]:
    names = (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_ACCESS_TOKEN",
        "SUPABASE_DB_PASSWORD",
        "SUPABASE_POOLER_HOST",
    )
    return {name: bool(os.environ.get(name)) for name in names}


def project_ref() -> str | None:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    match = URL_RE.match(url)
    return match.group(1) if match else None


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: object | None = None,
    timeout: float = 30.0,
) -> object:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {err.code} for {url}: {detail[:500]}") from err


def mgmt_query(sql: str) -> list[dict]:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN") or ""
    ref = project_ref() or PROJECT_REF
    if not token:
        raise RuntimeError("SUPABASE_ACCESS_TOKEN is not set")
    url = f"https://api.supabase.com/v1/projects/{ref}/database/query"
    payload = http_json(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        body={"query": sql},
    )
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected Management API response type: {type(payload).__name__}")
    return payload


def postgres_query(sql: str) -> list[dict]:
    try:
        import pg8000.dbapi
    except ImportError as err:
        raise RuntimeError("pg8000 is not installed; run `pip install -r requirements-dev.txt`") from err

    ref = project_ref() or PROJECT_REF
    password = os.environ.get("SUPABASE_DB_PASSWORD") or ""
    if not password:
        raise RuntimeError("SUPABASE_DB_PASSWORD is not set")

    # Supabase session-pooler format from the official docs:
    # postgres.[PROJECT_REF]@aws-0-[REGION].pooler.supabase.com:5432/postgres
    # The region/host must come from verified project config; do not guess it.
    host = os.environ.get("SUPABASE_POOLER_HOST") or ""
    if not host:
        raise RuntimeError("SUPABASE_POOLER_HOST is not set")
    port = int(os.environ.get("SUPABASE_POOLER_PORT") or "5432")
    conn = pg8000.dbapi.connect(
        user=f"postgres.{ref}",
        password=password,
        host=host,
        port=port,
        database="postgres",
        ssl_context=ssl.create_default_context(),
        timeout=20,
        application_name="dsm-jobs-schema-verify",
    )
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            cols = [str(col[0]) for col in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()
    finally:
        conn.close()


def rest_table_ok(base_url: str, service_key: str, table: str) -> bool:
    # No column-specific select — user_job_status has no id column.
    url = f"{base_url}/rest/v1/{urllib.parse.quote(table, safe='')}?limit=0"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise RuntimeError(f"PostgREST probe failed for {table}: HTTP {err.code}") from err


def verify_reachable() -> bool:
    """Lightweight 'is Supabase up?' check using the publishable (anon) key.

    Used on push-triggered deploys, where the powerful service key is
    intentionally withheld (security). It can't verify schema depth, but it
    confirms the REST backend is reachable so a freshly published frontend
    isn't pointed at a dead API. Any HTTP response (incl. 401/403/404 from RLS)
    proves the host is up; only a 5xx or a connection error counts as down.
    """
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or ""
    if not URL_RE.match(url) or not key:
        print("FAIL: need SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY for reachability check")
        return False
    req = urllib.request.Request(
        f"{url}/rest/v1/", headers={"apikey": key}, method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            print(f"Supabase REST reachable: HTTP {resp.status}")
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as err:
        # An HTTP error status still proves the host responded (it's up).
        print(f"Supabase REST reachable: HTTP {err.code}")
        return 200 <= err.code < 500
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        print(f"FAIL: Supabase unreachable: {err}")
        return False


def print_rows(label: str, rows: list[dict]) -> None:
    print("")
    print(f"=== {label} ===")
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    print("\t".join(cols))
    for row in rows:
        print("\t".join(str(row.get(c, "")) for c in cols))


def verify_full(query) -> bool:
    ok = True
    tables = query(TABLES_SQL)
    print_rows("Portal tables present", tables)
    found = {str(r.get("table_name")) for r in tables}
    missing = [t for t in EXPECTED_TABLES if t not in found]
    if missing:
        print(f"FAIL: missing tables: {', '.join(missing)}")
        ok = False

    rls_rows = query(RLS_SQL)
    print_rows("RLS enabled on portal tables", rls_rows)
    for row in rls_rows:
        name = str(row.get("table_name"))
        enabled = row.get("rls_enabled")
        if enabled not in (True, "t", "true", 1):
            print(f"FAIL: RLS not enabled on {name}")
            ok = False
    if len(rls_rows) != len(EXPECTED_TABLES):
        print(f"FAIL: expected {len(EXPECTED_TABLES)} RLS rows, got {len(rls_rows)}")
        ok = False

    policy_rows = query(POLICIES_SQL)
    print_rows("RLS policy counts", policy_rows)
    counts = {str(r.get("tablename")): int(r.get("policy_count") or 0) for r in policy_rows}
    for table in EXPECTED_TABLES:
        if counts.get(table, 0) < 1:
            print(f"FAIL: no RLS policies on {table}")
            ok = False
    return ok


def verify_partial(*, mgmt_failed: bool = False) -> bool:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not URL_RE.match(url) or not key:
        print("FAIL: need SUPABASE_URL + SUPABASE_SERVICE_KEY for PostgREST probe")
        return False
    print("")
    print("=== PostgREST table probes (partial — RLS not checked) ===")
    if mgmt_failed:
        print("WARN: Management API blocked or failed; RLS/policy SQL checks skipped.")
    elif not os.environ.get("SUPABASE_ACCESS_TOKEN"):
        print("WARN: SUPABASE_ACCESS_TOKEN unset; skipping information_schema / pg_policies checks.")
    ok = True
    for table in EXPECTED_TABLES:
        reachable = rest_table_ok(url, key, table)
        status = "OK" if reachable else "MISSING"
        print(f"{table}\t{status}")
        if not reachable:
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Read-only Supabase schema/RLS verifier.")
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="Fail unless schema/RLS/policy checks run via Management API or direct Postgres.",
    )
    args = parser.parse_args(argv)

    load_standard_env()

    keys = present_keys()
    print(f"Repo: {REPO_ROOT}")
    print(f"Project ref: {project_ref() or PROJECT_REF}")
    print("Env keys present (values never shown):")
    for name, present in keys.items():
        print(f"  {name}: {'yes' if present else 'no'}")

    if keys["SUPABASE_PUBLISHABLE_KEY"] and not keys["SUPABASE_SERVICE_KEY"]:
        print("")
        print(
            "NOTE: publishable key alone cannot verify schema depth — "
            "add SUPABASE_SERVICE_KEY or SUPABASE_ACCESS_TOKEN to .env"
        )

    go = False
    mode = "none"

    # 1) Full schema verify — Management API (access token) or direct Postgres.
    if keys["SUPABASE_ACCESS_TOKEN"]:
        print("")
        print("Mode: Management API (read-only SQL via api.supabase.com)")
        try:
            go = verify_full(mgmt_query)
            mode = "full"
        except RuntimeError as err:
            print(f"WARN: Management API unavailable ({err})")
    if mode != "full" and keys["SUPABASE_DB_PASSWORD"] and keys["SUPABASE_POOLER_HOST"]:
        print("")
        print("Mode: direct Postgres via Supabase session pooler (read-only schema checks)")
        try:
            go = verify_full(postgres_query)
            mode = "full"
        except RuntimeError as pg_err:
            print(f"WARN: direct Postgres unavailable ({pg_err})")

    # 2) Partial — PostgREST table probes (needs the service key).
    if mode != "full" and keys["SUPABASE_SERVICE_KEY"] and keys["SUPABASE_URL"]:
        print("")
        print("Mode: PostgREST table probes (tables only; RLS not checked)")
        try:
            go = verify_partial(mgmt_failed=bool(keys["SUPABASE_ACCESS_TOKEN"]))
            mode = "partial"
        except RuntimeError as err:
            print(f"FAIL: {err}")
            return 1

    # 3) Reachability only — publishable/anon key. Used on push-triggered
    #    deploys, where the powerful service key is intentionally withheld
    #    (PR #129 security gate). Confirms the backend is UP so the freshly
    #    published frontend isn't pointed at a dead API; the daily cron and
    #    manual dispatch (which carry full creds) still run the deep verify.
    if mode == "none" and keys["SUPABASE_PUBLISHABLE_KEY"] and keys["SUPABASE_URL"]:
        print("")
        print("Mode: reachability ping (publishable key — no service key on this run)")
        go = verify_reachable()
        mode = "reachable"

    print("")
    if mode == "none":
        print(
            "NO-GO: set SUPABASE_URL plus one of SUPABASE_ACCESS_TOKEN (full verify), "
            "SUPABASE_SERVICE_KEY (table probe), or SUPABASE_PUBLISHABLE_KEY (reachability)"
        )
        return 1

    if go and args.require_full and mode != "full":
        print("NO-GO: --require-full needs successful Management API or direct Postgres checks")
        return 1

    if go:
        print({
            "full": "GO (full schema verify)",
            "partial": "GO (partial — tables only; RLS/policies not checked)",
            "reachable": "GO (reachability only — backend is up; schema not deep-verified)",
        }.get(mode, "GO"))
        return 0
    print("NO-GO: schema checks failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
