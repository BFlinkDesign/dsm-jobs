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
- Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs
  (no auth). absolute_url = REAL employer apply page. pay_input_ranges has no
  reliable period field on the public board API, so its pay is treated as
  unlisted (never guessed). Per-company only (board token list).
- Lever  GET https://api.lever.co/v0/postings/{company}?mode=json (no auth).
  applyUrl = real ATS apply page. salaryRange {min,max,currency,interval};
  interval 'per-hour-wage' used directly, 'per-year-salary' /2080, else skip.
  Per-company only.
- Careerjet  GET https://search.api.careerjet.net/v4/query (key = affid).
  Fields title/company/locations/description/url/date + salary_min/_max/
  salary_type (Y/M/W/D/H, employer-stated). Wired when CAREERJET_AFFID set.
  (careerjet.com/partners/api)
- CareerOneStop (USDOL) -- key-gated stub; response envelope not verifiable
  from the official doc in this pass (bot-blocked), left unimplemented rather
  than guessed. See fetch_careeronestop.
- NEOGOV / GovernmentJobs.com  GET
  https://www.governmentjobs.com/SearchEngine/JobsFeed?agency={slug} (keyless
  RSS). Standard RSS fields (title/link/pubDate) un-namespaced; pay + location
  in the joblisting: namespace (minimumSalary/maximumSalary/salaryCurrency/
  salaryInterval/location/jobId). Salary is EMPLOYER-STATED USD (Hour/Year
  converted to hourly; non-USD or other intervals suppressed) -> stated=True.
  Government posts directly = highest scam-safety. link = real apply page.
  Per-agency: NEOGOV_AGENCIES holds slugs verified live (200 + items) for the
  Des Moines metro. XML parsed with stdlib ElementTree behind a DOCTYPE/ENTITY
  guard (runtime is stdlib-only; defusedxml would break the no-pip-install CD).

Field shapes for Greenhouse/Lever/Careerjet were verified live / against
official docs on 2026-06-10. Re-verify before changing the mappings.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

USAJOBS_HOST = "https://data.usajobs.gov/api/search"
JOOBLE_HOST = "https://jooble.org/api/"
JSEARCH_HOST = "https://jsearch.p.rapidapi.com/search"
GREENHOUSE_HOST = "https://boards-api.greenhouse.io/v1/boards/"
LEVER_HOST = "https://api.lever.co/v0/postings/"
CAREERJET_HOST = "https://search.api.careerjet.net/v4/query"

# Per-company ATS boards, each verified live (HTTP 200 + postings) before
# listing. Operator-extensible: add a board token only after confirming it
# returns 200 + jobs AND its Iowa titles can pass the title allowlist.
# 2026-06-12 probe: olsson 332 jobs (56 IA, DSM office); aloyoga 641 (Jordan
# Creek WDM retail, "sales associate" passes); momsmeals 33 (12 IA, DSM/Ankeny
# customer-service titles). Probed + skipped: boxlunch (2.6k payload, only
# manager titles in IA), tsmg (same-title-across-cities spam shape),
# dwolla (live board, 0 postings — recheck later).
ATS_BOARDS = {
    "greenhouse": ["businessolver", "olsson", "aloyoga"],
    "lever": ["telligen", "momsmeals"],
}

_ALLOWED_PREFIXES = (
    "https://data.usajobs.gov/",
    "https://jooble.org/",
    "https://jsearch.p.rapidapi.com/",
    "https://boards-api.greenhouse.io/",
    "https://api.lever.co/",
    "https://search.api.careerjet.net/",
    "https://www.governmentjobs.com/",
    "https://api.smartrecruiters.com/",
    # Workday hosts are per-tenant subdomains ({tenant}.{dc}.myworkdayjobs.com);
    # fetch_workday passes each board's own host via allowed_prefixes per call.
)


def _request_json(url: str, *, headers: "dict[str, str] | None" = None, body: object = None,
                  attempts: int = 3, allowed_prefixes: "tuple[str, ...] | None" = None) -> object:
    """GET (or POST when body is not None) returning parsed JSON.
    Bounded retry on 5xx/network errors, fail-fast on 4xx — same policy as
    the Adzuna fetcher. allowed_prefixes overrides the provider allowlist for
    callers with their own pinned base URL (portal.push validates the Supabase
    project URL shape before passing it here)."""
    if not url.startswith(allowed_prefixes or _ALLOWED_PREFIXES):  # defense-in-depth, CWE-939
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
    raise RuntimeError("provider request failed after retries")  # unreachable; satisfies type-checker


def _request_text(url: str, *, attempts: int = 3,
                  allowed_prefixes: "tuple[str, ...] | None" = None) -> str:
    """GET returning decoded text (for XML/RSS feeds). Same allowlist + bounded
    retry policy as _request_json; HTTPS-allowlisted hosts only (CWE-939)."""
    if not url.startswith(allowed_prefixes or _ALLOWED_PREFIXES):
        raise RuntimeError("refusing non-allowlisted provider URL")
    req = urllib.request.Request(url, headers={"User-Agent": "admin-job-finder/1.0"})
    for attempt in range(1, attempts + 1):
        try:
            # nosemgrep - url allowlist-pinned above; HTTPS hosts only.
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return resp.read().decode("utf-8-sig", "replace")
        except urllib.error.HTTPError as err:
            if err.code < 500 or attempt == attempts:
                raise RuntimeError(f"provider HTTP {err.code}") from err
        except urllib.error.URLError as err:
            if attempt == attempts:
                raise RuntimeError(f"provider network error: {err.reason}") from err
        time.sleep(5 * attempt)
    raise RuntimeError("unreachable")


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
JSEARCH_REMOTE_QUERY = "remote administrative assistant no degree"

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


# ── ATS boards (Greenhouse / Lever) — no auth, real employer apply URLs ─────
# These are the highest-trust source: a known employer's own application page.
# Per-company (not metro-searchable), so they run off ATS_BOARDS. The scanner's
# own filters (in_polk_or_dallas, is_admin_title, scam shield) then keep only
# DSM-area admin postings — a remote/out-of-area ATS row is dropped downstream.

_REMOTE_LOC_HINTS = ("remote", "work from home", "work remotely", "anywhere", "wfh")


def _ats_source(location):
    """ATS rows carry a freeform location. Mark remote ones 'remote' so the
    county filter doesn't drop them (a trusted employer survives the remote
    scam check); everything else is 'local' and gets county-filtered."""
    loc = (location or "").lower()
    return "remote" if any(h in loc for h in _REMOTE_LOC_HINTS) else "local"


def ats_enabled():
    return bool(ATS_BOARDS.get("greenhouse") or ATS_BOARDS.get("lever"))


def _greenhouse_rows(payload, verdict_fn):
    rows = []
    for j in payload.get("jobs") or []:
        loc = (j.get("location") or {}).get("name") or ""
        # pay_input_ranges has no reliable period on the public board API ->
        # never converted to a number (invariant #1); pay stays unlisted.
        rows.append({
            "id": "gh-" + str(j.get("id") or ""),
            "title": j.get("title") or "",
            "company": (j.get("company_name") or "").strip() or "(company not listed)",
            "location": loc,
            "hourly_min": None,
            "hourly_max": None,
            "predicted": True,                 # no usable stated number here
            "verdict": verdict_fn(None, None, stated=False),
            "created": (j.get("updated_at") or "")[:10],
            "url": j.get("absolute_url") or "",
            "source": _ats_source(loc),
            "description": "",
        })
    return rows


_LEVER_INTERVAL = {"per-hour-wage": 1.0, "per-year-salary": 1.0 / 2080}


def _lever_hourly(sr):
    if not sr:
        return None, None
    factor = _LEVER_INTERVAL.get((sr.get("interval") or "").lower())
    if factor is None:
        return None, None
    try:
        lo = float(sr["min"]) * factor if sr.get("min") is not None else None
        hi = float(sr["max"]) * factor if sr.get("max") is not None else None
    except (TypeError, ValueError):
        return None, None
    return (round(lo, 2) if lo else None), (round(hi, 2) if hi else None)


def _lever_rows(payload, verdict_fn):
    rows = []
    for j in payload or []:
        loc = (j.get("categories") or {}).get("location") or ""
        lo, hi = _lever_hourly(j.get("salaryRange"))
        stated = lo is not None or hi is not None
        rows.append({
            "id": "lvr-" + str(j.get("id") or ""),
            "title": j.get("text") or "",
            "company": "",                     # Lever board is single-company; filled by caller
            "location": loc,
            "hourly_min": lo,
            "hourly_max": hi,
            "predicted": not stated,
            "verdict": verdict_fn(lo, hi, stated=stated),
            "created": "",                     # createdAt is epoch ms; left blank (freshness optional)
            "url": j.get("applyUrl") or j.get("hostedUrl") or "",
            "source": _ats_source(loc),
            "description": (j.get("descriptionPlain") or "")[:2000].strip(),
        })
    return rows


def fetch_ats(titles, location, verdict_fn, log):
    del titles, location
    rows = []
    for token in ATS_BOARDS.get("greenhouse", []):
        try:
            payload = _request_json(f"{GREENHOUSE_HOST}{token}/jobs?pay_transparency=true")
            rows.extend(_greenhouse_rows(payload, verdict_fn))
        except Exception as err:  # noqa: BLE001 - per-board isolation
            print(f"  WARNING: greenhouse board '{token}' failed: {err}", file=sys.stderr)
    for token in ATS_BOARDS.get("lever", []):
        try:
            payload = _request_json(f"{LEVER_HOST}{token}?mode=json")
            board_rows = _lever_rows(payload, verdict_fn)
            for r in board_rows:               # Lever omits company on the row; it's the board token
                r["company"] = token
            rows.extend(board_rows)
        except Exception as err:  # noqa: BLE001
            print(f"  WARNING: lever board '{token}' failed: {err}", file=sys.stderr)
    log(f"  ats    : {len(rows)} postings")
    return rows


# ── Careerjet (key = affid) ────────────────────────────────────────────────

_CAREERJET_TYPE_TO_HOURLY = {"H": 1.0, "Y": 1.0 / 2080}


def careerjet_enabled():
    return bool(os.environ.get("CAREERJET_AFFID"))


def _careerjet_hourly(j):
    factor = _CAREERJET_TYPE_TO_HOURLY.get((j.get("salary_type") or "").upper())
    if factor is None:
        return None, None
    try:
        lo = float(j["salary_min"]) * factor if j.get("salary_min") else None
        hi = float(j["salary_max"]) * factor if j.get("salary_max") else None
    except (TypeError, ValueError):
        return None, None
    return (round(lo, 2) if lo else None), (round(hi, 2) if hi else None)


def _careerjet_rows(payload, verdict_fn):
    rows = []
    for j in payload.get("jobs") or []:
        lo, hi = _careerjet_hourly(j)
        stated = lo is not None or hi is not None
        rows.append({
            "id": "cj-" + str(j.get("url") or "")[-40:],
            "title": j.get("title") or "",
            "company": j.get("company") or "(company not listed)",
            "location": j.get("locations") or "",
            "hourly_min": lo,
            "hourly_max": hi,
            "predicted": not stated,
            "verdict": verdict_fn(lo, hi, stated=stated),
            "created": (j.get("date") or "")[:10],
            "url": j.get("url") or "",
            "source": _ats_source(j.get("locations") or ""),
            "description": (j.get("description") or "").strip(),
        })
    return rows


def fetch_careerjet(titles, location, verdict_fn, log):
    affid = os.environ["CAREERJET_AFFID"]
    rows = []
    for kw in titles:
        params = urllib.parse.urlencode({
            "affid": affid, "keywords": kw, "location": location,
            "locale_code": "en_US", "page": "1", "pagesize": "50",
            "user_ip": "127.0.0.1", "user_agent": "admin-job-finder/1.0",
        })
        payload = _request_json(CAREERJET_HOST + "?" + params)
        rows.extend(_careerjet_rows(payload, verdict_fn))
        time.sleep(0.4)
    log(f"  careerjet: {len(rows)} postings")
    return rows


# ── CareerOneStop (USDOL) — key-gated stub ─────────────────────────────────

def careeronestop_enabled():
    return bool(os.environ.get("CAREERONESTOP_TOKEN") and os.environ.get("CAREERONESTOP_USERID"))


def fetch_careeronestop(titles, location, verdict_fn, log):
    """USDOL List Jobs API (api.careeronestop.org). Lowest scam baseline
    (govt + state workforce boards). NOT wired: the official response envelope
    could not be verified from the doc in this pass, and fabricating a field
    map would risk silent data loss. Wire after confirming the JSON shape
    against a live authed call. Docs: careeronestop.org/Developers/WebAPI."""
    raise NotImplementedError("CareerOneStop field map unverified — confirm envelope before enabling")


# ── NEOGOV / GovernmentJobs.com (keyless RSS, gold-standard scam-safety) ────
# Per-agency public feed: government employers post directly, salaries are
# EMPLOYER-STATED USD ranges, apply URLs are real. Verified live 2026-06-12 —
# only slugs that returned 200 + items are listed (item counts in comments).
# Each agency name is set so employer_is_trusted() ("Government" group) matches.
NEOGOV_FEED = "https://www.governmentjobs.com/SearchEngine/JobsFeed"
NEOGOV_AGENCIES = [
    ("iowa", "State of Iowa"),              # 197 items (statewide; metro-filtered downstream)
    ("desmoines", "City of Des Moines"),    # 13
    # FIX 2026-06-15: the bare "johnston" slug is *Johnston County, NC* (verified
    # live: channel <title> = "Johnston County, NC"), not the Iowa city — it was
    # mislabeled and only ever survived because the Polk/Dallas filter dropped its
    # NC rows. The Iowa city's real slug is "cityofjohnston".
    ("cityofjohnston", "City of Johnston"),  # 4 (Johnston, IA 50131 — Polk County)
    ("urbandale", "City of Urbandale"),     # 2
    ("waukee", "City of Waukee"),           # 2
    # Added 2026-06-15 — Polk/Dallas-County gov feeds verified live (HTTP 200) and
    # confirmed end-to-end through the full filter chain + scam shield.
    #   dallascountyia — surfaces a Receptionist (Adel) TODAY (live-tested).
    #   cityofjohnston, bondurant — Polk-County feeds whose current single posting
    #     is a non-admin title (Support Specialist / Building Official Coordinator)
    #     so they yield 0 right now, but they're cheap, high-trust, and will catch a
    #     clean admin/clerk/receptionist post when one is listed (same rationale as
    #     the tiny urbandale/waukee feeds above).
    # Probed + dropped: dmww (location field is the facility name "Water Works Park",
    #   never a parseable Polk/Dallas city -> can't pass in_polk_or_dallas); Warren-Co
    #   (norwalkiowa) + Story-Co (cityofames) are outside the Polk/Dallas metro filter.
    ("dallascountyia", "Dallas County"),    # 2 (Receptionist, Adel — Dallas County)
    ("bondurant", "City of Bondurant"),     # 1 (Building Official Coordinator; Polk 50035)
    # Added 2026-06-18 — more Polk/Dallas-metro gov employers (CANDIDATES, pending
    # the nightly scan's live confirmation). Gov office/clerk work is an ideal fit:
    # daytime, benefits, no degree. These are cheap + high-trust; the metro location
    # filter drops anything that resolves outside Polk/Dallas, a wrong/dead slug just
    # fails soft, and source-health.yml flags persistently-empty feeds to prune.
    # Slugs follow GovernmentJobs.com's bare-name convention.
    ("westdesmoines", "City of West Des Moines"),   # Polk/Dallas, 50265/66
    ("ankeny", "City of Ankeny"),                   # Polk, 50023
    ("clive", "City of Clive"),                     # Polk/Dallas, 50325
    ("altoona", "City of Altoona"),                 # Polk, 50009
    ("grimes", "City of Grimes"),                   # Polk/Dallas, 50111 (her city)
    ("pleasanthill", "City of Pleasant Hill"),      # Polk, 50327
    ("polkcountyiowa", "Polk County"),              # the metro's biggest gov employer
    ("dmacc", "Des Moines Area Community College"), # Ankeny/DSM campuses
    ("dmps", "Des Moines Public Schools"),          # clerical / front-office roles
]
_NEOGOV_NS = "{http://www.neogov.com/namespaces/JobListing}"
_NEOGOV_INTERVAL = {  # -> multiplier to hourly; only reliably-convertible units
    "hour": 1.0, "hourly": 1.0,
    "year": 1.0 / 2080, "annual": 1.0 / 2080, "annually": 1.0 / 2080,
}


def neogov_enabled():
    return True  # keyless public feeds


def _neogov_hourly(lo_s, hi_s, interval, currency):
    """Employer-stated salary -> hourly. USD only; Hour/Year only. Anything
    else (other currency or pay period we can't convert exactly) -> suppress
    the number rather than guess (invariant #1)."""
    if (currency or "USD").upper() != "USD":
        return None, None
    factor = _NEOGOV_INTERVAL.get((interval or "").strip().lower())
    if factor is None:
        return None, None
    try:
        lo = float(lo_s) * factor if lo_s else None
        hi = float(hi_s) * factor if hi_s else None
    except (TypeError, ValueError):
        return None, None
    return (round(lo, 2) if lo else None), (round(hi, 2) if hi else None)


def _neogov_date(pubdate):
    try:
        return parsedate_to_datetime(pubdate).date().isoformat() if pubdate else ""
    except (TypeError, ValueError):
        return ""


def _neogov_rows(xml_text, company, verdict_fn):
    # XXE / billion-laughs defense WITHOUT a third-party dep (the app's runtime
    # is stdlib-only by design — the CD runs python with no pip install, so
    # defusedxml is not an option here). Both attacks REQUIRE a DTD/entity
    # declaration, and stdlib ElementTree never resolves *external* entities;
    # rejecting any DOCTYPE/ENTITY (case-insensitive) before parsing closes the
    # remaining internal-entity-expansion vector. A genuine RSS feed has neither.
    head = xml_text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in xml_text.upper():
        raise RuntimeError("refusing NEOGOV feed with DTD/entity declarations")
    root = ET.fromstring(xml_text)  # noqa: S314 - DTD/entity-guarded above

    def g(item, tag):
        el = item.find(_NEOGOV_NS + tag)
        if el is None:
            el = item.find(tag)  # standard RSS fields are un-namespaced
        return (el.text or "").strip() if el is not None and el.text else ""

    rows = []
    for it in root.findall(".//item"):
        title = g(it, "title")
        url = g(it, "link") or g(it, "guid")
        if not title or not url:
            continue
        lo, hi = _neogov_hourly(g(it, "minimumSalary"), g(it, "maximumSalary"),
                                g(it, "salaryInterval"), g(it, "salaryCurrency"))
        stated = lo is not None or hi is not None
        loc = g(it, "location").replace(" - ", ", ")  # -> commas for in_polk_or_dallas()
        rows.append({
            "id": "gov-" + (g(it, "jobId") or url)[-40:],
            "title": title,
            "company": company,
            "location": loc,
            "hourly_min": lo,
            "hourly_max": hi,
            "predicted": not stated,
            "verdict": verdict_fn(lo, hi, stated=stated),
            "created": _neogov_date(g(it, "pubDate")),
            "url": url,
            "source": "local",  # government metro postings are in-person
            "description": _strip_tags(g(it, "qualifications") or g(it, "description"))[:2000].strip(),
        })
    return rows


def fetch_neogov(titles, location, verdict_fn, log):
    del titles, location
    rows = []
    for slug, company in NEOGOV_AGENCIES:
        try:
            xml_text = _request_text(f"{NEOGOV_FEED}?agency={slug}")
            rows.extend(_neogov_rows(xml_text, company, verdict_fn))
        except Exception as err:  # noqa: BLE001 - per-agency isolation
            print(f"  WARNING: neogov agency '{slug}' failed: {err}", file=sys.stderr)
    log(f"  gov    : {len(rows)} postings")
    return rows


# ── Workday (keyless CxS public endpoint; big DSM enterprise employers) ─────
# POST {base}/wday/cxs/{tenant}/{site}/jobs with a JSON search body. No salary
# on the list endpoint -> pay stays "Pay not listed" (never guessed). Each board
# was verified live 2026-06-13 (tenant.dc/site read from the employer's real
# careers URL; admin roles confirmed present). Workday is THE enterprise ATS for
# DSM insurers/financials, so this is high admin-role density.
WORKDAY_BOARDS = [
    # (tenant, datacenter, site, company label)
    ("athene", "wd5", "athene_careers", "Athene"),          # West Des Moines HQ
    ("corteva", "wd5", "Corteva", "Corteva Agriscience"),   # Johnston HQ
    ("nationwide", "wd1", "Nationwide_Career", "Nationwide"),
    ("godirect", "wd5", "voya_jobs", "Voya Financial"),
    # Probed 2026-06-15 + REJECTED after live end-to-end testing: hyvee (grocery —
    # "administrative" returns Pharmacy Clerk / Security Officer, 0 office-admin),
    # emcins (0 clean metro admin in snapshot), trinityhealth/MercyOne (national
    # tenant — floods ~100 out-of-state rows; its Des Moines roles carry facility-
    # name locations like "MMCIA - MercyOne West Grand Clinic" that fail the metro
    # filter). Re-add only with a location facet that isolates the DSM metro.
]
# CxS searchText matches title+description; a few admin terms keep volume low
# and precision high vs. pulling every posting from these large employers.
_WORKDAY_QUERIES = ["administrative", "office", "receptionist"]


def workday_enabled():
    return bool(WORKDAY_BOARDS)  # keyless; always on unless the list is emptied


def _workday_rows(payload, base, company, verdict_fn):
    rows = []
    for j in (payload or {}).get("jobPostings") or []:
        title = j.get("title") or ""
        path = j.get("externalPath") or ""
        if not title or not path:
            continue
        bullets = j.get("bulletFields") or []
        jid = bullets[0] if bullets else path
        loc = j.get("locationsText") or ""   # may be "N Locations" (vague -> drops in metro filter)
        rows.append({
            "id": "wd-" + str(jid),
            "title": title,
            "company": company,
            "location": loc,
            "hourly_min": None,
            "hourly_max": None,
            "predicted": True,                # no salary on the CxS list endpoint
            "verdict": verdict_fn(None, None, stated=False),
            "created": "",                    # postedOn is relative text, not a date
            "url": base + path,
            "source": _ats_source(loc),
            "description": "",
        })
    return rows


def fetch_workday(titles, location, verdict_fn, log):
    del titles, location
    rows, seen = [], set()
    for tenant, dc, site, company in WORKDAY_BOARDS:
        host = f"https://{tenant}.{dc}.myworkdayjobs.com"
        api = f"{host}/wday/cxs/{tenant}/{site}/jobs"
        base = f"{host}/{site}"
        for q in _WORKDAY_QUERIES:
            try:
                payload = _request_json(
                    api, headers={"Accept": "application/json"},
                    body={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q},
                    allowed_prefixes=(host + "/",))
                for r in _workday_rows(payload, base, company, verdict_fn):
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        rows.append(r)
            except Exception as err:  # noqa: BLE001 - per-board/query isolation
                print(f"  WARNING: workday '{tenant}/{q}' failed: {err}", file=sys.stderr)
    log(f"  workday: {len(rows)} postings")
    return rows


# ── SmartRecruiters (keyless Posting API) ───────────────────────────────────
SMARTRECRUITERS_HOST = "https://api.smartrecruiters.com/v1/companies/"
SMARTRECRUITERS_COMPANIES = [
    ("wellmarkinc", "Wellmark"),   # Des Moines HQ (Blue Cross Blue Shield)
]


def smartrecruiters_enabled():
    return bool(SMARTRECRUITERS_COMPANIES)


def _smartrecruiters_rows(payload, company, verdict_fn):
    rows = []
    for j in (payload or {}).get("content") or []:
        title = j.get("name") or ""
        ident = (j.get("company") or {}).get("identifier") or ""
        jid = j.get("id") or ""
        if not title or not ident or not jid:
            continue
        loc = j.get("location") or {}
        loc_str = ", ".join(p for p in (loc.get("city"), loc.get("region")) if p) \
            or (loc.get("fullLocation") or "")
        rows.append({
            "id": "sr-" + str(jid),
            "title": title,
            "company": company,
            "location": loc_str,
            "hourly_min": None,
            "hourly_max": None,
            "predicted": True,            # no reliable salary on the list endpoint
            "verdict": verdict_fn(None, None, stated=False),
            "created": (j.get("releasedDate") or "")[:10],
            # Real public apply page; identifier is per-posting (correct casing).
            "url": f"https://jobs.smartrecruiters.com/{ident}/{jid}",
            "source": "remote" if loc.get("remote") else _ats_source(loc_str),
            "description": "",
        })
    return rows


def fetch_smartrecruiters(titles, location, verdict_fn, log):
    del titles, location
    rows = []
    for ident, company in SMARTRECRUITERS_COMPANIES:
        try:
            payload = _request_json(f"{SMARTRECRUITERS_HOST}{ident}/postings?limit=100")
            rows.extend(_smartrecruiters_rows(payload, company, verdict_fn))
        except Exception as err:  # noqa: BLE001 - per-company isolation
            print(f"  WARNING: smartrecruiters '{ident}' failed: {err}", file=sys.stderr)
    log(f"  smartrec: {len(rows)} postings")
    return rows


# ── registry ───────────────────────────────────────────────────────────────

PROVIDERS = [
    ("usajobs", usajobs_enabled, fetch_usajobs),
    ("jooble", jooble_enabled, fetch_jooble),
    ("jsearch", jsearch_enabled, fetch_jsearch),
    ("ats", ats_enabled, fetch_ats),
    ("careerjet", careerjet_enabled, fetch_careerjet),
    ("neogov", neogov_enabled, fetch_neogov),  # keyless gov feeds — always on
    ("workday", workday_enabled, fetch_workday),  # keyless enterprise ATS — always on
    ("smartrecruiters", smartrecruiters_enabled, fetch_smartrecruiters),  # keyless — always on
    # ("careeronestop", careeronestop_enabled, fetch_careeronestop),  # stub
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
