#!/usr/bin/env python
"""
find_admin_jobs.py - Find admin / clerical jobs near a location, via the Adzuna API.

Built for: office/admin roles (entry through experienced — Lilly has years of
admin behind her), no-degree-required friendly, >= $19/hr, within driving
distance of Grimes, IA (Des Moines metro) PLUS remote roles.

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
import base64
import binascii
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import providers

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
# experienced admin (Lilly's level), light office-adjacent, general no-degree.
TITLES = [
    # admin / clerical (the heart of what she wants — kept deliberately broad)
    "administrative assistant", "office assistant", "receptionist", "front desk",
    "data entry", "office clerk", "administrative coordinator", "secretary",
    "clerical", "file clerk", "administrative specialist", "office coordinator",
    "office administrator", "data entry clerk", "data entry specialist",
    "clerical assistant", "general office clerk", "administrative associate",
    "department assistant", "program assistant", "office support",
    # experienced admin — years of admin experience, no degree needed
    "executive assistant", "office manager", "administrative manager",
    "senior administrative assistant", "operations assistant", "executive secretary",
    "administrative officer",
    # light office-adjacent / clerical specialties
    "scheduler", "scheduling coordinator", "medical receptionist", "medical secretary",
    "billing clerk", "accounts payable clerk", "accounting clerk", "accounting assistant",
    "payroll clerk", "medical records clerk", "patient access representative",
    "patient service representative", "registration clerk", "intake coordinator",
    "human resources assistant", "bank teller", "dispatcher", "mail clerk",
    "customer service representative", "customer service associate",
    "call center representative",
    # caregiving (day-friendly, no degree)
    "caregiver",
]

# Subset that genuinely exists as remote work (skip remote calls for in-person roles).
REMOTE_TITLES = [
    "administrative assistant", "data entry", "receptionist", "scheduler",
    "customer service representative", "call center representative",
    "billing clerk", "medical records clerk",
    "executive assistant", "administrative coordinator", "operations assistant",
]

# Titles that LOOK like admin but are actually skilled/licensed roles that need
# credentials she doesn't have. Matched against the job title.
EXCLUDE_TITLE_WORDS = [
    "network", "systems", "system administrator", "database", "salesforce",
    "devops", "sql", "linux", "server", "cyber", "security administrator",
    "it administrator", "engineer", "developer", "registered nurse", "pharmacy",
    "phlebotom", "therapist", "physician", "attorney", "paralegal director",
]
# Entries matched as prefixes (no trailing word boundary): cybersecurity,
# phlebotomist/phlebotomy.
EXCLUDE_PREFIX_WORDS = {"cyber", "phlebotom"}

# Words that, if present in the title, mark a job as REMOTE.
REMOTE_HINTS = ["remote", "work from home", "wfh", "telecommute", "virtual"]

# ── Scam shield + attainability (the end user cannot self-vet) ─────────────

# Description phrases that are unambiguous job-scam tells (advance-fee,
# off-platform "interviews", reshipping, PII harvesting). Fatal for EVERY
# employer — a spoofed listing can carry a trusted name.
SCAM_HARD_FLAGS = [
    "purchase your own equipment", "buy equipment", "equipment fee", "startup fee",
    "registration fee", "application fee", "pay a fee", "upfront payment", "send money",
    "reship", "repackage", "package forwarding",
    "mystery shopper", "secret shopper", "telegram", "whatsapp", "google hangouts",
    "signal app", "text us at", "no experience needed and earn", "weekly pay of $",
    "social security number to apply", "ssn to apply", "bank details to apply",
    "immediate start no interview", "hiring asap no interview",
    "bitcoin", "crypto",
    # Mule-script shapes: legit postings say "cashing checks"/"wire transfers
    # processing" as duties, but the scam script says "cash a check" / "wire
    # transfer the balance". Keep these fatal even under a trusted name —
    # spoofed listings borrow real employers.
    "wire transfer", "cash a check",
]

# Phrases that are scam-shaped from an UNKNOWN employer but are ordinary job
# duties at a bank / credit union / retailer (teller, cashier, AP/AR work):
# "process payments", "money order", "gift card"... A trusted employer match
# rescues these; an unknown employer does not.
SCAM_FINANCIAL_DUTY_FLAGS = [
    "cashier's check", "cashier check", "money order",
    "gift card", "venmo", "cash app", "zelle",
    "process payments", "payment processing",
]

# Title phrases that are scam-prone roles for this profile (esp. remote).
SCAM_TITLE_FLAGS = [
    "personal assistant", "executive assistant to", "package handler remote",
    "reshipping", "payment processor", "money transfer", "mystery shopper",
    "data entry from home", "typing job", "envelope",
    # Gig / "paid panel" bait that targets admin seekers ("Remote Market Research
    # Panel — Administrative Assistant Welcome", "Paid Focus Group Panelist",
    # "Product Tester WFH"). DISTINCTIVE phrases only — NOT bare "market research"
    # — so legit "Market Research Coordinator/Analyst" admin roles are not hidden.
    # (added 2026-06-16, pre-release review.)
    "research panel", "paid focus group", "focus group panelist", "product tester",
    "survey taker", "paid panelist", "online panelist",
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
        "olsson", "mom's meals", "momsmeals",
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


def _trusted_rx(hints):
    """Compile hints into a word-bounded matcher. A substring match (`'ups' in
    'startups'`) wrongly trusts junk names like 'Quick Startups Staffing' or
    'Marshalling Logistics' ('marsh'), which then rescues them from scam signals
    and floats them to the top — a real hole for a scam-targeted user. Word
    boundaries keep every legitimate hit ('CVS Health', 'State of Iowa') while
    refusing accidental substrings."""
    return re.compile("|".join(r"\b" + re.escape(h) + r"\b" for h in hints), re.IGNORECASE)


_TRUSTED_EMPLOYER_RX = _trusted_rx(TRUSTED_EMPLOYER_HINTS)
_TRUSTED_GROUP_RX = {label: _trusted_rx(hints) for label, hints in TRUSTED_EMPLOYER_GROUPS.items()}

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

# Positive US signals (state names + USPS abbrevs + nation tags). A remote
# posting must carry one of these to survive the US-only guard.
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}
US_STATE_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
US_NATION_TAGS = {"us", "usa", "u s", "u s a", "united states", "america",
                  "stateside", "nationwide", "contiguous", "anywhere in the us"}
US_LOCATION_TOKENS = US_STATE_NAMES | US_STATE_ABBREVS | US_NATION_TAGS

# Clear non-US markers — countries + a few foreign remote-job hub cities. Word-
# boundaried so 'india' can't match 'Indiana' nor 'uk' match 'Paducah'. Kept
# focused on what actually shows up in aggregator location strings.
NON_US_MARKERS = {
    "united kingdom", "uk", "england", "scotland", "wales", "ireland",
    "london", "manchester", "canada", "ontario", "toronto", "quebec",
    "vancouver", "alberta", "india", "bangalore", "bengaluru", "mumbai",
    "delhi", "hyderabad", "pune", "chennai", "philippines", "manila",
    "germany", "berlin", "france", "paris", "spain", "madrid", "barcelona",
    "italy", "rome", "netherlands", "amsterdam", "poland", "warsaw",
    "portugal", "lisbon", "romania", "ukraine", "kyiv", "australia",
    "sydney", "melbourne", "singapore", "malaysia", "mexico", "brazil",
    "argentina", "colombia", "nigeria", "lagos", "kenya", "south africa",
    "pakistan", "bangladesh", "indonesia", "vietnam", "thailand", "japan",
    "tokyo", "china", "shanghai", "hong kong", "dubai", "uae", "europe",
    "emea", "apac", "latam",
}

# Rough ONE-WAY drive times from Grimes (the user's home base) to each metro /
# near-metro town, in minutes. These are COARSE ESTIMATES — they only bin a job
# into a "how far is it?" band for the in-app commute-radius chooser; they are
# NOT turn-by-turn accurate. This map is ALSO the build-time gate for "is this
# job commutable?": a local posting whose town isn't here is dropped. So it must
# cover every Polk/Dallas place in POLK_DALLAS_PLACES (parity — don't regress the
# old county filter) plus the nearby Warren/Story/Jasper towns the radius chooser
# now reaches. Token-matched on comma-separated location parts; longest name wins
# ("west des moines" before "des moines").
COMMUTE_MINUTES_FROM_GRIMES = {
    # — Polk County —
    "grimes": 5, "johnston": 12, "urbandale": 12, "granger": 12, "clive": 15,
    "polk city": 15, "berwick": 18, "windsor heights": 18, "ankeny": 18,
    "west des moines": 18, "des moines": 20, "saylorville": 20, "polk county": 20,
    "alleman": 22, "elkhart": 22, "sheldahl": 22, "altoona": 25, "pleasant hill": 25,
    "bondurant": 25, "runnells": 30, "mitchellville": 30,
    # — Dallas County —
    "dallas center": 12, "waukee": 15, "adel": 15, "dallas county": 22,
    "van meter": 25, "de soto": 25, "woodward": 25, "minburn": 28, "bouton": 28,
    "redfield": 30, "perry": 30, "dexter": 32, "dawson": 35, "linden": 38,
    # — Warren County (south metro; newly reachable via the radius chooser) —
    "cumming": 22, "norwalk": 28, "carlisle": 32, "warren county": 35,
    "hartford": 35, "martensdale": 35, "indianola": 38, "new virginia": 45,
    # — Story County (north) —
    "slater": 22, "huxley": 28, "cambridge": 33, "kelley": 33, "maxwell": 35,
    "ames": 38, "story county": 40, "gilbert": 40, "nevada": 45, "story city": 45,
    # — Jasper County (east; Newton shows up in live results) —
    "newton": 38,
}

# Genuinely out-of-scope EXECUTIVE / non-admin tiers -> dropped. NOTE: we do
# NOT drop "senior", "lead", "manager", "supervisor", or "executive assistant"
# anymore — Lilly has years of admin experience ("basically a master's degree"),
# so experienced-admin roles (Office Manager, Executive Assistant, Senior Admin,
# Admin Supervisor) belong in her feed. The is_admin_title() gate still keeps
# everything to real admin/clerical work, so a "Sales Manager" is dropped there.
# Executive / supervisory / above-entry words that disqualify a role — UNLESS
# the title is one of the experienced-ADMIN exceptions below (she has years of
# admin, so "Office Manager" / "Senior Administrative Assistant" stay). This is
# what drops "Client Services Lead", "Member Services Team Lead" and "Senior
# Client Services Lead" — supervisory roles the old (admin-only) seniority list
# let through. ALL matched on \b word boundaries: critically, the old plain
# "coo" substring was silently dropping every "COOrdinator" — fixed here.
SENIORITY_DROP_TERMS = [
    "director", "head of", "chief", "vp", "vice president",
    "ceo", "cfo", "coo", "president",
    "team lead", "lead", "senior", "sr", "supervisor", "manager",
    "principal", "foreman", "superintendent",
]
_SENIORITY_RX = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in SENIORITY_DROP_TERMS) + r")\b")
# Experienced-admin titles that OUTRANK the supervisory drop (kept on purpose).
ADMIN_SENIORITY_OK = [
    "office manager", "administrative manager", "admin manager",
    "senior administrative", "lead administrative", "administrative supervisor",
    "office supervisor", "executive assistant", "executive administrative",
    "executive coordinator", "executive secretary",
]

# A job is kept only if its TITLE contains one of these admin/clerical terms.
# This is the precision gate: Adzuna fuzzy-matches queries and returns lots of
# "Coordinator/Manager/Specialist/Investigator" roles that are not real
# admin/reception work. Requiring an admin term in the title drops that noise.
# Grouped by the plain-language category shown as a filter chip in the app.
# First matching group wins, so "front desk" lands in Office, not elsewhere.
CATEGORY_TERMS = {
    "Office": [
        "administrative assistant", "admin assistant", "administrative support",
        "administrative coordinator", "administrative specialist", "administrative aide",
        "administrative associate", "administrative technician", "administrative officer",
        "receptionist", "front desk", "front office", "office assistant",
        "office administrator", "office coordinator", "office clerk", "office support",
        "office associate", "office specialist", "general office", "data entry",
        "data entry clerk", "data clerk", "data processor", "file clerk", "clerk typist",
        "clerical", "secretary", "medical secretary", "typist", "word processor",
        "scheduling coordinator", "scheduler", "scheduling", "admin coordinator",
        "department assistant", "program assistant", "program coordinator",
        "project coordinator", "project assistant", "staff assistant", "switchboard",
        "dispatcher", "billing", "accounts payable", "accounts receivable",
        "accounting clerk", "accounting assistant", "bookkeeper", "bookkeeping",
        "payroll", "medical records", "records clerk", "mail clerk", "patient access",
        "patient service", "registration", "registrar", "intake", "insurance verification",
        "human resources assistant", "hr assistant", "recruiting coordinator",
        # Experienced-admin roles (Lilly's level — years of admin = a master's):
        "executive assistant", "executive administrative", "executive secretary",
        "office manager", "administrative manager", "admin manager",
        "administrative supervisor", "office supervisor", "senior administrative",
        "lead administrative", "operations assistant", "executive coordinator",
    ],
    "Customer service": [
        "customer service", "customer support", "client service", "member service",
        "call center", "bank teller", "teller",
    ],
    "Caregiving": [
        "caregiver", "caretaker", "home care",
    ],
    # NOTE: "Food & cleaning" and "Production & labor" categories were removed on
    # her request — she's a single mom and those shifts/roles don't fit. Their
    # terms are gone from the allowlist, so such jobs from any source are dropped.
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
# If one of these appears near a degree mention, it is NOT a hard requirement
# ("bachelor's preferred", "no degree required", "or equivalent experience").
DEGREE_SOFTENERS = [
    "preferred", "a plus", "is a plus", "nice to have", "not required",
    "no degree", "without a degree", "or equivalent", "desired", "bonus",
    "helpful", "ideal but",
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
    """Word-boundary match so 'engineer' can't kill 'Engineering Office
    Assistant'. A few entries are deliberate prefixes (cyber->cybersecurity,
    phlebotom->phlebotomist/phlebotomy)."""
    t = (title or "").lower()
    for word in EXCLUDE_TITLE_WORDS:
        tail = r"" if word in EXCLUDE_PREFIX_WORDS else r"\b"
        if re.search(r"\b" + re.escape(word) + tail, t):
            return True
    return False


def looks_remote(job):
    blob = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return any(h in blob for h in REMOTE_HINTS)


def requires_degree(job):
    """True only when a degree mention reads as a hard requirement. 'Bachelor's
    preferred but not required' / 'no degree required' must NOT drop a posting —
    those are exactly the jobs this user can get."""
    blob = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    for h in DEGREE_REQUIRED_HINTS:
        for m in re.finditer(re.escape(h), blob):
            window = blob[max(0, m.start() - 30): m.end() + 80]
            if not any(s in window for s in DEGREE_SOFTENERS):
                return True
    return False


# Day-shift gate. She's a single mom with no childcare, so evening / night /
# overnight / late-ending roles don't work. We drop a posting ONLY when it
# clearly signals a non-day shift — a job that says nothing about hours is kept
# (those are standard daytime). Phrase hints first, then explicit time ranges
# that cross midnight or end late in the evening.
NIGHT_SHIFT_HINTS = (
    "2nd shift", "second shift", "3rd shift", "third shift", "2nd/3rd shift",
    "second and third shift", "night shift", "nights shift", "overnight",
    "over night", "graveyard", "swing shift", "evening shift", "afternoon shift",
    "closing shift", "pm shift", "p.m. shift", "weekends only", "weekend only",
    "nights and weekends", "evenings and weekends", "must be available nights",
    "must work nights", "must be available evenings",
    "to midnight", "until midnight", "til midnight", "midnight shift",
)
# A time range whose END is in the a.m. (e.g. "3 PM to 12 AM" — crosses midnight).
_OVERNIGHT_RANGE = re.compile(r"(?:-|–|to|until|till|thru)\s*(?:1[0-2]|[1-9])(?::\d\d)?\s*a\.?\s*m", re.I)
# A time range that ENDS at 8–11 p.m. (too late for evening pickup).
_LATE_PM_END = re.compile(r"(?:-|–|to|until|till|thru)\s*(?:8|9|10|11)(?::\d\d)?\s*p\.?\s*m", re.I)


def is_day_shift(job):
    """False only when the posting clearly runs evenings/nights/overnight or ends
    late; True (kept) when it says nothing about shift."""
    blob = ((job.get("title") or "") + "  " + (job.get("description") or "")).lower()
    if any(h in blob for h in NIGHT_SHIFT_HINTS):
        return False
    if _OVERNIGHT_RANGE.search(blob) or _LATE_PM_END.search(blob):
        return False
    return True


def is_remote_row(row):
    """Remote / work-from-home postings are EXEMPT from the day-shift gate — she
    can fit those around her child. In-person jobs must read as daytime (8–5)."""
    return row.get("source") == "remote" or title_is_remote(row)


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
    c = company or ""
    for label, rx in _TRUSTED_GROUP_RX.items():
        if rx.search(c):
            return label
    return ""


def in_polk_or_dallas(location):
    """True when a location names a Polk/Dallas County place (or the county).
    Token match on the comma-separated parts so 'Adel' can't fire on a street
    name. Unknown/blank locations are NOT in-county — the user asked for these
    two counties only."""
    tokens = [t.strip().lower() for t in (location or "").split(",")]
    return any(t in POLK_DALLAS_PLACES for t in tokens)


def is_us_location(location):
    """Positive US signal: a US state name/abbrev, an explicit US / 'United
    States' / 'USA' tag, or a Polk/Dallas place. Lets a genuinely-US remote row
    through while foreign-leaning ones are dropped."""
    if in_polk_or_dallas(location):
        return True
    blob = " " + re.sub(r"[^a-z ]+", " ", (location or "").lower()) + " "
    return any(f" {s} " in blob for s in US_LOCATION_TOKENS)


def looks_non_us(location):
    """True when a location clearly names a non-US place. HARD guard: this is a
    US-only board — no European (or other foreign) postings, ever. A foreign
    marker only counts when there is NO US signal, so US cities that share a
    foreign name pass ('Paris, Texas', 'London, KY'). Word-boundaried so
    'Indiana' can't trip 'india'."""
    blob = " " + re.sub(r"[^a-z ]+", " ", (location or "").lower()) + " "
    if not any(f" {m} " in blob for m in NON_US_MARKERS):
        return False
    return not is_us_location(location)


def passes_us_filter(row):
    """Allow a row only if it is unambiguously US. Local rows are already
    county-filtered (definitionally US). Remote rows must carry a US signal and
    must NOT read as foreign — this kills European/other foreign trash a
    provider's country pin might miss."""
    loc = row.get("location") or ""
    if looks_non_us(loc):
        return False
    if row.get("source") == "local":
        return True
    return is_us_location(loc) or "remote" in loc.lower()


# County-level keys in COMMUTE_MINUTES_FROM_GRIMES are FALLBACKS: used only when
# a posting names no specific city, so "Grimes, Polk County" resolves to Grimes
# (5), not the Polk-County average (20).
_COMMUTE_COUNTY_TOKENS = {"polk county", "dallas county", "warren county", "story county"}


def commute_minutes(location):
    """Coarse drive-time (minutes) from Grimes for a known metro/near-metro town,
    else None. Token-matched on the comma-separated location parts so a street
    name can't fire ('Ames' won't match 'James St'). A named CITY wins over a
    county fallback (closest city if several are listed). Single source of truth
    for BOTH the commutable-job gate and the in-app radius chooser, so the drive
    time shown on a card and the radius it's filtered by can never disagree."""
    tokens = [t.strip().lower() for t in (location or "").split(",")]
    cities = [COMMUTE_MINUTES_FROM_GRIMES[t] for t in tokens
              if t in COMMUTE_MINUTES_FROM_GRIMES and t not in _COMMUTE_COUNTY_TOKENS]
    if cities:
        return min(cities)
    counties = [COMMUTE_MINUTES_FROM_GRIMES[t] for t in tokens if t in _COMMUTE_COUNTY_TOKENS]
    return min(counties) if counties else None


def commute_text(location):
    """'~15 min drive' for a known metro town, else ''."""
    m = commute_minutes(location)
    return f"~{m} min drive" if m is not None else ""


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
    return bool(_TRUSTED_EMPLOYER_RX.search(company or ""))


def is_attainable(title):
    """Drop senior/competitive roles this user realistically won't be hired into."""
    t = (title or "").lower()
    # Experienced-admin titles (Office Manager, Senior Administrative Assistant)
    # are kept even though they contain a supervisory word.
    if any(ok in t for ok in ADMIN_SENIORITY_OK):
        return True
    # Otherwise any executive/supervisory word (Director / Lead / Senior /
    # Manager …) means it's above this user's realistic entry level.
    return not _SENIORITY_RX.search(t)


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

    # Hard scam tells in the description -> always scam, even for trusted names.
    for p in SCAM_HARD_FLAGS:
        if p in desc:
            reasons.append(f"description mentions '{p}'")
    for p in SCAM_TITLE_FLAGS:
        if p in title:
            reasons.append(f"scam-prone title ('{p}')")
    # A signing/sign-on bonus advertised IN THE TITLE — usually all-caps with a
    # dollar figure ("NOW OFFERING A $15K SIGN-ON BONUS!") — is promo-spam shaped;
    # real entry admin postings don't put a bonus in the title. Title-only + a
    # dollar amount keeps it distinctive, so a body that mentions a bonus is safe.
    if re.search(r"sign[\s-]?on bonus|signing bonus", title) or \
       re.search(r"\$\s?\d[\d,]*\s?k?\b[^.]{0,18}\bbonus\b", title):
        reasons.append("scam-prone title ('sign-on bonus advertised in title')")
    # Financial-duty phrases: ordinary teller/cashier/AP work at a trusted LOCAL
    # employer, scam-shaped anywhere else. A REMOTE posting that merely NAMES a
    # trusted employer is the spoofed-name check-cashing shape, so it does NOT
    # get the trusted rescue (a real trusted employer's teller/AP role is local).
    if not trusted or remote:
        for p in SCAM_FINANCIAL_DUTY_FLAGS:
            if p in desc:
                reasons.append(f"description mentions '{p}'")

    # Same employer + role spammed across 3+ cities.
    key = (_norm_company(company), title[:25])
    if len(spam_index.get(key, set())) >= 3:
        reasons.append("same posting spammed across many cities")

    # "company not listed" / blank employer.
    if not company.strip() or "not listed" in company.lower():
        reasons.append("no employer name")

    if reasons:
        # A trusted employer can't rescue a hard description tell. Absent those, a
        # known LOCAL employer downgrades structural noise to safe — but a REMOTE
        # posting never gets the trusted rescue: naming a trusted brand on a
        # remote listing that ALSO shows a structural tell (cross-city spam, blank
        # employer, financial-duty language) is the spoofed-brand shape, and a
        # real trusted employer's entry-admin role is local. A clean remote role
        # from a trusted name (no tells) still reaches the safe path below.
        hard = any("description mentions" in r or "scam-prone" in r for r in reasons)
        if hard:
            return {"level": "scam", "reasons": reasons}
        if trusted and not remote:
            return {"level": "safe", "reasons": []}
        return {"level": "scam", "reasons": reasons}

    # No explicit flags. Remote postings get extra suspicion.
    if remote:
        # Unrealistic pay for entry remote admin is bait — even when the posting
        # NAMES a trusted employer, because a real trusted employer's entry-admin
        # role isn't a $30+/hr remote gig. A trusted name alone is easy to fake,
        # so remote doesn't get the trusted rescue here (same spoofed-name logic
        # as the financial-duty check above).
        if hourly is not None and hourly >= 30:
            who = "spoofed trusted name" if trusted else "unknown employer"
            return {"level": "scam",
                    "reasons": [f"remote, {who}, pay ${hourly:.0f}/hr is too good for entry admin"]}
        if not trusted:
            return {"level": "suspect",
                    "reasons": ["remote role from an employer we couldn't recognize"]}

    return {"level": "safe", "reasons": []}


def salary_verdict(hourly_min, hourly_max, *, stated):
    """The ONE place a wage becomes a verdict (shared with providers.py).
    SAFETY: providers *predict* pay when the employer didn't post it (Adzuna
    flags it; Jooble doesn't even say). A non-stated wage NEVER earns a number
    or a $19+ badge. Wage FLOOR test: the LOW end of a stated range must clear
    $19 ("$16-$23" does not count)."""
    floor = hourly_min if hourly_min is not None else hourly_max
    if floor is None or not stated:
        return "unlisted"               # no pay, or only a guess
    if floor >= MIN_HOURLY:
        return "meets"
    return "below"


def normalize(job, source):
    """Flatten an Adzuna result into the row we care about + a salary verdict."""
    title = job.get("title") or ""
    company = (job.get("company") or {}).get("display_name") or "(company not listed)"
    location = (job.get("location") or {}).get("display_name") or ""
    smin = job.get("salary_min")
    smax = job.get("salary_max")
    # Fail CLOSED: a wage counts as employer-STATED only when the flag is an
    # explicit not-predicted value. A boolean true, an int 1, "2", or any
    # unexpected shape -> treated as a GUESS (invariant #1: never show a guessed
    # wage as a number). Adzuna sends "0"/"1" strings today; this survives a
    # type change to bool/int without ever failing open.
    predicted = str(job.get("salary_is_predicted", "1")).strip().lower() not in ("0", "false", "no")

    hourly_min = to_hourly(smin)
    hourly_max = to_hourly(smax)
    verdict = salary_verdict(hourly_min, hourly_max, stated=not predicted)

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

    # Extra providers (USAJobs/Jooble/...; each active only when its keys
    # exist). Same filters + scam shield apply to every source.
    for r in providers.collect_extra(TITLES, LOCATION, salary_verdict,
                                     log=(print if verbose else (lambda *_: None))):
        if r["id"] and r["id"] not in seen:
            seen[r["id"]] = r

    all_rows = list(seen.values())
    rows = [r for r in all_rows
            if is_admin_title(r["title"])
            and is_attainable(r["title"])
            and not title_excluded(r["title"])
            and not requires_degree(r)
            and (is_remote_row(r) or is_day_shift(r))
            and passes_us_filter(r)
            and (r["source"] != "local" or commute_minutes(r["location"]) is not None)]
    dropped = len(all_rows) - len(rows)
    if verbose and dropped:
        print(f"  (filtered out {dropped} non-admin / senior / skilled / degree / "
              f"night-shift / out-of-county postings)")
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


# "Will train" — employer-stated phrases that mean a candidate without
# credentials or history is genuinely in the running. This is the app's
# counterweight to a buyer's-market feed: the badge is only shown when the
# EMPLOYER said it (substring of their own posting text), never inferred.
TRAIN_HINTS = [
    "no experience necessary", "no experience needed", "no experience required",
    "no prior experience", "experience not required", "experience is not required",
    "will train", "we train", "we'll train", "willing to train",
    "paid training", "training provided", "training is provided",
    "on-the-job training", "on the job training", "no degree required",
]


def will_train(description):
    """True when the posting itself says training is provided / no experience."""
    d = (description or "").lower()
    return any(h in d for h in TRAIN_HINTS)


def _jobs_payload(safe_rows):
    """Build the JSON list the front-end app renders."""
    jobs = []
    for r in friend_sort(safe_rows):
        label, color = VERDICT_LABEL.get(r["verdict"], ("?", "#57606a"))
        # Only an EMPLOYER-STATED salary shows a number; predicted/none -> "Pay not listed".
        stated = (not r["predicted"]) and (r["hourly_min"] is not None or r["hourly_max"] is not None)
        floor = r["hourly_min"] if r["hourly_min"] is not None else (r["hourly_max"] or 0)
        # Stable per-job id. Falling back to "" (when both id and url are empty)
        # would collapse multiple jobs onto one localStorage key, so an "Applied"
        # tap on one would flip another (and corrupt her work-search log). Use
        # the content tuple as a last-resort distinct key.
        jid = r.get("id") or r.get("url") or "|".join(
            (r.get("title") or "", r.get("company") or "", r.get("location") or ""))
        jobs.append({
            "id": str(jid),
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
            "commuteMin": None if r["source"] == "remote" else commute_minutes(r["location"]),
            "about": snippet(r.get("description")),
            "trains": will_train(r.get("description")),
        })
    return jobs


def _portal_rows(safe_rows, last_seen_iso):
    """Schema-shaped rows for portal.push (public.jobs upsert).

    Built from the SAME helpers as _jobs_payload so the portal can never
    disagree with the page. Invariant #1: pay_text is the display string the
    card shows ("Pay not listed" unless employer-stated); no numeric wage
    column exists in the portal at all. first_seen is deliberately omitted:
    the DB default stamps it on insert, and merge-duplicates leaves it alone
    on update (only columns present in the payload are merged).
    """
    rows = []
    for r in friend_sort(safe_rows):
        stated = (not r["predicted"]) and (r["hourly_min"] is not None or r["hourly_max"] is not None)
        rows.append({
            "id": str(r.get("id") or r["url"]),
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "pay_text": salary_text(r) if stated else "Pay not listed",
            "verdict": r["verdict"],
            "category": job_category(r["title"]),
            "trust_label": trusted_reason(r["company"]),
            "commute": "" if r["source"] == "remote" else commute_text(r["location"]),
            "url": r["url"],
            "about": snippet(r.get("description")),
            "trains": will_train(r.get("description")),
            "source": r["source"],
            "posted": r["created"] or None,    # "" would be rejected by the date column
            "last_seen": last_seen_iso,
        })
    return rows


def _is_browser_safe_supabase_key(key):
    """True ONLY for a key that is safe to embed in a world-readable page.

    Positive allowlist (not a blacklist): accept the new publishable format
    (``sb_publishable_...``) or a legacy anon JWT whose decoded ``role`` claim
    is exactly ``"anon"``. Everything else is rejected — including a legacy
    ``service_role`` JWT, whose ``role`` lives *inside* the base64url payload
    (a substring blacklist on ``"service_role"`` misses it). This is the safety
    net for the realistic operator mistake of pasting the secret/service_role
    key into the publishable env var.
    """
    if key.startswith("sb_publishable_"):
        return True
    parts = key.split(".")                        # legacy keys are JWTs: h.p.s
    if len(parts) == 3 and parts[0].startswith("eyJ"):
        payload = parts[1] + "=" * (-len(parts[1]) % 4)   # restore b64 padding
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return False                          # unparseable -> not safe
        return claims.get("role") == "anon"
    return False


def _portal_web_config():
    """Browser config for the portal, or None when not configured.

    Uses the PUBLISHABLE/anon key only (browser-safe by design; RLS +
    invite-only auth are the boundary). A secret/service_role key must NEVER
    reach the public page — see _is_browser_safe_supabase_key for the gate.
    """
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or ""
    if not (re.match(r"^https://[a-z0-9-]+\.supabase\.co$", url) and key):
        return None
    if not _is_browser_safe_supabase_key(key):
        raise RuntimeError(
            "SUPABASE_PUBLISHABLE_KEY is not a browser-safe key - refusing to "
            "embed it in the public page. Use the publishable (or legacy anon) "
            "key, NEVER the secret / service_role key."
        )
    return {"url": url, "key": key}


# Pinned + SRI-locked: the browser refuses the script if the CDN bytes ever
# change. Hash computed from the fetched artifact 2026-06-12 (sha384).
PORTAL_SCRIPT_TAG = (
    '<script id="sbjs" defer crossorigin="anonymous" '
    'integrity="sha384-EjUdIVmzWliPzdzhxZ9ZoO0etXLKWuUPUftAGxP6qH6Lm4oLwoLaJR0Ba4pIDiDL" '
    'src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.108.1/dist/umd/supabase.js">'
    "</script>"
)


def _sentry_web_config():
    """Browser Sentry config, or None when SENTRY_DSN is unset.

    A Sentry DSN is PUBLIC/embeddable by design (it only authorizes SENDING
    events, never reading them), so it is safe in the static page. It is still a
    build-time var so the feature self-gates: no DSN -> no Sentry script and no
    init are emitted, and the page is byte-for-byte the same as before. Privacy
    for our single vulnerable user is enforced in the init (sendDefaultPii
    false, no replay, beforeSend strips user/request/context).
    """
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return None
    # Shape-check so a typo/garbage value can't silently ship a broken tag.
    if not re.match(r"^https://[0-9a-f]+@o\d+\.ingest\.(us\.|de\.)?sentry\.io/\d+$", dsn):
        raise RuntimeError(
            "SENTRY_DSN does not look like a Sentry DSN "
            "(https://<key>@o<org>.ingest.us.sentry.io/<project>) - refusing to embed it."
        )
    return {"dsn": dsn}


# Pinned + SRI-locked exactly like the Supabase tag. Tracing bundle (errors +
# performance; NO replay, so no DOM recording is ever shipped) v10.57.0; the
# sha384 was computed from the fetched artifact 2026-06-14. If the version is
# bumped, recompute the hash for that exact file or the browser silently refuses
# the script (fail-safe: the app still works, Sentry just won't load).
SENTRY_CDN_TAG = (
    '<script '
    'src="https://browser.sentry-cdn.com/10.57.0/bundle.tracing.min.js" '
    'integrity="sha384-fm7orKrUHTJhAKcdqNq6Kb/0qIpMNYz3TbwoEoiA3hdbnHqSBhIqMAZ4XS09pCU5" '
    'crossorigin="anonymous"></script>'
)

# Privacy-locked init. The single vulnerable user IS the spec: no PII, no
# session replay, request/user/context stripped before anything leaves the
# device, breadcrumbs scrubbed of typed text. Light tracing only (opted in for
# the AI/perf monitor). Wrapped in try/catch so monitoring can never break the
# app. The DSN is interpolated via json.dumps -> always a safe JS string.
_SENTRY_INIT_TMPL = """<script>
(function(){ if(!window.Sentry) return; try {
  Sentry.init({
    dsn: __DSN__,
    sendDefaultPii: false,
    tracesSampleRate: 0.1,
    beforeBreadcrumb: function(crumb){
      if (crumb && crumb.category === 'ui.input') return null;
      if (crumb && crumb.data) { delete crumb.data.from; delete crumb.data.to; }
      return crumb;
    },
    beforeSend: function(event){
      delete event.user; delete event.request;
      delete event.contexts; delete event.server_name;
      return event;
    }
  });
} catch (e) { /* monitoring must never break the app */ } })();
</script>"""


def _sentry_head(sentry_cfg):
    """Return the <head> Sentry block (SRI tag + privacy-locked init), or ""."""
    if not sentry_cfg:
        return ""
    dsn_js = json.dumps(sentry_cfg["dsn"])
    return SENTRY_CDN_TAG + "\n" + _SENTRY_INIT_TMPL.replace("__DSN__", dsn_js)


def write_html(safe_rows, hidden_count, total_checked, path, generated,
               contact="me", contact_phone="", portal_cfg=None, sentry_cfg=None):
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
    portal_json = (json.dumps(portal_cfg, ensure_ascii=False).replace("</", "<\\/")
                   if portal_cfg else "null")
    out = (APP_TEMPLATE
           .replace("##JOBS##", jobs_json)
           .replace("##META##", meta_json)
           .replace("##SENTRY##", _sentry_head(sentry_cfg))
           .replace("##PORTAL_SCRIPT##", PORTAL_SCRIPT_TAG if portal_cfg else "")
           .replace("##PORTAL##", portal_json))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)


# Mobile-first installable PWA. {{tokens}} are filled by write_html; CSS/JS braces
# are literal (this is NOT an f-string).
APP_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0e0a16">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Job Board">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<title>Job Board — Grimes &amp; Des Moines</title>
##SENTRY##
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
##PORTAL_SCRIPT##
<style>
:root{
 /* Premium goth-violet system. Deep ink-black with a violet undertone, a single
    refined accent, and a LAYERED elevation scale — no neon, no glassmorphism.
    Variable names kept from the old theme so every component re-skins here. */
 --paper:#0b0712; --card:#15101f; --surface:#1d1630; --ink:#f3eeff; --ink2:#a99bc9; --line:#291f40;
 --green:#a855f7; --green-d:#d2b8ff; --green-soft:color-mix(in oklab,#a855f7 18%,transparent);
 --gold:#e9d5ff; --red:#ff8a80;
 --accent:linear-gradient(135deg,#a855f7 0%,#7c3aed 58%,#6d28d9 100%);
 /* Soft, layered, premium — replaces the old neon 0 0 16px glow everywhere. */
 --shadow:0 1px 2px rgba(0,0,0,.40),0 14px 32px -10px rgba(0,0,0,.55);
 --shadow-lg:0 2px 6px rgba(0,0,0,.42),0 28px 64px -14px rgba(0,0,0,.62);
 --glow:0 12px 30px -10px color-mix(in oklab,#a855f7 60%,transparent);
 --ring:0 0 0 3px color-mix(in oklab,#a855f7 26%,transparent);
}
*{box-sizing:border-box}
[hidden]{display:none !important}   /* beat component display rules (flex etc.) */
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
 font-family:'Atkinson Hyperlegible',-apple-system,Segoe UI,Roboto,Arial,sans-serif;
 font-size:17px;line-height:1.55;-webkit-font-smoothing:antialiased}
/* Star field: pure CSS, fixed, behind everything; gentle twinkle. */
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
 background:
  radial-gradient(1.5px 1.5px at 12% 18%, rgba(233,213,255,.55) 50%, transparent 51%),
  radial-gradient(1px 1px at 78% 9%,  rgba(192,132,252,.5) 50%, transparent 51%),
  radial-gradient(1.5px 1.5px at 64% 32%, rgba(233,213,255,.35) 50%, transparent 51%),
  radial-gradient(1px 1px at 31% 56%, rgba(192,132,252,.4) 50%, transparent 51%),
  radial-gradient(1.5px 1.5px at 88% 64%, rgba(233,213,255,.45) 50%, transparent 51%),
  radial-gradient(1px 1px at 9% 83%,  rgba(192,132,252,.4) 50%, transparent 51%),
  radial-gradient(1.5px 1.5px at 47% 92%, rgba(233,213,255,.3) 50%, transparent 51%),
  radial-gradient(ellipse 120% 60% at 50% -10%, rgba(88,28,135,.28), transparent 60%);
 animation:twinkle 7s ease-in-out infinite alternate}
@keyframes twinkle{from{opacity:.55}to{opacity:1}}
.app{max-width:640px;margin:0 auto;padding:0 16px 120px}
svg{display:inline-block;vertical-align:-2px}
/* App bar — solid (no glassmorphism), hairline rule + faint violet wash. */
header.bar{position:sticky;top:0;z-index:20;
 background:linear-gradient(180deg,#120c1d,var(--paper));margin:0 -16px;padding:16px;
 border-bottom:1px solid var(--line)}
.brandrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink2);font-weight:700}
/* Solid premium wordmark — no gradient-text, no spinning glyph. The accent
   lives on one word (.word b) for a clean, intentional two-tone. */
.word{font-family:inherit;font-weight:800;font-size:27px;line-height:1.04;letter-spacing:-.015em;
 color:var(--ink);display:inline-block}
.word b{font-weight:800;color:var(--green-d)}
.safebadge{display:inline-flex;align-items:center;gap:6px;background:var(--green-soft);color:var(--green-d);
 font-size:12px;font-weight:700;padding:6px 10px;border-radius:999px;white-space:nowrap;
 border:1px solid rgba(192,132,252,.35)}
.summary{color:var(--ink2);font-size:14px;margin-top:6px}
/* Account control (compact, top-right, expand/collapse) */
.acctbtn{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;
 border-radius:50%;background:var(--card);border:1.5px solid var(--line);color:var(--ink2);cursor:pointer;transition:.15s}
.acctbtn:active{transform:scale(.94)}
.acctbtn.in{background:var(--green);border-color:var(--green);color:#fff}
.acctinitial{font:inherit;font-weight:800;font-size:17px;line-height:1}
.subrow{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.subrow .summary{margin-top:0;flex:1;min-width:120px}
.acctpop{position:absolute;top:60px;right:16px;z-index:30;width:min(290px,84vw);background:var(--card);
 border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:14px;animation:pop .14s ease both}
@keyframes pop{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.acctcopy{color:var(--ink2);font-size:14px;line-height:1.5;margin-bottom:10px}
.acctemail{font-weight:700;font-size:15px;color:var(--ink);margin-bottom:10px;word-break:break-all}
.acctprimary{width:100%;background:var(--green);color:#fff;border:0;border-radius:11px;font:inherit;
 font-weight:700;font-size:15px;padding:12px;min-height:46px;cursor:pointer}
.acctitem{width:100%;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:11px;
 font:inherit;font-weight:700;font-size:15px;padding:11px;min-height:46px;cursor:pointer}
/* Collapsible filter panel */
.filtertoggle{display:flex;align-items:center;gap:9px;width:100%;background:var(--card);
 border:1.5px solid var(--line);border-radius:12px;padding:12px 14px;font:inherit;font-weight:700;
 font-size:15px;color:var(--ink);min-height:50px;cursor:pointer}
.filtertoggle .ftlabel{flex:1;text-align:left}
.filtertoggle .ftchev{transition:transform .2s;color:var(--ink2)}
.filtertoggle[aria-expanded="true"] .ftchev{transform:rotate(180deg)}
.filtcount{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:22px;
 padding:0 6px;border-radius:999px;background:var(--green);color:#fff;font-size:12px;font-weight:800}
.filterpanel{padding-top:4px;animation:pop .16s ease both}
.uploadbtn{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:10px}
/* Premium (AI) features stay hidden until signed in — no dead-end buttons that
   only say "sign in" when tapped. The .authed class is toggled by showIn/showOut. */
.app:not(.authed) [data-act="tailor"]{display:none}
.app:not(.authed) #resumecard{display:none}
.app:not(.authed) #chatcard{display:none}
/* No freebies without an account. Signed-out users can BROWSE the scam-checked
   jobs (proof of value), but every action — Apply / Save / track / notes /
   share — is swapped for one "create a free account" CTA per card, and the
   Today / My apps / My corner tabs show the benefits screen instead. */
.app:not(.authed) .card .apply,
.app:not(.authed) .card .actions,
.app:not(.authed) .card .notes{display:none}
.app.authed .lockcta{display:none}
.lockcta{display:flex;align-items:center;justify-content:center;gap:9px;width:100%;margin-top:14px;
 padding:15px;border:0;border-radius:13px;font:inherit;font-weight:800;font-size:16px;min-height:54px;
 color:#fff;cursor:pointer;letter-spacing:.01em;
 background:linear-gradient(135deg,color-mix(in oklab,var(--green) 92%,#fff) 0%,#7e22ce 60%,#6b21a8 100%);
 box-shadow:0 8px 22px color-mix(in oklab,var(--green) 45%,transparent),inset 0 1px 0 rgba(255,255,255,.22)}
.lockcta:active{transform:translateY(1px) scale(.99)}
.lockcta svg{flex:0 0 auto}
/* Account-benefits "what you unlock" screen (shown when a locked tab is tapped) */
.lockview{padding:14px 2px 6px;text-align:center;animation:rise .3s cubic-bezier(.2,.7,.3,1) both}
.lockhero{font-family:inherit;font-weight:800;font-size:clamp(24px,7vw,30px);line-height:1.12;margin:8px 0 6px;color:var(--ink)}
.lockhero .hl{color:var(--green-d)}
.locksub{color:var(--ink2);font-size:16px;line-height:1.5;max-width:30ch;margin:0 auto 18px}
.lockperks{list-style:none;margin:0 auto 20px;padding:0;max-width:24rem;text-align:left}
.lockperks li{display:flex;align-items:flex-start;gap:11px;padding:11px 13px;margin:9px 0;border-radius:13px;
 background:var(--card);border:1px solid var(--line);font-size:15.5px;line-height:1.4;color:var(--ink)}
.lockperks li svg{flex:0 0 auto;margin-top:2px;color:var(--green-d)}
.lockperks li b{font-weight:800}
.lockperks li span{color:var(--ink2);font-weight:500}
.lockbtns{display:flex;flex-direction:column;gap:10px;max-width:24rem;margin:0 auto}
.lockbtns .lockcta{margin-top:0}
.locksecondary{width:100%;background:var(--surface);color:var(--ink);border:1px solid var(--line);
 border-radius:13px;font:inherit;font-weight:700;font-size:15px;padding:13px;min-height:50px;cursor:pointer}
.lockcrisis{margin-top:20px;font-size:13px;color:var(--ink2);line-height:1.5}
.lockcrisis a{color:var(--green-d);font-weight:700}
/* Safety */
.safety{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--red);
 border-radius:14px;padding:14px 16px;margin:18px 0;box-shadow:var(--shadow)}
.safety h2{margin:0 0 4px;font-family:inherit;font-size:19px;font-weight:600;
 display:flex;align-items:center;gap:8px}
.safety h2 svg{color:var(--red)}
.safety ul{margin:8px 0;padding-left:20px}
.safety li{margin:5px 0}
.safety .note{color:var(--ink2);font-size:15px;margin-top:6px}
.callbtn{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:12px;
 background:var(--card);border:2px solid var(--red);color:var(--red);text-decoration:none;font-weight:700;
 padding:13px;border-radius:11px;font-size:16px;min-height:52px}
/* Controls */
.controls{padding:10px 0 2px}
.searchwrap{position:relative}
.searchwrap svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--ink2)}
.search{width:100%;font:inherit;font-size:17px;padding:14px 16px 14px 44px;border:1.5px solid var(--line);
 border-radius:12px;background:var(--card);min-height:52px;color:var(--ink)}
.search:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.chips{display:flex;gap:8px;overflow-x:auto;padding:6px 0 5px;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chiplabel{font-size:.7rem;letter-spacing:.05em;text-transform:uppercase;color:#9a92ad;font-weight:700;margin:12px 2px 0}
.chiplabel:first-of-type{margin-top:4px}
.chip{flex:0 0 auto;background:var(--card);border:1.5px solid var(--line);border-radius:999px;
 padding:9px 15px;font:inherit;font-size:15px;font-weight:700;color:var(--ink2);min-height:44px;white-space:nowrap;transition:.15s}
.chip[aria-pressed="true"]{background:var(--green);color:#fff;border-color:var(--green)}
/* Lists */
.progress{display:flex;align-items:center;gap:7px;color:var(--green-d);font-weight:700;font-size:14px;margin:8px 2px 0}
.count{color:var(--ink2);font-size:13px;letter-spacing:.04em;text-transform:uppercase;font-weight:700;margin:14px 2px 4px}
.card{position:relative;background:
  linear-gradient(180deg,color-mix(in oklab,var(--card) 88%,var(--green-soft)),var(--card));
 border:1px solid var(--line);border-radius:18px;padding:17px 16px 15px;margin:13px 0;
 box-shadow:var(--shadow);animation:rise .3s cubic-bezier(.2,.7,.3,1) both}
/* Hairline gradient edge-light along the top for a premium, lit feel. */
.card::before{content:"";position:absolute;inset:0 0 auto;height:1px;border-radius:18px 18px 0 0;
 background:linear-gradient(90deg,transparent,color-mix(in oklab,var(--green) 55%,transparent),transparent);
 pointer-events:none}
.cardtop{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.pill{display:inline-flex;align-items:center;font-size:13px;font-weight:700;padding:5px 11px;border-radius:8px}
.pill.good{background:var(--green);color:#fff}
.pill.none{background:var(--surface);color:var(--ink2)}
.verified{display:inline-flex;align-items:center;gap:5px;color:var(--gold);font-size:13px;font-weight:700}
.title{font-family:inherit;font-size:20px;font-weight:600;line-height:1.18;margin:0 0 3px}
.co{font-size:16px;font-weight:700;color:var(--ink)}
.meta{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--ink2);font-size:14px;margin-top:9px}
.meta span{display:inline-flex;align-items:center;gap:6px}
.apply{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:14px;
 background:var(--accent);color:#fff;box-shadow:var(--glow),inset 0 1px 0 rgba(255,255,255,.22);
 text-decoration:none;font-weight:800;padding:15px;border-radius:13px;font-size:17px;min-height:54px;
 letter-spacing:.01em;transition:transform .12s ease,box-shadow .12s ease}
.apply:active{transform:scale(.985);background:#6b21a8}
.actions{display:flex;gap:8px;margin-top:9px}
.act{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:6px;background:var(--card);
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
.stale{background:rgba(255,123,114,.1);border:1px solid rgba(255,123,114,.35);color:#ffb4ae;border-radius:12px;
 padding:12px 14px;margin:14px 0 0;font-size:15px;line-height:1.45}
.coach{background:var(--green-soft);border:1px solid rgba(192,132,252,.3);border-radius:14px;
 padding:14px 44px 12px 16px;margin:18px 0;position:relative}
.coach h2{margin:0 0 4px;font-size:18px;font-weight:700;color:var(--green-d)}
.coach ul{margin:6px 0 2px;padding-left:20px}
.coach li{margin:5px 0;font-size:15px}
.coach .dismiss{position:absolute;top:8px;right:8px;background:none;border:0;font:inherit;
 font-size:22px;line-height:1;color:var(--green-d);padding:8px;cursor:pointer}
.newtag{display:inline-flex;align-items:center;background:var(--gold);color:#2e1065;font-size:12px;
 font-weight:700;padding:4px 9px;border-radius:999px}
.traintag{display:inline-flex;align-items:center;gap:4px;background:rgba(192,132,252,.18);color:var(--green-d);
 font-size:12px;font-weight:700;padding:4px 9px;border-radius:999px;border:1px solid rgba(192,132,252,.4)}
.pillrow{display:inline-flex;align-items:center;gap:7px}
.about{margin-top:10px;font-size:15px;color:var(--ink2)}
.about summary{cursor:pointer;font-weight:700;color:var(--green-d);font-size:14px;list-style-position:inside}
.about p{margin:6px 0 0}
.nudge{margin-top:10px;background:var(--green-soft);border-left:3px solid var(--gold);padding:8px 11px;
 font-size:14px;color:var(--green-d);border-radius:7px}
.notes{display:none;margin-top:9px}
.notes.open{display:block}
.notes textarea{width:100%;min-height:84px;font:inherit;font-size:15px;border:1.5px solid var(--line);
 border-radius:11px;padding:10px 12px;background:var(--card);color:var(--ink);resize:vertical}
.notes textarea:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.old{color:var(--ink2)}
/* Portal sync bar (hidden entirely unless the portal is configured) */
.sync{background:var(--green-soft);border:1px solid rgba(192,132,252,.3);border-radius:14px;padding:13px 16px;margin:14px 0 0}
.syncrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.synccopy{font-size:15px;color:var(--green-d);line-height:1.45}
.synccopy .who{font-weight:700}
.syncbtn{flex:0 0 auto;background:var(--green);color:#fff;border:0;border-radius:11px;font:inherit;
 font-weight:700;font-size:15px;padding:11px 18px;min-height:48px;cursor:pointer}
.syncbtn:active{transform:scale(.97)}
.syncform{margin-top:4px}
.syncform label{display:block;font-size:14px;font-weight:700;color:var(--green-d);margin:2px 0 6px}
.syncform .search{padding-left:16px}
.syncform .apply{margin-top:10px;width:100%;border:0;font:inherit;cursor:pointer}
.syncform .act{margin-top:8px;width:100%;background:var(--card);cursor:pointer}
.syncform .cancel{margin-top:8px;width:100%;background:none;border:0;color:var(--ink2);
 font:inherit;font-size:14px;font-weight:700;padding:8px;cursor:pointer}
.syncmsg{margin-top:9px;font-size:14px;color:var(--green-d);font-weight:700}
.syncmsg.err{color:var(--red)}
.syncout{flex:0 0 auto;max-width:120px}
/* Full-screen auth modal */
.authov{position:fixed;inset:0;z-index:60;background:rgba(8,5,14,.86);backdrop-filter:blur(8px);
 display:flex;align-items:flex-start;justify-content:center;overflow-y:auto;padding:24px 14px calc(24px + env(safe-area-inset-bottom))}
.authcard{background:var(--card);border:1px solid var(--line);border-radius:20px;width:100%;max-width:420px;
 margin:auto;padding:22px 20px;box-shadow:var(--glow);animation:rise .25s ease both}
.authcard h2{margin:0 0 2px;font-size:23px;font-weight:700}
.authcard .sub{color:var(--ink2);font-size:14px;margin:0 0 16px;line-height:1.45}
.authx{position:absolute;top:14px;right:16px;background:none;border:0;color:var(--ink2);font-size:26px;
 line-height:1;cursor:pointer;padding:6px}
.pkbtn{display:flex;align-items:center;justify-content:center;gap:9px;width:100%;
 background:linear-gradient(135deg,#9333ea,#7e22ce);color:#fff;box-shadow:var(--glow);
 border:0;border-radius:13px;font:inherit;font-weight:700;font-size:16px;padding:15px;min-height:54px;cursor:pointer}
.pkbtn:active{transform:scale(.985)}
.authdiv{display:flex;align-items:center;gap:10px;color:var(--ink2);font-size:12px;
 letter-spacing:.1em;text-transform:uppercase;margin:16px 0}
.authdiv::before,.authdiv::after{content:"";flex:1;height:1px;background:var(--line)}
.authfield{display:block;width:100%;font:inherit;font-size:16px;padding:13px 14px;margin:8px 0 0;
 border:1.5px solid var(--line);border-radius:11px;background:var(--surface);color:var(--ink);min-height:50px}
.authfield:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.authprimary{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;margin-top:10px;
 background:var(--green);color:#fff;border:0;border-radius:11px;font:inherit;font-weight:700;font-size:16px;
 padding:14px;min-height:52px;cursor:pointer}
.authprimary:active{transform:scale(.985);background:var(--green-d)}
.authsecondary{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;margin-top:9px;
 background:var(--card);color:var(--ink);border:1.5px solid var(--line);border-radius:11px;font:inherit;
 font-weight:700;font-size:15px;padding:12px;min-height:50px;cursor:pointer}
.authsecondary:active{transform:scale(.97)}
.authlink{background:none;border:0;color:var(--green-d);font:inherit;font-size:14px;font-weight:700;
 cursor:pointer;padding:8px 2px;text-decoration:underline}
.authrow{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:6px;flex-wrap:wrap}
.authmsg{margin-top:12px;font-size:14px;font-weight:700;color:var(--green-d);line-height:1.45}
.authmsg.err{color:var(--red)}
.authnote{margin-top:14px;font-size:12px;color:var(--ink2);line-height:1.5;text-align:center}
/* Bottom tab bar */
.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:30;display:flex;justify-content:space-around;
 background:#0d0917;border-top:1px solid var(--line);box-shadow:0 -8px 24px -12px rgba(0,0,0,.7);
 padding:6px 4px calc(8px + env(safe-area-inset-bottom))}
.tab{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;background:none;border:0;
 color:var(--ink2);font:inherit;font-size:11px;font-weight:700;padding:7px 2px;min-height:52px;cursor:pointer;
 border-radius:10px;transition:.15s}
.tab[aria-current="true"]{color:var(--green-d)}
.tab:active{transform:scale(.94)}
/* Section intros, encouragement, cards */
.picksintro h2{margin:18px 0 4px;font-size:22px;font-weight:700}
.picksintro p{margin:0 0 6px;color:var(--ink2);font-size:15px;line-height:1.5}
.weekline{font-weight:700;color:var(--green-d)}
.sparkle{color:var(--green-d);display:inline-block;opacity:.7;font-size:.82em;vertical-align:.06em}
.enc{margin:18px 2px 0;color:var(--green-d);font-size:15px;font-weight:700;text-align:center}
.logbtns{display:flex;gap:8px;margin:6px 0 8px}
.logbtns .act{flex:1}
.lognote{color:var(--ink2);font-size:14px;line-height:1.5;margin:4px 2px 10px}
.rescard{background:rgba(255,123,114,.07);border:1px solid rgba(255,123,114,.3);border-radius:14px;
 padding:14px 16px;margin:14px 0}
.rescard h3{margin:0 0 8px;font-size:17px;color:#ffb4ae}
.resline{margin:7px 0;font-size:15px;line-height:1.5}
.resline a{color:var(--green-d);font-weight:700;text-decoration:none;border-bottom:1px solid rgba(192,132,252,.4)}
.resnote{margin:10px 0 0;color:var(--ink2);font-size:14px;line-height:1.5}
.resnote a{color:var(--green-d)}
.quizcard,.chatcard{background:var(--card);border:1px solid var(--line);border-radius:16px;
 padding:16px;margin:14px 0;box-shadow:var(--shadow)}
.quizcard h3,.chatcard h3{margin:0 0 6px;font-size:18px}
.quizcard p,.chatcard p{margin:0 0 10px;color:var(--ink2);font-size:15px;line-height:1.5}
.qq{margin:12px 0 4px;font-weight:700;font-size:16px}
.qopts{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.qopt{background:var(--surface);border:1.5px solid var(--line);border-radius:999px;color:var(--ink2);
 font:inherit;font-size:14px;font-weight:700;padding:10px 14px;min-height:44px;cursor:pointer;transition:.12s}
.qopt[aria-pressed="true"]{background:var(--green);border-color:var(--green);color:#fff;box-shadow:var(--glow)}
.qdone{color:var(--green-d);font-weight:700;font-size:14px;margin-top:8px}
/* ── Ruby the emotional-support cow (signed-in only) ───────────────────────
   A designed mascot, not a sticker. Black-and-white cow face with purple
   accents so she sits inside the goth palette. NO glassmorphism. */
.rubyface{display:block;width:54px;height:54px;flex:0 0 auto;
 filter:drop-shadow(0 6px 14px rgba(168,85,247,.30))}
.rubyface--sm{width:48px;height:48px}
.rubyface--bar{width:40px;height:40px}
.rb-head{fill:#0e0a16;stroke:#cdbdf0;stroke-width:1.4}
.rb-muz{fill:#241a38;stroke:#cdbdf0;stroke-width:1.2}
.rb-spot{fill:#1c1430;stroke:#a855f7;stroke-width:1.1;opacity:.92}
.rb-horn{fill:#3a2c5e;stroke:#cdbdf0;stroke-width:1}
.rb-eye{fill:#e9defb}
.rb-nos{fill:#7c3aed;opacity:.9}
.rubyintro{display:flex;align-items:center;gap:14px;margin-bottom:2px}
.rubyintro h3{margin:0;font-size:19px;display:flex;align-items:center;gap:7px}
.rubycow{font-size:16px;filter:saturate(.85)}
.rubytag{margin:2px 0 0;color:var(--green-d);font-size:13px;font-weight:700;letter-spacing:.01em}
.rubyopen{display:inline-flex;align-items:center;justify-content:center;gap:9px;width:100%;margin-top:12px;
 background:var(--green);color:#fff;border:0;border-radius:13px;font:inherit;font-weight:700;font-size:16px;
 padding:14px;min-height:52px;cursor:pointer;box-shadow:var(--glow)}
.rubyopen:active{transform:scale(.985);background:var(--green-d)}

/* Full-screen overlay — solid surfaces, a soft purple halo behind Ruby. */
.rubyov{position:fixed;inset:0;z-index:70;background:var(--paper);
 display:flex;align-items:stretch;justify-content:center;
 animation:rubyrise .26s cubic-bezier(.2,.7,.2,1) both}
@keyframes rubyrise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.rubyshell{position:relative;display:flex;flex-direction:column;width:100%;max-width:620px;
 padding:0 14px env(safe-area-inset-bottom);
 background:radial-gradient(120% 60% at 50% -8%, rgba(168,85,247,.16), transparent 62%)}
.rubybar{display:flex;align-items:center;gap:11px;padding:14px 2px 12px;
 padding-top:calc(14px + env(safe-area-inset-top));border-bottom:1px solid var(--line)}
.rubybarname{display:flex;flex-direction:column;line-height:1.18;margin-right:auto}
.rubybarname b{font-size:17px;font-weight:800}
.rubybarname span{font-size:12.5px;color:var(--ink2)}
.rubyspk,.rubyclose{flex:0 0 auto;display:flex;align-items:center;justify-content:center;
 width:44px;height:44px;border-radius:12px;border:1px solid var(--line);background:var(--card);
 color:var(--ink2);cursor:pointer;transition:.14s}
.rubyspk:active,.rubyclose:active{transform:scale(.94)}
.rubyclose{font-size:28px;line-height:1;color:var(--ink2)}
.rubyspk .ic-off{display:none}
.rubyspk[aria-pressed="false"]{color:var(--ink2)}
.rubyspk[aria-pressed="false"] .ic-on{display:none}
.rubyspk[aria-pressed="false"] .ic-off{display:block}
.rubyspk[aria-pressed="true"]{color:var(--green-d);border-color:var(--green-soft)}
.rubylog{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:10px;
 padding:16px 2px;scroll-behavior:smooth}
.bub{max-width:86%;padding:11px 15px;border-radius:18px;font-size:15.5px;line-height:1.5;white-space:pre-wrap;
 animation:bubin .2s ease both}
@keyframes bubin{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.bub.me{align-self:flex-end;background:var(--green);color:#fff;border-bottom-right-radius:7px}
.bub.ai{align-self:flex-start;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-bottom-left-radius:7px}
.bub.think{color:var(--ink2)}
.bub.think i{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green-d);margin:0 2px;
 animation:rubythink 1.1s infinite ease-in-out}
.bub.think i:nth-child(2){animation-delay:.18s}
.bub.think i:nth-child(3){animation-delay:.36s}
@keyframes rubythink{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
.rubylisten{display:flex;align-items:center;gap:10px;justify-content:center;padding:8px 0 2px;
 color:var(--green-d);font-size:14px;font-weight:700}
.rubywave{display:inline-flex;align-items:center;gap:3px;height:20px}
.rubywave i{width:3px;height:100%;border-radius:2px;background:var(--green-d);
 animation:rubywave 1s infinite ease-in-out}
.rubywave i:nth-child(1){animation-delay:0s}
.rubywave i:nth-child(2){animation-delay:.12s}
.rubywave i:nth-child(3){animation-delay:.24s}
.rubywave i:nth-child(4){animation-delay:.36s}
.rubywave i:nth-child(5){animation-delay:.48s}
@keyframes rubywave{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}
.rubydock{display:flex;align-items:center;gap:9px;padding:8px 0 14px}
.rubymic,.rubysend{flex:0 0 auto;display:flex;align-items:center;justify-content:center;
 width:52px;height:52px;border-radius:14px;border:0;cursor:pointer;transition:.14s}
.rubymic{background:var(--card);border:1.5px solid var(--line);color:var(--ink)}
.rubymic:active{transform:scale(.95)}
.rubymic.on{background:var(--green);border-color:var(--green);color:#fff;box-shadow:var(--glow);
 animation:rubypulse 1.4s infinite}
@keyframes rubypulse{0%,100%{box-shadow:0 0 0 0 var(--green-soft)}50%{box-shadow:0 0 0 12px transparent}}
.rubyinput{flex:1;min-width:0;font:inherit;font-size:16px;padding:14px 15px;
 border:1.5px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink);min-height:52px}
.rubyinput:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.rubysend{background:var(--green);color:#fff;box-shadow:var(--glow)}
.rubysend:active{transform:scale(.95);background:var(--green-d)}
.rubyfine{margin:0 0 12px;font-size:12px;color:var(--ink2);line-height:1.5;text-align:center}
@media (prefers-reduced-motion:reduce){
 .rubyov,.bub,.rubymic.on,.rubywave i,.bub.think i{animation:none}
}
#tailormodal .authcard{max-height:88vh;overflow-y:auto;text-align:left}
/* Spooky résumé-summoning loader — drifting bats + a glowing moon over a
   calibrated progress bar. Goth on purpose: she loves black + purple. */
.spookload{padding:6px 2px 2px}
.spooksky{position:relative;height:64px;margin:4px 0 16px;border-radius:14px;overflow:hidden;
 background:radial-gradient(ellipse 120% 90% at 82% 12%, rgba(147,51,234,.24), transparent 60%),
            linear-gradient(180deg,#130d20,#0e0a16);border:1px solid var(--line)}
.spookmoon{position:absolute;right:16px;top:10px;width:28px;height:28px;border-radius:50%;
 background:radial-gradient(circle at 36% 34%, #f3ecff, #c9a8ff 60%, #7e22ce);
 box-shadow:0 0 22px rgba(201,168,255,.6)}
.bat{position:absolute;left:-30px;line-height:1;filter:drop-shadow(0 0 5px rgba(168,85,247,.55));
 will-change:left,transform;animation:batfly linear infinite}
.bat.b1{top:8%;font-size:20px;animation-duration:3.4s;animation-delay:-.2s}
.bat.b2{top:44%;font-size:14px;opacity:.85;animation-duration:4.6s;animation-delay:-1.6s}
.bat.b3{top:62%;font-size:16px;opacity:.9;animation-duration:2.9s;animation-delay:-2.4s}
@keyframes batfly{
 0%{left:-30px;transform:translateY(0) rotate(-5deg)}
 25%{transform:translateY(-9px) rotate(5deg)}
 50%{transform:translateY(5px) rotate(-5deg)}
 75%{transform:translateY(-7px) rotate(5deg)}
 100%{left:calc(100% + 30px);transform:translateY(0) rotate(-5deg)}}
.spookbar{height:11px;border-radius:999px;background:var(--surface);border:1px solid var(--line);overflow:hidden}
.spookbar i{display:block;height:100%;width:0;border-radius:999px;
 background:linear-gradient(90deg,#6b21a8,#9333ea,#c084fc);
 box-shadow:0 0 14px rgba(168,85,247,.7);transition:width .35s cubic-bezier(.3,.7,.3,1)}
.spookmsg{margin-top:11px;text-align:center;color:var(--green-d);font-size:14px;font-weight:700}
@media(prefers-reduced-motion:reduce){.bat{display:none}}
.tailorsec{margin:14px 0}
.tailorsec h3{margin:0 0 6px;font-size:15px}
.tailorta{width:100%;min-height:150px;resize:vertical;font-size:14px;line-height:1.5;white-space:pre-wrap}
.tailorsec .syncbtn{margin-top:8px}
.reslist{margin:6px 0 0;padding-left:18px;color:var(--ink2);font-size:14px;line-height:1.5}
.reslist li{margin:3px 0}
.tailorpaste{width:100%;min-height:120px;resize:vertical;font-size:14px;line-height:1.5}
.tailorhint{font-size:12.5px;color:var(--ink2);margin:8px 0 2px;line-height:1.45}
/* The copy/download row under the result — wraps on a narrow phone. */
.tailorgrab{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 2px}
.tailorgrab .syncbtn{flex:1 1 calc(50% - 4px);min-width:130px}
.tailorgrab .syncbtn.alt{background:var(--card);color:var(--ink);border:1.5px solid var(--line)}
.chatnote{font-size:12px;color:var(--ink2);margin-top:8px;line-height:1.45}
/* FAQ */
.faq{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:12px 16px;margin:10px 0}
.faq summary{font-weight:700;cursor:pointer;font-size:16px;color:var(--ink)}
.faq p{color:var(--ink2);font-size:15px;line-height:1.55;margin:8px 0 2px}
/* Toast + applied celebration */
/* Celebration splash — centered, clean, professional. (Big visual overhaul of
   the whole site is being handled as its own design pass; this is just legible.) */
.toast{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);
 z-index:60;display:flex;align-items:center;justify-content:center;gap:14px;text-align:center;
 background:#1b1230;color:#f3ecff;font-size:18px;font-weight:700;line-height:1.45;
 padding:22px 28px;border-radius:16px;max-width:84vw;border:1px solid #3a2a5c;
 box-shadow:0 24px 60px rgba(0,0,0,.55);
 opacity:0;animation:splashin .4s cubic-bezier(.16,1,.3,1) forwards}
@keyframes splashin{to{opacity:1;transform:translate(-50%,-50%) scale(1)}}
.toast button{background:#2a1d44;border:1px solid #4a3870;color:#f3ecff;font:inherit;font-weight:700;
 cursor:pointer;padding:9px 16px;border-radius:10px}
@media(prefers-reduced-motion:reduce){.toast{animation:none;opacity:1;transform:translate(-50%,-50%)}}
.burst{position:fixed;z-index:50;pointer-events:none;color:#c084fc;font-size:16px;animation:burst 1s ease-out forwards}
@keyframes burst{0%{opacity:1;transform:translate(0,0) scale(.6) rotate(0)}100%{opacity:0;transform:translate(var(--bx),var(--by)) scale(1.3) rotate(120deg)}}
/* Snooze (Not today) */
.act.snz.on{background:var(--surface);color:var(--green-d);border-color:rgba(192,132,252,.4)}
/* Follow-up contact + alerts */
.followup{margin-top:10px;padding:10px 12px;background:var(--surface);border:1px solid var(--line);border-radius:12px}
.followup h4{margin:0 0 8px;font-size:14px;font-weight:700;color:var(--green-d)}
.followfld{width:100%;margin:6px 0;font:inherit;font-size:15px;padding:10px 12px;border-radius:10px;
 border:1.5px solid var(--line);background:var(--card);color:var(--ink)}
.followfld:focus{outline:none;border-color:var(--green)}
.followrow{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.followrow .act{flex:1 1 auto;min-width:120px}
.followalert,.followbanner{background:rgba(192,132,252,.12);border:1px solid rgba(192,132,252,.35);
 color:var(--green-d);border-radius:12px;padding:12px 14px;margin:10px 0;font-size:15px;line-height:1.45}
.followalert b,.followbanner b{color:#f3ecff}
.followbanner{cursor:pointer}
.tab{position:relative}
.tab .badge{position:absolute;top:4px;left:58%;min-width:17px;height:17px;padding:0 5px;border-radius:999px;
 background:var(--green);color:#fff;font-size:10px;font-weight:800;line-height:17px;text-align:center}
/* Call script */
.script{margin-top:9px}
.script summary{cursor:pointer;font-weight:700;color:var(--green-d);font-size:14px;list-style-position:inside}
.script blockquote{margin:8px 0 0;padding:10px 12px;background:var(--surface);border-left:3px solid var(--green);
 border-radius:8px;color:var(--ink);font-size:15px;line-height:1.55}
/* Work-search log: print only */
@media print{
 body{background:#fff;color:#000}
 body::before{display:none}
 .app>*:not(#worklog),.tabbar,.toast{display:none !important}
 #worklog{display:block !important;color:#000}
 #worklog h1{font-size:18px;margin:0 0 2px}
 #worklog p{font-size:12px;margin:2px 0 10px}
 #worklog table{width:100%;border-collapse:collapse;font-size:12px}
 #worklog th,#worklog td{border:1px solid #444;padding:6px 8px;text-align:left;vertical-align:top}
}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.card{animation:none}}
</style>
</head>
<body>
<div class="app">
  <header class="bar">
    <div class="brandrow">
      <div>
        <div class="eyebrow">Grimes &middot; Des Moines metro</div>
        <div class="word">Job <b>Board</b></div>
      </div>
      <button class="acctbtn" id="acctbtn" aria-label="Your account" aria-expanded="false" aria-haspopup="true">
        <svg class="accticon" id="accticon" viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><circle cx="12" cy="8" r="3.4"/><path d="M5.5 19.2a6.5 6.5 0 0 1 13 0"/></svg>
        <span class="acctinitial" id="acctinitial" hidden></span>
      </button>
    </div>
    <div class="subrow">
      <span class="safebadge"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg>Scam-checked</span>
      <span class="summary" id="summary"></span>
    </div>
    <!-- Account popover (collapsed by default; the account button toggles it) -->
    <div class="acctpop" id="acctpop" hidden>
      <div id="acctpop-out">
        <div class="acctcopy">New phone or tablet? Sign in and your Applied, Saved, notes &amp; chats follow you everywhere.</div>
        <button class="acctprimary" id="acctsignin">Sign in</button>
      </div>
      <div id="acctpop-in" hidden>
        <div class="acctemail" id="acctemail"></div>
        <button class="acctitem" id="acctsignout">Sign out</button>
      </div>
    </div>
  </header>

  <div class="stale" id="stale" hidden></div>
  <div class="followbanner" id="followbanner" hidden role="button" tabindex="0"
    aria-label="Open follow-up reminders"></div>

  <!-- Full-screen auth modal (all modern sign-in methods) -->
  <div class="authov" id="authmodal" hidden>
    <div class="authcard" style="position:relative">
      <button class="authx" id="authclose" aria-label="Close">&times;</button>

      <!-- main sign-in / sign-up panel -->
      <div id="authmain">
        <h2 id="authtitle">Welcome back <span class="sparkle">&#10022;</span></h2>
        <p class="sub" id="authsub">Sign in so your jobs, notes and chats follow you to any device.</p>

        <!-- Real <form> so phone password managers (Google / Apple) reliably
             offer to SAVE and autofill the password. Its default submit is
             neutralized in JS; sign-in still runs through the existing button. -->
        <form id="authform">
          <input class="authfield" id="authlegal" type="text" autocomplete="name"
            placeholder="Your legal name (for your work-search log)" aria-label="Legal name" hidden>
          <input class="authfield" id="authpref" type="text" autocomplete="given-name"
            placeholder="What should we call you? (preferred name)" aria-label="Preferred name" hidden>
          <input class="authfield" id="authemail" type="email" inputmode="email" autocomplete="username"
            placeholder="Your email address" aria-label="Email">
          <input class="authfield" id="authpass" type="password" autocomplete="current-password"
            placeholder="Password" aria-label="Password" hidden>
          <button type="submit" class="authprimary" id="authprimarybtn">Continue</button>
        </form>
        <button class="authsecondary" id="authmagic">Email me a sign-in link instead</button>
        <button class="authsecondary" id="authgoogle" hidden>
          <svg viewBox="0 0 24 24" width="17" height="17"><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 01-2.4 3.7v3h3.9c2.3-2.1 3.5-5.2 3.5-8.9z"/><path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3a7.2 7.2 0 01-10.8-3.8H1.2v3.1A12 12 0 0012 24z"/><path fill="#FBBC05" d="M5.3 14.3a7.2 7.2 0 010-4.6V6.6H1.2a12 12 0 000 10.8z"/><path fill="#EA4335" d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.4-3.4A12 12 0 001.2 6.6l4.1 3.1A7.2 7.2 0 0112 4.8z"/></svg>
          Continue with Google
        </button>

        <div class="authrow">
          <button class="authlink" id="authtoggle">New here? Create an account</button>
          <button class="authlink" id="authforgot" hidden>Forgot password?</button>
        </div>
        <div class="authmsg" id="authmsg" role="status"></div>

        <!-- Optional, secondary: Face ID / fingerprint. De-emphasized so the
             password manager flow is the obvious default. -->
        <div class="authdiv" id="authpkdiv">or, if you like</div>
        <button class="pkbtn" id="authpasskey">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><circle cx="9" cy="9" r="3.2"/><path d="M3.5 19c.6-3 3-4.5 5.5-4.5"/><path d="M14 10.5c2 0 3.5 1.5 3.5 3.5 0 1.2-.6 2.2-1.4 2.9V21l-1.3-1-1.3 1v-4.1a3.5 3.5 0 01-1-2.4c0-2 1.5-3.5 3.5-3.5z"/></svg>
          Use Face ID / fingerprint instead (optional)
        </button>
        <p class="authnote">Your info is private and never shared. Face ID / fingerprint is
        optional &mdash; an email and password is all you need.</p>
      </div>

      <!-- set-new-password panel (shown after a reset link) -->
      <div id="authrecover" hidden>
        <h2>Set a new password <span class="sparkle">&#10022;</span></h2>
        <p class="sub">Pick a new password for your account.</p>
        <input class="authfield" id="authnewpass" type="password" autocomplete="new-password"
          placeholder="New password (8+ characters)" aria-label="New password">
        <button class="authprimary" id="authsetpass">Save new password</button>
        <div class="authmsg" id="authrecmsg" role="status"></div>
      </div>
    </div>
  </div>

  <!-- Résumé tailoring result -->
  <div class="authov" id="tailormodal" hidden>
    <div class="authcard" style="position:relative">
      <button class="authx" data-act="closetailor" aria-label="Close">&times;</button>
      <h2>Tailor your r&eacute;sum&eacute; <span class="sparkle">&#10022;</span></h2>
      <div id="tailorbody"></div>
    </div>
  </div>

  <!-- TODAY view: 3 curated picks, one small win at a time -->
  <section id="todaywrap" hidden>
    <div class="picksintro">
      <h2>Today&rsquo;s 3 picks <span class="sparkle">&#10022;</span></h2>
      <p>You don&rsquo;t have to look at every job. Here are 3 good ones for today &mdash;
      checked, no degree needed. Apply to one and you&rsquo;ve done today&rsquo;s job search.</p>
    </div>
    <div id="picks"></div>
    <div class="enc" id="todayenc"></div>
  </section>

  <!-- MY APPS view: applications + the Iowa work-search log -->
  <section id="appswrap" hidden>
    <div class="picksintro">
      <h2>My applications</h2>
      <p class="weekline" id="weekline"></p>
    </div>
    <div class="followalert" id="followalert" hidden></div>
    <button class="act" id="notifybtn" type="button" hidden>Turn on phone reminders for follow-ups</button>
    <div class="logbtns">
      <button class="act" id="printlog">Print my work-search log</button>
      <button class="act" id="copylog">Copy as text</button>
    </div>
    <p class="lognote">Iowa unemployment asks for <b>4 work-search activities each week</b>
    (Sunday&ndash;Saturday), and at least 3 must be job applications. This log keeps
    them for you &mdash; print it or copy it into your weekly claim.</p>
    <div id="applist"></div>
  </section>

  <!-- MY CORNER view: companion, quiz, resources -->
  <section id="cornerwrap" hidden>
    <div class="picksintro">
      <h2 id="cornerhi">My corner <span class="sparkle">&#10022;</span></h2>
      <p id="cornergreet"></p>
    </div>

    <div class="quizcard" id="namecard">
      <h3>Your name <span class="sparkle">&#10022;</span></h3>
      <p>In the app we call you by your <b>preferred name</b>. Your <b>legal name</b> is
      used only on your printable work-search log (for unemployment or court).</p>
      <input class="authfield" id="nm-pref" type="text" autocomplete="given-name"
        placeholder="Preferred name (what we call you)" aria-label="Preferred name">
      <input class="authfield" id="nm-legal" type="text" autocomplete="name"
        placeholder="Legal name (for your work-search log)" aria-label="Legal name">
      <button class="authprimary" data-act="saveprofile">Save my name</button>
    </div>

    <div class="quizcard" id="resumecard">
      <h3>My r&eacute;sum&eacute; <span class="sparkle">&#10022;</span></h3>
      <p>Upload your r&eacute;sum&eacute; (or paste it). Then on any job you can tap
      <b>&#10022; Tailor</b> and I&rsquo;ll re-organize <i>your own</i> experience to fit
      that posting &mdash; never adding anything you didn&rsquo;t write. You can paste the
      full job description there too, for a sharper match. Saved on this phone.</p>
      <input type="file" id="resumefile" accept=".docx,.pdf,.md,.markdown,.txt,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden>
      <button class="authsecondary uploadbtn" data-act="uploadresume">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 16V4"/><path d="M8 8l4-4 4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
        Upload a file (.docx, .pdf, .md, .txt)
      </button>
      <textarea class="authfield" id="resumebox" rows="6" style="min-height:120px;resize:vertical"
        placeholder="&hellip;or paste your r&eacute;sum&eacute; text here" aria-label="Your r&eacute;sum&eacute;"></textarea>
      <button class="authprimary" data-act="saveresume">Save my r&eacute;sum&eacute;</button>
      <div class="authmsg" id="resumemsg" role="status"></div>
    </div>

    <div class="rescard">
      <h3>Need help right now?</h3>
      <p class="resline"><b>988</b> &mdash; call or text, free, 24/7 (Suicide &amp; Crisis Lifeline)</p>
      <p class="resline"><b>Your Life Iowa</b> &mdash; call <a href="tel:8555818111">855-581-8111</a>
        or text <a href="sms:8558958398">855-895-8398</a>, free, 24/7</p>
      <p class="resline"><b>Iowa Warm Line</b> &mdash; <a href="tel:8447759276">844-775-9276</a>
        &mdash; just want someone kind to talk to? That&rsquo;s what this one is for.</p>
      <p class="resnote">All free. No insurance needed. No questions asked.
        More help (food, rent, free clinics): dial <b>2-1-1</b> or visit
        <a href="https://www.211iowa.org" target="_blank" rel="noopener">211iowa.org</a>.</p>
    </div>

    <div class="quizcard" id="quizcard">
      <h3>About me <span class="sparkle">&#10022;</span></h3>
      <p id="quizintro">Answer a few easy questions and the Jobs page starts putting
      the right ones first. No wrong answers. Change them any time.</p>
      <div id="quizbody"></div>
    </div>

    <div class="chatcard" id="chatcard">
      <div class="rubyintro">
        <span class="rubyface rubyface--sm" aria-hidden="true">
          <svg viewBox="0 0 64 64" width="100%" height="100%">
            <path class="rb-horn" d="M14 22c-5-3-8-9-7-13 4 1 8 5 9 11z"/>
            <path class="rb-horn" d="M50 22c5-3 8-9 7-13-4 1-8 5-9 11z"/>
            <ellipse class="rb-head" cx="32" cy="34" rx="22" ry="21"/>
            <path class="rb-spot" d="M16 24c-3 4-3 10 1 12 4-2 5-9 2-13-1-1-2-1-3 1z"/>
            <path class="rb-spot" d="M47 21c4 1 7 6 5 10-4 0-8-4-7-9 0-1 1-1 2-1z"/>
            <ellipse class="rb-muz" cx="32" cy="44" rx="13" ry="10"/>
            <circle class="rb-nos" cx="26" cy="44" r="2.3"/>
            <circle class="rb-nos" cx="38" cy="44" r="2.3"/>
            <circle class="rb-eye" cx="24" cy="30" r="2.6"/>
            <circle class="rb-eye" cx="40" cy="30" r="2.6"/>
          </svg>
        </span>
        <div>
          <h3>Ruby <span class="rubycow" aria-hidden="true">&#x1F404;</span></h3>
          <p class="rubytag">Your emotional support cow</p>
        </div>
      </div>
      <p id="chatstate">Ruby is a calm, kind check-in &mdash; she remembers you and helps
      with the search. You can type to her or just talk out loud. She turns on once
      sign-in is set up; your quiz answers above already make the app smarter today.</p>
      <button class="rubyopen" id="rubyopen" type="button" hidden>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 01-9 8.4L3 21l1.1-9A8.4 8.4 0 1121 11.5z"/></svg>
        Talk to Ruby
      </button>
    </div>
  </section>

  <!-- RUBY full-screen companion overlay (signed-in only; mounted/opened from JS).
       Premium goth, NOT glassmorphism — solid surfaces. Voice via Web Speech API. -->
  <div class="rubyov" id="rubyov" hidden role="dialog" aria-modal="true" aria-label="Ruby, your emotional support cow">
    <div class="rubyshell">
      <header class="rubybar">
        <span class="rubyface rubyface--bar" aria-hidden="true">
          <svg viewBox="0 0 64 64" width="100%" height="100%">
            <path class="rb-horn" d="M14 22c-5-3-8-9-7-13 4 1 8 5 9 11z"/>
            <path class="rb-horn" d="M50 22c5-3 8-9 7-13-4 1-8 5-9 11z"/>
            <ellipse class="rb-head" cx="32" cy="34" rx="22" ry="21"/>
            <path class="rb-spot" d="M16 24c-3 4-3 10 1 12 4-2 5-9 2-13-1-1-2-1-3 1z"/>
            <path class="rb-spot" d="M47 21c4 1 7 6 5 10-4 0-8-4-7-9 0-1 1-1 2-1z"/>
            <ellipse class="rb-muz" cx="32" cy="44" rx="13" ry="10"/>
            <circle class="rb-nos" cx="26" cy="44" r="2.3"/>
            <circle class="rb-nos" cx="38" cy="44" r="2.3"/>
            <circle class="rb-eye" cx="24" cy="30" r="2.6"/>
            <circle class="rb-eye" cx="40" cy="30" r="2.6"/>
          </svg>
        </span>
        <div class="rubybarname"><b>Ruby</b><span>Emotional support cow &#x1F404;</span></div>
        <button class="rubyspk" id="rubyspk" type="button" hidden aria-pressed="true" aria-label="Read Ruby's replies aloud">
          <svg class="ic-on" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13"/></svg>
          <svg class="ic-off" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M22 9l-6 6M16 9l6 6"/></svg>
        </button>
        <button class="rubyclose" id="rubyclose" type="button" aria-label="Close Ruby">&times;</button>
      </header>
      <div class="rubylog" id="rubylog" aria-live="polite"></div>
      <div class="rubylisten" id="rubylisten" hidden aria-hidden="true">
        <span class="rubywave"><i></i><i></i><i></i><i></i><i></i></span>
        Listening&hellip;
      </div>
      <div class="rubydock">
        <button class="rubymic" id="rubymic" type="button" hidden aria-label="Talk to Ruby with your voice">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v4"/></svg>
        </button>
        <input class="rubyinput" id="rubyinput" type="text" maxlength="4000" autocomplete="off"
          placeholder="Tell Ruby how you're doing&hellip;" aria-label="Message Ruby">
        <button class="rubysend" id="rubysend" type="button" aria-label="Send">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
        </button>
      </div>
      <p class="rubyfine" id="rubyfine">Ruby's a kind helper, not a therapist &mdash; if things feel heavy, tap the
      crisis card above for real people, 24/7. Your chats are saved privately to your account so she remembers you.</p>
    </div>
  </div>

  <!-- ACCOUNT-BENEFITS screen: shown when a signed-out user taps a locked tab
       (Today / My apps / My corner). "No freebies without an account." -->
  <section id="lockwrap" hidden>
    <div class="lockview">
      <div class="lockhero">Your free account <span class="hl">unlocks all of it</span></div>
      <p class="locksub" id="locksub">Browsing is free. Make a free account to actually use the app — it takes 10 seconds and saves everything to you.</p>
      <ul class="lockperks">
        <li><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4 4 10-10"/></svg><div><b>Save &amp; track every application</b> <span>— one tap, and your jobs, “applied” dates and notes follow you to any phone.</span></div></li>
        <li><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4 4 10-10"/></svg><div><b>AI résumé tailoring</b> <span>— rewrites your real experience to fit each job, in your words, never made up.</span></div></li>
        <li><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4 4 10-10"/></svg><div><b>Ruby, your support cow 🐄</b> <span>— a kind check-in you can type or talk to; she remembers you and helps with the search.</span></div></li>
        <li><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4 4 10-10"/></svg><div><b>Printable work-search log</b> <span>— your weekly Iowa unemployment list, filled in automatically.</span></div></li>
      </ul>
      <div class="lockbtns">
        <button class="lockcta" data-act="signup"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6z"/></svg>Create my free account</button>
        <button class="locksecondary" data-act="signin">I already have one — sign in</button>
      </div>
      <p class="lockcrisis">Need help right now? <b>988</b> (call/text, free, 24/7) ·
        Your Life Iowa <a href="tel:8555818111">855-581-8111</a>. Always free, no account.</p>
    </div>
  </section>

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

  <div id="faqwrap" hidden>
    <div class="picksintro"><h2>Questions people ask</h2></div>
    <details class="faq"><summary>Are these jobs real?</summary>
      <p>Every job here came from the employer's own hiring system or a checked job site,
      and each one was screened for scams before you ever see it. Jobs that looked wrong
      were removed &mdash; the number we removed is shown at the bottom of the Jobs page.</p></details>
    <details class="faq"><summary>What does &ldquo;Will train&rdquo; mean?</summary>
      <p>The employer wrote in their own posting that no experience is needed or that they
      provide training. Those are great ones to try even if you don&rsquo;t feel qualified.</p></details>
    <details class="faq"><summary>What if I don't meet everything they ask for?</summary>
      <p>Job ads are wish lists. If you can do about half of what they list, apply anyway &mdash;
      that's normal and employers expect it.</p></details>
    <details class="faq"><summary>Why does it say &ldquo;Pay not listed&rdquo;?</summary>
      <p>The employer didn&rsquo;t post the wage. That&rsquo;s common and not a bad sign &mdash;
      ask what it pays when they contact you.</p></details>
    <details class="faq"><summary>How does the work-search log work?</summary>
      <p>When you mark a job Applied, it's saved with the date automatically. The
      <b>My apps</b> tab can print or copy your weekly list for your Iowa unemployment claim.</p></details>
    <details class="faq"><summary>How do follow-up reminders work?</summary>
      <p>Each applied job gets a <b>Follow-up contact</b> section — save who to call, their phone
      or email, and when to check back (it defaults to 5 days after you apply). <b>My apps</b>
      shows a badge when a follow-up is due, and you can turn on phone reminders there.</p></details>
    <details class="faq"><summary>Is my information private?</summary>
      <p>Everything stays on your phone unless you choose to sign in. Signing in saves your
      jobs, notes and chats to a private account so a new phone doesn&rsquo;t lose them. It&rsquo;s
      never sold, never shown to employers, and never used for ads. The only person who could
      ever see what&rsquo;s saved is the person who set this up for you &mdash; nobody else.</p></details>
  </div>

  <div class="controls">
    <button class="filtertoggle" id="filtertoggle" aria-expanded="false" aria-controls="filterpanel">
      <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-3.5-3.5"/></svg>
      <span class="ftlabel">Search &amp; filter</span>
      <span class="filtcount" id="filtcount" hidden></span>
      <svg class="ftchev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
    </button>
    <div class="filterpanel" id="filterpanel" hidden>
      <div class="searchwrap">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-3.5-3.5"/></svg>
        <input class="search" id="search" type="search" inputmode="search"
          placeholder="Search job or employer" aria-label="Search jobs">
      </div>
      <div class="chiplabel">Filter</div>
      <div class="chips" id="chips"></div>
      <div class="chiplabel" id="catlabel">Job type</div>
      <div class="chips" id="catchips"></div>
      <div class="chiplabel">How far you'll drive from Grimes</div>
      <div class="chips" id="commutechips" aria-label="How far you will drive"></div>
    </div>
  </div>

  <div class="progress" id="progress"></div>
  <div class="count" id="count"></div>
  <div id="list"></div>
  <div class="empty" id="empty" hidden></div>
  <div class="enc" id="footenc"></div>
  <div class="foot" id="foot"></div>

  <!-- Print-only: the Iowa work-search log -->
  <div id="worklog" hidden></div>
</div>

<div class="toast" id="toast" hidden>
  <span id="toasttext"></span><button id="toastact" hidden></button>
</div>

<nav class="tabbar" aria-label="App sections">
  <button class="tab" id="nav-jobs" aria-current="true"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/></svg><span>Jobs</span></button>
  <button class="tab" id="nav-today" aria-current="false"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l2.2 5.4L20 9l-4.4 3.9L17 19l-5-3.2L7 19l1.4-6.1L4 9l5.8-.6z"/></svg><span>Today</span></button>
  <button class="tab" id="nav-apps" aria-current="false"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h9"/></svg><span>My apps</span></button>
  <button class="tab" id="nav-corner" aria-current="false"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 21s-7-4.6-9.3-9A5.4 5.4 0 0112 6.3 5.4 5.4 0 0121.3 12c-2.3 4.4-9.3 9-9.3 9z"/></svg><span>My corner</span></button>
  <button class="tab" id="nav-help" aria-current="false"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 114.1 1.9c-.8.7-1.6 1.2-1.6 2.3"/><circle cx="12" cy="16.8" r=".5"/></svg><span>Help</span></button>
</nav>

<script>
const JOBS = ##JOBS##;
const META = ##META##;
const PORTAL = ##PORTAL##;   // {url, key(publishable)} or null when not configured
const LS = "myjobs:v1";
function load(){ try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return {}} }
function save(s){ try{localStorage.setItem(LS, JSON.stringify(s))}catch(e){} }
function today(){ return new Date().toISOString().slice(0,10); }
function daysSince(d){ const t=Date.parse(String(d).slice(0,10)+"T00:00:00");
  return isNaN(t) ? null : Math.max(0, Math.floor((Date.now()-t)/864e5)); }
function ago(d){ const n=daysSince(d); if(n==null) return "";
  if(n===0) return "today"; if(n===1) return "yesterday";
  if(n<14) return n+" days ago"; return Math.round(n/7)+" weeks ago"; }
function addDaysISO(d, n){
  const t=Date.parse(String(d).slice(0,10)+"T00:00:00");
  if(isNaN(t)) return today();
  return new Date(t+n*864e5).toISOString().slice(0,10);
}
function daysUntil(d){
  const t=Date.parse(String(d).slice(0,10)+"T00:00:00");
  if(isNaN(t)) return null;
  return Math.ceil((t-Date.now())/864e5);
}
function safeTel(u){ return String(u||"").replace(/[^0-9+]/g,""); }
function notifPerm(){ return typeof Notification!=="undefined" ? Notification.permission : "denied"; }
function safeMail(u){
  const m=String(u||"").trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(m) ? m : "";
}
function ensureFollowUp(id){
  if(!state.followUps[id]){
    const appliedOn=state.applied[id]||today();
    state.followUps[id]={name:"",phone:"",email:"",on:addDaysISO(appliedOn,5),done:false};
  }
  return state.followUps[id];
}
function followUpsDue(){
  return appliedEntries().filter(function(r){
    const fu=state.followUps[r.id];
    return fu && !fu.done && fu.on && fu.on<=today();
  });
}
function followUpBlockHTML(id, j){
  if(!(id in state.applied)) return "";
  const fu=ensureFollowUp(id);
  const tel=safeTel(fu.phone), mail=safeMail(fu.email);
  const due=fu.on && !fu.done && fu.on<=today();
  const soon=fu.on && !fu.done && fu.on>today() && daysUntil(fu.on)!=null && daysUntil(fu.on)<=3;
  return '<div class="followup">'+
    '<h4>'+(due?'&#9888; Follow up today':(soon?'Follow up '+esc(ago(fu.on)||fu.on):'Follow-up contact'))+'</h4>'+
    '<input class="followfld" data-fu-name="'+esc(id)+'" placeholder="Who to contact (recruiter, HR…)" value="'+esc(fu.name)+'">'+
    '<input class="followfld" data-fu-phone="'+esc(id)+'" type="tel" inputmode="tel" autocomplete="tel" placeholder="Phone number" value="'+esc(fu.phone)+'">'+
    '<input class="followfld" data-fu-email="'+esc(id)+'" type="email" inputmode="email" autocomplete="email" placeholder="Email" value="'+esc(fu.email)+'">'+
    '<input class="followfld" data-fu-on="'+esc(id)+'" type="date" aria-label="Follow-up date" value="'+esc(fu.on||"")+'">'+
    '<div class="followrow">'+
      (tel?'<a class="act" style="text-decoration:none" href="tel:'+esc(tel)+'">'+IC.pen+'Call</a>':'')+
      (mail?'<a class="act" style="text-decoration:none" href="mailto:'+esc(mail)+'">'+IC.pen+'Email</a>':'')+
      (j?callScriptHTML(j, state.applied[id]||fu.on):'')+
      (fu.done
        ?'<button class="act" data-act="fuedit" data-id="'+esc(id)+'">'+IC.pen+'Edit follow-up</button>'
        :'<button class="act applied on" data-act="fudone" data-id="'+esc(id)+'">'+IC.check+'I followed up</button>')+
    '</div></div>';
}
function renderFollowAlerts(){
  const due=followUpsDue();
  const n=due.length;
  const alertEl=document.getElementById("followalert");
  const banner=document.getElementById("followbanner");
  const notifyBtn=document.getElementById("notifybtn");
  if(alertEl){
    if(n){
      const names=due.slice(0,2).map(function(r){
        const fu=state.followUps[r.id]||{};
        return esc(r.title)+(fu.name?" — "+esc(fu.name):"");
      }).join("<br>");
      alertEl.hidden=false;
      alertEl.innerHTML='<b>'+n+(n===1?" follow-up is":" follow-ups are")+' due</b> — a quick call or email shows you&rsquo;re serious.'+
        (names?"<br>"+names:"");
    } else { alertEl.hidden=true; alertEl.innerHTML=""; }
  }
  if(banner){
    if(n){
      banner.hidden=false;
      banner.innerHTML='<b>'+n+(n===1?" follow-up":" follow-ups")+' ready</b> — tap to see who to contact.';
    } else { banner.hidden=true; banner.innerHTML=""; }
  }
  if(notifyBtn){
    const canNotify=(typeof Notification!=="undefined");
    notifyBtn.hidden=!canNotify || notifPerm()==="granted" || !Object.keys(state.applied).length;
    if(!notifyBtn.hidden) notifyBtn.textContent=
      notifPerm()==="denied" ? "Reminders blocked — enable in phone settings"
      : "Turn on phone reminders for follow-ups";
    notifyBtn.disabled=canNotify && notifPerm()==="denied";
  }
  const tab=document.getElementById("nav-apps");
  if(tab){
    let badge=tab.querySelector(".badge");
    if(n){
      if(!badge){ badge=document.createElement("span"); badge.className="badge"; tab.appendChild(badge); }
      badge.textContent=n>9?"9+":String(n); badge.hidden=false;
    } else if(badge) badge.hidden=true;
  }
}
function maybeNotifyFollowUps(){
  if(typeof Notification==="undefined" || notifPerm()!=="granted") return;
  const due=followUpsDue();
  if(!due.length || state.followAlertDay===today()) return;
  state.followAlertDay=today(); persist();
  due.slice(0,3).forEach(function(r, i){
    const fu=state.followUps[r.id]||{};
    setTimeout(function(){
      try{
        new Notification("Time to follow up",{body:r.title+(fu.name?" — "+fu.name:""),
          tag:"followup-"+r.id, icon:"./icon-192.png"});
      }catch(e){}
    }, i*400);
  });
}

let state = load();
// applied used to be an array of ids; it's now a map id -> date applied.
if(Array.isArray(state.applied)){ const m={}; state.applied.forEach(id=>m[id]=today()); state.applied=m; }
state.applied = state.applied || {};
state.saved   = new Set(state.saved||[]);
state.hidden  = new Set(state.hidden||[]);
state.notes   = state.notes || {};
state.coachOff = !!state.coachOff;
state.snooze  = state.snooze || {};          // id -> "come back on" date (gentler than Hide)
state.savedAt = state.savedAt || {};         // id -> date saved (for gentle "still want this?" nudges)
state.resume  = state.resume  || "";         // her base résumé text (this device only; fed to the tailor)
state.appliedLog = state.appliedLog || {};   // id -> {t,c,d,u} captured at apply time, so the
                                             // work-search log survives jobs leaving the feed
state.followUps = state.followUps || {};     // id -> {name,phone,email,on,done} follow-up tracker
state.followAlertDay = state.followAlertDay || "";  // last day we fired daily follow-up alerts
state.profile = state.profile || {};         // quiz answers -> "For you" feed boost
state.maxCommute = state.maxCommute || "";   // "" = any distance; else a minutes cap ("20"/"30"/"45")
const prevSeen = new Set(state.seen||[]);
function persist(){ save({applied:state.applied, saved:[...state.saved], hidden:[...state.hidden],
  notes:state.notes, seen:JOBS.map(j=>j.id), coachOff:state.coachOff,
  snooze:state.snooze, savedAt:state.savedAt, appliedLog:state.appliedLog, followUps:state.followUps,
  followAlertDay:state.followAlertDay, profile:state.profile,
  resume:state.resume, maxCommute:state.maxCommute}); }
// Ledger backfill: any applied job still in today's feed gets its details kept.
JOBS.forEach(j=>{ if(state.applied[j.id] && !state.appliedLog[j.id])
  state.appliedLog[j.id]={t:j.title,c:j.company,d:state.applied[j.id],u:j.url}; });

// "New since your last visit": anything not on the page they saw last time.
// On the very first visit nothing is badged (everything would be "new").
const firstVisit = prevSeen.size===0;
const isNew = {};
JOBS.forEach(j=>{ if(!firstVisit && !prevSeen.has(j.id)) isNew[j.id]=true; });
const newCount = Object.keys(isNew).length;
persist();

const openNotes = new Set();
let portalSync = null;   // set by the portal IIFE when sign-in is configured; null otherwise
let filters = { q:"", cat:"", pay:false, inperson:false, remote:false, known:false,
                saved:false, applied:false, showHidden:false, trains:false,
                maxCommute: state.maxCommute || "" };
function snoozedNow(id){
  const until = state.snooze[id];
  return until && until > today();           // ISO dates compare as strings
}

const CHIPS = [
  ["trains","Will train ✦"], ["pay","$19+/hr"], ["inperson","In person"],
  ["remote","Work from home"], ["known","Verified employer"], ["saved","Saved"],
  ["applied","Applied"], ["showHidden","Hidden"],
];
const CATS = [...new Set(JOBS.map(j=>j.category).filter(Boolean))];
// Commute-radius chooser (single-select). "" = any distance; the others cap the
// drive time in minutes. Lilly picks how far she'll drive; remote jobs always show.
const COMMUTE_BANDS = [["","Any distance"],["20","Within 20 min"],["30","Within 30 min"],["45","Within 45 min"]];
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
  lock:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></svg>',
  spark:'<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6z"/></svg>',
};

function esc(s){return String(s==null?"":s).replace(/[&<>"'`]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;","`":"&#96;"}[c];});}
function safeUrl(u){try{var p=new URL(u,location.href);return (p.protocol==="http:"||p.protocol==="https:")?p.href:"#";}catch(e){return "#";}}

function matches(j){
  if(!filters.showHidden && state.hidden.has(j.id)) return false;
  if(filters.showHidden && !state.hidden.has(j.id)) return false;
  if(!filters.showHidden && snoozedNow(j.id)) return false;   // "Not today" naps
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
  if(filters.trains && !j.trains) return false;
  // Commute radius: remote jobs always pass; local jobs must be within the cap.
  if(filters.maxCommute && !j.remote && (j.commuteMin==null || j.commuteMin > +filters.maxCommute)) return false;
  return true;
}

/* "For you": quiz answers gently float matching jobs upward inside the
   scanner's trust-first order. A boost, never a burial — base order is kept
   as the tiebreaker (invariant: never bury "Pay not listed" or low scorers). */
function forYouScore(j){
  const p = state.profile; let s = 0;
  if(p.kind){
    const k = j.category||"";
    if(p.kind==="people" && k==="Customer service") s+=2;
    if(p.kind==="quiet"  && k==="Office") s+=2;
    if(p.kind==="hands"  && k==="Caregiving") s+=2;
    if(p.kind==="care"   && k==="Caregiving") s+=2;
  }
  if(p.where==="home" && j.remote) s+=2;
  if(p.where==="out" && !j.remote) s+=1;
  if(p.confidence==="low" && j.trains) s+=2;      // "will train" first when feeling shaky
  if(p.pay==="must" && j.good) s+=1;
  return s;
}
function orderForYou(list){
  if(!Object.keys(state.profile).length) return list;
  return list.map((j,i)=>[forYouScore(j), -i, j])
             .sort((a,b)=> b[0]-a[0] || b[1]-a[1])
             .map(x=>x[2]);
}

function render(){
  const good = JOBS.filter(j=>j.good).length;
  document.getElementById("summary").textContent =
    JOBS.length + " safe jobs · every one scam-checked, no degree needed" +
    (newCount ? " · " + newCount + " new" : "") +
    " · updated " + META.generated;
  const ap = Object.keys(state.applied).length;
  const wk = appsThisWeek();
  const prog = document.getElementById("progress");
  prog.innerHTML = ap ? (IC.check + "You've applied to " + ap + (ap===1?" job":" jobs") +
    (wk ? " · " + wk + " this week" : "")) : "";

  // Jobs are pre-sorted by the scanner: verified employers first, then $19+,
  // then newest. "For you" (quiz) only nudges within that — never buries.
  const list = orderForYou(JOBS.filter(matches));

  document.getElementById("count").textContent =
    list.length + (filters.showHidden?" hidden ":" ") + (list.length===1?"job":"jobs");

  const wrap = document.getElementById("list");
  wrap.innerHTML = "";
  const empty = document.getElementById("empty");
  empty.hidden = list.length>0;
  if(!list.length){ empty.innerHTML = IC.eye + "<div>Nothing matches those filters right now — that&rsquo;s the filters, not you. Tap one off above to see more, or check back tomorrow; fresh jobs arrive every morning.</div>"; }

  list.forEach(function(j,i){ wrap.appendChild(cardEl(j,i)); });
  updateFilterCount();
  renderPicks(); renderApps(); renderCorner();
  renderFollowAlerts();
  maybeNotifyFollowUps();
}

// Collapsible filter panel: collapsed by default so jobs are visible immediately;
// the count badge shows how many filters are active while it's closed.
function updateFilterCount(){
  var n = document.querySelectorAll('#chips .chip[aria-pressed="true"], #catchips .chip[aria-pressed="true"]').length;
  if((filters.q||"").trim()) n++;
  if(state.maxCommute) n++;
  var el = document.getElementById("filtcount");
  if(el){ el.hidden = n===0; if(n) el.textContent = n; }
}
(function(){
  var tog=document.getElementById("filtertoggle"), panel=document.getElementById("filterpanel");
  if(!tog||!panel) return;
  tog.addEventListener("click", function(){
    var willOpen = panel.hidden;
    panel.hidden = !willOpen;
    tog.setAttribute("aria-expanded", willOpen ? "true" : "false");
  });
})();

function callScriptHTML(j, appliedOn){
  // A word-for-word script takes the fear out of the follow-up call.
  const when = ago(appliedOn) || "recently";
  return '<details class="script"><summary>'+IC.pen+' What do I say if I call?</summary>'+
    '<blockquote>&ldquo;Hi! My name is ____. I applied for the '+esc(j.title)+
    ' job '+esc(when)+', and I wanted to check if it&rsquo;s still open and if you need anything else from me.'+
    ' Thank you!&rdquo;</blockquote>'+
    '<p style="margin:6px 0 0;font-size:13px;color:var(--ink2)">That&rsquo;s the whole call. Short is perfect. If voicemail, say the same thing plus your phone number.</p>'+
    '</details>';
}

function cardEl(j, i){
  const appliedOn = state.applied[j.id], applied = !!appliedOn, saved = state.saved.has(j.id);
  const note = state.notes[j.id] || "";
  const appliedDays = applied ? daysSince(appliedOn) : null;
  const savedDays = saved ? daysSince(state.savedAt[j.id]) : null;   // null for pre-timestamp saves
  const payCls = j.good ? "good" : "none";
  const verified = j.trusted
    ? '<span class="verified">'+IC.check+'Verified'+(j.trustLabel?' — '+esc(j.trustLabel):' employer')+'</span>'
    : '<span></span>';
  const where = j.remote ? (IC.home+"Work from home") : (IC.bldg+"In person");
  const postedDays = daysSince(j.posted);
  const snoozed = snoozedNow(j.id);
  const el = document.createElement("div");
  el.className = "card";
  el.style.animationDelay = (Math.min(i,12)*0.025)+"s";
  el.innerHTML =
    '<div class="cardtop"><span class="pillrow"><span class="pill '+payCls+'">'+esc(j.pay)+'</span>'+
      (j.trains?'<span class="traintag">&#10022; Will train</span>':'')+
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
      ?'<div class="nudge">You applied '+esc(ago(appliedOn))+' — it\'s okay to call and ask about your application.'+
        callScriptHTML(j, appliedOn)+'</div>':'')+
    (!applied&&saved&&savedDays!=null&&savedDays>=3
      ?'<div class="nudge">You saved this '+esc(ago(state.savedAt[j.id]))+' — want to apply today? No pressure; I\'m proud of you either way. &#10022;</div>':'')+
    (postedDays!=null&&postedDays>=30
      ?'<div class="nudge">This one&rsquo;s been posted a while — worth a quick check that it&rsquo;s still open before you spend time on it.</div>':'')+
    '<a class="apply" href="'+esc(safeUrl(j.url))+'" target="_blank" rel="noopener" data-act="open" data-id="'+esc(j.id)+'">Apply'+IC.arrow+'</a>'+
    '<div class="actions">'+
      '<button class="act applied'+(applied?' on':'')+'" data-act="applied" data-id="'+esc(j.id)+'">'+IC.check+(applied?'Applied':'I applied')+'</button>'+
      '<button class="act'+(saved?' on':'')+'" data-act="saved" data-id="'+esc(j.id)+'">'+IC.bookmark+(saved?'Saved':'Save')+'</button>'+
      '<button class="act snz'+(snoozed?' on':'')+'" data-act="snooze" data-id="'+esc(j.id)+'">'+IC.eye+(snoozed?'Napping':'Not today')+'</button>'+
      '<button class="act" data-act="hide" data-id="'+esc(j.id)+'">'+IC.eye+(filters.showHidden?'Unhide':'Hide')+'</button>'+
    '</div>'+
    '<div class="actions">'+
      '<button class="act'+(note?' on':'')+'" data-act="notes" data-id="'+esc(j.id)+'">'+IC.pen+(note?'My notes':'Add note')+'</button>'+
      '<button class="act" data-act="tailor" data-id="'+esc(j.id)+'">&#10022; Tailor résumé</button>'+
      (navigator.share?'<button class="act" data-act="share" data-id="'+esc(j.id)+'">'+IC.share+'Share</button>':'')+
    '</div>'+
    '<div class="notes'+(openNotes.has(j.id)?' open':'')+'">'+
      '<textarea data-note="'+esc(j.id)+'" placeholder="Your notes — interview times, what they said">'+esc(note)+'</textarea>'+
    '</div>'+
    (applied ? followUpBlockHTML(j.id, j) : '')+
    // Signed-out: the actions above are hidden by CSS and THIS is the only
    // button — one tap opens the free sign-up. No freebies without an account.
    '<button class="lockcta" data-act="signup">'+IC.lock+'Create a free account to apply &amp; save</button>';
  return el;
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
  var clbl = document.getElementById("catlabel"); if(clbl) clbl.hidden = CATS.length<2;
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
  const mc = document.getElementById("commutechips"); mc.innerHTML="";
  for(const [val,label] of COMMUTE_BANDS){
    const b=document.createElement("button");
    b.className="chip"; b.textContent=label;
    b.setAttribute("aria-pressed", String(val===filters.maxCommute));
    b.onclick=()=>{
      filters.maxCommute=val; state.maxCommute=val; persist();
      [...mc.children].forEach((ch,i)=>ch.setAttribute("aria-pressed", String(COMMUTE_BANDS[i][0]===filters.maxCommute)));
      render();
    };
    mc.appendChild(b);
  }
}

const jobById = new Map(JOBS.map(j=>[j.id,j]));

function markApplied(id, el){
  state.appliedLog[id] = state.appliedLog[id] ||
    (jobById.get(id) ? {t:jobById.get(id).title, c:jobById.get(id).company,
                        d:today(), u:jobById.get(id).url} : {t:"(job)", c:"", d:today(), u:""});
  state.appliedLog[id].d = state.applied[id];
  ensureFollowUp(id);
  // Full date+time stamp captured the moment she logs it — for unemployment/court
  // documentation. (It records when the activity was logged in the app.)
  state.appliedLog[id].ts = new Date().toISOString();
  celebrate(el);
}

/* ── Résumé tailoring ─────────────────────────────────────────────────────
   The tailor button is on every card; the actual call is JWT-gated to a signed-
   in user via the portal's window.__tailorInvoke bridge (set when signed in).
   Her résumé never leaves this device except in that one authenticated call. */
function openTailorModal(){ var m=document.getElementById("tailormodal"); if(m) m.hidden=false; }
function closeTailorModal(){ stopSpook(); var m=document.getElementById("tailormodal"); if(m) m.hidden=true; }
function setTailorBody(html){ var b=document.getElementById("tailorbody"); if(b) b.innerHTML=html; }
/* Build the plain text for one part, or both joined with a clear separator so
   she can paste them into one document without losing her place. */
function tailorText(which){
  var d=window.__tailorData; if(!d) return "";
  var r=(d.resume||""), c=(d.cover_note||"");
  if(which==="cover") return c;
  if(which==="both"){
    return c ? (r+"\n\n\n=== COVER NOTE ===\n\n"+c) : r;
  }
  return r;
}
/* One clipboard write covers it — "both" puts résumé + a separator + cover note
   in a single copy, so she never has to copy twice or lose her spot. */
function copyTailor(which){
  var text=tailorText(which);
  if(!text){ showToast("Nothing to copy yet."); return; }
  var label = which==="both" ? "Copied both ✦" : "Copied ✦";
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(function(){ showToast(label); })
      .catch(function(){ showToast("Couldn't copy — select the text and copy it."); });
  } else { showToast("Select the text and copy it."); }
}
/* Offline-safe download fallback — saves a .txt she can keep or attach. */
function downloadTailor(which){
  var text=tailorText(which);
  if(!text){ showToast("Nothing to save yet."); return; }
  var name = which==="cover" ? "cover-note.txt" : which==="both" ? "resume-and-cover-note.txt" : "tailored-resume.txt";
  try{
    var blob=new Blob([text],{type:"text/plain"});
    var url=URL.createObjectURL(blob);
    var a=document.createElement("a");
    a.href=url; a.download=name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){ URL.revokeObjectURL(url); }, 1000);
    showToast("Saved "+name+" ✦");
  }catch(e){ showToast("Couldn't save — copy the text instead."); }
}
function renderTailorResult(j, d){
  window.__tailorData = d;
  var changes = (d.changes||[]).map(function(c){ return '<li>'+esc(c)+'</li>'; }).join("");
  var both = d.cover_note ? 'both' : 'resume';
  setTailorBody(
    '<p class="sub">For <b>'+esc(j.title)+'</b> at '+esc(j.company)+
      ' — built only from what you wrote. Read it over; it&rsquo;s yours to edit.</p>'+
    (changes?'<div class="tailorsec"><h3>What I emphasized</h3><ul class="reslist">'+changes+'</ul></div>':'')+
    /* Copy/download the whole thing at once — no second clipboard trip needed. */
    '<div class="tailorgrab">'+
      '<button class="syncbtn" data-act="copytailor" data-copy="'+both+'">'+
        (d.cover_note?'Copy both':'Copy résumé')+'</button>'+
      '<button class="syncbtn alt" data-act="dltailor" data-copy="'+both+'">'+
        (d.cover_note?'Download both':'Download .txt')+'</button>'+
    '</div>'+
    '<div class="tailorsec"><h3>Your tailored résumé</h3>'+
      '<textarea class="authfield tailorta" readonly aria-label="Tailored résumé">'+esc(d.resume)+'</textarea>'+
      '<div class="tailorgrab">'+
        '<button class="syncbtn" data-act="copytailor" data-copy="resume">Copy résumé</button>'+
        '<button class="syncbtn alt" data-act="dltailor" data-copy="resume">Download</button>'+
      '</div></div>'+
    (d.cover_note?'<div class="tailorsec"><h3>A short note to send with it</h3>'+
      '<textarea class="authfield tailorta" readonly aria-label="Cover note">'+esc(d.cover_note)+'</textarea>'+
      '<div class="tailorgrab">'+
        '<button class="syncbtn" data-act="copytailor" data-copy="cover">Copy note</button>'+
        '<button class="syncbtn alt" data-act="dltailor" data-copy="cover">Download</button>'+
      '</div></div>':'')+
    '<p class="authnote">Always read it before you send — every line should be true to your real experience.</p>'
  );
}
/* Spooky "time left" loader for the AI wait. The real finish time is the API's
   to decide, so we calibrate: ease the bar toward ~94% over ~9s (a typical
   Sonnet tailoring), cycling stage messages, then snap to done when it lands. */
var _spookTimer = null;
function spookLoaderHTML(jobTitle){
  return '<div class="spookload">'+
    '<div class="spooksky"><span class="spookmoon"></span>'+
      '<span class="bat b1">&#129415;</span><span class="bat b2">&#129415;</span><span class="bat b3">&#129415;</span></div>'+
    '<div class="spookbar"><i id="spookfill"></i></div>'+
    '<div class="spookmsg" id="spookmsg">Summoning your r&eacute;sum&eacute; for '+esc(jobTitle)+'&hellip;</div>'+
  '</div>';
}
function startSpook(jobTitle){
  setTailorBody(spookLoaderHTML(jobTitle));
  var fill=document.getElementById("spookfill"), msg=document.getElementById("spookmsg");
  var stages=["Reading your real experience…","Matching it to this job…",
              "Choosing what to lead with…","Polishing the wording…","Almost there…"];
  var t0=Date.now(), DUR=9000;
  if(_spookTimer) clearInterval(_spookTimer);
  _spookTimer=setInterval(function(){
    var el=Date.now()-t0;
    if(fill) fill.style.width=Math.min(94,(el/DUR)*94).toFixed(1)+"%";
    if(msg){ var i=Math.min(stages.length-1, Math.floor(el/(DUR/stages.length))); msg.textContent=stages[i]; }
  },180);
}
function stopSpook(){
  if(_spookTimer){ clearInterval(_spookTimer); _spookTimer=null; }
  var fill=document.getElementById("spookfill"); if(fill) fill.style.width="100%";  // snap to done
}

/* Step 1: open the modal on an intro panel that lets her (optionally) paste the
   FULL job description. We only have a snippet from the listing; the full posting
   makes the tailoring much sharper. It's optional — she can tap straight through. */
function tailorJob(id){
  var j=jobById.get(id); if(!j) return;
  var resume=(state.resume||"").trim();
  if(resume.length<40){
    showToast("Add your résumé in My corner first ✦", "My corner", function(){
      setView("corner"); var b=document.getElementById("resumebox"); if(b) b.focus();
    });
    return;
  }
  if(!window.__tailorInvoke){
    showToast("Sign in (in My corner) to tailor your résumé — it keeps it private.", "My corner",
      function(){ setView("corner"); });
    return;
  }
  window.__tailorJobId = id;
  openTailorModal();
  setTailorBody(
    '<p class="sub">For <b>'+esc(j.title)+'</b> at '+esc(j.company)+'.</p>'+
    '<div class="tailorsec">'+
      '<h3>Paste the full job description</h3>'+
      '<p class="tailorhint">Optional, but it makes the match much better. Open the posting, '+
        'copy the whole description, and paste it here. Leave it blank and I&rsquo;ll use what we already have.</p>'+
      '<textarea class="authfield tailorpaste" id="tailorjd" aria-label="Full job description (optional)" '+
        'placeholder="Paste the job description here (optional)&hellip;"></textarea>'+
    '</div>'+
    '<button class="authprimary" data-act="runtailor">Tailor my résumé <span class="sparkle">&#10022;</span></button>'+
    '<p class="authnote">Built only from what you wrote — never adds anything you didn&rsquo;t.</p>'
  );
  var ta=document.getElementById("tailorjd"); if(ta) ta.focus();
}
/* Step 2: fire the engine. Prefer the pasted full description; fall back to the
   listing snippet. stopSpook in .finally so the loader timer can never leak. */
function runTailor(){
  var id=window.__tailorJobId;
  var j=jobById.get(id); if(!j) return;
  var resume=(state.resume||"").trim();
  var ta=document.getElementById("tailorjd");
  var pasted=ta ? (ta.value||"").trim() : "";
  var snippet=((j.about||"")+" "+(j.title||"")).trim();
  var jobText = pasted.length>=40 ? pasted : snippet;  // her full paste wins
  startSpook(j.title);
  Promise.resolve().then(function(){
    return window.__tailorInvoke({ resume:resume, jobTitle:j.title, company:j.company, jobText:jobText });
  })
    .then(function(r){
      var d=r&&r.data;
      if(!d || d.error || !d.resume){
        setTailorBody('<p class="sub">'+esc((d&&d.error)||"I couldn't put that together just now — try again in a minute.")+'</p>');
        return;
      }
      renderTailorResult(j, d);
    })
    .catch(function(){ setTailorBody('<p class="sub">No connection right now — try again when you&rsquo;re back online.</p>'); })
    .finally(function(){ stopSpook(); });
}
// Backdrop tap + Escape close the tailor modal.
(function(){
  var m=document.getElementById("tailormodal"); if(!m) return;
  m.addEventListener("click", function(e){ if(e.target===m) closeTailorModal(); });
  m.addEventListener("keydown", function(e){ if(e.key==="Escape") closeTailorModal(); });
})();

/* ── Résumé file upload: .docx / .pdf / .md / .txt -> text ─────────────────
   docx is parsed in-page with no dependencies (ZIP + DecompressionStream),
   pdf uses pdf.js loaded on demand, md/txt are read directly. The docx path
   and a real .pdf were verified against her actual résumé files. */
async function _inflateRaw(bytes){
  const ds = new DecompressionStream("deflate-raw");
  const s = new Response(bytes).body.pipeThrough(ds);
  return new Uint8Array(await new Response(s).arrayBuffer());
}
function _docxXmlToText(xml){
  var s = xml.replace(/<w:tab\b[^>]*\/?>/g, "\t").replace(/<\/w:p>/g, "\n")
    .replace(/<w:p\b[^>]*\/>/g, "\n").replace(/<w:br\b[^>]*\/?>/g, "\n").replace(/<[^>]+>/g, "");
  s = s.replace(/&amp;/g,"&").replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&quot;/g,'"')
       .replace(/&apos;/g,"'").replace(/&#(\d+);/g, function(_,n){ return String.fromCharCode(+n); });
  return s.replace(/[ \t]+\n/g,"\n").replace(/\n{3,}/g,"\n\n").trim();
}
async function _docxToText(buf){
  var u8 = new Uint8Array(buf), dv = new DataView(buf), eocd = -1;
  for(var i=u8.length-22; i>=0; i--){ if(dv.getUint32(i,true)===0x06054b50){ eocd=i; break; } }
  if(eocd<0) throw new Error("That doesn't look like a .docx file.");
  var cdOff=dv.getUint32(eocd+16,true), cnt=dv.getUint16(eocd+10,true), p=cdOff, t=null;
  for(var n=0; n<cnt; n++){
    if(dv.getUint32(p,true)!==0x02014b50) break;
    var method=dv.getUint16(p+10,true), compSize=dv.getUint32(p+20,true);
    var nameLen=dv.getUint16(p+28,true), extraLen=dv.getUint16(p+30,true), cmtLen=dv.getUint16(p+32,true);
    var localOff=dv.getUint32(p+42,true);
    var name=new TextDecoder().decode(u8.subarray(p+46, p+46+nameLen));
    if(name==="word/document.xml"){ t={method:method, compSize:compSize, localOff:localOff}; break; }
    p += 46 + nameLen + extraLen + cmtLen;
  }
  if(!t) throw new Error("Couldn't read the text in that .docx.");
  var lh=t.localOff;
  if(dv.getUint32(lh,true)!==0x04034b50) throw new Error("That .docx looks damaged.");
  var dstart = lh + 30 + dv.getUint16(lh+26,true) + dv.getUint16(lh+28,true);
  var comp = u8.subarray(dstart, dstart + t.compSize), xmlBytes;
  if(t.method===0) xmlBytes = comp;
  else if(t.method===8) xmlBytes = await _inflateRaw(comp);
  else throw new Error("Unsupported compression in that .docx.");
  return _docxXmlToText(new TextDecoder().decode(xmlBytes));
}
var _pdfjs = null;
// pdf.js is the one CDN dependency loaded by dynamic import(), which (unlike a
// <script integrity>) can't carry an SRI attribute. We pin it anyway: fetch the
// bytes with the native integrity option (browser verifies SHA-384 and rejects
// a tampered bundle), then import / run from a same-origin blob URL. Mirrors how
// supabase-js and Sentry are SRI-pinned. Hashes are verified by verify/pdfjs_sri.py.
var PDFJS_VER = "4.7.76";
var PDFJS_SRI = "sha384-qgyx6GmMWoI003drRr62DU41/67b3n7M2G0EXu2WhaOsBqONtHyay9Vw4aIivyOX";
var PDFJS_WORKER_SRI = "sha384-ATeT9bCTw1LFxZRSxFHBli/+35MHo/faKiXDlvCvxK2ENYquq3OIA9RkrOW44G/L";
async function _verifiedBlobUrl(url, sri){
  var resp = await fetch(url, { integrity: sri, mode: "cors", credentials: "omit" });
  if(!resp.ok) throw new Error("Couldn't load the PDF reader.");
  return URL.createObjectURL(await resp.blob());  // throws if the SHA-384 doesn't match
}
async function _loadPdfjs(){
  if(_pdfjs) return _pdfjs;
  var base = "https://cdn.jsdelivr.net/npm/pdfjs-dist@" + PDFJS_VER + "/build/";
  var lib = await import(await _verifiedBlobUrl(base + "pdf.min.mjs", PDFJS_SRI));
  lib.GlobalWorkerOptions.workerSrc = await _verifiedBlobUrl(base + "pdf.worker.min.mjs", PDFJS_WORKER_SRI);
  _pdfjs = lib; return lib;
}
async function _pdfToText(buf){
  var lib = await _loadPdfjs();
  var pdf = await lib.getDocument({ data: buf }).promise, out = [];
  for(var i=1; i<=pdf.numPages; i++){
    var page = await pdf.getPage(i), tc = await page.getTextContent();
    out.push(tc.items.map(function(it){ return it.str; }).join(" "));
  }
  return out.join("\n\n").replace(/[ \t]+\n/g,"\n").replace(/\n{3,}/g,"\n\n").trim();
}
async function extractResumeFile(file){
  var name = (file.name||"").toLowerCase();
  if(name.endsWith(".txt") || name.endsWith(".md") || name.endsWith(".markdown") ||
     file.type==="text/plain" || file.type==="text/markdown")
    return (await file.text()).trim();
  if(name.endsWith(".docx")) return _docxToText(await file.arrayBuffer());
  if(name.endsWith(".pdf") || file.type==="application/pdf") return _pdfToText(await file.arrayBuffer());
  if(name.endsWith(".doc")) throw new Error("Old .doc files aren't supported — save it as .docx, or paste the text.");
  throw new Error("Use a .docx, .pdf, .md, or .txt file — or paste the text below.");
}
(function(){
  var fi = document.getElementById("resumefile"); if(!fi) return;
  fi.addEventListener("change", function(){
    var file = fi.files && fi.files[0]; if(!file) return;
    var msgEl = document.getElementById("resumemsg");
    if(file.size > 8*1024*1024){ if(msgEl) msgEl.textContent = "That file's quite large — try a smaller one, or paste the text."; fi.value=""; return; }
    if(msgEl) msgEl.textContent = "Reading " + file.name + "…";
    extractResumeFile(file).then(function(text){
      text = (text||"").trim();
      if(text.length < 40){
        if(msgEl) msgEl.textContent = "I couldn't find readable text in that (a scanned PDF, maybe?). Paste your résumé below instead.";
        fi.value=""; return;
      }
      var box = document.getElementById("resumebox"); if(box) box.value = text;
      state.resume = text; persist();
      if(msgEl) msgEl.textContent = "Loaded from " + file.name + " ✦ — look it over, then Save.";
      showToast("Résumé loaded ✦");
      fi.value="";
    }).catch(function(err){
      if(msgEl) msgEl.textContent = (err && err.message) || "I couldn't read that file — try paste instead.";
      fi.value="";
    });
  });
})();

// Delegated on the app container so Jobs, Today's picks and My-apps cards
// all share one set of handlers.
document.querySelector(".app").addEventListener("click",(e)=>{
  const t=e.target.closest("[data-act]"); if(!t) return;
  const id=t.getAttribute("data-id"), act=t.getAttribute("data-act");
  if(act==="open"){
    if(!state.applied[id]){ state.applied[id]=today(); markApplied(id, t); }
    persist(); setTimeout(render,400); return;
  }
  e.preventDefault();
  if(act==="applied"){
    if(state.applied[id]){ delete state.applied[id]; delete state.followUps[id]; }
    else { state.applied[id]=today(); markApplied(id, t); }
  }
  if(act==="saved"){ if(state.saved.has(id)){ state.saved.delete(id); delete state.savedAt[id]; }
    else { state.saved.add(id); state.savedAt[id]=today(); haptic(8); } }
  if(act==="hide"){ state.hidden.has(id)?state.hidden.delete(id):state.hidden.add(id); }
  if(act==="snooze"){
    if(snoozedNow(id)){ delete state.snooze[id]; }
    else {
      const until=new Date(); until.setDate(until.getDate()+3);
      state.snooze[id]=until.toISOString().slice(0,10);
      showToast("Okay — it'll come back in a few days.", "Undo",
        function(){ delete state.snooze[id]; persist(); render(); });
    }
  }
  if(act==="notes"){
    const box=t.closest(".card").querySelector(".notes");
    const open=box.classList.toggle("open");
    open ? openNotes.add(id) : openNotes.delete(id);
    if(open) box.querySelector("textarea").focus();
    return;                       // no re-render; keep the textarea focused
  }
  if(act==="fudone"){
    ensureFollowUp(id).done=true; persist();
    if(portalSync) portalSync.followUps();
    render(); return;
  }
  if(act==="fuedit"){
    ensureFollowUp(id).done=false; persist();
    if(portalSync) portalSync.followUps();
    render(); return;
  }
  if(act==="share"){
    const j=jobById.get(id);
    if(j && navigator.share){ navigator.share({title:j.title+" at "+j.company, url:safeUrl(j.url)}).catch(()=>{}); }
    return;
  }
  if(act==="qopt"){ quizPick(t); return; }
  if(act==="signup"){ openAuth("signup"); return; }
  if(act==="signin"){ openAuth("signin"); return; }
  if(act==="tailor"){ tailorJob(id); return; }
  if(act==="uploadresume"){ var fin=document.getElementById("resumefile"); if(fin) fin.click(); return; }
  if(act==="closetailor"){ closeTailorModal(); return; }
  if(act==="runtailor"){ runTailor(); return; }
  if(act==="copytailor"){ copyTailor(t.getAttribute("data-copy")); return; }
  if(act==="dltailor"){ downloadTailor(t.getAttribute("data-copy")); return; }
  if(act==="saveresume"){
    var rbox=document.getElementById("resumebox");
    if(rbox){
      state.resume = rbox.value.trim(); persist();
      var rmsg=document.getElementById("resumemsg");
      if(rmsg) rmsg.textContent = state.resume ? "Saved on this phone ✦" : "Cleared.";
      showToast(state.resume ? "Résumé saved — tap ✦ Tailor on any job ✦" : "Résumé cleared.");
    }
    return;
  }
  if(act==="saveprofile"){
    var L=document.getElementById("nm-legal"), P=document.getElementById("nm-pref");
    if(P) state.profile.preferredName = P.value.trim();
    if(L) state.profile.legalName = L.value.trim();
    persist(); renderCorner();
    if(portalSync) portalSync.profile();
    showToast("Saved. We'll call you " + (state.profile.preferredName || "by your name") + " here. ✦");
    return;
  }
  persist(); render();
});

// Auto-save notes as they type (no re-render, so the keyboard stays up).
const followTimers = {};
document.querySelector(".app").addEventListener("input",(e)=>{
  const t=e.target;
  const fuId=t.getAttribute("data-fu-name")||t.getAttribute("data-fu-phone")||
             t.getAttribute("data-fu-email")||t.getAttribute("data-fu-on");
  if(fuId){
    const fu=ensureFollowUp(fuId);
    if(t.hasAttribute("data-fu-name")) fu.name=t.value;
    if(t.hasAttribute("data-fu-phone")) fu.phone=t.value;
    if(t.hasAttribute("data-fu-email")) fu.email=t.value;
    if(t.hasAttribute("data-fu-on")) fu.on=t.value;
    persist();
    clearTimeout(followTimers[fuId]);
    followTimers[fuId]=setTimeout(function(){
      if(portalSync) portalSync.followUps();
      renderFollowAlerts();
    }, 700);
    return;
  }
  const id=t.getAttribute("data-note"); if(!id) return;
  const v=t.value;
  if(v.trim()) state.notes[id]=v; else delete state.notes[id];
  persist();
});

let _searchTimer = null;
document.getElementById("search").addEventListener("input",(e)=>{
  filters.q=e.target.value;                          // keep the field responsive
  clearTimeout(_searchTimer);                         // but debounce the heavy re-render
  _searchTimer=setTimeout(render, 150);               // so typing stays smooth on a phone
});

(function callBtn(){
  const b=document.getElementById("callbtn");
  const who = META.contact || "someone you trust";
  if(META.phone){ b.href="tel:"+META.phone.replace(/[^0-9+]/g,""); b.textContent="Something feels wrong? Call "+who; }
  else { b.removeAttribute("href"); b.style.cursor="default"; b.textContent="Something feels wrong? Ask "+who+" before you reply"; }
})();

(function followBanner(){
  const b=document.getElementById("followbanner"); if(!b) return;
  function go(){ setView("apps"); }
  b.addEventListener("click", go);
  b.addEventListener("keydown", function(e){
    if(e.key==="Enter"||e.key===" "){ e.preventDefault(); go(); }
  });
})();

(function followNotifyBtn(){
  const btn=document.getElementById("notifybtn"); if(!btn) return;
  btn.addEventListener("click", function(){
    if(typeof Notification==="undefined") return;
    Notification.requestPermission().then(function(p){
      if(p==="granted") showToast("Reminders on — we'll nudge you when it's time to follow up ✦");
      else if(p==="denied") showToast("Blocked in phone settings — you can still see alerts in My apps.");
      renderFollowAlerts(); maybeNotifyFollowUps();
    });
  });
})();

document.addEventListener("visibilitychange", function(){
  if(document.visibilityState==="visible"){ renderFollowAlerts(); maybeNotifyFollowUps(); }
});

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
  "<div>Tip: tap Share, then <b>Add to Home Screen</b> to keep this on your phone.</div>"+
  "<button id='installbtn' hidden style='margin-top:8px;background:var(--card);border:1px solid var(--line);"+
  "color:var(--green-d);font:inherit;font-weight:700;font-size:14px;padding:10px 18px;border-radius:999px;"+
  "min-height:44px;cursor:pointer'>Add this app to your phone</button>";
// Real one-tap install when the browser offers it (Chrome/Android). On iOS Safari
// the event never fires, so the Share -> Add to Home Screen tip above remains.
var deferredInstall = null;
window.addEventListener("beforeinstallprompt", function(e){
  e.preventDefault(); deferredInstall = e;
  var b = document.getElementById("installbtn"); if(b) b.hidden = false;
});
(function(){
  var b = document.getElementById("installbtn"); if(!b) return;
  b.onclick = function(){
    if(!deferredInstall) return;
    deferredInstall.prompt();
    deferredInstall.userChoice.finally(function(){ deferredInstall = null; b.hidden = true; });
  };
})();
window.addEventListener("appinstalled", function(){
  var b = document.getElementById("installbtn"); if(b) b.hidden = true;
});

/* ── Gentle engine: encouragement, celebration, toast ─────────────────── */
// Deterministic per-day hash — used by todaysPicks() to rotate which jobs lead
// the Today view (stable for a given day so the list doesn't shuffle on every tap).
function dayHash(){ const d=today(); let h=0; for(let i=0;i<d.length;i++) h=(h*31+d.charCodeAt(i))>>>0; return h; }
// Words of affirmation — a LARGE pool in Daddy's voice. pickEnc() draws from a
// shuffled "bag" so every line shows once before any repeats (then reshuffles),
// and the footer + greeting + each visit get a fresh one — never the same phrase
// sitting there all day.
const ENC_LINES = [
  "Job ads are wish lists. If you can do half of it, apply — you're more qualified than you let yourself believe. — Daddy",
  "You showed up today. That's the whole battle, and you won it. — Daddy",
  "One application beats five you never send. Small is enough. I'm proud of you. — Daddy",
  "“Pay not listed” isn't a no — it's just a question you get to ask. — Daddy",
  "Rough day? The jobs will keep. Be as kind to yourself as I am to you. — Daddy",
  "You are not behind. You're exactly where the next right step starts. — Daddy",
  "Your worth was never up for hire. A job is something you do, not who you are. — Daddy",
  "Send one. Just one. Then go rest knowing you moved the needle. — Daddy",
  "A 'no' from one office is just a door pointing you to the right one. — Daddy",
  "The bravest thing you'll do today is try. You've already got that in you. — Daddy",
  "Nervous hands still fill out applications. Do it scared — that counts double. — Daddy",
  "You don't have to feel ready. You just have to begin. I'm right here. — Daddy",
  "Every screen you fill out is proof you didn't give up. That's everything. — Daddy",
  "Slow progress is still progress. We're not racing anyone. — Daddy",
  "I'd hire you in a heartbeat. The right employer will see what I see. — Daddy",
  "Take the morning gently. The afternoon can hold one small step. — Daddy",
  "You survived 100% of your hardest days. Today's no match for you. — Daddy",
  "Rejection isn't a verdict on you. It's just traffic on the way there. — Daddy",
  "Tidy beats perfect. Send the good-enough application and breathe. — Daddy",
  "You are allowed to be proud of small wins. I sure am. — Daddy",
  "The fact that you're still trying tells me everything about your heart. — Daddy",
  "Rest is part of the work, not a break from it. Lie down guilt-free. — Daddy",
  "One steady step a day adds up faster than you'd ever guess. — Daddy",
  "You don't need to have it figured out. You just need to keep showing up. — Daddy",
  "Whatever today holds, you won't face it alone. — Daddy",
  "Courage isn't loud. Sometimes it's just opening the app again. — Daddy",
  "Your past doesn't disqualify you. It made you someone who keeps going. — Daddy",
  "Apply like someone who's already been believed in — because you have. — Daddy",
  "The hard part is starting. You're stronger than the blank form. — Daddy",
  "Good things are coming, and you're doing the work to meet them. — Daddy",
  "You are not too much, and you are not too late. — Daddy",
  "Every employer here was checked, so you're safe to just be yourself. — Daddy",
  "Drink some water, take a breath, and tap one job. That's a full day's brave. — Daddy",
  "I'm not proud of you because you applied. I'm proud of you, period. — Daddy",
  "The version of you a year from now is cheering for this exact moment. — Daddy",
  "You can do hard things gently. There's no prize for white-knuckling it. — Daddy",
  "If today all you did was open this, that's a start — and starts matter. — Daddy",
  "Confidence comes after you act, not before. So act, and let it catch up. — Daddy",
  "You've got a steady, capable mind. Let an employer be lucky to find it. — Daddy",
  "No experience? You have a lifetime of figuring things out. That's experience. — Daddy",
  "The right job is looking for someone exactly like you. Help it find you. — Daddy",
  "Be patient with yourself. Healing and job-hunting run on the same clock. — Daddy",
  "You don't have to earn rest. But you've earned it anyway today. — Daddy",
  "Tap one job before the doubt talks you out of it. Quick — I'll wait. — Daddy",
  "Whatever the inner critic says, I outrank it. And I say you've got this. — Daddy",
  "Some days 'enough' is just getting out of bed. That's a yes from me. — Daddy",
  "You are building a life, one small honest step at a time. Keep building. — Daddy",
  "Showing up imperfectly beats waiting to be perfect every single time. — Daddy",
  "The work you put in today is a gift to the you of next month. — Daddy",
  "You're not starting over. You're starting from experience. — Daddy",
  "I believe in you on the days you can't, so lean on that and keep moving. — Daddy",
  "A quiet day of trying is still a day you didn't quit. I see it. — Daddy",
  "Worthy of the job, worthy of rest, worthy of good things. All of it. — Daddy",
  "One foot, then the other. That's the whole secret. — Daddy",
  "You handle more than you give yourself credit for. Give yourself credit. — Daddy",
  "Send it before you're sure. Sure is overrated; brave is everything. — Daddy",
  "The list felt long, so just take the top one. Done is better than perfect. — Daddy",
  "Your name on an application is a small act of hope. I love seeing it. — Daddy",
  "If it was easy you wouldn't need to be brave — and look, you are. — Daddy",
  "Take up space. You belong in that interview chair. — Daddy",
  "Progress you can't feel is still progress you're making. Trust it. — Daddy",
  "You are doing better than the voice in your head is telling you. — Daddy",
  "Today doesn't have to be a big day. It just has to be a kind one. — Daddy",
  "Whatever happens with the search, you're still my greatest pride. — Daddy",
  "The effort is yours to give; the outcome isn't yours to carry alone. — Daddy",
  "One application is a complete success. Don't let 'more' steal that. — Daddy",
  "Breathe in: I can try. Breathe out: that's enough. Now tap one. — Daddy",
  "You've come further than you can see from where you're standing. — Daddy",
  "Steady wins this. And steady is exactly what you are. — Daddy",
  "There's no wrong pace for healing or hunting. Yours is the right one. — Daddy",
  "I'd rather you send one with a calm heart than ten in a panic. — Daddy",
  "The door you're looking for opens for the people who keep knocking. — Daddy",
  "You are not a burden for needing time. You're a person, and you're mine. — Daddy",
  "Small and consistent beats big and burned-out. Go small today. — Daddy",
  "Each 'apply' is you betting on yourself. Smart bet. I'd take it. — Daddy",
  "You don't have to be fearless. You just have to be willing. You are. — Daddy",
  "The right people will be glad you walked in. Go let them. — Daddy",
  "Give yourself the grace you'd give anyone you love. You deserve it too. — Daddy",
  "However today goes, you can come back tomorrow. The door stays open. — Daddy",
  "You're allowed to want a good life. Reaching for it is not too much. — Daddy",
  "Quiet courage is still courage. You've got more than you know. — Daddy",
  "One honest try today. That's the assignment, and you're acing it. — Daddy",
  "Your effort counts even when no one writes back. I'm counting it. — Daddy",
  "Be brave for ten minutes. That's usually all a step takes. — Daddy",
  "You are not behind your old self, your friends, or anyone. You're on time. — Daddy",
  "The hardest worker I know is also allowed to rest. Both are true. — Daddy",
  "Keep going gently. Gentle and forward is still forward. — Daddy",
  "If you can read this and try one thing, today was a win. — Daddy",
  "You're worth the wait, and you're worth the work. Now go, sweetheart. — Daddy",
  "Whatever you get done today, come back and let me tell you I'm proud. ✦ — Daddy",
];
let _encBag = [];
function pickEnc(){
  if(!_encBag.length) _encBag = ENC_LINES.map(function(_, i){ return i; });
  var k = Math.floor(Math.random() * _encBag.length);
  var i = _encBag.splice(k, 1)[0];        // pull it OUT so it can't recur this cycle
  return ENC_LINES[i];
}
const KIND_LINES = [
  "That took real effort. Proud of you. ✦ — Daddy",
  "Applied! That's a genuine step forward. ✦ — Daddy",
  "Look at you go. One more out the door. ✦ — Daddy",
  "Done — and it's in your weekly log too. ✦ — Daddy",
  "That's my girl. Keep that momentum. ✦ — Daddy",
  "Sent! That's courage you can be proud of. ✦ — Daddy",
  "Another one in. You're on a roll. ✦ — Daddy",
  "Yes! That's a real step toward a real job. ✦ — Daddy",
  "Brave done quietly is still brave. Proud of you. ✦ — Daddy",
  "That's the way. One honest try at a time. ✦ — Daddy",
  "Logged and counted. You're building something. ✦ — Daddy",
  "Look at you keeping promises to yourself. ✦ — Daddy",
  "You did the scary thing. I'm beaming. ✦ — Daddy",
  "Steady and brave — that's exactly who you are. ✦ — Daddy",
];
let toastTimer = null;
function showToast(text, label, fn){
  const t=document.getElementById("toast"), b=document.getElementById("toastact");
  document.getElementById("toasttext").textContent = text;
  if(label && fn){ b.hidden=false; b.textContent=label; b.onclick=function(){ t.hidden=true; fn(); }; }
  else { b.hidden=true; b.onclick=null; }
  t.hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(function(){ t.hidden=true; }, 6000);
}
const REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
// Subtle haptic on positive actions — the kind of thing a phone user feels as
// quality. Silently no-ops where unsupported (iOS Safari) or reduced-motion is on.
function haptic(pattern){ if(!REDUCED && navigator.vibrate){ try{ navigator.vibrate(pattern); }catch(e){} } }
function celebrate(el){
  haptic(12);
  showToast(KIND_LINES[Math.floor(Math.random() * KIND_LINES.length)]);
  if(REDUCED || !el || !el.getBoundingClientRect) return;
  const r = el.getBoundingClientRect();
  for(let i=0;i<8;i++){
    const s=document.createElement("span");
    s.className="burst"; s.textContent="✦";
    s.style.left=(r.left+r.width/2)+"px"; s.style.top=(r.top+r.height/2)+"px";
    s.style.setProperty("--bx", (Math.cos(i/8*6.283)*70+(Math.random()*20-10))+"px");
    s.style.setProperty("--by", (Math.sin(i/8*6.283)*70-30)+"px");
    document.body.appendChild(s);
    setTimeout(function(){ s.remove(); }, 1100);
  }
}

/* ── Weekly tally (Iowa work-search week runs Sunday–Saturday) ──────────── */
function weekStart(){
  const d=new Date(); d.setDate(d.getDate()-d.getDay());   // back to Sunday
  return d.toISOString().slice(0,10);
}
function appsThisWeek(){
  const ws=weekStart();
  return Object.values(state.applied).filter(function(d){ return d && d>=ws; }).length;
}

/* ── Today's 3 picks: deterministic per day, trusted/will-train first ──── */
function todaysPicks(){
  // Keep applied jobs in the pool so the day's picks stay put — she can just
  // tap "Applied" again to undo a mistake (they don't vanish from Today).
  const pool = JOBS.filter(function(j){
    return !state.hidden.has(j.id) && !snoozedNow(j.id);
  });
  const ranked = orderForYou(pool).map(function(j,i){
    return [ (j.trusted?2:0)+(j.trains?1:0), -i, j ];
  }).sort(function(a,b){ return b[0]-a[0] || b[1]-a[1]; }).map(function(x){ return x[2]; });
  const picks=[], h=dayHash(), top=ranked.slice(0, Math.min(12, ranked.length));
  for(let k=0; k<top.length && picks.length<3; k++){
    picks.push(top[(h+k*5) % top.length]);
    for(let dup=0; dup<picks.length-1; dup++)
      if(picks[dup]===picks[picks.length-1]){ picks.pop(); break; }
  }
  return picks;
}
function renderPicks(){
  const wrap=document.getElementById("picks"); if(!wrap) return;
  wrap.innerHTML="";
  const picks=todaysPicks();
  document.getElementById("todayenc").textContent = pickEnc();
  if(!picks.length){
    wrap.innerHTML='<div class="empty">'+IC.check+"<div>You've worked through today's list — genuinely well done. New jobs arrive every morning.</div></div>";
    return;
  }
  picks.forEach(function(j,i){ wrap.appendChild(cardEl(j,i)); });
}

/* ── My applications + the printable Iowa work-search log ──────────────── */
function appliedEntries(){
  return Object.keys(state.applied).map(function(id){
    const lg = state.appliedLog[id] || {};
    const j = jobById.get(id);
    return { id:id, date: state.applied[id] || lg.d || "", ts: lg.ts || "",
             title: lg.t || (j&&j.title) || "(job no longer listed)",
             company: lg.c || (j&&j.company) || "",
             url: lg.u || (j&&j.url) || "" };
  }).sort(function(a,b){ return a.date<b.date?1:-1; });
}
// A human date+time stamp for the work-search log. Falls back to the date alone
// for entries logged before timestamps existed.
function fmtStamp(ts, date){
  if(ts){ var dt=new Date(ts);
    if(!isNaN(dt.getTime())) return dt.toLocaleString([],
      {year:"numeric",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}); }
  return date || "";
}
function renderApps(){
  const wrap=document.getElementById("applist"); if(!wrap) return;
  const wk=appsThisWeek();
  document.getElementById("weekline").innerHTML =
    wk + (wk===1?" application":" applications") + " this week " +
    (wk>=3 ? "— that covers the 3 applications Iowa asks for. ✦"
           : "— Iowa asks for 4 work-search activities a week, 3 of them applications.");
  wrap.innerHTML="";
  const rows=appliedEntries();
  if(!rows.length){
    wrap.innerHTML='<div class="empty">'+IC.pen+'<div>Nothing here yet — and that\'s okay. When you tap <b>Apply</b> or <b>I applied</b> on a job, it lands here with the date saved.</div></div>';
    return;
  }
  rows.forEach(function(r){
    const j=jobById.get(r.id);
    const days=daysSince(r.date);
    const fu=state.followUps[r.id]||{};
    const el=document.createElement("div");
    el.className="card";
    el.innerHTML =
      '<div class="title">'+esc(r.title)+'</div>'+
      '<div class="co">'+esc(r.company)+'</div>'+
      '<div class="meta"><span>'+IC.check+'applied '+esc(ago(r.date)||r.date)+'</span>'+
        (fu.name?'<span>'+IC.pen+esc(fu.name)+'</span>':'')+
        (fu.on&&!fu.done?'<span>'+IC.pen+(fu.on<=today()?'follow up today':'follow up '+esc(ago(fu.on)||fu.on))+'</span>':'')+
      '</div>'+
      followUpBlockHTML(r.id, j)+
      (days!=null&&days>=5&&j&&!fu.done?'<div class="nudge">It\'s been a bit — a quick call shows you\'re serious.'+callScriptHTML(j,r.date)+'</div>':'')+
      (r.url?'<div class="actions"><a class="act" style="text-decoration:none" href="'+esc(safeUrl(r.url))+'" target="_blank" rel="noopener">'+IC.arrow+'View job</a>'+
      '<button class="act" data-act="applied" data-id="'+esc(r.id)+'">'+IC.eye+'Un-mark</button></div>':'');
    wrap.appendChild(el);
  });
}
function logRowsText(){
  return appliedEntries().map(function(r){
    return fmtStamp(r.ts, r.date)+"  —  "+r.title+(r.company?", "+r.company:"")+"  —  applied online";
  });
}
document.getElementById("printlog").onclick = function(){
  // The log is a legal/unemployment document, so it carries her LEGAL name.
  // Autofill it; if we don't have it yet, ask once (and remember it).
  var legal = (state.profile.legalName||"").trim();
  if(!legal){
    var entered = window.prompt("Your legal name for the work-search log "+
      "(used for unemployment or court — you can change it later in My corner):", "");
    if(entered && entered.trim()){
      legal = entered.trim(); state.profile.legalName = legal; persist();
      renderCorner(); if(portalSync) portalSync.profile();
    }
  }
  const rows=appliedEntries();
  const wl=document.getElementById("worklog");
  wl.innerHTML =
    "<h1>Work-Search Log</h1>"+
    "<p>Name: "+(legal?esc(legal):"______________________")+"   Week of "+esc(weekStart())+" (Sunday–Saturday)   "+
    "Iowa asks for 4 reemployment activities per week; at least 3 must be job applications.</p>"+
    "<table><tr><th>Logged (date &amp; time)</th><th>Position</th><th>Employer</th><th>How</th><th>Result / notes</th></tr>"+
    rows.map(function(r){
      return "<tr><td>"+esc(fmtStamp(r.ts, r.date))+"</td><td>"+esc(r.title)+"</td><td>"+esc(r.company)+
             "</td><td>Online application</td><td>"+esc((state.notes[r.id]||"").slice(0,80))+"</td></tr>";
    }).join("")+
    (rows.length?"":"<tr><td colspan=5>(no applications logged yet)</td></tr>")+
    "</table>";
  wl.hidden=false;
  window.print();
};
document.getElementById("copylog").onclick = function(){
  var legal=(state.profile.legalName||"").trim();
  const text=(legal?legal+" — work-search log":"My work-search log")+"\n"+logRowsText().join("\n");
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
    .then(function(){ showToast("Copied — paste it into your weekly claim or a text."); })
    .catch(function(){ showToast("Couldn't copy automatically — use Print instead."); });
};

/* ── My corner: greeting + about-me quiz (tunes the feed today; the AI
      companion plugs in here once sign-in exists) ───────────────────────── */
const QUIZ = [
  ["kind","What kind of work sounds best right now?",
    [["people","With people"],["quiet","Quiet & organized"],["hands","Keeping my hands busy"],["care","Caring for others"]]],
  ["where","Where would you rather be?",
    [["out","Out of the house"],["home","Working from home"],["either","Either is fine"]]],
  ["time","What hours fit your life?",
    [["day","Daytime"],["evening","Evenings"],["any","Whatever works"]]],
  ["pay","Posted pay?",
    [["must","Show $19+ first"],["open","Good jobs, listed pay or not"]]],
  ["confidence","How are you feeling about applying?",
    [["low","Nervous — start me easy"],["ok","Ready — bring it on"]]],
];
function renderCorner(){
  const body=document.getElementById("quizbody"); if(!body) return;
  const h=new Date().getHours();
  const part = h<5?"You're up late":(h<12?"Good morning":(h<17?"Good afternoon":"Good evening"));
  const pref = (state.profile.preferredName||"").trim();   // in-app we use the PREFERRED name only
  document.getElementById("cornergreet").textContent =
    part+(pref?", "+pref:"")+". This page is just for you — no job list, no pressure. "+pickEnc();
  // Name editor reflects whatever's saved (preferred shown in app; legal used on the log).
  var nmP=document.getElementById("nm-pref"), nmL=document.getElementById("nm-legal");
  if(nmP) nmP.value = state.profile.preferredName || "";
  if(nmL) nmL.value = state.profile.legalName || "";
  var rbx=document.getElementById("resumebox"); if(rbx && document.activeElement!==rbx) rbx.value = state.resume || "";
  body.innerHTML = QUIZ.map(function(q){
    return '<div class="qq">'+esc(q[1])+'</div><div class="qopts">'+
      q[2].map(function(o){
        const on = state.profile[q[0]]===o[0];
        return '<button class="qopt" data-act="qopt" data-q="'+esc(q[0])+'" data-v="'+esc(o[0])+'" aria-pressed="'+on+'">'+esc(o[1])+'</button>';
      }).join("")+'</div>';
  }).join("") +
  (Object.keys(state.profile).length>=QUIZ.length
    ? '<div class="qdone">Got it. The Jobs page now puts your kind of work first. ✦</div>' : "");
}
function quizPick(t){
  const q=t.getAttribute("data-q"), v=t.getAttribute("data-v");
  state.profile[q] = (state.profile[q]===v) ? undefined : v;
  if(state.profile[q]===undefined) delete state.profile[q];
  persist(); render();
  if(portalSync) portalSync.profile();
}

/* ── Bottom-nav views ───────────────────────────────────────────────────── */
const VIEWS = {
  jobs:   [".controls","#progress","#count","#list","#empty"],
  today:  ["#todaywrap"],
  apps:   ["#appswrap"],
  corner: ["#cornerwrap"],
  help:   [".safety","#coach","#faqwrap"],
};
// Tabs that need an account. Signed-out, tapping one shows the benefits screen
// (#lockwrap) instead of its content. Jobs (browse/teaser) and Help (safety +
// crisis lines) stay open — gating a crisis hotline would be harmful.
const LOCKED_VIEWS = { today:1, apps:1, corner:1 };
function isAuthed(){ var a=document.querySelector(".app"); return !!(a && a.classList.contains("authed")); }
function setView(name){
  var locked = !!LOCKED_VIEWS[name] && !isAuthed();
  Object.keys(VIEWS).forEach(function(v){
    VIEWS[v].forEach(function(sel){
      const el=document.querySelector(sel);
      if(el) el.hidden = locked || (v!==name) || (sel==="#coach" && state.coachOff) ||
                         (sel==="#empty" && el.hidden && v===name && name==="jobs");
    });
    const btn=document.getElementById("nav-"+v);
    if(btn) btn.setAttribute("aria-current", String(v===name));
  });
  var lw=document.getElementById("lockwrap"); if(lw) lw.hidden = !locked;
  if(locked){ tuneLockView(name); window.scrollTo({top:0}); return; }
  if(name==="jobs") render(); else { renderPicks(); renderApps(); renderCorner(); }
  // Fresh words of affirmation on every tab entry (and on reload) — never the
  // same phrase twice in a row that the eye can notice. Today/corner refresh
  // their own enc inside renderPicks()/renderCorner(); the Jobs footer is here.
  if(name==="jobs"){ var fe=document.getElementById("footenc"); if(fe) fe.textContent=pickEnc(); }
  window.scrollTo({top:0});
}
["jobs","today","apps","corner","help"].forEach(function(v){
  const b=document.getElementById("nav-"+v);
  if(b) b.onclick=function(){ setView(v); };
});
// Tailor the benefits-screen subcopy to whichever locked tab was tapped.
function tuneLockView(name){
  var sub=document.getElementById("locksub"); if(!sub) return;
  var msg={
    today:"“Today’s 3 picks” is a free-account feature — a tiny, doable shortlist each morning so the search never feels like too much.",
    apps: "Tracking what you’ve applied to (and your printable Iowa work-search log) saves to your free account so a new phone never loses it.",
    corner:"Your corner — résumé tailoring, Ruby (your support cow 🐄), and your saved details — lives in your free account so it follows you everywhere."
  }[name];
  sub.textContent = msg || "Browsing is free. Make a free account to actually use the app — it takes 10 seconds and saves everything to you.";
}
// Bridge to the sign-in/up modal (defined in the portal block when configured).
function openAuth(which){
  if(window.__openAuth){ window.__openAuth(which); return; }
  showToast("Sign-in isn’t set up on this device yet — your browsing still works.");
}

buildChips();
render();
setView("jobs");   // also seeds #footenc with a fresh phrase (see setView)

/* ── Portal: optional sign-in so saves follow the user across devices. ──────
   The page is fully usable without it: not configured -> this whole block is
   inert; configured but offline -> localStorage keeps working and we retry
   when the connection returns. All user-visible text goes through
   textContent or esc(); the publishable key here is browser-safe BY DESIGN
   (RLS + invite-only auth are the security boundary, never this key). */
(function(){
  if(!PORTAL || !PORTAL.url || !PORTAL.key) return;
  var tag = document.getElementById("sbjs");
  function boot(){ if(window.supabase && window.supabase.createClient) init(); }
  if(window.supabase){ boot(); }
  else if(tag){ tag.addEventListener("load", boot); }
  // CDN unreachable (offline first load): no listener fires, app runs as-is.

  function init(){
    // experimental.passkey enables signInWithPasskey/registerPasskey (verified
    // present in supabase-js 2.108.1; project WebAuthn RP is configured).
    var sb = window.supabase.createClient(PORTAL.url, PORTAL.key,
      { auth: { experimental: { passkey: true } } });
    var PAGE = location.origin + location.pathname;
    var acctBtn = document.getElementById("acctbtn"),
        acctPop = document.getElementById("acctpop"),
        acctOut = document.getElementById("acctpop-out"),
        acctIn = document.getElementById("acctpop-in"),
        acctEmail = document.getElementById("acctemail"),
        acctInitial = document.getElementById("acctinitial"),
        acctIcon = document.getElementById("accticon"),
        modal = document.getElementById("authmodal"),
        msg = document.getElementById("authmsg");
    var emailEl = document.getElementById("authemail"),
        passEl = document.getElementById("authpass"),
        legalEl = document.getElementById("authlegal"),
        prefEl = document.getElementById("authpref"),
        primaryBtn = document.getElementById("authprimarybtn"),
        toggleBtn = document.getElementById("authtoggle"),
        forgotBtn = document.getElementById("authforgot"),
        titleEl = document.getElementById("authtitle"),
        subEl = document.getElementById("authsub");
    var user = null, mode = "signin";  // "signin" | "signup"
    var noteRowId = {}, noteTimers = {};

    // Account popover: a small top-right control that expands/collapses, instead
    // of the old full-width band that pushed the job list down.
    function closeAcct(){ acctPop.hidden = true; acctBtn.setAttribute("aria-expanded", "false"); }
    acctBtn.onclick = function(e){
      e.stopPropagation();
      var willOpen = acctPop.hidden;
      acctPop.hidden = !willOpen;
      acctBtn.setAttribute("aria-expanded", willOpen ? "true" : "false");
    };
    document.addEventListener("click", function(e){
      if(!acctPop.hidden && !acctBtn.contains(e.target) && !acctPop.contains(e.target)) closeAcct();
    });

    var supportsPasskey = !!(window.PublicKeyCredential) && typeof sb.auth.signInWithPasskey === "function";
    if(!supportsPasskey){
      var pk = document.getElementById("authpasskey"); if(pk) pk.hidden = true;
      var pkd = document.getElementById("authpkdiv"); if(pkd) pkd.hidden = true;
    }
    // Social sign-in buttons appear ONLY for providers the project actually
    // enables — so a not-yet-configured Google button is never a dead end. If
    // Google OAuth is turned on later, the button shows up on its own.
    fetch(PORTAL.url + "/auth/v1/settings", { headers: { apikey: PORTAL.key } })
      .then(function(r){ return r.json(); })
      .then(function(s){ var ext = (s && s.external) || {};
        var g = document.getElementById("authgoogle"); if(g && ext.google) g.hidden = false; })
      .catch(function(){});

    function setMsg(t, isErr){ msg.textContent = t || ""; msg.className = "authmsg" + (isErr ? " err" : ""); }
    function reflectView(){
      // Re-run the active tab so the lock screen ↔ real content swaps the moment
      // auth state changes (sign in from a locked tab → its content appears).
      var cur="jobs"; ["jobs","today","apps","corner","help"].forEach(function(v){
        var b=document.getElementById("nav-"+v);
        if(b && b.getAttribute("aria-current")==="true") cur=v; });
      if(typeof setView==="function") setView(cur);
    }
    function showOut(){
      acctOut.hidden = false; acctIn.hidden = true;
      acctBtn.classList.remove("in"); acctIcon.hidden = false; acctInitial.hidden = true;
      acctBtn.setAttribute("aria-label", "Sign in");
      var app = document.querySelector(".app"); if(app) app.classList.remove("authed");
      reflectView();
    }
    function showIn(extra){
      acctOut.hidden = true; acctIn.hidden = false;
      var email = (user && user.email) || "signed in";
      acctEmail.textContent = email;
      var nm = ((state.profile && state.profile.preferredName) || "").trim();
      acctInitial.textContent = (nm || email || "?").charAt(0).toUpperCase();
      acctInitial.hidden = false; acctIcon.hidden = true;
      acctBtn.classList.add("in");
      acctBtn.setAttribute("aria-label", "Your account — signed in as " + email);
      var app = document.querySelector(".app"); if(app) app.classList.add("authed");
      reflectView();
    }
    function openModal(){ modal.hidden = false; setMsg(""); document.getElementById("authrecover").hidden = true;
      document.getElementById("authmain").hidden = false; setTimeout(function(){ emailEl.focus(); }, 60); }
    function closeModal(){ modal.hidden = true; }
    function friendly(error){
      var m = String(error && error.message || "");
      if(/passkey|webauthn|credential/i.test(m) && /no|not found|none/i.test(m))
        return "No passkey found on this device yet. Sign in another way first, then add Face ID below.";
      if(/already registered|user already/i.test(m)) return "You already have an account — try signing in instead.";
      if(/invalid login|invalid credentials|wrong/i.test(m)) return "That email and password don't match. Try again or use a sign-in link.";
      if(/email not confirmed|confirm/i.test(m)) return "Check your email and tap the confirm link first, then sign in.";
      if(/signup.*(not|dis)|not.*allowed/i.test(m)) return "New accounts are paused — ask " + (META.contact || "the person who set this up") + ".";
      if(/rate|too many/i.test(m)) return "Too many tries — wait a minute, then try again.";
      if(/fetch|network|load failed/i.test(m)) return "No internet right now — your saves are safe on this phone.";
      return (m || "Something went wrong").slice(0, 110);
    }

    function setMode(m){
      mode = m;
      var up = (m === "signup");
      titleEl.innerHTML = (up ? "Create your account " : "Welcome back ") + '<span class="sparkle">&#10022;</span>';
      subEl.textContent = up ? "Make an account so your jobs, notes and chats are saved and follow you."
                             : "Sign in so your jobs, notes and chats follow you to any device.";
      passEl.hidden = false;
      passEl.setAttribute("autocomplete", up ? "new-password" : "current-password");
      passEl.placeholder = up ? "Choose a password (8+ characters)" : "Password";
      legalEl.hidden = !up; prefEl.hidden = !up;   // names asked only when creating an account
      primaryBtn.textContent = up ? "Create account" : "Sign in";
      toggleBtn.textContent = up ? "Already have an account? Sign in" : "New here? Create an account";
      forgotBtn.hidden = up;
      setMsg("");
    }

    document.getElementById("acctsignin").onclick = function(){ closeAcct(); setMode("signin"); openModal(); };
    document.getElementById("authclose").onclick = closeModal;
    modal.addEventListener("click", function(e){ if(e.target === modal) closeModal(); });
    // Expected keyboard behavior: Enter submits the form, Escape closes the modal.
    modal.addEventListener("keydown", function(e){
      if(e.key === "Escape"){ closeModal(); return; }
      if(e.key === "Enter" && !document.getElementById("authmain").hidden){
        e.preventDefault(); primaryBtn.click();
      }
    });
    toggleBtn.onclick = function(){ setMode(mode === "signin" ? "signup" : "signin"); emailEl.focus(); };

    // Passkey — Face ID / fingerprint, no password.
    document.getElementById("authpasskey").onclick = function(){
      setMsg("Waiting for Face ID / fingerprint…");
      sb.auth.signInWithPasskey().then(function(r){
        if(r.error) setMsg(friendly(r.error), true);  // success handled by onAuthStateChange
      }).catch(function(e){ setMsg(friendly(e), true); });
    };

    // Email + password (sign in OR create account).
    primaryBtn.onclick = function(){
      var em = emailEl.value.trim(), pw = passEl.value;
      if(!/.+@.+\..+/.test(em)){ setMsg("That doesn't look like an email address.", true); return; }
      if(pw.length < 8){ setMsg("Password needs at least 8 characters.", true); return; }
      var legal = legalEl.value.trim(), pref = prefEl.value.trim();
      if(mode === "signup" && !legal){
        setMsg("Please add your legal name — it goes on your work-search log.", true); return; }
      setMsg(mode === "signup" ? "Creating your account…" : "Signing you in…");
      var p = mode === "signup"
        ? sb.auth.signUp({ email: em, password: pw, options: { emailRedirectTo: PAGE,
            data: { legal_name: legal, preferred_name: pref || legal.split(" ")[0] } } })
        : sb.auth.signInWithPassword({ email: em, password: pw });
      p.then(function(r){
        if(r.error){ setMsg(friendly(r.error), true); return; }
        if(mode === "signup"){
          // Keep names locally now; syncAll pushes them once the session is live.
          state.profile.legalName = legal;
          state.profile.preferredName = pref || legal.split(" ")[0];
          persist(); renderCorner();
        }
        if(mode === "signup" && r.data && r.data.user && !r.data.session)
          setMsg("Account made! Check your email and tap the confirm link, then come back and sign in.");
        // session present -> onAuthStateChange closes the modal + syncs (incl. profile).
      });
    };

    // Neutralize the form's default submit (no page reload). The submit EVENT
    // still fires on click / Enter — that's the signal phone password managers
    // (Google / Apple) use to offer to SAVE the password. The actual sign-in
    // keeps running through primaryBtn.onclick above, unchanged.
    var authForm = document.getElementById("authform");
    if(authForm) authForm.addEventListener("submit", function(e){ e.preventDefault(); });

    // Magic link — passwordless.
    document.getElementById("authmagic").onclick = function(){
      var em = emailEl.value.trim();
      if(!/.+@.+\..+/.test(em)){ setMsg("Enter your email above first, then tap this.", true); return; }
      setMsg("Sending your link…");
      sb.auth.signInWithOtp({ email: em, options: { emailRedirectTo: PAGE } })
        .then(function(r){ setMsg(r.error ? friendly(r.error) : "Link sent! Open your email ON THIS DEVICE and tap it.", !!r.error); });
    };

    // Google OAuth (degrades gracefully if the provider isn't configured yet).
    document.getElementById("authgoogle").onclick = function(){
      setMsg("Opening Google…");
      sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo: PAGE } })
        .then(function(r){ if(r.error) setMsg(/provider|not enabled|unsupported/i.test(r.error.message||"")
          ? "Google sign-in isn't set up yet — use a sign-in link or password for now." : friendly(r.error), true); });
    };

    // Forgot password -> recovery email.
    forgotBtn.onclick = function(){
      var em = emailEl.value.trim();
      if(!/.+@.+\..+/.test(em)){ setMsg("Enter your email above first, then tap Forgot password.", true); return; }
      setMsg("Sending a reset link…");
      sb.auth.resetPasswordForEmail(em, { redirectTo: PAGE })
        .then(function(r){ setMsg(r.error ? friendly(r.error) : "Reset link sent — open it from your email to set a new password.", !!r.error); });
    };

    // Set-new-password panel (after the user returns via a recovery link).
    document.getElementById("authsetpass").onclick = function(){
      var np = document.getElementById("authnewpass").value;
      var rmsg = document.getElementById("authrecmsg");
      if(np.length < 8){ rmsg.textContent = "At least 8 characters, please."; rmsg.className = "authmsg err"; return; }
      rmsg.textContent = "Saving…"; rmsg.className = "authmsg";
      sb.auth.updateUser({ password: np }).then(function(r){
        if(r.error){ rmsg.textContent = friendly(r.error); rmsg.className = "authmsg err"; }
        else { rmsg.textContent = "Done! You're signed in."; setTimeout(closeModal, 900); }
      });
    };

    document.getElementById("acctsignout").onclick = function(){
      closeAcct();
      sb.auth.signOut().catch(function(){});  // onAuthStateChange flips UI; local saves stay
    };

    // Offer to add a passkey once signed in (so next time = instant Face ID).
    function offerPasskey(){
      if(!supportsPasskey || !user) return;
      if(localStorage.getItem("pk_offered:" + user.id)) return;
      localStorage.setItem("pk_offered:" + user.id, "1");
      showToast("Add Face ID for instant sign-in next time?", "Add", function(){
        sb.auth.registerPasskey().then(function(r){
          showToast(r.error ? "Couldn't add it — that's okay, you're still signed in." : "Face ID ready ✦ — Daddy");
        }).catch(function(){ showToast("Couldn't add it this time — no worries."); });
      });
    }

    sb.auth.onAuthStateChange(function(evt, session){
      if(evt === "PASSWORD_RECOVERY"){
        modal.hidden = false;
        document.getElementById("authmain").hidden = true;
        document.getElementById("authrecover").hidden = false;
        return;
      }
      var u = session && session.user || null;
      var justIn = !!u && !user;
      user = u;
      setTailorBridge();
      if(user){ showIn(); closeModal(); if(justIn){ syncAll(); } }  // no auto passkey nudge
      else { showOut(); }
    });
    sb.auth.getSession().then(function(r){
      user = r.data && r.data.session && r.data.session.user || null;
      setTailorBridge();
      if(user){ showIn(); syncAll(); } else { showOut(); }
    }).catch(function(){ showOut(); });

    // Bridge so the (out-of-scope) card handler can call the JWT-gated tailor
    // function only while signed in; cleared on sign-out.
    function setTailorBridge(){
      window.__tailorInvoke = user
        ? function(payload){ return sb.functions.invoke("resume-tailor", { body: payload }); }
        : null;
    }
    // Bridge so the lock CTAs (card "Create a free account", benefits screen)
    // open the real auth modal. Only exists when the portal is configured.
    window.__openAuth = function(which){ closeAcct(); setMode(which==="signin"?"signin":"signup"); openModal(); };

    /* Pull server state, merge (a flag set anywhere stays set; newest note
       wins), then push back anything only this device knew about — which IS
       the localStorage import on first sign-in, no separate path needed. */
    function syncAll(){
      Promise.all([
        sb.from("user_job_status").select("job_id,applied,applied_on,saved,hidden"),
        sb.from("job_notes").select("id,job_id,body,created_at").order("created_at", { ascending: false }),
        sb.from("user_profile").select("profile").maybeSingle(),
      ]).then(function(res){
        if(res[0].error) throw res[0].error;
        if(res[1].error) throw res[1].error;
        // res[2] (profile) is null for a brand-new user — that's not an error.
        var localIds = {};
        Object.keys(state.applied).forEach(function(id){ localIds[id] = 1; });
        state.saved.forEach(function(id){ localIds[id] = 1; });
        state.hidden.forEach(function(id){ localIds[id] = 1; });
        var server = {};
        (res[0].data || []).forEach(function(r){
          server[r.job_id] = r;
          if(r.applied && !state.applied[r.job_id]) state.applied[r.job_id] = r.applied_on || today();
          if(r.saved) state.saved.add(r.job_id);
          if(r.hidden) state.hidden.add(r.job_id);
        });
        var sawNote = {};
        (res[1].data || []).forEach(function(r){
          if(sawNote[r.job_id]) return;            // newest-first: keep only latest
          sawNote[r.job_id] = 1; noteRowId[r.job_id] = r.id;
          state.notes[r.job_id] = r.body;
        });
        // Profile (quiz answers + legal/preferred name): server is a backup, local
        // wins. Fill only keys we don't already have, then push the merged result.
        var sp = (res[2] && res[2].data && res[2].data.profile) || {};
        Object.keys(sp).forEach(function(k){
          if(k==="followUps") return;
          if(state.profile[k] === undefined || state.profile[k] === "") state.profile[k] = sp[k];
        });
        var sfu = sp.followUps || {};
        Object.keys(sfu).forEach(function(id){
          if(!state.followUps[id]) state.followUps[id] = sfu[id];
          else {
            var l=state.followUps[id], r=sfu[id]||{};
            l.name = l.name || r.name || "";
            l.phone = l.phone || r.phone || "";
            l.email = l.email || r.email || "";
            if(!l.on) l.on = r.on || "";
            l.done = !!(l.done || r.done);
          }
        });
        persist(); render(); renderCorner();
        var toPush = Object.keys(localIds).filter(function(id){
          var s = server[id] || {};
          return (!!state.applied[id]) !== !!s.applied ||
                 state.saved.has(id) !== !!s.saved ||
                 state.hidden.has(id) !== !!s.hidden;
        });
        pushStatus(toPush);
        Object.keys(state.notes).forEach(function(id){ if(!sawNote[id]) pushNote(id); });
        pushProfile();
        showIn();
      }).catch(function(e){
        console.log("[portal] sync failed:", e && e.message || e);
        showIn("Signed in — will sync when online");
      });
    }

    function statusRow(id){
      return { job_id: id, applied: !!state.applied[id], applied_on: state.applied[id] || null,
               saved: state.saved.has(id), hidden: state.hidden.has(id),
               updated_at: new Date().toISOString() };
    }
    function pushStatus(ids){
      if(!user || !ids.length) return;
      sb.from("user_job_status").upsert(ids.map(statusRow), { onConflict: "user_id,job_id" })
        .then(function(r){ if(r.error) console.log("[portal] status push:", r.error.message); });
    }
    function pushNote(id){
      if(!user) return;
      var body = state.notes[id] || "";
      if(!body){
        if(noteRowId[id]){
          sb.from("job_notes").delete().eq("id", noteRowId[id])
            .then(function(){ delete noteRowId[id]; });
        }
        return;
      }
      if(noteRowId[id]){
        sb.from("job_notes").update({ body: body }).eq("id", noteRowId[id])
          .then(function(r){ if(r.error) console.log("[portal] note push:", r.error.message); });
      } else {
        sb.from("job_notes").insert({ job_id: id, body: body }).select("id").single()
          .then(function(r){
            if(r.data) noteRowId[id] = r.data.id;
            if(r.error) console.log("[portal] note push:", r.error.message);
          });
      }
    }
    function pushProfile(){
      if(!user) return;
      state.profile.followUps = state.followUps;
      sb.from("user_profile").upsert({ profile: state.profile }, { onConflict: "user_id" })
        .then(function(r){ if(r.error) console.log("[portal] profile push:", r.error.message); });
    }
    // Let the (non-portal) main script trigger a profile sync after name/quiz/follow-up edits.
    portalSync = { profile: pushProfile, followUps: pushProfile };

    // Live mutations: these delegated listeners run AFTER the main handlers
    // above (same container, registered later), so state is already updated.
    document.querySelector(".app").addEventListener("click", function(e){
      var t = e.target.closest("[data-act]"); if(!t || !user) return;
      var act = t.getAttribute("data-act");
      if(act === "applied" || act === "saved" || act === "hide" || act === "open")
        pushStatus([t.getAttribute("data-id")]);
    });
    document.querySelector(".app").addEventListener("input", function(e){
      var t = e.target.closest("[data-note]"); if(!t || !user) return;
      var id = t.getAttribute("data-note");
      clearTimeout(noteTimers[id]);
      noteTimers[id] = setTimeout(function(){ pushNote(id); }, 900);
    });
    window.addEventListener("online", function(){ if(user) syncAll(); });

    /* Ruby the emotional-support cow: a full-screen companion, signed-in only.
       Replies come from the 'companion' Edge Function (the Anthropic key lives
       server-side; this page never sees it). Voice is 100% browser-native and
       free: Web Speech mic input + SpeechSynthesis read-aloud, both feature-
       detected so nothing dead ever shows on an unsupported browser. */
    function mountRuby(){
      const card=document.getElementById("chatcard"); if(!card) return;
      if(!user){ return; }
      if(card.dataset.live){ return; }
      card.dataset.live="1";
      const openBtn=document.getElementById("rubyopen");
      const ov=document.getElementById("rubyov");
      const log=document.getElementById("rubylog");
      if(!openBtn||!ov||!log) return;
      openBtn.hidden=false;

      const inp=document.getElementById("rubyinput");
      const sendBtn=document.getElementById("rubysend");
      const closeBtn=document.getElementById("rubyclose");
      const micBtn=document.getElementById("rubymic");
      const spkBtn=document.getElementById("rubyspk");
      const listen=document.getElementById("rubylisten");

      /* ── Read-aloud (SpeechSynthesis) — opt-in toggle, calm voice if any. ── */
      const speechOK = ("speechSynthesis" in window) &&
        (typeof window.SpeechSynthesisUtterance !== "undefined");
      let speakOn = speechOK && localStorage.getItem("rubySpeak")!=="0";
      let voice=null;
      function pickVoice(){
        try{
          const vs=window.speechSynthesis.getVoices()||[];
          const pref=["Samantha","Google US English","Microsoft Aria","Microsoft Jenny","Victoria","Karen","Moira"];
          for(const name of pref){ const v=vs.find(function(x){return x.name===name;}); if(v){voice=v;return;} }
          voice=vs.find(function(x){return /en[-_]US/i.test(x.lang)&&/female|woman/i.test(x.name);})
               ||vs.find(function(x){return /^en/i.test(x.lang);})||vs[0]||null;
        }catch(e){}
      }
      if(speechOK){
        spkBtn.hidden=false;
        spkBtn.setAttribute("aria-pressed", speakOn?"true":"false");
        pickVoice();
        try{ window.speechSynthesis.onvoiceschanged=pickVoice; }catch(e){}
        spkBtn.onclick=function(){
          speakOn=!speakOn; spkBtn.setAttribute("aria-pressed", speakOn?"true":"false");
          localStorage.setItem("rubySpeak", speakOn?"1":"0");
          if(!speakOn){ try{ window.speechSynthesis.cancel(); }catch(e){} }
        };
      }
      function speak(text){
        if(!speakOn||!speechOK||!text) return;
        try{
          window.speechSynthesis.cancel();
          const u=new window.SpeechSynthesisUtterance(text);
          if(voice) u.voice=voice;
          u.rate=0.96; u.pitch=1.0; u.volume=1.0;
          window.speechSynthesis.speak(u);
        }catch(e){}
      }

      function addBub(cls, text){
        const b=document.createElement("div"); b.className="bub "+cls; b.textContent=text;
        log.appendChild(b); log.scrollTop=log.scrollHeight; return b;
      }
      let historyLoaded=false;
      function loadHistory(){
        if(historyLoaded) return; historyLoaded=true;
        sb.from("chat_messages").select("role,body").order("created_at",{ascending:false}).limit(14)
          .then(function(r){
            const rows=(r&&r.data||[]).reverse();
            if(!rows.length){
              addBub("ai","Hi, sweet thing — I'm Ruby. 🐄 No pressure today; just tell me how you're doing, or tap a job and I'll help you with it. Moo means I'm in your corner.");
            } else {
              rows.forEach(function(m){ addBub(m.role==="user"?"me":"ai", m.body); });
            }
          })
          .catch(function(){ addBub("ai","Hi, I'm Ruby. 🐄 Tell me how you're doing whenever you're ready."); });
      }

      let sending=false;
      function send(){
        if(sending) return;
        const msg=(inp.value||"").trim(); if(!msg) return;
        sending=true; inp.value=""; addBub("me", msg);
        const wait=document.createElement("div");
        wait.className="bub ai think"; wait.innerHTML="<i></i><i></i><i></i>";
        log.appendChild(wait); log.scrollTop=log.scrollHeight;
        sb.functions.invoke("companion", { body: { message: msg } })
          .then(function(r){
            const reply=(r&&r.data&&r.data.reply) ? r.data.reply
              : "I'm having a little trouble right now — your message is saved, try me again in a minute. 💜";
            wait.className="bub ai"; wait.textContent=reply; log.scrollTop=log.scrollHeight;
            speak(reply);
          })
          .catch(function(){
            wait.className="bub ai";
            wait.textContent="No connection right now — I'll be right here when you're back online. 💜";
          })
          .finally(function(){ sending=false; });
      }
      sendBtn.onclick=send;
      inp.addEventListener("keydown",function(e){
        if(e.key==="Enter"){ e.preventDefault(); send(); }
      });

      /* ── Mic input (SpeechRecognition) — fills the box + auto-sends. ── */
      const SR = (typeof window.SpeechRecognition!=="undefined") ? window.SpeechRecognition
               : (typeof window.webkitSpeechRecognition!=="undefined") ? window.webkitSpeechRecognition
               : null;
      let rec=null, listening=false;
      function setListening(on){
        listening=on;
        if(micBtn) micBtn.classList.toggle("on", on);
        if(listen){ listen.hidden=!on; }
      }
      if(SR && micBtn){
        micBtn.hidden=false;
        micBtn.onclick=function(){
          if(listening){ try{ rec.stop(); }catch(e){} return; }
          try{ window.speechSynthesis && window.speechSynthesis.cancel(); }catch(e){}
          try{
            rec=new SR();
            rec.lang="en-US"; rec.interimResults=false; rec.maxAlternatives=1; rec.continuous=false;
            rec.onresult=function(ev){
              let said="";
              try{ said=ev.results[0][0].transcript||""; }catch(e){}
              if(said){ inp.value=said; setListening(false); send(); }
            };
            rec.onerror=function(){ setListening(false); };
            rec.onend=function(){ setListening(false); };
            setListening(true); rec.start();
          }catch(e){ setListening(false); }
        };
      }

      function openRuby(){
        ov.hidden=false; document.body.style.overflow="hidden";
        loadHistory();
        try{ inp.focus(); }catch(e){}
      }
      function closeRuby(){
        ov.hidden=true; document.body.style.overflow="";
        if(listening){ try{ rec.stop(); }catch(e){} }
        try{ window.speechSynthesis && window.speechSynthesis.cancel(); }catch(e){}
      }
      openBtn.onclick=openRuby;
      closeBtn.onclick=closeRuby;
      document.addEventListener("keydown",function(e){
        if(e.key==="Escape" && !ov.hidden) closeRuby();
      });
    }
    const _showIn = showIn;
    showIn = function(extra){ _showIn(extra); mountRuby(); };
    if(user) mountRuby();
  }
})();

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
            and (r["source"] != "local" or commute_minutes(r["location"]) is not None)]
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
    ap.add_argument("--push-supabase", action="store_true",
                    help="After building the site, upsert today's safe rows into the "
                         "Supabase portal (no-op unless SUPABASE_URL + SUPABASE_SERVICE_KEY are set).")
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
    # Portal config never reaches a --mock page: canned data must not gain a
    # sign-in surface, and a mock page must never be deployed anyway.
    portal_cfg = None if args.mock else _portal_web_config()
    sentry_cfg = None if args.mock else _sentry_web_config()
    write_html(safe, len(hidden), len(rows), html_path, human,
               contact=args.contact, contact_phone=args.contact_phone,
               portal_cfg=portal_cfg, sentry_cfg=sentry_cfg)

    if args.push_supabase:
        if args.mock:
            print("  portal : refusing to push --mock data to Supabase")
        else:
            from portal import push as portal_push
            if portal_push.supabase_enabled():
                try:
                    portal_push.push_jobs(_portal_rows(safe, stamp.isoformat()), log=print)
                except RuntimeError as err:
                    # Loud but non-fatal: the public site must publish even
                    # when the portal is down. CI surfaces this in the log.
                    print(f"  WARNING: portal push failed (site still publishes): {err}",
                          file=sys.stderr)
            else:
                print("  portal : not configured (SUPABASE_URL/SUPABASE_SERVICE_KEY) - skipped")

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
