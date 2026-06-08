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
import html
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


# Plain-language rules card shown at the top of the friend's page.
RULES_HTML = """
<div class="rules">
  <div class="rules-h">Read this first — how to stay safe</div>
  <div class="ok">These jobs were checked and look real and a good fit for you.
    Click <b>View &amp; apply</b>, read the job, and apply.</div>
  <div class="bad"><b>It is ALWAYS a scam</b> if a "job" asks you to:
    <ul>
      <li>Pay money, a fee, or buy your own equipment to start</li>
      <li>Cash a check and send part of it back, or buy gift cards</li>
      <li>Give your Social Security number or bank info before a real interview</li>
      <li>Only talk by text, Telegram, or WhatsApp</li>
    </ul>
    If a job asks for any of that — <b>STOP and call {contact}.</b> Don't reply, don't send anything.</div>
</div>
"""


def write_html(safe_rows, hidden_count, total_checked, path, generated, contact="me"):
    rows = friend_sort(safe_rows)
    n_good = sum(1 for r in rows if r["verdict"] in ("meets", "estimated_ok"))

    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jobs for you — Des Moines</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0;
         background: #f6f8fa; color: #1f2328; }}
  .wrap {{ max-width: 820px; margin: 0 auto; padding: 22px 16px 64px; }}
  h1 {{ font-size: 23px; margin: 0 0 4px; }}
  .sub {{ color: #57606a; font-size: 14px; margin-bottom: 16px; }}
  .rules {{ background: #fff8e5; border: 2px solid #e3b341; border-radius: 12px;
           padding: 14px 16px; margin-bottom: 22px; font-size: 15px; line-height: 1.5; }}
  .rules-h {{ font-weight: 700; font-size: 16px; margin-bottom: 8px; }}
  .rules .ok {{ margin-bottom: 10px; }}
  .rules .bad {{ background: #fff; border-left: 4px solid #cf222e; padding: 8px 12px; border-radius: 6px; }}
  .rules ul {{ margin: 6px 0 6px 0; padding-left: 20px; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
          padding: 14px 16px; margin: 10px 0; }}
  .t {{ font-size: 17px; font-weight: 600; margin: 0 0 2px; }}
  .co {{ color: #1f2328; font-size: 15px; font-weight: 600; }}
  .trust {{ color: #1a7f37; font-size: 13px; font-weight: 600; }}
  .meta {{ color: #57606a; font-size: 13px; margin-top: 4px; }}
  .tag {{ display: inline-block; font-size: 12px; font-weight: 600; color: #fff;
         padding: 2px 8px; border-radius: 999px; margin-bottom: 6px; }}
  a.apply {{ display: inline-block; margin-top: 10px; font-size: 15px; background:#0969da;
            color:#fff; text-decoration: none; padding: 8px 14px; border-radius: 8px; font-weight: 600; }}
  .section {{ font-size: 14px; font-weight: 700; color: #1f2328; margin: 26px 0 6px; }}
</style></head><body><div class="wrap">
<h1>Jobs for you — Des Moines area</h1>
<div class="sub">{len(rows)} jobs that look safe and fit you &middot; {n_good} pay $19+/hr
&middot; updated {generated}</div>
{RULES_HTML.format(contact=html.escape(contact))}
<div class="sub">We looked at {total_checked} postings and <b>hid {hidden_count}</b> that
looked like scams so you won't see them.</div>
"""]

    order = [("meets", "✅ Pays $19+/hr"),
             ("estimated_ok", "✅ Pays about $19+/hr (confirm the pay when you apply)"),
             ("unlisted", "Pay not listed"),
             ("below", "Under $19/hr (extra options)")]

    for verdict, heading in order:
        group = [r for r in rows if r["verdict"] == verdict]
        if not group:
            continue
        parts.append(f'<div class="section">{html.escape(heading)} ({len(group)})</div>')
        for r in group:
            label, color = VERDICT_LABEL.get(r["verdict"], ("?", "#57606a"))
            src = "Work from home" if r["source"] == "remote" else "In person"
            trust = '<span class="trust">✓ known employer</span> &middot; ' \
                if employer_is_trusted(r["company"]) else ""
            parts.append(f"""<div class="card">
  <span class="tag" style="background:{color}">{html.escape(label)}</span>
  <div class="t">{html.escape(r['title'])}</div>
  <div class="co">{html.escape(r['company'])}</div>
  <div class="meta">{trust}{html.escape(r['location'])} &middot; {html.escape(salary_text(r))}
  &middot; {src} &middot; posted {html.escape(r['created'] or 'n/a')}</div>
  <a class="apply" href="{html.escape(r['url'])}" target="_blank" rel="noopener">View &amp; apply</a>
</div>""")

    parts.append("</div></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))


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
    csv_path = os.path.join(base, f"admin-jobs-{datestr}.csv")
    html_path = os.path.join(base, f"admin-jobs-{datestr}.html")
    write_csv(sort_rows(rows), csv_path)           # full audit incl. hidden
    write_html(safe, len(hidden), len(rows), html_path, human, contact=args.contact)

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
    print(f"SEND THIS to your friend: {html_path}")
    print(f"Your audit (all + scam reasons): {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
