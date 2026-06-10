# dsm-jobs

![CI](https://github.com/BFlinkDesign/dsm-jobs/actions/workflows/ci.yml/badge.svg)

Finds **admin / office / clerical** jobs that are **no-degree / HS-diploma friendly** and
target **$19+/hr**, across the **Des Moines metro** (~20 mi: Grimes, Ankeny, Waukee, West
Des Moines, Johnston, Urbandale, Altoona, Clive) **plus remote** roles. You run it, it
writes a clean HTML page you forward to your friend.

It uses the **Adzuna** job API (a legitimate aggregator that re-publishes Indeed/etc.
postings) instead of scraping Indeed/LinkedIn directly — so it won't get IP-banned and
won't break every week.

## One-time setup

1. **Get a free Adzuna API key** (2 min): https://developer.adzuna.com/signup
   - After signing up you'll see an **Application ID** and an **Application Key**.
2. In this folder, copy `.env.example` to `.env` and paste the two values:
   ```
   ADZUNA_APP_ID=...
   ADZUNA_APP_KEY=...
   ```
   (`.env` is gitignored — keys never get committed.)

## Run it

```
python find_admin_jobs.py --contact "Your Name"   # live search
python find_admin_jobs.py --mock                   # demo, no key needed
python find_admin_jobs.py --min-hourly 20          # raise the wage floor
```

`--contact` puts your name on the page ("if a job looks like a scam, call ___").

## Safety / scam shield (important)

Job boards are full of scams targeting entry-level applicants, so this tool does the
vetting **up front** — the published list is safe by default:

- **Scams are hidden, not just labeled.** Postings with scam tells are removed from the
  page entirely; a warning label still invites a click, removal doesn't.
- **What gets hidden:** advance-fee / check-cashing / gift-card / wire language; off-platform
  "interviews" (Telegram/WhatsApp); the same employer+role spammed across many cities;
  unknown-employer **remote** roles (remote admin/data-entry is the #1 scam category);
  remote pay that's too good to be true for entry admin.
- **Attainable only.** Senior/lead/manager/competitive titles are dropped — only realistic
  entry-level admin/reception/clerical roles are shown.
- **Known employers first.** Recognizable local/government employers are sorted to the top.
- **You get the audit.** The CSV lists *every* posting with a `safety` column and the exact
  `scam_reasons`, so you can spot-check what was hidden.

The HTML page also opens with a plain-language rule sheet: never pay money, buy equipment,
cash a check, or give SSN/bank info to get a job.

It creates two dated files in this folder:
- **`admin-jobs-YYYY-MM-DD.html`** ← open this, then forward it to your friend
- `admin-jobs-YYYY-MM-DD.csv` ← same data as a spreadsheet

## How results are grouped

| Tag | Meaning |
|-----|---------|
| **PAYS $19+/hr** | Employer listed a wage at/above $19/hr |
| **est. $19+/hr** | Adzuna *estimated* the wage — verify in the actual posting |
| **salary not posted** | No wage given; still worth a look (many admin jobs hide pay) |
| **below $19/hr** | Shown for reference only |

## Tuning (optional)

Open `find_admin_jobs.py` and edit the **CONFIG** block near the top:
- `LOCATION` / `DISTANCE_KM` — move the center or widen the radius (16km ≈ 10 mi)
- `TITLES` — add/remove job titles to search
- `EXCLUDE_TITLE_WORDS` — titles to skip (already drops IT "administrator" roles)
- `MIN_HOURLY` — the wage floor

## Development / CI

```bash
pip install -r requirements-dev.txt
ruff check .
pytest --timeout=30 --timeout-method=thread
python find_admin_jobs.py --mock   # end-to-end without a key
```

- **CI** (`.github/workflows/ci.yml`): runs ruff + compile + 13 unit/smoke tests + the
  mock pipeline on every push/PR (Python 3.11 & 3.12), plus a secret-shape scan.
- **CD** (`.github/workflows/scan.yml`): scheduled daily live scan. Add repo secrets
  `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` (Settings → Secrets → Actions) to enable it; results
  are published as downloadable workflow artifacts. Until the secrets exist it skips
  cleanly (green, not a failure).

## Notes / limits

- Adzuna's free tier allows plenty of calls for a daily run.
- Salary data comes from Adzuna; always confirm pay in the real posting before applying.
- Remote results are national (filtered to postings that actually say "remote").
- This finds and *filters* jobs — your friend still reviews and applies (no auto-apply).
