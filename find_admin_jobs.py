#!/usr/bin/env python
"""
find_admin_jobs.py - Find admin / clerical jobs near a location, via the Adzuna API.

Built for: entry-level office/admin roles, high-school-diploma friendly, >= $19/hr,
within driving distance of Grimes, IA (Des Moines metro) PLUS remote roles.

Why Adzuna (not scraping Indeed/LinkedIn):
  - Adzuna is a sanctioned job-aggregator API that RE-publishes Indeed/Reed/etc.
    postings. Same jobs, but querying it does not get you IP-banned the way
    scraping Indeed does. Free API key, ~250 calls/day on the free tier.

Setup (one time):
  1. Get a free key at https://developer.adzuna.com/signup  -> you get an
     "Application ID" and an "Application Key".
  2. Copy .env.example to .env and paste the two values in.
  3. python find_admin_jobs.py            (real run)
     python find_admin_jobs.py --mock     (demo run, no key needed)

Output:
  - A clean HTML report you can forward to your friend.
  - A CSV with the same data.
Both land in this folder, dated.

No third-party packages required - stock Python 3 only.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Windows-safe console output (avoid cp1252 crashes); keep print() text ASCII.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --------------------------------------------------------------------------
# CONFIG - edit these to tune the search
# --------------------------------------------------------------------------

COUNTRY = "us"                      # Adzuna country code
LOCATION = "Des Moines, Iowa"      # center of the LOCAL search (DSM metro)
DISTANCE_KM = 32                    # ~20 miles -> covers Grimes, Ankeny, Waukee,
                                    #   WDM, Johnston, Urbandale, Altoona, Clive
MIN_HOURLY = 19.0                  # the friend's wage floor
HOURS_PER_YEAR = 2080             # 40 hrs/wk * 52 wks; used to convert annual<->hourly
MAX_DAYS_OLD = 30                  # ignore stale postings
RESULTS_PER_PAGE = 50             # Adzuna max per page

# Job titles to search (each is a separate query for relevance, then de-duped).
# Search queries (each is one API call per source). Grouped: admin/clerical,
# light office-adjacent, and entry-level general (no-degree, $19+ paths).
TITLES = [
    # admin / clerical
    "administrative assistant", "office assistant", "receptionist", "front desk",
    "data entry", "office clerk", "administrative coordinator", "secretary",
    "clerical", "file clerk",
    # light office-adjacent
    "scheduler", "medical receptionist", "billing clerk", "accounts payable clerk",
    "medical records clerk", "bank teller", "customer service representative",
    "call center representative", "mail clerk",
    # entry-level general (no degree)
    "retail associate", "cashier", "stocker",
    "food service worker", "caregiver", "housekeeper", "production associate",
    "general laborer",
]

# Subset that genuinely exists as remote work (skip remote calls for in-person roles).
REMOTE_TITLES = [
    "administrative assistant", "data entry", "receptionist", "scheduler",
    "customer service representative", "call center representative",
    "billing clerk", "medical records clerk",
]

# Titles that LOOK like admin but are actually skilled/licensed roles a HS-diploma
# entry-level applicant should not be funneled into. Matched against the job title.
EXCLUDE_TITLE_WORDS = [
    "network", "systems", "system administrator", "database", "salesforce",
    "devops", "sql", "linux", "server", "cyber", "security administrator",
    "it administrator", "engineer", "developer", "registered nurse", "pharmacy",
    "phlebotom", "therapist", "physician", "attorney", "paralegal director",
]

# Words that, if present in the title, mark a job as REMOTE.
REMOTE_HINTS = ["remote", "work from home", "wfh", "telecommute", "virtual"]

# ── Scam shield + attainability (the end user cannot self-vet) ─────────────

# Description phrases that are strong job-scam tells (advance-fee, check fraud,
# off-platform "interviews", PII harvesting). Any hit => LIKELY SCAM, hidden.
SCAM_DESCRIPTION_FLAGS = [
    "wire transfer", "cashier's check", "cashier check", "cash a check", "money order",
    "gift card", "bitcoin", "crypto", "venmo", "cash app", "zelle",
    "purchase your own equipment", "buy equipment", "equipment fee", "startup fee",
    "registration fee", "application fee", "pay a fee", "upfront payment", "send money",
    "process payments", "payment processing", "reship", "repackage", "package forwarding",
    "mystery shopper", "secret shopper", "telegram", "whatsapp", "google hangouts",
    "signal app", "text us at", "no experience needed and earn", "weekly pay of $",
    "social security number to apply", "ssn to apply", "bank details to apply",
    "immediate start no interview", "hiring asap no interview",
]

# Title phrases that are scam-prone roles for this profile (esp. remote).
SCAM_TITLE_FLAGS = [
    "personal assistant", "executive assistant to", "package handler remote",
    "reshipping", "payment processor", "money transfer", "mystery shopper",
    "data entry from home", "typing job", "envelope",
]

# Recognizable, lower-risk employers (local/government/known). Boosts to SAFE and
# sorts first. Substring match on company name, case-insensitive. Grouped so the
# app can say WHY an employer is verified ("Government", "Healthcare", ...) —
# that label teaches the end user what a legitimate employer looks like.
TRUSTED_EMPLOYER_GROUPS = {
    "Government": [
        "state of iowa", "city of", "county", "department of", "police",
        "veterans affairs", "social security administration", "library",
    ],
    "School or college": [
        "school district", "community school", "dmacc", "drake university",
        "grand view", "des moines area", "iowa state",
    ],
    "Healthcare": [
        "unitypoint", "mercyone", "broadlawns", "the iowa clinic", "wesley life",
        "mercy", "methodist", "genesis health", "iowa health", "wellpoint",
        "humana", "labcorp", "quest diagnostics", "amgen", "cvs", "walgreens",
    ],
    "Bank or insurance": [
        "wells fargo", "principal financial", "nationwide", "wellmark", "athene",
        "emc insurance", "credit union", "bankers trust", "u.s. bank", "us bank",
        "edward jones", "voya", "marsh",
    ],
    "Major local company": [
        "hy-vee", "hyvee", "fareway", "casey's", "caseys", "john deere", "corteva",
        "pella", "vermeer", "kum & go", "kwik", "meredith", "businessolver",
        "dotdash", "ruan", "kemin", "pioneer", "telligen", "gartner",
    ],
    "National company": [
        "ups", "fedex", "target", "walmart", "amazon", "concentrix",
        "teleperformance", "sykes", "menards", "lowe's", "home depot", "costco",
    ],
    "Staffing agency": [
        "aerotek", "robert half", "kelly services", "kelly", "express employment",
        "adecco", "manpower", "randstad",
    ],
    "Community organization": [
        "goodwill", "salvation army", "ymca",
    ],
}
TRUSTED_EMPLOYER_HINTS = [h for hints in TRUSTED_EMPLOYER_GROUPS.values() for h in hints]

# Local jobs must be in Polk or Dallas County. Adzuna locations are city-based,
# so this is an allowlist of every city/CDP in the two counties; a local posting
# whose location doesn't name one of these places (or the county itself) is
# dropped, even inside the search radius (e.g. Norwalk/Indianola in Warren Co).
# Matched as comma-separated location tokens, exact, case-insensitive.
POLK_DALLAS_PLACES = {
    "polk county", "dallas county",
    # Polk County
    "des moines", "west des moines", "ankeny", "urbandale", "johnston",
    "altoona", "pleasant hill", "clive", "grimes", "windsor heights",
    "bondurant", "polk city", "mitchellville", "elkhart", "alleman",
    "runnells", "saylorville", "sheldahl", "berwick",
    # Dallas County
    "waukee", "adel", "perry", "granger", "dallas center", "van meter",
    "de soto", "minburn", "woodward", "dawson", "redfield", "dexter",
    "linden", "bouton",
}

# Rough drive times from Grimes (the user's home base) to each metro suburb,
# in minutes. Matched as a substring of the posting's location, longest first
# ("west des moines" before "des moines"). Coarse on purpose — it only needs
# to answer "can I get there?", not navigate.
COMMUTE_MINUTES_FROM_GRIMES = {
    "grimes": 5, "dallas center": 12, "johnston": 12, "urbandale": 12,
    "polk city": 15, "waukee": 15, "clive": 15, "adel": 15, "windsor heights": 18,
    "ankeny": 18, "west des moines": 18, "des moines": 20, "saylorville": 20,
    "altoona": 25, "pleasant hill": 25, "bondurant": 25, "granger": 12,
    "perry": 30, "van meter": 25, "woodward": 25, "mitchellville": 30,
}

# Seniority / competitiveness markers -> not attainable for this user; dropped.
SENIORITY_DROP_TERMS = [
    "senior ", "sr. ", "sr ", " lead", "lead ", "manager", "director", "supervisor",
    "head of", "chief", "vp ", "vice president", "executive ", " iii", " iv",
    "level 3", "level iii", "principal admin",
]

# A job is kept only if its TITLE contains one of these admin/clerical terms.
# This is the precision gate: Adzuna fuzzy-matches queries and returns lots of
# "Coordinator/Manager/Specialist/Investigator" roles that are not entry-level
# admin/reception work. Requiring an admin term in the title drops that noise.
# Grouped by the plain-language category shown as a filter chip in the app.
# First matching group wins, so "front desk" lands in Office, not elsewhere.
CATEGORY_TERMS = {
    "Office": [
        "administrative assistant", "admin assistant", "administrative support",
        "administrative coordinator", "administrative specialist", "administrative aide",
        "receptionist", "front desk", "front office", "office assistant",
        "office administrator", "office coordinator", "office clerk", "office support",
        "data entry", "file clerk", "clerk typist", "clerical", "secretary",
        "scheduling coordinator", "scheduler", "office associate", "admin coordinator",
        "billing", "accounts payable", "accounts receivable", "medical records",
        "mail clerk", "patient access", "records clerk", "data clerk", "intake",
    ],
    "Customer service": [
        "customer service", "call center", "bank teller", "teller",
    ],
    "Store & retail": [
        "retail associate", "sales associate", "cashier", "stocker",
    ],
    "Caregiving": [
        "caregiver", "caretaker", "home care",
    ],
    "Food & cleaning": [
        "food service", "dishwasher", "housekeep", "janitor", "custodian",
    ],
    "Production & labor": [
        "production associate", "production worker", "general labor", "laborer",
        "assembler",
    ],
}
ADMIN_TITLE_TERMS = [t for terms in CATEGORY_TERMS.values() for t in terms]

# Phrases that mean a 4-year/college degree is REQUIRED. Jobs matching these are
# dropped (the target applicant has zero college). "preferred" / "associate" are
# intentionally NOT here -- those don't disqualify.
DEGREE_REQUIRED_HINTS = [
    "bachelor", "master's degree", "masters degree", "master degree",
    "4-year degree", "four-year degree", "b.s. degree", "b.a. degree",
    "degree required", "degree is required", "college degree required",
]

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
ADZUNA_ALLOWED_PREFIX = "https://api.adzuna.com/"


# --------------------------------------------------------------------------
# .env loading (tiny, no dependency). Never prints values.
# --------------------------------------------------------------------------

def load_env(path=".env"):
    """Read KEY=VALUE lines from .env into os.environ (utf-8-sig handles BOM)."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except OSError as err:
        print(f"WARN: could not read {path}: {err}")


# Scam/spam blocklist (employer names or domains that are ALWAYS hidden).
BLOCKLIST = []


def load_blocklist(path="scam_blocklist.txt"):
    items = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        items.append(line.lower())
        except OSError:
            pass
    return items


# --------------------------------------------------------------------------
# Adzuna API
# --------------------------------------------------------------------------

def adzuna_request(params, page=1):
    """One Adzuna search call. Returns the parsed JSON dict or raises."""
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        raise RuntimeError(
            "Missing ADZUNA_APP_ID / ADZUNA_APP_KEY. Copy .env.example to .env and "
            "paste your free key from https://developer.adzuna.com/signup"
        )
    query = {"app_id": app_id, "app_key": app_key, "content-type": "application/json"}
    query.update(params)
    url = ADZUNA_BASE.format(country=COUNTRY, page=page) + "?" + urllib.parse.urlencode(query)
    # Defense-in-depth: the URL is built from constants, but hard-pin scheme+host so
    # no value can ever redirect this to file:// or another host (CWE-939).
    if not url.startswith(ADZUNA_ALLOWED_PREFIX):
        raise RuntimeError("refusing non-Adzuna URL")
    req = urllib.request.Request(url, headers={"User-Agent": "admin-job-finder/1.0"})
    # Transient 5xx / network blips killed a scheduled scan (Adzuna 503, 2026-06-10);
    # retry those a bounded number of times. 4xx (bad key, bad request) never retries.
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            # nosemgrep - url validated against ADZUNA_ALLOWED_PREFIX above; HTTPS host only.
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")[:300]
            if err.code < 500 or attempt == attempts:
                raise RuntimeError(f"Adzuna HTTP {err.code}: {body}") from err
            print(f"  Adzuna HTTP {err.code}, retry {attempt}/{attempts - 1}...", file=sys.stderr)
        except urllib.error.URLError as err:
            if attempt == attempts:
                raise RuntimeError(f"Network error contacting Adzuna: {err.reason}") from err
            print(f"  Network error ({err.reason}), retry {attempt}/{attempts - 1}...", file=sys.stderr)
        time.sleep(5 * attempt)


def search_title(title, *, remote=False):
    """Search one title. Local => where+distance; remote => national + 'remote' term."""
    if remote:
        params = {
            "what": f"{title} remote",
            "where": "United States",
            "results_per_page": RESULTS_PER_PAGE,
            "max_days_old": MAX_DAYS_OLD,
            "sort_by": "date",
        }
    else:
        params = {
            "what": title,
            "where": LOCATION,
            "distance": DISTANCE_KM,
            "results_per_page": RESULTS_PER_PAGE,
            "max_days_old": MAX_DAYS_OLD,
            "sort_by": "date",
        }
    data = adzuna_request(params, page=1)
    return data.get("results", [])


# --------------------------------------------------------------------------
# Normalization + classification
# --------------------------------------------------------------------------

def to_hourly(annual):
    if annual is None:
        return None
    try:
        return round(float(annual) / HOURS_PER_YEAR, 2)
    except (TypeError, ValueError):
        return None


def title_excluded(title):
    t = (title or "").lower()
    return any(word in t for word in EXCLUDE_TITLE_WORDS)


def looks_remote(job):
    blob = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return any(h in blob for h in REMOTE_HINTS)


def requires_degree(job):
    blob = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return any(h in blob for h in DEGREE_REQUIRED_HINTS)


def is_admin_title(title):
    """Precision gate: keep only genuine admin/clerical titles."""
    t = (title or "").lower()
    return any(term in t for term in ADMIN_TITLE_TERMS)


def job_category(title):
    """Plain-language category for the title (the app's type-filter chips)."""
    t = (title or "").lower()
    for category, terms in CATEGORY_TERMS.items():
        if any(term in t for term in terms):
            return category
    return ""


def trusted_reason(company):
    """Why an employer is on the trusted list ('Government', ...), or ''."""
    c = (company or "").lower()
    for label, hints in TRUSTED_EMPLOYER_GROUPS.items():
        if any(h in c for h in hints):
            return label
    return ""


def in_polk_or_dallas(location):
    """True when a location names a Polk/Dallas County place (or the county).
    Token match on the comma-separated parts so 'Adel' can't fire on a street
    name. Unknown/blank locations are NOT in-county — the user asked for these
    two counties only."""
    tokens = [t.strip().lower() for t in (location or "").split(",")]
    return any(t in POLK_DALLAS_PLACES for t in tokens)


def commute_text(location):
    """'~15 min drive' for a known metro suburb, else ''. Longest match wins
    so 'West Des Moines' doesn't get Des Moines' time."""
    loc = (location or "").lower()
    best = None
    for town, minutes in COMMUTE_MINUTES_FROM_GRIMES.items():
        if town in loc and (best is None or len(town) > best[0]):
            best = (len(town), minutes)
    return f"~{best[1]} min drive" if best else ""


def snippet(description, limit=240):
    """Short 'what you'd do' excerpt for the card: collapsed whitespace,
    cut at a word boundary. Adzuna descriptions are already plain text."""
    text = " ".join((description or "").split())
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip(",;:.") + "…"


def title_is_remote(job):
    """Stricter than looks_remote: only the TITLE counts, so an in-office job that
    merely mentions 'remote' in its description is NOT treated as remote."""
    return any(h in (job.get("title") or "").lower() for h in REMOTE_HINTS)


def employer_is_trusted(company):
    c = (company or "").lower()
    return any(h in c for h in TRUSTED_EMPLOYER_HINTS)


def is_attainable(title):
    """Drop senior/competitive roles this user realistically won't be hired into."""
    t = (title or "").lower()
    return not any(term in t for term in SENIORITY_DROP_TERMS)


def _norm_company(company):
    return "".join(ch for ch in (company or "").lower() if ch.isalnum())


def build_spam_index(rows):
    """Map (company, core-title-word) -> set of distinct locations. Same employer +
    same role posted across many cities is the classic job-board spam/scam pattern."""
    index = {}
    for r in rows:
        key = (_norm_company(r["company"]), (r["title"] or "").lower()[:25])
        index.setdefault(key, set()).add((r["location"] or "").lower())
    return index


def scam_assessment(row, spam_index):
    """
    Return {"level": "safe"|"suspect"|"scam", "reasons": [...]}.
    Designed to be CONSERVATIVE for a user who would fall for a scam: when in
    doubt about a remote/unknown-employer posting, mark it suspect (hidden).
    """
    reasons = []
    title = (row["title"] or "").lower()
    company = row["company"] or ""
    desc = (row.get("description") or "").lower()

    # Hard blocklist (confirmed scams) overrides everything, including trusted.
    block_blob = (company + " " + (row.get("url") or "")).lower()
    for b in BLOCKLIST:
        if b in block_blob:
            return {"level": "scam", "reasons": ["on scam blocklist (" + b + ")"]}
    trusted = employer_is_trusted(company)
    remote = row["source"] == "remote" or title_is_remote(row)
    hourly = row["hourly_max"] if row["hourly_max"] is not None else row["hourly_min"]

    # Hard scam tells in the description -> always scam.
    for p in SCAM_DESCRIPTION_FLAGS:
        if p in desc:
            reasons.append(f"description mentions '{p}'")
    for p in SCAM_TITLE_FLAGS:
        if p in title:
            reasons.append(f"scam-prone title ('{p}')")

    # Same employer + role spammed across 3+ cities.
    key = (_norm_company(company), title[:25])
    if len(spam_index.get(key, set())) >= 3:
        reasons.append("same posting spammed across many cities")

    # "company not listed" / blank employer.
    if not company.strip() or "not listed" in company.lower():
        reasons.append("no employer name")

    if reasons:
        # Trusted employer can't rescue a hard description tell, but absent those,
        # a known employer downgrades structural noise to safe.
        hard = any("description mentions" in r or "scam-prone" in r for r in reasons)
        if hard:
            return {"level": "scam", "reasons": reasons}
        if trusted:
            return {"level": "safe", "reasons": []}
        return {"level": "scam", "reasons": reasons}

    # No explicit flags. Apply extra suspicion to remote + unknown employer.
    if remote and not trusted:
        # Unrealistic pay for entry remote admin is bait.
        if hourly is not None and hourly >= 30:
            return {"level": "scam",
                    "reasons": [f"remote, unknown employer, pay ${hourly:.0f}/hr is too good for entry admin"]}
        return {"level": "suspect",
                "reasons": ["remote role from an employer we couldn't recognize"]}

    return {"level": "safe", "reasons": []}


def normalize(job, source):
    """Flatten an Adzuna result into the row we care about + a salary verdict."""
    title = job.get("title") or ""
    company = (job.get("company") or {}).get("display_name") or "(company not listed)"
    location = (job.get("location") or {}).get("display_name") or ""
    smin = job.get("salary_min")
    smax = job.get("salary_max")
    predicted = str(job.get("salary_is_predicted", "0")) == "1"

    hourly_min = to_hourly(smin)
    hourly_max = to_hourly(smax)
    # SAFETY: Adzuna *predicts* pay when the employer didn't post it. Those guesses
    # are unreliable per-listing, so we NEVER promise a number from them. Only an
    # employer-STATED salary earns a dollar figure / a $19+ badge. Wage FLOOR test:
    # the LOW end of a stated range must clear $19 ("$16-$23" does not count).
    floor = hourly_min if hourly_min is not None else hourly_max

    if floor is None or predicted:
        verdict = "unlisted"            # no pay, or only an Adzuna guess
    elif floor >= MIN_HOURLY:
        verdict = "meets"
    else:
        verdict = "below"

    return {
        "id": job.get("id"),
        "title": title,
        "company": company,
        "location": location,
        "hourly_min": hourly_min,
        "hourly_max": hourly_max,
        "predicted": predicted,
        "verdict": verdict,
        "created": (job.get("created") or "")[:10],
        "url": job.get("redirect_url") or "",
        "source": source,
        "description": (job.get("description") or "").strip(),
    }


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------

def collect(verbose=True):
    seen = {}
    def add(jobs, source):
        for j in jobs:
            jid = j.get("id")
            if jid and jid not in seen:
                seen[jid] = normalize(j, source)

    for title in TITLES:
        if verbose:
            print(f"  local : {title}")
        add(search_title(title, remote=False), "local")
        time.sleep(0.3)

    # Remote pass across titles that actually exist as remote work.
    for title in REMOTE_TITLES:
        if verbose:
            print(f"  remote: {title}")
        for j in search_title(title, remote=True):
            if title_is_remote(j):
                jid = j.get("id")
                if jid and jid not in seen:
                    seen[jid] = normalize(j, "remote")
        time.sleep(0.3)

    all_rows = list(seen.values())
    rows = [r for r in all_rows
            if is_admin_title(r["title"])
            and is_attainable(r["title"])
            and not title_excluded(r["title"])
            and not requires_degree(r)
            and (r["source"] != "local" or in_polk_or_dallas(r["location"]))]
    dropped = len(all_rows) - len(rows)
    if verbose and dropped:
        print(f"  (filtered out {dropped} non-admin / senior / skilled / degree / "
              f"out-of-county postings)")
    rows, dupes = dedupe_rows(rows)
    if verbose and dupes:
        print(f"  (collapsed {dupes} duplicate postings of the same job)")
    return rows


def dedupe_rows(rows):
    """Adzuna re-publishes the same posting from multiple boards under different
    IDs. Collapse rows with the same employer + title + location, keeping the
    newest. Returns (deduped_rows, number_collapsed)."""
    best = {}
    for r in rows:
        key = (_norm_company(r["company"]), (r["title"] or "").lower().strip(),
               (r["location"] or "").lower().strip())
        cur = best.get(key)
        if cur is None or (r["created"] or "") > (cur["created"] or ""):
            best[key] = r
    return list(best.values()), len(rows) - len(best)


def sort_rows(rows):
    # Group by salary verdict (best first), newest-first within each group.
    rank = {"meets": 0, "unlisted": 1, "below": 2}
    return sorted(rows, key=lambda r: (rank.get(r["verdict"], 9), _neg_date(r["created"])))


def _neg_date(d):
    # Sort newest-first within a verdict group.
    return (9999 - int(d[:4]) if d[:4].isdigit() else 9999, d)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

VERDICT_LABEL = {
    "meets":    ("Pays $19+/hr",   "#1a7f37"),
    "unlisted": ("Pay not listed", "#5b6470"),
    "below":    ("Under $19/hr",   "#a04100"),
}


def salary_text(r):
    lo, hi = r["hourly_min"], r["hourly_max"]
    if lo is None and hi is None:
        return "not posted"
    if lo is not None and hi is not None and lo != hi:
        s = f"${lo:.0f}-${hi:.0f}/hr"
    else:
        v = hi if hi is not None else lo
        s = f"${v:.0f}/hr"
    if r["predicted"]:
        s += " (estimated)"
    return s


def friend_sort(rows):
    """Trusted/known employers first, then $19+ first, then newest. The app
    preserves this order — re-sorting by pay would bury 'Pay not listed' jobs,
    which are often the best leads (see invariant #2)."""
    rank = {"meets": 0, "unlisted": 1, "below": 2}
    return sorted(rows, key=lambda r: (0 if employer_is_trusted(r["company"]) else 1,
                                       rank.get(r["verdict"], 9), _neg_date(r["created"])))


def write_csv(rows, path):
    """Full audit CSV (every row incl. hidden), with the scam verdict + reasons."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["safety", "verdict", "title", "company", "location",
                    "salary_hourly", "posted", "source", "scam_reasons", "url"])
        for r in rows:
            sc = r.get("scam", {"level": "safe", "reasons": []})
            w.writerow([sc["level"], VERDICT_LABEL.get(r["verdict"], ("?", ""))[0],
                        r["title"], r["company"], r["location"], salary_text(r),
                        r["created"], r["source"], "; ".join(sc["reasons"]), r["url"]])


def _jobs_payload(safe_rows):
    """Build the JSON list the front-end app renders."""
    jobs = []
    for r in friend_sort(safe_rows):
        label, color = VERDICT_LABEL.get(r["verdict"], ("?", "#57606a"))
        # Only an EMPLOYER-STATED salary shows a number; predicted/none -> "Pay not listed".
        stated = (not r["predicted"]) and (r["hourly_min"] is not None or r["hourly_max"] is not None)
        floor = r["hourly_min"] if r["hourly_min"] is not None else (r["hourly_max"] or 0)
        jobs.append({
            "id": str(r.get("id") or r["url"]),
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "pay": salary_text(r) if stated else "Pay not listed",
            "payNum": float(floor) if stated else 0.0,
            "remote": r["source"] == "remote",
            "trusted": employer_is_trusted(r["company"]),
            "trustLabel": trusted_reason(r["company"]),
            "good": r["verdict"] == "meets",          # only employer-stated $19+
            "tagLabel": label,
            "tagColor": color,
            "posted": r["created"] or "",
            "url": r["url"],
            "category": job_category(r["title"]),
            "commute": "" if r["source"] == "remote" else commute_text(r["location"]),
            "about": snippet(r.get("description")),
        })
    return jobs


def write_html(safe_rows, hidden_count, total_checked, path, generated,
               contact="me", contact_phone=""):
    jobs = _jobs_payload(safe_rows)
    meta = {
        "contact": contact,
        "phone": contact_phone,
        "generated": generated,
        "hidden": hidden_count,
        "total": total_checked,
    }
    jobs_json = json.dumps(jobs, ensure_ascii=False).replace("</", "<\\/")
    meta_json = json.dumps(meta, ensure_ascii=False).replace("</", "<\\/")
    out = (APP_TEMPLATE
           .replace("##JOBS##", jobs_json)
           .replace("##META##", meta_json))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)


# Mobile-first installable PWA. {{tokens}} are filled by write_html; CSS/JS braces
# are literal (this is NOT an f-string).
APP_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#ffffff">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Job Board">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<title>Job Board — Des Moines</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{
 --paper:#ffffff; --card:#ffffff; --surface:#f6f7f9; --ink:#0d1117; --ink2:#5b636e; --line:#e7e9ee;
 --green:#0f9d63; --green-d:#0b7c4e; --green-soft:#e8f7ef;
 --gold:#0b7c4e; --red:#d23b35; --shadow:0 1px 2px rgba(13,17,23,.04);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
 font-family:'Atkinson Hyperlegible',-apple-system,Segoe UI,Roboto,Arial,sans-serif;
 font-size:17px;line-height:1.55;-webkit-font-smoothing:antialiased}
.app{max-width:640px;margin:0 auto;padding:0 16px 120px}
svg{display:inline-block;vertical-align:-2px}
/* App bar */
header.bar{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.9);
 backdrop-filter:saturate(1.1) blur(10px);margin:0 -16px;padding:16px;
 border-bottom:1px solid var(--line)}
.brandrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);font-weight:700}
.word{font-family:inherit;font-weight:600;font-size:26px;line-height:1.05;letter-spacing:-.01em}
.safebadge{display:inline-flex;align-items:center;gap:6px;background:var(--green-soft);color:var(--green-d);
 font-size:12px;font-weight:700;padding:6px 10px;border-radius:999px;white-space:nowrap}
.summary{color:var(--ink2);font-size:14px;margin-top:6px}
/* Safety */
.safety{background:#fff;border:1px solid var(--line);border-left:4px solid var(--red);
 border-radius:14px;padding:14px 16px;margin:18px 0;box-shadow:var(--shadow)}
.safety h2{margin:0 0 4px;font-family:inherit;font-size:19px;font-weight:600;
 display:flex;align-items:center;gap:8px}
.safety h2 svg{color:var(--red)}
.safety ul{margin:8px 0;padding-left:20px}
.safety li{margin:5px 0}
.safety .note{color:var(--ink2);font-size:15px;margin-top:6px}
.callbtn{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:12px;
 background:#fff;border:2px solid var(--red);color:var(--red);text-decoration:none;font-weight:700;
 padding:13px;border-radius:11px;font-size:16px;min-height:52px}
/* Controls */
.controls{position:sticky;top:62px;z-index:15;background:var(--paper);padding:10px 0 2px}
.searchwrap{position:relative}
.searchwrap svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--ink2)}
.search{width:100%;font:inherit;font-size:17px;padding:14px 16px 14px 44px;border:1.5px solid var(--line);
 border-radius:12px;background:#fff;min-height:52px;color:var(--ink)}
.search:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.chips{display:flex;gap:8px;overflow-x:auto;padding:11px 0 5px;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;background:#fff;border:1.5px solid var(--line);border-radius:999px;
 padding:9px 15px;font:inherit;font-size:15px;font-weight:700;color:var(--ink2);min-height:44px;white-space:nowrap;transition:.15s}
.chip[aria-pressed="true"]{background:var(--green);color:#fff;border-color:var(--green)}
/* Lists */
.progress{display:flex;align-items:center;gap:7px;color:var(--green-d);font-weight:700;font-size:14px;margin:8px 2px 0}
.count{color:var(--ink2);font-size:13px;letter-spacing:.04em;text-transform:uppercase;font-weight:700;margin:14px 2px 4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 16px 14px;margin:12px 0;
 box-shadow:var(--shadow);animation:rise .3s cubic-bezier(.2,.7,.3,1) both}
.cardtop{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.pill{display:inline-flex;align-items:center;font-size:13px;font-weight:700;padding:5px 11px;border-radius:8px}
.pill.good{background:var(--green);color:#fff}
.pill.none{background:var(--surface);color:var(--ink2)}
.verified{display:inline-flex;align-items:center;gap:5px;color:var(--gold);font-size:13px;font-weight:700}
.title{font-family:inherit;font-size:20px;font-weight:600;line-height:1.18;margin:0 0 3px}
.co{font-size:16px;font-weight:700;color:var(--ink)}
.meta{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--ink2);font-size:14px;margin-top:9px}
.meta span{display:inline-flex;align-items:center;gap:6px}
.apply{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:14px;background:var(--green);color:#fff;
 text-decoration:none;font-weight:700;padding:15px;border-radius:11px;font-size:17px;min-height:54px;transition:.12s}
.apply:active{transform:scale(.985);background:var(--green-d)}
.actions{display:flex;gap:8px;margin-top:9px}
.act{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:6px;background:#fff;
 border:1.5px solid var(--line);border-radius:11px;padding:11px 6px;font:inherit;font-size:14px;font-weight:700;
 min-height:48px;color:var(--ink2);transition:.12s}
.act:active{transform:scale(.97)}
.act.on{background:var(--green);color:#fff;border-color:var(--green)}
.act.applied.on{background:var(--green-d);border-color:var(--green-d)}
.empty{text-align:center;color:var(--ink2);padding:52px 16px;font-size:17px}
.empty svg{color:var(--line);margin-bottom:10px}
.foot{display:flex;flex-direction:column;align-items:center;gap:6px;color:var(--ink2);font-size:13px;
 text-align:center;margin:30px 0 0;line-height:1.6;border-top:1px solid var(--line);padding-top:18px}
/* Enhancements */
.stale{background:#fff3e2;border:1px solid #ecd2a8;color:#7a5417;border-radius:12px;
 padding:12px 14px;margin:14px 0 0;font-size:15px;line-height:1.45}
.coach{background:var(--green-soft);border:1px solid #cfe3da;border-radius:14px;
 padding:14px 44px 12px 16px;margin:18px 0;position:relative}
.coach h2{margin:0 0 4px;font-family:'Fraunces',Georgia,serif;font-size:18px;font-weight:600;color:var(--green-d)}
.coach ul{margin:6px 0 2px;padding-left:20px}
.coach li{margin:5px 0;font-size:15px}
.coach .dismiss{position:absolute;top:8px;right:8px;background:none;border:0;font:inherit;
 font-size:22px;line-height:1;color:var(--green-d);padding:8px;cursor:pointer}
.newtag{display:inline-flex;align-items:center;background:var(--gold);color:#fff;font-size:12px;
 font-weight:700;padding:4px 9px;border-radius:999px}
.pillrow{display:inline-flex;align-items:center;gap:7px}
.about{margin-top:10px;font-size:15px;color:var(--ink2)}
.about summary{cursor:pointer;font-weight:700;color:var(--green-d);font-size:14px;list-style-position:inside}
.about p{margin:6px 0 0}
.nudge{margin-top:10px;background:#fff3e2;border-left:3px solid var(--gold);padding:8px 11px;
 font-size:14px;color:#7a5417;border-radius:7px}
.notes{display:none;margin-top:9px}
.notes.open{display:block}
.notes textarea{width:100%;min-height:84px;font:inherit;font-size:15px;border:1.5px solid var(--line);
 border-radius:11px;padding:10px 12px;background:#fff;color:var(--ink);resize:vertical}
.notes textarea:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.old{color:#9aa39e}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.card{animation:none}}
</style>
</head>
<body>
<div class="app">
  <header class="bar">
    <div class="brandrow">
      <div>
        <div class="eyebrow">Des Moines Metro</div>
        <div class="word">Job Board</div>
      </div>
      <span class="safebadge"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg>Scam-checked</span>
    </div>
    <div class="summary" id="summary"></div>
  </header>

  <div class="stale" id="stale" hidden></div>

  <section class="safety">
    <h2><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z"/></svg>Before you apply</h2>
    <div>These jobs were checked and look real. Read the posting, then apply.</div>
    <div class="note"><b>It's a scam if a job ever asks you to:</b></div>
    <ul>
      <li>Pay money or buy equipment to start</li>
      <li>Cash a check and send part back, or buy gift cards</li>
      <li>Give your Social Security or bank number before a real interview</li>
      <li>Only talk by text, Telegram, or WhatsApp</li>
    </ul>
    <div class="note"><b>“Pay not listed”</b> means the employer didn't post the wage — ask what it pays when you apply.</div>
    <a class="callbtn" id="callbtn" href="#"></a>
  </section>

  <section class="coach" id="coach" hidden>
    <button class="dismiss" id="coachoff" aria-label="Dismiss tips">&times;</button>
    <h2>Tips for applying</h2>
    <ul>
      <li><b>"Pay not listed" is normal</b> — ask what it pays when you apply.</li>
      <li>Applying in person? Bring your ID and a list of past jobs with dates.</li>
      <li>A real employer will invite you to a phone call or an in-person interview — never just texting.</li>
      <li>No answer after a week? It's okay to call and ask about your application.</li>
    </ul>
  </section>

  <div class="controls">
    <div class="searchwrap">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-3.5-3.5"/></svg>
      <input class="search" id="search" type="search" inputmode="search"
        placeholder="Search job or employer" aria-label="Search jobs">
    </div>
    <div class="chips" id="chips"></div>
    <div class="chips" id="catchips"></div>
  </div>

  <div class="progress" id="progress"></div>
  <div class="count" id="count"></div>
  <div id="list"></div>
  <div class="empty" id="empty" hidden></div>
  <div class="foot" id="foot"></div>
</div>

<script>
const JOBS = ##JOBS##;
const META = ##META##;
const LS = "myjobs:v1";
function load(){ try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return {}} }
function save(s){ try{localStorage.setItem(LS, JSON.stringify(s))}catch(e){} }
function today(){ return new Date().toISOString().slice(0,10); }
function daysSince(d){ const t=Date.parse(String(d).slice(0,10)+"T00:00:00");
  return isNaN(t) ? null : Math.max(0, Math.floor((Date.now()-t)/864e5)); }
function ago(d){ const n=daysSince(d); if(n==null) return "";
  if(n===0) return "today"; if(n===1) return "yesterday";
  if(n<14) return n+" days ago"; return Math.round(n/7)+" weeks ago"; }

let state = load();
// applied used to be an array of ids; it's now a map id -> date applied.
if(Array.isArray(state.applied)){ const m={}; state.applied.forEach(id=>m[id]=today()); state.applied=m; }
state.applied = state.applied || {};
state.saved   = new Set(state.saved||[]);
state.hidden  = new Set(state.hidden||[]);
state.notes   = state.notes || {};
state.coachOff = !!state.coachOff;
const prevSeen = new Set(state.seen||[]);
function persist(){ save({applied:state.applied, saved:[...state.saved], hidden:[...state.hidden],
  notes:state.notes, seen:JOBS.map(j=>j.id), coachOff:state.coachOff}); }

// "New since your last visit": anything not on the page they saw last time.
// On the very first visit nothing is badged (everything would be "new").
const firstVisit = prevSeen.size===0;
const isNew = {};
JOBS.forEach(j=>{ if(!firstVisit && !prevSeen.has(j.id)) isNew[j.id]=true; });
const newCount = Object.keys(isNew).length;
persist();

const openNotes = new Set();
let filters = { q:"", cat:"", pay:false, inperson:false, remote:false, known:false,
                saved:false, applied:false, showHidden:false };

const CHIPS = [
  ["pay","$19+/hr"], ["inperson","In person"], ["remote","Work from home"],
  ["known","Verified employer"], ["saved","Saved"], ["applied","Applied"],
  ["showHidden","Hidden"],
];
const CATS = [...new Set(JOBS.map(j=>j.category).filter(Boolean))];
const IC = {
  pin:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 21s-6.5-5.7-6.5-10.5a6.5 6.5 0 0113 0C18.5 15.3 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.3"/></svg>',
  home:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/></svg>',
  bldg:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2"/></svg>',
  check:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5 5-5"/></svg>',
  bookmark:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  eye:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="2.5"/></svg>',
  arrow:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  car:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 11l1.5-4.5A2 2 0 018.4 5h7.2a2 2 0 011.9 1.5L19 11"/><rect x="3" y="11" width="18" height="6" rx="2"/><circle cx="7.5" cy="17" r="1.5"/><circle cx="16.5" cy="17" r="1.5"/></svg>',
  pen:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 013 3L8 19z"/></svg>',
  share:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M5 12v8h14v-8"/></svg>',
};

function esc(s){return String(s==null?"":s).replace(/[&<>"'`]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;","`":"&#96;"}[c];});}
function safeUrl(u){try{var p=new URL(u,location.href);return (p.protocol==="http:"||p.protocol==="https:")?p.href:"#";}catch(e){return "#";}}

function matches(j){
  if(!filters.showHidden && state.hidden.has(j.id)) return false;
  if(filters.showHidden && !state.hidden.has(j.id)) return false;
  if(filters.q){
    const q=filters.q.toLowerCase();
    if(!((j.title+" "+j.company+" "+j.location).toLowerCase().includes(q))) return false;
  }
  if(filters.cat && j.category!==filters.cat) return false;
  if(filters.pay && !j.good) return false;
  if(filters.inperson && j.remote) return false;
  if(filters.remote && !j.remote) return false;
  if(filters.known && !j.trusted) return false;
  if(filters.saved && !state.saved.has(j.id)) return false;
  if(filters.applied && !(j.id in state.applied)) return false;
  return true;
}

function render(){
  const good = JOBS.filter(j=>j.good).length;
  document.getElementById("summary").textContent =
    JOBS.length + " safe jobs · " + good + " pay $19+/hr" +
    (newCount ? " · " + newCount + " new since your last visit" : "") +
    " · updated " + META.generated;
  const ap = Object.keys(state.applied).length;
  const prog = document.getElementById("progress");
  prog.innerHTML = ap ? (IC.check + "You've applied to " + ap + (ap===1?" job":" jobs")) : "";

  // Jobs are pre-sorted by the scanner: verified employers first, then $19+,
  // then newest. Keep that order — sorting by pay here would bury the
  // "Pay not listed" jobs, which are often the best leads.
  const list = JOBS.filter(matches);

  document.getElementById("count").textContent =
    list.length + (filters.showHidden?" hidden ":" ") + (list.length===1?"job":"jobs");

  const wrap = document.getElementById("list");
  wrap.innerHTML = "";
  const empty = document.getElementById("empty");
  empty.hidden = list.length>0;
  if(!list.length){ empty.innerHTML = IC.eye + "<div>No jobs match. Turn off a filter to see more.</div>"; }

  list.forEach(function(j,i){
    const appliedOn = state.applied[j.id], applied = !!appliedOn, saved = state.saved.has(j.id);
    const note = state.notes[j.id] || "";
    const appliedDays = applied ? daysSince(appliedOn) : null;
    const payCls = j.good ? "good" : "none";
    const verified = j.trusted
      ? '<span class="verified">'+IC.check+'Verified'+(j.trustLabel?' — '+esc(j.trustLabel):' employer')+'</span>'
      : '<span></span>';
    const where = j.remote ? (IC.home+"Work from home") : (IC.bldg+"In person");
    const postedDays = daysSince(j.posted);
    const el = document.createElement("div");
    el.className = "card";
    el.style.animationDelay = (Math.min(i,12)*0.025)+"s";
    el.innerHTML =
      '<div class="cardtop"><span class="pillrow"><span class="pill '+payCls+'">'+esc(j.pay)+'</span>'+
        (isNew[j.id]?'<span class="newtag">New</span>':'')+'</span>'+verified+'</div>'+
      '<div class="title">'+esc(j.title)+'</div>'+
      '<div class="co">'+esc(j.company)+'</div>'+
      '<div class="meta">'+
        '<span>'+IC.pin+esc(j.location)+'</span>'+
        (j.commute?'<span>'+IC.car+esc(j.commute)+'</span>':'')+
        '<span>'+where+'</span>'+
        (j.posted?'<span'+(postedDays!=null&&postedDays>=21?' class="old"':'')+'>posted '+esc(ago(j.posted)||j.posted)+'</span>':'')+
      '</div>'+
      (j.about?'<details class="about"><summary>What you\'d do</summary><p>'+esc(j.about)+'</p></details>':'')+
      (applied&&appliedDays!=null&&appliedDays>=5
        ?'<div class="nudge">You applied '+esc(ago(appliedOn))+' — it\'s okay to call and ask about your application.</div>':'')+
      '<a class="apply" href="'+esc(safeUrl(j.url))+'" target="_blank" rel="noopener" data-act="open" data-id="'+esc(j.id)+'">Apply'+IC.arrow+'</a>'+
      '<div class="actions">'+
        '<button class="act applied'+(applied?' on':'')+'" data-act="applied" data-id="'+esc(j.id)+'">'+IC.check+(applied?'Applied':'I applied')+'</button>'+
        '<button class="act'+(saved?' on':'')+'" data-act="saved" data-id="'+esc(j.id)+'">'+IC.bookmark+(saved?'Saved':'Save')+'</button>'+
        '<button class="act" data-act="hide" data-id="'+esc(j.id)+'">'+IC.eye+(filters.showHidden?'Unhide':'Hide')+'</button>'+
      '</div>'+
      '<div class="actions">'+
        '<button class="act'+(note?' on':'')+'" data-act="notes" data-id="'+esc(j.id)+'">'+IC.pen+(note?'My notes':'Add note')+'</button>'+
        (navigator.share?'<button class="act" data-act="share" data-id="'+esc(j.id)+'">'+IC.share+'Send to '+esc(META.contact||"a friend")+'</button>':'')+
      '</div>'+
      '<div class="notes'+(openNotes.has(j.id)?' open':'')+'">'+
        '<textarea data-note="'+esc(j.id)+'" placeholder="Your notes — who you talked to, when to follow up">'+esc(note)+'</textarea>'+
      '</div>';
    wrap.appendChild(el);
  });
}

function buildChips(){
  const c = document.getElementById("chips"); c.innerHTML="";
  for(const [key,label] of CHIPS){
    const b=document.createElement("button");
    b.className="chip"; b.textContent=label; b.setAttribute("aria-pressed","false");
    b.onclick=()=>{
      filters[key]=!filters[key];
      if(key==="inperson"&&filters.inperson) filters.remote=false;
      if(key==="remote"&&filters.remote) filters.inperson=false;
      [...c.children].forEach((ch,i)=>ch.setAttribute("aria-pressed", String(filters[CHIPS[i][0]])));
      render();
    };
    c.appendChild(b);
  }
  const cc = document.getElementById("catchips"); cc.innerHTML="";
  cc.hidden = CATS.length<2;
  for(const cat of CATS){
    const b=document.createElement("button");
    b.className="chip"; b.textContent=cat; b.setAttribute("aria-pressed","false");
    b.onclick=()=>{
      filters.cat = (filters.cat===cat) ? "" : cat;
      [...cc.children].forEach(ch=>ch.setAttribute("aria-pressed", String(ch.textContent===filters.cat)));
      render();
    };
    cc.appendChild(b);
  }
}

const jobById = new Map(JOBS.map(j=>[j.id,j]));

document.getElementById("list").addEventListener("click",(e)=>{
  const t=e.target.closest("[data-act]"); if(!t) return;
  const id=t.getAttribute("data-id"), act=t.getAttribute("data-act");
  if(act==="open"){ if(!state.applied[id]) state.applied[id]=today(); persist(); setTimeout(render,400); return; }
  e.preventDefault();
  if(act==="applied"){ state.applied[id] ? delete state.applied[id] : state.applied[id]=today(); }
  if(act==="saved"){ state.saved.has(id)?state.saved.delete(id):state.saved.add(id); }
  if(act==="hide"){ state.hidden.has(id)?state.hidden.delete(id):state.hidden.add(id); }
  if(act==="notes"){
    const box=t.closest(".card").querySelector(".notes");
    const open=box.classList.toggle("open");
    open ? openNotes.add(id) : openNotes.delete(id);
    if(open) box.querySelector("textarea").focus();
    return;                       // no re-render; keep the textarea focused
  }
  if(act==="share"){
    const j=jobById.get(id);
    if(j && navigator.share){ navigator.share({title:j.title+" at "+j.company, url:safeUrl(j.url)}).catch(()=>{}); }
    return;
  }
  persist(); render();
});

// Auto-save notes as they type (no re-render, so the keyboard stays up).
document.getElementById("list").addEventListener("input",(e)=>{
  const t=e.target.closest("[data-note]"); if(!t) return;
  const id=t.getAttribute("data-note");
  const v=t.value;
  if(v.trim()) state.notes[id]=v; else delete state.notes[id];
  persist();
});

document.getElementById("search").addEventListener("input",(e)=>{ filters.q=e.target.value; render(); });

(function callBtn(){
  const b=document.getElementById("callbtn");
  const who = META.contact || "someone you trust";
  if(META.phone){ b.href="tel:"+META.phone.replace(/[^0-9+]/g,""); b.textContent="Something feels wrong? Call "+who; }
  else { b.removeAttribute("href"); b.style.cursor="default"; b.textContent="Something feels wrong? Ask "+who+" before you reply"; }
})();

// Warn when the list itself is old (offline, or the daily scan stopped).
(function staleBanner(){
  const el=document.getElementById("stale");
  const n=daysSince(META.generated);
  if(n!=null && n>=3){
    el.hidden=false;
    el.innerHTML="These jobs are from <b>"+esc(String(META.generated).slice(0,10))+"</b>. "+
      "Open this app with internet to get today's list.";
  }
})();

// Dismissible "Tips for applying" card.
(function coach(){
  const el=document.getElementById("coach");
  el.hidden = state.coachOff;
  document.getElementById("coachoff").onclick=()=>{ state.coachOff=true; el.hidden=true; persist(); };
})();

document.getElementById("foot").innerHTML =
  "<div>We checked "+META.total+" postings and hid <b>"+META.hidden+"</b> that looked like scams.</div>"+
  "<div>Tip: tap Share, then <b>Add to Home Screen</b> to keep this on your phone.</div>";

buildChips();
render();
if("serviceWorker" in navigator){ navigator.serviceWorker.register("sw.js").catch(()=>{}); }
</script>
</body>
</html>"""


# --------------------------------------------------------------------------
# Mock data (for --mock: prove the pipeline + show output without a key)
# --------------------------------------------------------------------------

def mock_results():
    return [
        {"id": "1", "title": "Administrative Assistant", "company": {"display_name": "Hy-Vee"},
         "location": {"display_name": "Urbandale, IA"}, "salary_min": 41600, "salary_max": 45760,
         "salary_is_predicted": "0", "created": "2026-06-03T00:00:00Z",
         "redirect_url": "https://www.adzuna.com/job/1", "description": "Front office support, scheduling."},
        {"id": "2", "title": "Receptionist", "company": {"display_name": "Dental Office"},
         "location": {"display_name": "Johnston, IA"}, "salary_min": 37440, "salary_max": 39520,
         "salary_is_predicted": "1", "created": "2026-06-01T00:00:00Z",
         "redirect_url": "https://www.adzuna.com/job/2", "description": "Greet patients, answer phones."},
        {"id": "3", "title": "Office Clerk", "company": {"display_name": "Logistics Co"},
         "location": {"display_name": "Grimes, IA"}, "salary_min": None, "salary_max": None,
         "salary_is_predicted": "0", "created": "2026-06-04T00:00:00Z",
         "redirect_url": "https://www.adzuna.com/job/3", "description": "Filing, data entry, mail."},
        {"id": "4", "title": "Data Entry Specialist (Remote)", "company": {"display_name": "BPO Inc"},
         "location": {"display_name": "Remote, US"}, "salary_min": 39520, "salary_max": 43680,
         "salary_is_predicted": "0", "created": "2026-06-02T00:00:00Z",
         "redirect_url": "https://www.adzuna.com/job/4", "description": "Remote data entry, work from home."},
        {"id": "5", "title": "Network Administrator", "company": {"display_name": "Tech LLC"},
         "location": {"display_name": "Clive, IA"}, "salary_min": 75000, "salary_max": 90000,
         "salary_is_predicted": "0", "created": "2026-06-02T00:00:00Z",
         "redirect_url": "https://www.adzuna.com/job/5", "description": "Manage network infra."},
        {"id": "6", "title": "Front Desk Associate", "company": {"display_name": "Gym"},
         "location": {"display_name": "Waukee, IA"}, "salary_min": 31200, "salary_max": 33280,
         "salary_is_predicted": "0", "created": "2026-05-30T00:00:00Z",
         "redirect_url": "https://www.adzuna.com/job/6", "description": "Check-in members."},
    ]


def collect_mock():
    seen = {}
    for j in mock_results():
        if requires_degree(j):
            continue
        seen[j["id"]] = normalize(j, "remote" if looks_remote(j) else "local")
    rows = [r for r in seen.values()
            if is_admin_title(r["title"]) and is_attainable(r["title"])
            and not title_excluded(r["title"])
            and (r["source"] != "local" or in_polk_or_dallas(r["location"]))]
    return dedupe_rows(rows)[0]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    global MIN_HOURLY
    ap = argparse.ArgumentParser(description="Find admin/clerical jobs near Grimes, IA via Adzuna.")
    ap.add_argument("--mock", action="store_true", help="Use canned data (no API key needed).")
    ap.add_argument("--min-hourly", type=float, default=MIN_HOURLY, help="Wage floor (default 19).")
    ap.add_argument("--contact", default="me",
                    help="Name the friend should call if a job looks like a scam (shown in the page).")
    ap.add_argument("--contact-phone", default="",
                    help="Optional phone number for the in-page 'Call' button (tel: link).")
    args = ap.parse_args()

    MIN_HOURLY = args.min_hourly

    load_env()

    print("Admin Job Finder")
    print("=" * 40)
    if args.mock:
        print("MODE: mock (no live API calls)")
        rows = collect_mock()
    else:
        print(f"MODE: live  |  near {LOCATION} (~{DISTANCE_KM}km) + remote  |  floor ${MIN_HOURLY:.0f}/hr")
        print("Searching Adzuna...")
        try:
            rows = collect()
        except RuntimeError as err:
            print(f"\nERROR: {err}")
            return 1

    # Scam shield: assess every row, then split safe vs hidden.
    global BLOCKLIST
    BLOCKLIST = load_blocklist(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "scam_blocklist.txt"))
    spam_index = build_spam_index(rows)
    for r in rows:
        r["scam"] = scam_assessment(r, spam_index)
    safe = sort_rows([r for r in rows if r["scam"]["level"] == "safe"])
    hidden = [r for r in rows if r["scam"]["level"] != "safe"]

    stamp = datetime.now(timezone.utc).astimezone()
    datestr = stamp.strftime("%Y-%m-%d")
    human = stamp.strftime("%Y-%m-%d %H:%M")

    base = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(base, "web")
    os.makedirs(web_dir, exist_ok=True)
    csv_path = os.path.join(base, f"admin-jobs-{datestr}.csv")
    html_path = os.path.join(web_dir, "index.html")     # the mobile PWA
    write_csv(sort_rows(rows), csv_path)                 # full audit incl. hidden
    write_html(safe, len(hidden), len(rows), html_path, human,
               contact=args.contact, contact_phone=args.contact_phone)

    n_good = sum(1 for r in safe if r["verdict"] == "meets")
    print("-" * 40)
    print(f"Total jobs found:    {len(rows)}")
    print(f"  Safe to send:      {len(safe)}  (of which {n_good} pay $19+/hr)")
    print(f"  Hidden as scams:   {len(hidden)}")
    if hidden:
        from collections import Counter
        why = Counter(r["scam"]["reasons"][0] for r in hidden if r["scam"]["reasons"])
        for reason, c in why.most_common(4):
            print(f"      - {c}x {reason}")
    print("-" * 40)
    print(f"MOBILE APP (send/host this folder): {web_dir}")
    print(f"  open locally: {html_path}")
    print(f"Your audit (all + scam reasons):    {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
