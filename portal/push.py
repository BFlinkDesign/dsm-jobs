"""Scanner -> Supabase: upsert the day's safe rows into public.jobs.

Transport only — row shaping lives in find_admin_jobs._portal_rows() so the
display strings (pay_text, trust_label, commute) are built by the exact same
code the PWA uses. Invariant #1 carries over: pay_text is a display string;
a predicted wage is never stored as a number anywhere.

Auth model (per portal/README.md): this module uses the SERVICE key, which
bypasses RLS by design — it runs only in CI or on the operator's machine,
never in a browser. Config comes from the environment:

    SUPABASE_URL          https://<project-ref>.supabase.co
    SUPABASE_SERVICE_KEY  service/secret key (NEVER the publishable key)

Both unset -> push is silently skipped (the static PWA is unaffected).
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse
from typing import Any, Callable

import providers

# Project URL shape is validated before any request is made; the validated
# base then becomes the per-call allowlist for providers._request_json.
_URL_RE = re.compile(r"^https://[a-z0-9-]+\.supabase\.co$")
_BATCH = 500  # PostgREST handles far more; small batches keep errors readable.

Row = dict[str, Any]
Log = Callable[[str], object]


def _config() -> tuple[str, str]:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    return url, key


def supabase_enabled() -> bool:
    url, key = _config()
    return bool(_URL_RE.match(url)) and bool(key)


def purge_stale_jobs(not_seen_before_iso: str, log: Log = print) -> int:
    """Delete portal jobs whose last_seen is older than the cutoff. Returns count."""
    url, key = _config()
    if not _URL_RE.match(url) or not key:
        return 0
    endpoint = (
        f"{url}/rest/v1/jobs?last_seen=lt.{urllib.parse.quote(not_seen_before_iso, safe='')}"
        "&select=id"
    )
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": "return=representation",
    }
    try:
        echo = providers._request_json(
            endpoint,
            headers=headers,
            method="DELETE",
            allowed_prefixes=(url,),
        )
    except Exception as err:  # noqa: BLE001 - caller treats as non-fatal
        raise RuntimeError(f"portal stale purge failed: {err}") from err
    n = len(echo) if isinstance(echo, list) else 0
    if n:
        log(f"  portal : purged {n} stale job(s) not seen since {not_seen_before_iso[:10]}")
    return n


def push_jobs(rows: list[Row], log: Log = print) -> int:
    """Upsert schema-shaped rows into public.jobs. Returns rows written.

    Raises RuntimeError on config/transport errors — the CALLER decides
    whether that is fatal (CI prints a loud warning and still publishes the
    site; the feed must never be hostage to the portal).
    """
    url, key = _config()
    if not _URL_RE.match(url):
        raise RuntimeError("SUPABASE_URL is not a https://<ref>.supabase.co URL")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY is not set")
    if not rows:
        log("  portal : no rows to push")
        return 0

    endpoint = f"{url}/rest/v1/jobs?on_conflict=id&select=id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        # merge-duplicates = UPSERT; representation(select=id) keeps the echo
        # tiny while staying parseable JSON (return=minimal is an empty body).
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    written = 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i : i + _BATCH]
        echo = providers._request_json(
            endpoint,
            headers=headers,
            body=batch,
            allowed_prefixes=(url,),
        )
        if not isinstance(echo, list) or len(echo) != len(batch):
            # Upsert "succeeded" but the echo disagrees — surface it, do not
            # silently count rows that may not exist.
            raise RuntimeError(
                f"portal echo mismatch: sent {len(batch)}, got "
                f"{len(echo) if isinstance(echo, list) else type(echo).__name__}"
            )
        written += len(batch)
    log(f"  portal : upserted {written} jobs")
    return written


def main() -> int:  # pragma: no cover - tiny CLI for operator re-pushes
    print("Run via: python find_admin_jobs.py --push-supabase", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
