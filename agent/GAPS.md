# GAPS.md — quietly wrong: risks nobody has hit yet

Prioritized. Each gap has a closing action. Remove entries when closed
(note the closing PR).

---

## P0 — could hurt the end user

1. **Recovery/auth flows have no rendered E2E test.** The camera checks 8
   signed-out invariants; nothing drives forgot-password → error-hash →
   fresh-link in a real browser, which is exactly where the live lockout bug
   hid. *Close:* add an authed/recovery camera scenario (`verify/camera.py`
   extra check or a Playwright script in CI where Chromium is available), with
   a stubbed Supabase or a test project.
2. **Published-feed regressions are only caught by a human.** The nursing/
   retail leak sat in production until the owner noticed. *Close:* a weekly CI
   canary (`source-health.yml` is a natural home) that fetches
   `origin/gh-pages:jobs.json` and greps titles/companies for clinical/retail/
   sales/MLM tokens; opens an `auto-fix` issue on hits.

## P1 — operational

3. **Rendered-UI verification is off the PR path.** `camera.yml` runs only on
   `claude/camera-pass`/dispatch; UI PRs merge with no visual gate (the camera
   itself was silently broken for 10 days — #171 — proving the gap). *Close:*
   run the camera (or at least the js-smoke/css-lint tests with a pinned Node,
   which currently run against whatever node the runner ships) on every PR
   that touches `app/` — workflow change, goes through human review by design.
4. **`web/` tracked assets drift from `app/public/`.** `web/sw.js` was 10 days
   stale, missing the push handler (fixed in #172, but nothing prevents
   recurrence). *Close:* CI assertion `diff app/public/sw.js web/sw.js` (and
   manifest/icons), or stop tracking the copies in `web/`.
5. **Nightly direct-apply resolution adds outbound fan-out from CI** (~150
   HEAD/GETs to arbitrary redirect chains). Caps and fail-soft are in, but no
   observability. *Close:* log resolved/failed counts in the scan summary;
   alert via health monitor if resolution rate collapses (sign of blocking).
6. **Supabase MCP diagnostics don't work from subagents** (interactive
   approval unavailable → the password agent couldn't read auth logs).
   *Close:* run Supabase MCP reads from the main loop, or pre-approve
   read-only Supabase tools in `.claude/settings.json`.

## P2 — hygiene / debt

7. **Stale open PRs:** #151 (Cursor env, 6/29), #145 (review-pipeline, 6/27).
   Decide: merge or close. Debris confuses fleet sessions.
8. **Session-only monitoring dies with the session** (cron check-ins, PR
   subscriptions). `health.yml`/`source-health.yml` are the durable layer —
   keep pushing watchdog duties into workflows, not sessions.
9. **`requires_license_or_cert()` failed silently in the field** (the LPN row
   leaked). Title-level excludes now backstop it, but the description-hint
   list should grow from real misses the same way `scam_blocklist_autogen.txt`
   grows. *Close:* when a leak is found, add both the title canary AND the
   description phrase that should have caught it.
