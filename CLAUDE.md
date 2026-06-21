# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A job finder built **for one specific end user** on three hard design constraints: **phone/tablet only (no computer), zero tolerance for scam exposure (the app does all vetting up front), and realistic entry-level targeting (no senior/competitive roles).** Those three constraints are the real spec; design every change around them. It finds no-degree, entry-level **admin / office-adjacent / general** jobs in the **Des Moines metro + remote** via job-aggregator APIs, and publishes a mobile PWA.

- Local dev dir: `C:\Users\Brady.EAGLE\Desktop\admin-job-finder` (folder name is historical).
- Repo: **`BFlinkDesign/dsm-jobs`** (PUBLIC — single repo for code + site since the 2026-06-10 merge; formerly the private `dsm-job-finder` plus a separate public `dsm-jobs` Pages repo). The account was renamed from `EAGLE605` → `BFlinkDesign`. Because the repo is public: no secrets/PII in commits, comments, or workflow logs — Actions logs and artifacts are world-readable.
- Live app: **https://bflinkdesign.github.io/dsm-jobs/** — GitHub Pages, served from THIS repo's `gh-pages` branch (the repo name IS the URL path; renaming the repo breaks the end user's installed PWA).

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

bash verify/setup-web.sh && python verify/camera.py   # the CAMERA: render the PWA in pinned Chromium + inspect (8 checks)
```

**The camera is the deterministic visual-verification mechanism.** It renders the
`--mock` build (canned data) in Playwright's **bundled Chromium, pinned** by
`verify/requirements.txt` (== one fixed revision) — never system Chrome — with a
fixed viewport/scale, reduced-motion, and frozen animations, so the pixels are
reproducible across runs and machines. Three ways to drive that ONE mechanism:
(a) **local CLI** — `bash verify/setup-web.sh && python verify/camera.py`;
(b) **CI** — `.github/workflows/camera.yml` runs it on a pinned `ubuntu-24.04`
runner on push to `claude/camera-pass` (or manual dispatch) and pushes the PNGs
to the `camera-shots` branch, so a browserless sandbox can `git fetch` and view
them; (c) **web env** — the same `setup-web.sh` as the environment setup script,
which works once the environment's network policy permits the Chromium download.

Runtime is **stdlib-only** (no pip install to run). Dev/CI tooling: `pip install -r requirements-dev.txt` (ruff, pytest, pytest-timeout, mypy). The **camera** self-verifier (`verify/`) needs `playwright` + system Chrome — verify-only, not a runtime dep.

## Architecture (the big picture)

**One scanner script + one generated PWA + CI/CD that publishes to this repo's `gh-pages` branch.**

- **`find_admin_jobs.py`** — the backend, in order: query Adzuna per title (local pass + a remote-titles pass) → `normalize()` each posting → merge `providers.collect_extra()` rows → filter (`is_admin_title` allowlist, `is_attainable` seniority drop, `requires_degree` with softener window, and a **commute-range gate** — a local job is kept iff `commute_minutes()` resolves it to a known drive time; that map is now the single source of truth covering Polk/Dallas **+ nearby Warren/Story/Jasper**, replacing the old Polk/Dallas-only `in_polk_or_dallas` gate) → `scam_assessment()` per row → partition **safe vs hidden** → `write_html()` renders the PWA into `web/index.html`, `write_csv()` writes the full audit (safe + hidden, with `scam_reasons`). `salary_verdict()` is the ONE place a wage becomes a verdict.
- **`providers.py`** — extra sources, each fail-soft (one bad provider never kills the scan), routed through the single `salary_verdict()`. Key-gated: **USAJobs** (`USAJOBS_API_KEY`+`USAJOBS_EMAIL`; always employer-stated; PH/PA codes), **Jooble** (`JOOBLE_API_KEY`; free-text salary → always "Pay not listed"), **JSearch** (`JSEARCH_API_KEY`; 200 req/MONTH cap → fixed 5-query budget; prefers `apply_options[].is_direct` links), **Careerjet** (`CAREERJET_AFFID`; salary_type H/Y mapped). Always-on (no key): **ATS** — Greenhouse + Lever boards in `ATS_BOARDS` (real employer apply URLs; highest trust; Greenhouse pay→unlisted as its public API has no period field; Lever per-hour/per-year mapped). Add a board token only after confirming it returns 200+jobs live. **CareerOneStop** is an honest stub (envelope unverified). Remote-only/EU APIs (RemoteOK/Himalayas/Remotive/Arbeitnow) deliberately NOT wired — empty/EUR salary fields are dangerous under invariant #1. All shapes verified live/against official docs 2026-06-10.
  - **Update 2026-06-16:** **Jooble** + **JSearch** are key-provisioned and **live** (verified end-to-end against real DSM results). **Careerjet** is intentionally **inert/dropped** — its API requires a fixed server-IP allowlist + real-time per-user params (`user_ip`/`user_agent`) that don't fit a nightly CI scan from rotating GitHub-runner IPs. Additional **always-on keyless** sources are wired beyond Greenhouse/Lever: **NEOGOV / GovernmentJobs.com** gov feeds (`NEOGOV_AGENCIES` — State of Iowa + Polk/Dallas metro: Des Moines, Urbandale, Waukee, **Dallas County, Bondurant, City of Johnston** [the IA city — the bare `johnston` slug was Johnston County **NC** and was fixed]), **Workday** CxS (`WORKDAY_BOARDS` — Athene/Corteva/Nationwide/Voya), and **SmartRecruiters** (`SMARTRECRUITERS_COMPANIES` — Wellmark). Probed + **rejected** after live end-to-end testing (0 rows survived the metro+admin filters / national-noise flooding): The Muse, Hy-Vee/EMC/MercyOne Workday, dmww, Warren/Story-only NEOGOV feeds.
- **`portal/`** — Supabase portal (schema + RLS + setup runbook). **LIVE as of 2026-06-16** — project `tcclohxvhmwgjrtdkkuw` (`https://tcclohxvhmwgjrtdkkuw.supabase.co`), all 5 tables + RLS applied, **email signup/login working end-to-end** (verified: signup → instant session → RLS-isolated saved work). Config that makes it work: **Site URL** = `https://bflinkdesign.github.io/dsm-jobs/` + that redirect allow-listed, and **"Confirm email" OFF** (so a phone-only user gets in instantly — the default Supabase mailer is too spam-prone to depend on). Only the **`email`** provider is enabled; Google needs a Google Cloud OAuth client (front-end auto-shows the button once enabled), Apple is skipped ($99/yr dev program). The static PWA still works fully without sign-in (localStorage fallback). **Network note:** the Supabase *dashboard* (`auth.supabase.io`) is firewall-blocked from CNC-1 (Eagle network) — but `api.supabase.com` + `<ref>.supabase.co` are reachable, so config is done via the Management API with a personal access token generated off-network, or the dashboard from another device.
- **`web/`** — the PWA. `index.html` is **generated** (gitignored); `manifest.webmanifest`, `sw.js`, and icons are **committed** static shell. The app embeds jobs as inline JSON and does search / filter chips / sort / "Applied·Saved·Hide" via `localStorage`. The front end is rendered from `APP_TEMPLATE` (a non-f-string raw template filled with `##JOBS##` / `##META##` / `##SENTRY##` / `##PORTAL##` / `##PORTAL_SCRIPT##`).
  - **Update 2026-06-16:** the three filter rows are now **labeled** (`Filter` / `Job type` / `How far you'll drive from Grimes`) so they don't read as one ambiguous wall of pills. The **commute-radius chooser** (Any / 20 / 30 / 45 min) lets the end user pick how far she'll drive; it persists in localStorage and remote jobs always show. Social sign-in buttons (Google) render **only if the project actually enables that provider** (a `/auth/v1/settings` fetch) — so a not-yet-configured Google button is never a dead end.
- **CI** (`.github/workflows/ci.yml`): ruff + compile + tests + mock pipeline (3.11/3.12) + a self-contained secret-shape scan.
- **CD** (`.github/workflows/scan.yml`): daily + manual; builds `web/`, uploads the site bundle as an artifact (the audit CSV is deliberately NOT uploaded — artifacts are public), and force-pushes `web/` to this repo's `gh-pages` branch via the built-in `GITHUB_TOKEN` (`permissions: contents: write`; no PAT).
- **`verify/camera.py`** — the **camera** self-verifier: builds `--mock`, renders the PWA in real Chrome (Playwright via `channel="chrome"`), photographs each view, and inspects the live DOM against the invariants (8 checks incl. **invariant #1 at the render layer**, labeled filter rows, provider-aware auth, no render-garbage). Exit 0 iff all pass. Complements the unit tests: pytest proves the *logic*, the camera proves the *rendered reality*. Re-run before any deploy. Generated `verify/shots/` + `report.json` are gitignored; see `verify/README.md`.

## Load-bearing invariants (do not regress)

1. **Never present a guessed wage as a number.** Adzuna *predicts* ~74% of salaries (`salary_is_predicted=1`). Predicted → verdict `unlisted` → the card shows **"Pay not listed — ask when you apply"**, no number, no $19+ badge. Only employer-**stated** pay gets a figure or the `meets` ($19+) verdict. The `$19+` test uses the **low end** of a stated range.
2. **"Pay not listed" is normal, not a downgrade.** A verified-employer "Pay not listed" job is an excellent lead — rank by trust + freshness, never bury it. Confirmed-pay-only is NOT the goal.
3. **Scams are HIDDEN, not labeled** (warning labels don't reliably prevent clicks; removal does). Layers: `scam_blocklist.txt` (hard, overrides trusted), heuristic `scam_assessment` (advance-fee/check/gift-card phrases, off-platform interviews, same-employer-across-cities spam, unknown-employer remote, too-good remote pay), and `TRUSTED_EMPLOYER_HINTS` to avoid false hides. The friend's HTML shows safe-only + a count of how many were hidden; the CSV is the operator's audit.
4. **Attainability filter** drops senior/lead/manager/competitive titles — only realistic entry-level roles.
5. **XSS-safe rendering** in the embedded JS: all fields go through `esc()` (encodes `& < > " ' \``) and apply links through `safeUrl()` (http/https only). Embedded JSON has `</` escaped. Keep both if you touch `APP_TEMPLATE`.
6. **Secrets**: `ADZUNA_APP_ID/KEY` (and future provider keys) live in local `.env` (gitignored) and GitHub Actions secrets. Push a value to a GH secret via stdin (`gh secret set NAME` with piped input), never `--body`/argv. Collect into `.env` via the `/add-secret` masked dialog — never paste keys into chat.
7. **Verify rendered reality with vision, not text-scraping.** Grepping the generated HTML/source silently misses structure, layout, and visual regressions — a string can be present and still render broken, overlapped, or invisible. Screenshots/vision are the catch. Before claiming a UI change works (and before any deploy), confirm it by rendering and *looking* (the `verify/camera.py` camera, or a screenshot), not by string-matching alone. Text checks supplement vision; they never replace it.

## Deploy / publish

`main` is push-protected — land changes via branch → PR → squash-merge (re-verify the ruleset survived the 2026-06-10 public flip; visibility changes can disable rulesets). The site updates when `web/` lands on this repo's `gh-pages` branch: automatically by the daily CD, or manually (`git push origin gh-pages` from a worktree containing the built `web/`). NEVER deploy after a `--mock` run — it overwrites `web/index.html` with canned data.

## Shipped 2026-06-16

- **Jooble + JSearch live** (keys provisioned, proven against real DSM results). **Careerjet dropped** (IP-allowlist incompatibility). New keyless **NEOGOV gov feeds** (Dallas County, Bondurant, City of Johnston) + fixed the `johnston`=NC mislabel bug.
- **Commute-radius chooser** + commute-based metro gate (Polk/Dallas/Warren/Story).
- **Filter-row labels** + provider-aware social buttons.
- **Auth working end-to-end** (Supabase email signup/login + RLS; Site URL + confirm-email configured).
- **Scam-shield hardening** — gig/"paid panel" bait titles flagged (distinctive phrases only, so legit Market Research Coordinator roles aren't false-hidden).
- **The camera self-verifier** (`verify/camera.py`) — renders the PWA in real Chrome + inspects 8 invariant checks; ran 8/8 green + a visual pass on all 4 views.

## Planned / next

- **USAJobs** key (still pending — always employer-stated pay; free at developer.usajobs.gov). Remaining providers also **unlock WHOIS domain-age scam-checking** (Adzuna only exposes a JS redirect, so the employer domain isn't reachable from it).
- **Resend SMTP** for reliable password-reset + magic-link email (today's default Supabase mailer is spam-prone). Then "Confirm email" could go back ON.
- **Google OAuth** (optional — needs a Google Cloud client; the button auto-appears once enabled). Apple intentionally skipped ($99/yr).
- **Recover MercyOne/Hy-Vee/national-tenant sources via better parsing** (they have real metro jobs; the parser couldn't isolate them from national noise — see the rejected list in `providers.py`).
- Malwarebytes reputation was tested and returns "unknown" for job/ATS domains (sparse coverage) — not used.
- Figma design file: https://www.figma.com/design/HxvPka9GtLJYBJpHQwY0M7 (the live app captured for iteration).
