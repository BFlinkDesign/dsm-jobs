"""Extra job-source providers (beyond Adzuna). Stdlib-only.

Each provider activates only when its keys exist in the environment, fails
soft (one bad provider never kills the scan), and emits rows in the scanner's
canonical row shape via the verdict callback passed in from find_admin_jobs —
salary-verdict logic (invariant #1: never promise a guessed wage) lives in
exactly one place, there.

Sources + the doc facts each fetcher is written against:
- USAJobs  GET https://data.usajobs.gov/api/search, headers Host /
  User-Agent(=registered email) / Authorization-Key. Salary in
  PositionRemuneration[] (MinimumRange/MaximumRange strings +
  RateIntervalCode: PH=per hour, PA=per year; others ignored). Salaries come
  from the job announcement itself (no prediction feature) -> stated=True.
  (developer.usajobs.gov: api-reference/get-api-search, guides/authentication,
  codelist/remunerationrateintervalcodes)
- Jooble   POST https://jooble.org/api/{key} with JSON body
  {keywords, location, radius, page}. Response jobs[] fields: title, location,
  snippet, salary (FREE-TEXT STRING, no stated-vs-estimated flag -> we NEVER
  surface it as a number; verdict stays 'unlisted'), source, link, company,
  updated, id (number). (help.jooble.org REST API documentation)
- JSearch  (RapidAPI) -- wired when JSEARCH_API_KEY exists; see fetch_jsearch.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USAJOBS_HOST = "https://data.usajobs.gov/api/search"
JOOBLE_HOST = "https://jooble.org/api/"
JSEARCH_HOST = "https://jsearch.p.rapidapi.com/search"

_ALLOWED_PREFIXES = (
    "https://data.usajobs.gov/",
    "https://jooble.org/",
    "https://jsearch.p.rapidapi.com/",
)


def _request_json(url, *, headers=None, body=None, attempts=3):
    """GET (or POST when body is not None) returning parsed JSON.
    Bounded retry on 5xx/network errors, fail-fast on 4xx — same policy as
    the Adzuna fetcher."""
    if not url.startswith(_ALLOWED_PREFIXES):  # defense-in-depth, CWE-939
        raise RuntimeError("refusing non-allowlisted provider URL")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"User-Agent": "admin-job-finder/1.0"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    for attempt in range(1, attempts + 1):
        try:
            # nosemgrep - url allowlist-pinned above; HTTPS hosts only.
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:200]
            if err.code < 500 or attempt == attempts:
                raise RuntimeError(f"provider HTTP {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            if attempt == attempts:
                raise RuntimeError(f"provider network error: {err.reason}") from err
        time.sleep(5 * attempt)


# ── USAJobs ────────────────────────────────────────────────────────────────

def usajobs_enabled():
    return bool(os.environ.get("USAJOBS_API_KEY")) and bool(os.environ.get("USAJOBS_EMAIL"))


def _usajobs_headers():
    return {
        "Host": "data.usajobs.gov",
        "User-Agent": os.environ["USAJOBS_EMAIL"],
        "Authorization-Key": os.environ["USAJOBS_API_KEY"],
    }


def _usajobs_hourly(remuneration):
    """(hourly_min, hourly_max) from PositionRemuneration[]. PH is per-hour,
    PA per-year (/2080). Other interval codes (BW, PD, PM, WC...) are skipped —
    better 'Pay not listed' than a wrong conversion."""
    for r in remuneration or []:
        code = (r.get("RateIntervalCode") or "").upper()
        try:
            lo = float(r.get("MinimumRange") or 0) or None
            hi = float(r.get("MaximumRange") or 0) or None
        except (TypeError, ValueError):
            continue
        if code == "PH":
            return lo, hi
        if code == "PA":
            return (round(lo / 2080, 2) if lo else None,
                    round(hi / 2080, 2) if hi else None)
    return None, None


def _usajobs_rows(payload, source, verdict_fn):
    rows = []
    items = ((payload.get("SearchResult") or {}).get("SearchResultItems")) or []
    for item in items:
        d = item.get("MatchedObjectDescriptor") or {}
        lo, hi = _usajobs_hourly(d.get("PositionRemuneration"))
        rows.append({
            "id": "usaj-" + str(item.get("MatchedObjectId") or ""),
            "title": d.get("PositionTitle") or "",
            "company": d.get("OrganizationName") or "(agency not listed)",
            "location": d.get("PositionLocationDisplay") or "",
            "hourly_min": lo,
            "hourly_max": hi,
            "predicted": False,             # announcement data; no estimation feature
            "verdict": verdict_fn(lo, hi, stated=True),
            "created": (d.get("PublicationStartDate") or "")[:10],
            "url": d.get("PositionURI") or "",
            "source": source,
            "description": (d.get("QualificationSummary") or "").strip(),
        })
    return rows


def fetch_usajobs(titles, location, verdict_fn, log):
    rows = []
    for kw in titles:
        q = urllib.parse.urlencode({
            "Keyword": kw, "LocationName": location, "Radius": "25",
            "HiringPath": "public", "WhoMayApply": "public",
            "ResultsPerPage": "100",
        })
        payload = _request_json(USAJOBS_HOST + "?" + q, headers=_usajobs_headers())
        rows.extend(_usajobs_rows(payload, "local", verdict_fn))
        time.sleep(0.5)
    # Remote pass: RemoteIndicator=True returns only remote postings.
    q = urllib.parse.urlencode({
        "Keyword": "administrative assistant", "RemoteIndicator": "True",
        "HiringPath": "public", "WhoMayApply": "public", "ResultsPerPage": "100",
    })
    payload = _request_json(USAJOBS_HOST + "?" + q, headers=_usajobs_headers())
    rows.extend(_usajobs_rows(payload, "remote", verdict_fn))
    log(f"  usajobs: {len(rows)} postings")
    return rows


# ── Jooble ─────────────────────────────────────────────────────────────────

def jooble_enabled():
    return bool(os.environ.get("JOOBLE_API_KEY"))


def _jooble_rows(payload, verdict_fn):
    rows = []
    for j in payload.get("jobs") or []:
        # Jooble's salary is an unflagged free-text string ("17,600 UAH") with
        # no stated-vs-estimated provenance -> invariant #1 says it is NEVER
        # shown as a number. Verdict stays 'unlisted'.
        rows.append({
            "id": "joob-" + str(j.get("id") or ""),
            "title": j.get("title") or "",
            "company": j.get("company") or "(company not listed)",
            "location": j.get("location") or "",
            "hourly_min": None,
            "hourly_max": None,
            "predicted": True,              # unverifiable provenance == treat as guess
            "verdict": verdict_fn(None, None, stated=False),
            "created": (j.get("updated") or "")[:10],
            "url": j.get("link") or "",
            "source": "local",
            "description": _strip_tags(j.get("snippet") or ""),
        })
    return rows


def _strip_tags(text):
    out, in_tag = [], False
    for ch in text:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return "".join(out).strip()


def fetch_jooble(titles, location, verdict_fn, log):
    key = os.environ["JOOBLE_API_KEY"]
    rows = []
    # One call with comma-joined keywords (documented request shape), plus a
    # bounded per-title pass for breadth.
    for kw in titles:
        body = {"keywords": kw, "location": location, "radius": "40", "page": "1"}
        payload = _request_json(JOOBLE_HOST + key, body=body)
        rows.extend(_jooble_rows(payload, verdict_fn))
        time.sleep(0.5)
    log(f"  jooble : {len(rows)} postings")
    return rows


# ── JSearch (RapidAPI / OpenWeb Ninja) ─────────────────────────────────────
# Free BASIC plan: 200 requests/MONTH, hard-limited; EACH RESULT PAGE = one
# credit. So this provider runs a fixed, small query budget per scan instead
# of per-title fan-out (5 queries/day ~= 150 credits/month).
# Search-result salaries are parsed from the listing itself (null when the
# employer didn't state pay; estimates live on separate endpoints) -> a
# non-null salary here is employer-stated.

JSEARCH_LOCAL_QUERIES = [
    "administrative assistant jobs in des moines iowa",
    "receptionist jobs in des moines iowa",
    "office assistant jobs in des moines iowa",
    "data entry clerk jobs in des moines iowa",
]
JSEARCH_REMOTE_QUERY = "remote administrative assistant entry level"

_JSEARCH_PERIOD_TO_HOURLY = {"HOUR": 1.0, "YEAR": 1.0 / 2080}


def jsearch_enabled():
    return bool(os.environ.get("JSEARCH_API_KEY"))


def _jsearch_hourly(j):
    """Hourly (lo, hi) from job_min_salary/job_max_salary + job_salary_period.
    HOUR/YEAR convert cleanly; WEEK/MONTH/DAY are skipped (better 'Pay not
    listed' than a wrong conversion)."""
    factor = _JSEARCH_PERIOD_TO_HOURLY.get((j.get("job_salary_period") or "").upper())
    if factor is None:
        return None, None
    try:
        lo = float(j["job_min_salary"]) * factor if j.get("job_min_salary") is not None else None
        hi = float(j["job_max_salary"]) * factor if j.get("job_max_salary") is not None else None
    except (TypeError, ValueError):
        return None, None
    return (round(lo, 2) if lo else None), (round(hi, 2) if hi else None)


def _jsearch_apply_url(j):
    """Prefer a DIRECT employer/ATS link (is_direct true) over an aggregator."""
    for opt in j.get("apply_options") or []:
        if opt.get("is_direct") and opt.get("apply_link"):
            return opt["apply_link"]
    return j.get("job_apply_link") or ""


def _jsearch_rows(payload, source, verdict_fn):
    rows = []
    for j in payload.get("data") or []:
        lo, hi = _jsearch_hourly(j)
        stated = lo is not None or hi is not None
        rows.append({
            "id": "jsr-" + str(j.get("job_id") or ""),
            "title": j.get("job_title") or "",
            "company": j.get("employer_name") or "(company not listed)",
            "location": j.get("job_location")
                        or ", ".join(x for x in (j.get("job_city"), j.get("job_state")) if x),
            "hourly_min": lo,
            "hourly_max": hi,
            "predicted": not stated,
            "verdict": verdict_fn(lo, hi, stated=stated),
            "created": (j.get("job_posted_at_datetime_utc") or "")[:10],
            "url": _jsearch_apply_url(j),
            "source": source,
            "description": (j.get("job_description") or "").strip(),
        })
    return rows


def fetch_jsearch(titles, location, verdict_fn, log):
    del titles, location  # fixed query budget (200 credits/month hard cap)
    key = os.environ["JSEARCH_API_KEY"]
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    rows = []
    for q in JSEARCH_LOCAL_QUERIES:
        params = urllib.parse.urlencode({
            "query": q, "page": "1", "num_pages": "1",
            "date_posted": "week", "country": "us",
        })
        payload = _request_json(JSEARCH_HOST + "?" + params, headers=headers)
        rows.extend(_jsearch_rows(payload, "local", verdict_fn))
        time.sleep(0.5)
    params = urllib.parse.urlencode({
        "query": JSEARCH_REMOTE_QUERY, "page": "1", "num_pages": "1",
        "date_posted": "week", "country": "us", "work_from_home": "true",
    })
    payload = _request_json(JSEARCH_HOST + "?" + params, headers=headers)
    rows.extend(_jsearch_rows(payload, "remote", verdict_fn))
    log(f"  jsearch: {len(rows)} postings")
    return rows


# ── registry ───────────────────────────────────────────────────────────────

PROVIDERS = [
    ("usajobs", usajobs_enabled, fetch_usajobs),
    ("jooble", jooble_enabled, fetch_jooble),
    ("jsearch", jsearch_enabled, fetch_jsearch),
]


def collect_extra(titles, location, verdict_fn, log=print):
    """All enabled providers' rows. A provider failure is logged and skipped —
    Adzuna results must still ship if an extra source has a bad day."""
    rows = []
    for name, enabled, fetch in PROVIDERS:
        if not enabled():
            continue
        try:
            rows.extend(fetch(titles, location, verdict_fn, log))
        except Exception as err:  # noqa: BLE001 - isolation boundary, logged
            print(f"  WARNING: provider '{name}' failed, skipping: {err}", file=sys.stderr)
    return rows
