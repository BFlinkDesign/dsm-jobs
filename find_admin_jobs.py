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
    "warehouse associate", "retail associate", "cashier", "stocker",
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
# sorts first. Substring match on company name, case-insensitive.
TRUSTED_EMPLOYER_HINTS = [
    "state of iowa", "city of", "county", "school district", "community school",
    "dmacc", "drake university", "grand view", "des moines area", "iowa state",
    "unitypoint", "mercyone", "broadlawns", "the iowa clinic", "hy-vee", "hyvee",
    "fareway", "casey's", "caseys", "wells fargo", "principal financial", "nationwide",
    "wellmark", "athene", "emc insurance", "john deere", "corteva", "pella",
    "credit union", "bankers trust", "u.s. bank", "us bank", "wesley life",
    "goodwill", "salvation army", "ymca", "library", "department of", "police",
    "veterans affairs", "social security administration", "aerotek", "robert half",
    "kelly services", "express employment", "adecco", "manpower",
    # Additional recognizable Iowa + national employers (legit; reduces false hides)
    "vermeer", "kum & go", "kwik", "meredith", "voya", "gartner", "edward jones",
    "marsh", "businessolver", "dotdash", "ruan", "kemin", "pioneer", "telligen",
    "labcorp", "quest diagnostics", "amgen", "ups", "fedex", "target", "walmart",
    "amazon", "concentrix", "teleperformance", "sykes", "kelly", "randstad",
    "mercy", "methodist", "genesis health", "iowa health", "wellpoint", "humana",
    "cvs", "walgreens", "hyvee", "menards", "lowe's", "home depot", "costco",
]

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
ADMIN_TITLE_TERMS = [
    # admin / clerical
    "administrative assistant", "admin assistant", "administrative support",
    "administrative coordinator", "administrative specialist", "administrative aide",
    "receptionist", "front desk", "front office", "office assistant",
    "office administrator", "office coordinator", "office clerk", "office support",
    "data entry", "file clerk", "clerk typist", "clerical", "secretary",
    "scheduling coordinator", "scheduler", "office associate", "admin coordinator",
    # light office-adjacent
    "billing", "accounts payable", "accounts receivable", "medical records",
    "bank teller", "teller", "customer service", "call center", "mail clerk",
    "patient access", "records clerk", "data clerk", "intake",
    # entry-level general (no degree)
    "warehouse", "retail associate", "sales associate", "cashier", "stocker",
    "food service", "caregiver", "caretaker", "home care", "housekeep",
    "production associate", "production worker", "general labor", "laborer",
    "packer", "picker", "assembler", "dishwasher", "janitor", "custodian",
]

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
    try:
        # nosemgrep - url validated against ADZUNA_ALLOWED_PREFIX above; HTTPS host only.
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Adzuna HTTP {err.code}: {body}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Network error contacting Adzuna: {err.reason}") from err


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
            and not requires_degree(r)]
    dropped = len(all_rows) - len(rows)
    if verbose and dropped:
        print(f"  (filtered out {dropped} non-admin / senior / skilled / degree postings)")
    return rows


def sort_rows(rows):
    # Group by salary verdict (best first), newest-first within each group.
    rank = {"meets": 0, "estimated_ok": 1, "unlisted": 2, "below": 3}
    return sorted(rows, key=lambda r: (rank.get(r["verdict"], 9), _neg_date(r["created"])))


def _neg_date(d):
    # Sort newest-first within a verdict group.
    return (9999 - int(d[:4]) if d[:4].isdigit() else 9999, d)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

VERDICT_LABEL = {
    "meets":        ("Pays $19+/hr",          "#1a7f37"),
    "unlisted":     ("Pay not listed",        "#5b6470"),
    "below":        ("Under $19/hr",          "#a04100"),
    "estimated_ok": ("Pay not listed",        "#5b6470"),  # legacy; predicted now = unlisted
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
    """Trusted/known employers first, then $19+ first, then newest."""
    rank = {"meets": 0, "estimated_ok": 1, "unlisted": 2, "below": 3}
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
            "good": r["verdict"] == "meets",          # only employer-stated $19+
            "tagLabel": label,
            "tagColor": color,
            "posted": r["created"] or "",
            "url": r["url"],
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
<meta name="theme-color" content="#f4efe6">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Job Board">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<title>Job Board — Des Moines</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{
 --paper:#f7f3ea; --card:#fffdf8; --ink:#16241f; --ink2:#4c5a53; --line:#e6dccb;
 --green:#0f6b54; --green-d:#0a5340; --green-soft:#e6f0eb;
 --gold:#8a6d2b; --red:#a8312a; --shadow:0 1px 2px rgba(20,33,28,.05),0 6px 18px rgba(20,33,28,.06);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
 font-family:'Atkinson Hyperlegible',-apple-system,Segoe UI,Roboto,Arial,sans-serif;
 font-size:17px;line-height:1.55;-webkit-font-smoothing:antialiased}
.app{max-width:640px;margin:0 auto;padding:0 16px 120px}
svg{display:inline-block;vertical-align:-2px}
/* App bar */
header.bar{position:sticky;top:0;z-index:20;background:rgba(247,243,234,.92);
 backdrop-filter:saturate(1.1) blur(8px);margin:0 -16px;padding:14px 16px 12px;
 border-bottom:1px solid var(--line)}
.brandrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);font-weight:700}
.word{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:26px;line-height:1.05;letter-spacing:-.01em}
.safebadge{display:inline-flex;align-items:center;gap:6px;background:var(--green-soft);color:var(--green-d);
 font-size:12px;font-weight:700;padding:6px 10px;border-radius:999px;white-space:nowrap}
.summary{color:var(--ink2);font-size:14px;margin-top:6px}
/* Safety */
.safety{background:#fff;border:1px solid var(--line);border-left:4px solid var(--red);
 border-radius:14px;padding:14px 16px;margin:18px 0;box-shadow:var(--shadow)}
.safety h2{margin:0 0 4px;font-family:'Fraunces',Georgia,serif;font-size:19px;font-weight:600;
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
.pill.none{background:#f0ebe0;color:var(--ink2)}
.verified{display:inline-flex;align-items:center;gap:5px;color:var(--gold);font-size:13px;font-weight:700}
.title{font-family:'Fraunces',Georgia,serif;font-size:20px;font-weight:600;line-height:1.18;margin:0 0 3px}
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

  <div class="controls">
    <div class="searchwrap">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-3.5-3.5"/></svg>
      <input class="search" id="search" type="search" inputmode="search"
        placeholder="Search job or employer" aria-label="Search jobs">
    </div>
    <div class="chips" id="chips"></div>
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
let state = load();
state.applied = new Set(state.applied||[]);
state.saved   = new Set(state.saved||[]);
state.hidden  = new Set(state.hidden||[]);
function persist(){ save({applied:[...state.applied], saved:[...state.saved], hidden:[...state.hidden]}); }

let filters = { q:"", pay:false, inperson:false, remote:false, known:false, saved:false, applied:false, showHidden:false };
let sortBy = "pay";

const CHIPS = [
  ["pay","$19+/hr"], ["inperson","In person"], ["remote","Work from home"],
  ["known","Verified employer"], ["saved","Saved"], ["applied","Applied"],
  ["showHidden","Hidden"],
];
const IC = {
  pin:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 21s-6.5-5.7-6.5-10.5a6.5 6.5 0 0113 0C18.5 15.3 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.3"/></svg>',
  home:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/></svg>',
  bldg:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2"/></svg>',
  check:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5 5-5"/></svg>',
  bookmark:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  eye:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="2.5"/></svg>',
  arrow:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
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
  if(filters.pay && !j.good) return false;
  if(filters.inperson && j.remote) return false;
  if(filters.remote && !j.remote) return false;
  if(filters.known && !j.trusted) return false;
  if(filters.saved && !state.saved.has(j.id)) return false;
  if(filters.applied && !state.applied.has(j.id)) return false;
  return true;
}

function render(){
  const good = JOBS.filter(j=>j.good).length;
  document.getElementById("summary").textContent =
    JOBS.length + " safe jobs · " + good + " pay $19+/hr · updated " + META.generated;
  const ap = state.applied.size;
  const prog = document.getElementById("progress");
  prog.innerHTML = ap ? (IC.check + "You've applied to " + ap + (ap===1?" job":" jobs")) : "";

  const list = JOBS.filter(matches);
  list.sort((a,b)=> b.payNum-a.payNum || (b.posted<a.posted?-1: b.posted>a.posted?1:0));

  document.getElementById("count").textContent =
    list.length + (filters.showHidden?" hidden ":" ") + (list.length===1?"job":"jobs");

  const wrap = document.getElementById("list");
  wrap.innerHTML = "";
  const empty = document.getElementById("empty");
  empty.hidden = list.length>0;
  if(!list.length){ empty.innerHTML = IC.eye + "<div>No jobs match. Turn off a filter to see more.</div>"; }

  list.forEach(function(j,i){
    const applied = state.applied.has(j.id), saved = state.saved.has(j.id);
    const payCls = j.good ? "good" : "none";
    const verified = j.trusted ? '<span class="verified">'+IC.check+'Verified employer</span>' : '<span></span>';
    const where = j.remote ? (IC.home+"Work from home") : (IC.bldg+"In person");
    const el = document.createElement("div");
    el.className = "card";
    el.style.animationDelay = (Math.min(i,12)*0.025)+"s";
    el.innerHTML =
      '<div class="cardtop"><span class="pill '+payCls+'">'+esc(j.pay)+'</span>'+verified+'</div>'+
      '<div class="title">'+esc(j.title)+'</div>'+
      '<div class="co">'+esc(j.company)+'</div>'+
      '<div class="meta">'+
        '<span>'+IC.pin+esc(j.location)+'</span>'+
        '<span>'+where+'</span>'+
        (j.posted?'<span>posted '+esc(j.posted)+'</span>':'')+
      '</div>'+
      '<a class="apply" href="'+esc(safeUrl(j.url))+'" target="_blank" rel="noopener" data-act="open" data-id="'+esc(j.id)+'">Apply'+IC.arrow+'</a>'+
      '<div class="actions">'+
        '<button class="act applied'+(applied?' on':'')+'" data-act="applied" data-id="'+esc(j.id)+'">'+IC.check+(applied?'Applied':'I applied')+'</button>'+
        '<button class="act'+(saved?' on':'')+'" data-act="saved" data-id="'+esc(j.id)+'">'+IC.bookmark+(saved?'Saved':'Save')+'</button>'+
        '<button class="act" data-act="hide" data-id="'+esc(j.id)+'">'+IC.eye+(filters.showHidden?'Unhide':'Hide')+'</button>'+
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
}

document.getElementById("list").addEventListener("click",(e)=>{
  const t=e.target.closest("[data-act]"); if(!t) return;
  const id=t.getAttribute("data-id"), act=t.getAttribute("data-act");
  if(act==="open"){ state.applied.add(id); persist(); setTimeout(render,400); return; }
  e.preventDefault();
  if(act==="applied"){ state.applied.has(id)?state.applied.delete(id):state.applied.add(id); }
  if(act==="saved"){ state.saved.has(id)?state.saved.delete(id):state.saved.add(id); }
  if(act==="hide"){ state.hidden.has(id)?state.hidden.delete(id):state.hidden.add(id); }
  persist(); render();
});

document.getElementById("search").addEventListener("input",(e)=>{ filters.q=e.target.value; render(); });

(function callBtn(){
  const b=document.getElementById("callbtn");
  const who = META.contact || "someone you trust";
  if(META.phone){ b.href="tel:"+META.phone.replace(/[^0-9+]/g,""); b.textContent="Something feels wrong? Call "+who; }
  else { b.removeAttribute("href"); b.style.cursor="default"; b.textContent="Something feels wrong? Ask "+who+" before you reply"; }
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
    return [r for r in seen.values()
            if is_admin_title(r["title"]) and is_attainable(r["title"])
            and not title_excluded(r["title"])]


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

    n_good = sum(1 for r in safe if r["verdict"] in ("meets", "estimated_ok"))
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
