"""Apply-domain screening for the scam shield — stdlib-only WHOIS age checks.

Adzuna apply links are JS redirects (no employer domain). ATS/gov sources
expose real apply URLs; this module checks those domains on remote postings
from unrecognized employers. Fail-open: a WHOIS timeout never hides a job.
"""

from __future__ import annotations

import os
import re
import socket
import time
import urllib.parse
from datetime import datetime, timezone

# Young domains on remote unknown postings are a high-precision scam signal.
MIN_DOMAIN_AGE_DAYS = 45
# Bounded per scan so a huge result set cannot stall the nightly CD job.
MAX_WHOIS_LOOKUPS_PER_SCAN = 40
_WHOIS_SLEEP_S = 0.35

# Known ATS / government apply hosts — platform domains, not per-employer sites.
TRUSTED_APPLY_HOST_SUFFIXES = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "governmentjobs.com",
    "smartrecruiters.com",
    "usajobs.gov",
    "icims.com",
    "ultipro.com",
    "adp.com",
    "paycomonline.net",
    "jobvite.com",
    "taleo.net",
    "successfactors.com",
    "oraclecloud.com",
    "dayforcehcm.com",
    "bamboohr.com",
    "paylocity.com",
    "isolvedhire.com",
)

# Aggregator / redirect hosts — domain age is meaningless here.
SKIP_APPLY_HOST_SUFFIXES = (
    "adzuna.com",
    "indeed.com",
    "jooble.org",
    "ziprecruiter.com",
    "glassdoor.com",
    "monster.com",
    "careerbuilder.com",
    "linkedin.com",
    "simplyhired.com",
)

_DATE_PATTERNS = (
    re.compile(r"Creation Date:\s*(\S+)", re.I),
    re.compile(r"Created Date:\s*(\S+)", re.I),
    re.compile(r"Registered on:\s*(\S+)", re.I),
    re.compile(r"created:\s*(\S+)", re.I),
    re.compile(r"Registration Time:\s*(\S+)", re.I),
)

_TLD_WHOIS = {
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "org": "whois.pir.org",
    "io": "whois.nic.io",
    "co": "whois.nic.co",
    "us": "whois.nic.us",
    "info": "whois.afilias.net",
}

# A real DNS hostname: dot-separated alnum/hyphen labels, letters-only TLD. The
# letters-only TLD also rejects IP literals. We only ever open a port-43 socket
# to a string that matches this — so an attacker-chosen apply host, or a poisoned
# IANA referral, can't redirect the WHOIS lookup to an internal name or IP (SSRF).
_VALID_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# A count budget alone can't bound runtime: each lookup can stall on socket
# timeouts (8s connect + 8s/recv-chunk), so 40 slow lookups could blow past the
# scan's 540s step timeout. A wall-clock deadline caps total WHOIS time per scan.
MAX_WHOIS_SECONDS_PER_SCAN = 120
_lookup_budget = MAX_WHOIS_LOOKUPS_PER_SCAN
_deadline_monotonic: float | None = None


def reset_lookup_budget() -> None:
    global _lookup_budget, _deadline_monotonic
    _lookup_budget = MAX_WHOIS_LOOKUPS_PER_SCAN
    _deadline_monotonic = time.monotonic() + MAX_WHOIS_SECONDS_PER_SCAN


def apply_host(url: str) -> str:
    """Lowercase hostname from an http(s) apply URL, or ''."""
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_apply_url(url: str) -> str:
    """Stable apply URL for dedupe: host + path, no query/fragment."""
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/").lower()
    return f"{host}{path}" if host else ""


def _host_matches_suffix(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def is_trusted_apply_host(host: str) -> bool:
    return bool(host) and _host_matches_suffix(host, TRUSTED_APPLY_HOST_SUFFIXES)


def is_skipped_apply_host(host: str) -> bool:
    return bool(host) and _host_matches_suffix(host, SKIP_APPLY_HOST_SUFFIXES)


def _whois_raw(query: str, server: str) -> str:
    with socket.create_connection((server, 43), timeout=8) as sock:
        sock.sendall((query + "\r\n").encode())
        chunks: list[bytes] = []
        sock.settimeout(8)
        while True:
            try:
                block = sock.recv(8192)
            except socket.timeout:
                break
            if not block:
                break
            chunks.append(block)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _whois_server_for_domain(domain: str) -> str:
    """Resolve the WHOIS server for a host, or "" to skip. Never guesses an
    unvalidated host: a referral is honored only if it is itself a real
    hostname, and the old `whois.nic.{tld}` guess is dropped entirely."""
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if tld in _TLD_WHOIS:
        return _TLD_WHOIS[tld]
    try:
        ref = _whois_raw(tld, "whois.iana.org")
        match = re.search(r"whois:\s*(\S+)", ref, re.I)
        if match:
            server = match.group(1).strip().lower().rstrip(".")
            if _VALID_HOSTNAME.match(server):  # reject IP / internal / poisoned referral
                return server
    except OSError:
        pass
    return ""


def _parse_whois_date(raw: str) -> datetime | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        token = match.group(1).strip().rstrip(")")
        iso = token.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
            # WHOIS dates are UTC by convention; a naive value must NOT be read as
            # the runner's local time (that shifts the age across the < 45d cutoff).
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            return dt
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y", "%Y.%m.%d"):
            try:
                parsed = datetime.strptime(token[:10], fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def domain_creation_utc(host: str, cache: dict[str, datetime | None] | None = None) -> datetime | None:
    """Creation date from WHOIS, cached per host. None on skip/failure."""
    if not host or is_trusted_apply_host(host) or is_skipped_apply_host(host):
        return None
    store = cache if cache is not None else {}
    if host in store:
        return store[host]
    global _lookup_budget
    if _lookup_budget <= 0:
        store[host] = None
        return None
    if _deadline_monotonic is not None and time.monotonic() > _deadline_monotonic:
        store[host] = None       # WHOIS time budget for this scan is spent — fail open
        return None
    if not _VALID_HOSTNAME.match(host):  # only ever query a real hostname (SSRF guard)
        store[host] = None
        return None
    _lookup_budget -= 1
    created: datetime | None = None
    try:
        server = _whois_server_for_domain(host)
        if server:                       # unknown TLD with no valid referral -> skip
            raw = _whois_raw(host, server)
            created = _parse_whois_date(raw)
        time.sleep(_WHOIS_SLEEP_S)
    except OSError:
        created = None
    store[host] = created
    return created


def domain_age_days(host: str, cache: dict[str, datetime | None] | None = None) -> int | None:
    """Whole days since WHOIS creation, or None if unknown/skipped."""
    created = domain_creation_utc(host, cache)
    if created is None:
        return None
    delta = datetime.now(timezone.utc) - created
    return max(0, delta.days)


def domain_is_too_young(host: str, cache: dict[str, datetime | None] | None = None,
                        *, min_days: int = MIN_DOMAIN_AGE_DAYS) -> bool:
    age = domain_age_days(host, cache)
    return age is not None and age < min_days


def annotate_row(row: dict, cache: dict[str, datetime | None]) -> None:
    """Attach apply_host / domain_age_days for audit CSV + blocklist enrichment."""
    host = apply_host(row.get("url") or "")
    row["_apply_host"] = host
    if host and not is_trusted_apply_host(host) and not is_skipped_apply_host(host):
        row["_domain_age_days"] = domain_age_days(host, cache)
    else:
        row["_domain_age_days"] = None


def enrich_blocklist_autogen(hidden_rows: list[dict], path: str) -> list[str]:
    """Append young-domain hosts from scam-hidden rows to the autogen blocklist.

    Idempotent: already-listed hosts are skipped. Returns newly added hosts.
    """
    existing: set[str] = set()
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing.add(line.lower())
                lines.append(line.rstrip("\n"))
    added: list[str] = []
    for row in hidden_rows:
        if row.get("scam", {}).get("level") != "scam":
            continue
        host = row.get("_apply_host") or apply_host(row.get("url") or "")
        age = row.get("_domain_age_days")
        if not host or is_trusted_apply_host(host) or is_skipped_apply_host(host):
            continue
        if age is None or age >= MIN_DOMAIN_AGE_DAYS:
            continue
        key = host.lower()
        if key in existing:
            continue
        existing.add(key)
        added.append(host)
    if not added:
        return []
    if lines and lines[-1] != "":
        lines.append("")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append(f"# auto-added {stamp} — WHOIS age < {MIN_DOMAIN_AGE_DAYS}d on scam-hidden apply domain")
    lines.extend(sorted(added))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return added
