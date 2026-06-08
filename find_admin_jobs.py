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
TITLES = [
    "administrative assistant",
    "office assistant",
    "office administrator",
    "receptionist",
    "front desk",
    "data entry",
    "office clerk",
    "administrative coordinator",
    "office coordinator",
    "secretary",
    "clerical",
    "scheduler",
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
    "administrative assistant", "admin assistant", "administrative support",
    "administrative coordinator", "administrative specialist", "administrative aide",
    "receptionist", "front desk", "front office", "office assistant",
    "office administrator", "office coordinator", "office clerk", "office support",
    "data entry", "file clerk", "clerk typist", "clerical", "secretary",
    "scheduling coordinator", "scheduler", "office associate", "admin coordinator",
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
    # Wage FLOOR test: a job clears $19 only if the LOW end of its range does.
    # ($16-$23 should NOT count as "$19+"; the friend could be offered the bottom.)
    hourly_for_test = hourly_min if hourly_min is not None else hourly_max

    if hourly_for_test is None:
        verdict = "unlisted"            # no salary data at all
    elif hourly_for_test >= MIN_HOURLY:
        verdict = "estimated_ok" if predicted else "meets"
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

    # Remote pass across all admin titles (remote was explicitly requested).
    for title in TITLES:
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
    "meets":        ("PAYS $19+/hr",       "#1a7f37"),
    "estimated_ok": ("est. $19+/hr",       "#9a6700"),
    "unlisted":     ("salary not posted",  "#57606a"),
    "below":        ("below $19/hr",       "#cf222e"),
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
        floor = r["hourly_min"] if r["hourly_min"] is not None else (r["hourly_max"] or 0)
        jobs.append({
            "id": str(r.get("id") or r["url"]),
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "pay": salary_text(r),
            "payNum": float(floor or 0),
            "remote": r["source"] == "remote",
            "trusted": employer_is_trusted(r["company"]),
            "good": r["verdict"] in ("meets", "estimated_ok"),
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
<meta name="theme-color" content="#0d7c66">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="My Jobs">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<title>Jobs for you — Des Moines</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{
 --bg:#fbf7f0; --card:#fff; --ink:#1d2127; --muted:#5b6470; --line:#e8e1d4;
 --primary:#0d7c66; --primary-d:#0a5f4e; --amber-bg:#fff4d6; --amber-bd:#e3b341;
 --danger:#c0362c; --good:#1a7f37; --chip:#efe9dd;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:'Atkinson Hyperlegible',-apple-system,Segoe UI,Roboto,Arial,sans-serif;
 font-size:18px;line-height:1.5}
.app{max-width:680px;margin:0 auto;padding:0 14px 110px}
header.bar{position:sticky;top:0;z-index:20;background:var(--primary);color:#fff;
 margin:0 -14px;padding:16px;box-shadow:0 2px 10px rgba(0,0,0,.12)}
header .h1{font-size:23px;font-weight:700}
header .meta{font-size:14px;opacity:.93;margin-top:2px}
.safety{background:var(--amber-bg);border:2px solid var(--amber-bd);border-radius:14px;
 padding:14px 16px;margin:16px 0;font-size:17px}
.safety h2{margin:0 0 6px;font-size:19px}
.safety ul{margin:8px 0;padding-left:22px}
.safety li{margin:5px 0}
.callbtn{display:block;text-align:center;margin-top:12px;background:var(--danger);color:#fff;
 text-decoration:none;font-weight:700;padding:15px;border-radius:12px;font-size:18px;min-height:54px}
.toggle{background:none;border:none;color:var(--primary-d);font:inherit;font-weight:700;
 text-decoration:underline;padding:6px 0;cursor:pointer}
.controls{position:sticky;top:70px;z-index:15;background:var(--bg);padding:10px 0 4px}
.search{width:100%;font-size:18px;padding:15px 16px;border:2px solid var(--line);border-radius:12px;
 background:#fff;min-height:54px}
.chips{display:flex;gap:8px;overflow-x:auto;padding:10px 0 4px;-webkit-overflow-scrolling:touch}
.chip{flex:0 0 auto;background:var(--chip);border:2px solid transparent;border-radius:999px;
 padding:10px 16px;font-size:16px;font-weight:700;color:var(--ink);min-height:46px;white-space:nowrap}
.chip[aria-pressed="true"]{background:var(--primary);color:#fff}
.count{color:var(--muted);font-size:15px;margin:8px 2px}
.progress{color:var(--good);font-weight:700;font-size:15px;margin:2px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0;
 box-shadow:0 1px 4px rgba(0,0,0,.05);animation:rise .22s ease both}
.tag{display:inline-block;font-size:13px;font-weight:700;color:#fff;padding:3px 11px;border-radius:999px;margin-bottom:8px}
.title{font-size:20px;font-weight:700;margin:0 0 2px}
.co{font-size:17px;font-weight:700}
.known{color:var(--good)}
.r{color:var(--muted);font-size:15px;margin-top:6px}
.pay{font-weight:700;color:var(--ink)}
.apply{display:block;text-align:center;margin-top:14px;background:var(--primary);color:#fff;
 text-decoration:none;font-weight:700;padding:16px;border-radius:12px;font-size:19px;min-height:56px}
.apply:active{background:var(--primary-d)}
.actions{display:flex;gap:10px;margin-top:10px}
.act{flex:1;background:#fff;border:2px solid var(--line);border-radius:12px;padding:13px 8px;
 font:inherit;font-size:16px;font-weight:700;min-height:52px;color:var(--ink)}
.act.on{background:var(--primary);color:#fff;border-color:var(--primary)}
.act.applied.on{background:var(--good);border-color:var(--good)}
.empty{text-align:center;color:var(--muted);padding:48px 12px;font-size:18px}
.foot{color:var(--muted);font-size:14px;text-align:center;margin:26px 0 0;line-height:1.6}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.card{animation:none}}
</style>
</head>
<body>
<div class="app">
  <header class="bar">
    <div class="h1">Jobs for you</div>
    <div class="meta" id="hdrmeta"></div>
  </header>

  <section class="safety" id="safety">
    <h2>⚠️ Stay safe — read this</h2>
    <div>These jobs were checked and look real. Tap <b>Apply</b> to read and apply.</div>
    <div><b>It is ALWAYS a scam if a job asks you to:</b>
      <ul>
        <li>Pay money or buy equipment to start</li>
        <li>Cash a check and send part back, or buy gift cards</li>
        <li>Give your Social Security or bank number before a real interview</li>
        <li>Only talk by text, Telegram, or WhatsApp</li>
      </ul>
      If that happens — <b>STOP.</b> Don't reply. Don't send anything.</div>
    <a class="callbtn" id="callbtn" href="#">Something feels wrong? Ask first</a>
  </section>

  <div class="controls">
    <input class="search" id="search" type="search" inputmode="search"
      placeholder="Search job or company…" aria-label="Search jobs">
    <div class="chips" id="chips"></div>
  </div>

  <div class="progress" id="progress"></div>
  <div class="count" id="count"></div>
  <div id="list"></div>
  <div class="empty" id="empty" hidden>No jobs match. Tap “All jobs” to see everything.</div>

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
  ["pay","💵 $19+/hr only"], ["inperson","🏢 In person"], ["remote","🏠 Work from home"],
  ["known","✓ Known employer"], ["saved","⭐ Saved"], ["applied","✅ Applied"],
  ["showHidden","👁 Show hidden"],
];

function esc(s){ const d=document.createElement("div"); d.textContent=s==null?"":s; return d.innerHTML; }

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
  document.getElementById("hdrmeta").textContent =
    JOBS.length + " safe jobs · updated " + META.generated;
  const ap = state.applied.size;
  document.getElementById("progress").textContent = ap ? ("✅ You've applied to " + ap + (ap===1?" job":" jobs") + " — nice work!") : "";

  let list = JOBS.filter(matches);
  if(sortBy==="pay") list.sort((a,b)=> b.payNum-a.payNum || (b.posted<a.posted?-1:1));
  else list.sort((a,b)=> (b.posted<a.posted?-1: b.posted>a.posted?1:0));

  document.getElementById("count").textContent =
    "Showing " + list.length + (filters.showHidden? " hidden":" ") + " job" + (list.length===1?"":"s");

  const wrap = document.getElementById("list");
  wrap.innerHTML = "";
  document.getElementById("empty").hidden = list.length>0;

  for(const j of list){
    const applied = state.applied.has(j.id), saved = state.saved.has(j.id);
    const known = j.trusted ? '<span class="known">✓ known employer</span> · ' : '';
    const where = j.remote ? "🏠 Work from home" : "🏢 In person";
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML =
      '<span class="tag" style="background:'+j.tagColor+'">'+esc(j.tagLabel)+'</span>'+
      '<div class="title">'+esc(j.title)+'</div>'+
      '<div class="co">'+esc(j.company)+'</div>'+
      '<div class="r">'+known+esc(j.location)+'</div>'+
      '<div class="r"><span class="pay">'+esc(j.pay)+'</span> · '+where+
        (j.posted?(' · posted '+esc(j.posted)):'')+'</div>'+
      '<a class="apply" href="'+esc(j.url)+'" target="_blank" rel="noopener" '+
        'data-act="open" data-id="'+esc(j.id)+'">Apply</a>'+
      '<div class="actions">'+
        '<button class="act applied'+(applied?' on':'')+'" data-act="applied" data-id="'+esc(j.id)+'">'+(applied?'✅ Applied':'Mark applied')+'</button>'+
        '<button class="act'+(saved?' on':'')+'" data-act="saved" data-id="'+esc(j.id)+'">'+(saved?'⭐ Saved':'⭐ Save')+'</button>'+
        '<button class="act" data-act="hide" data-id="'+esc(j.id)+'">'+(filters.showHidden?'Unhide':'Hide')+'</button>'+
      '</div>';
    wrap.appendChild(el);
  }
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
  "We checked "+META.total+" postings and hid <b>"+META.hidden+"</b> that looked like scams.<br>"+
  "Tip: tap your phone's Share button → <b>Add to Home Screen</b> to keep this handy.";

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
