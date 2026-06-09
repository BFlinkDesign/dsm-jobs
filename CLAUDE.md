# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A job finder built **for one specific end user** — a friend who is **phone/tablet only (no computer), can't reliably spot a scam, and won't land competitive jobs.** Those three facts are the real spec; design every change around them. It finds no-degree, entry-level **admin / office-adjacent / general** jobs in the **Des Moines metro + remote** via job-aggregator APIs, and publishes a mobile PWA.

- Local dev dir: `C:\Users\Brady.EAGLE\Desktop\admin-job-finder` (folder name is historical; the repo/product is `dsm-job-finder`).
- Code repo: **`BFlinkDesign/dsm-job-finder`** (private). The account was renamed from `EAGLE605` → `BFlinkDesign`; git remotes pointing at `EAGLE605/...` still work via redirect, but new URLs use `BFlinkDesign`.
- Live app (public, auto/manually deployed): **https://bflinkdesign.github.io/dsm-jobs/** (served from the separate public repo `BFlinkDesign/dsm-jobs`, `gh-pages` branch).

## Commands

```bash
python find_admin_jobs.py --contact "Brady"     # live scan -> web/index.html + audit CSV (needs .env keys)
python find_admin_jobs.py --mock                 # full pipeline on canned data, no API key
python find_admin_jobs.py --min-hourly 20        # raise the wage floor
python find_admin_jobs.py --contact-phone "+1515..."  # adds a tel: "call" button to the page

python -m pytest -q --timeout=60                 # full suite (timeout is mandatory)
python -m pytest tests/test_find_admin_jobs.py::test_blocklist_hides_even_trusted -q   # single test
python -m ruff check find_admin_jobs.py tests    # lint (CI runs this)

python -m http.server 8137 --directory web --bind 127.0.0.1   # serve the PWA locally to test (file:// won't run the SW)
```

Runtime is **stdlib-only** (no pip install to run). Dev/CI tooling: `pip install -r requirements-dev.txt` (ruff, pytest, pytest-timeout).

## Architecture (the big picture)

**One scanner script + one generated PWA + CI/CD that deploys to a separate public repo.**

- **`find_admin_jobs.py`** — the whole backend, in order: query Adzuna per title (local pass + a remote-titles pass) → `normalize()` each posting → filter (`is_admin_title` allowlist, `is_attainable` seniority drop, `requires_degree`) → `scam_assessment()` per row → partition **safe vs hidden** → `write_html()` renders the PWA into `web/index.html`, `write_csv()` writes the full audit (safe + hidden, with `scam_reasons`).
- **`web/`** — the PWA. `index.html` is **generated** (gitignored); `manifest.webmanifest`, `sw.js`, and icons are **committed** static shell. The app embeds jobs as inline JSON and does search / filter chips / sort / "Applied·Saved·Hide" via `localStorage`. The front end is rendered from `APP_TEMPLATE` (a non-f-string raw template filled with `##JOBS##` / `##META##`).
- **CI** (`.github/workflows/ci.yml`): ruff + compile + tests + mock pipeline (3.11/3.12) + a self-contained secret-shape scan.
- **CD** (`.github/workflows/scan.yml`): daily + manual; builds `web/`, uploads it as an artifact, and (when `PAGES_PUSH_TOKEN` is set) force-pushes `web/` to `BFlinkDesign/dsm-jobs` `gh-pages` → the live link. Skips cleanly without the token.

## Load-bearing invariants (do not regress)

1. **Never present a guessed wage as a number.** Adzuna *predicts* ~74% of salaries (`salary_is_predicted=1`). Predicted → verdict `unlisted` → the card shows **"Pay not listed — ask when you apply"**, no number, no $19+ badge. Only employer-**stated** pay gets a figure or the `meets` ($19+) verdict. The `$19+` test uses the **low end** of a stated range.
2. **"Pay not listed" is normal, not a downgrade.** A verified-employer "Pay not listed" job is an excellent lead — rank by trust + freshness, never bury it. Confirmed-pay-only is NOT the goal.
3. **Scams are HIDDEN, not labeled** (the user would click a warning anyway). Layers: `scam_blocklist.txt` (hard, overrides trusted), heuristic `scam_assessment` (advance-fee/check/gift-card phrases, off-platform interviews, same-employer-across-cities spam, unknown-employer remote, too-good remote pay), and `TRUSTED_EMPLOYER_HINTS` to avoid false hides. The friend's HTML shows safe-only + a count of how many were hidden; the CSV is the operator's audit.
4. **Attainability filter** drops senior/lead/manager/competitive titles — only realistic entry-level roles.
5. **XSS-safe rendering** in the embedded JS: all fields go through `esc()` (encodes `& < > " ' \``) and apply links through `safeUrl()` (http/https only). Embedded JSON has `</` escaped. Keep both if you touch `APP_TEMPLATE`.
6. **Secrets**: `ADZUNA_APP_ID/KEY` (and future provider keys) live in local `.env` (gitignored) and GitHub Actions secrets. Push a value to a GH secret via stdin (`gh secret set NAME` with piped input), never `--body`/argv. Collect into `.env` via the `/add-secret` masked dialog — never paste keys into chat.

## Deploy / publish

`main` is push-protected — land changes via branch → PR → squash-merge. The site only updates when `web/` is pushed to `BFlinkDesign/dsm-jobs` `gh-pages`: automatically by CD once `PAGES_PUSH_TOKEN` (fine-grained PAT, Contents:write on `dsm-jobs`) is set, or manually by copying `web/*` to a temp repo and force-pushing `gh-pages`.

## Planned / in flight

- More sources for breadth + **real apply URLs**: USAJobs (always employer-stated pay), JSearch (Google-for-Jobs), Jooble — each behind a key, wired as providers. These also **unlock WHOIS domain-age scam-checking** (Adzuna only exposes a JS redirect, so the employer domain isn't reachable from it).
- Malwarebytes reputation was tested and returns "unknown" for job/ATS domains (sparse coverage) — not used.
- Figma design file: https://www.figma.com/design/HxvPka9GtLJYBJpHQwY0M7 (the live app captured for iteration).
