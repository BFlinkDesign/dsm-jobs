# ERRORS.md — what broke, why, and the rule adopted

Append-only. Format: **symptom → root cause → rule**. Every agent session that
hits a failure adds an entry; every agent session reads this before starting.

---

## 2026-07-02 (repo-review-audit session, PR #172)

### 1. PR CI went red on a commit that was "verified green" locally
- **Symptom:** `backend-checks (3.11/3.12)` failed on `cddac5f` — the new
  `test_password_recovery_handles_expired_link…` static guard failed in CI.
- **Root cause:** the fix and its guard test were split across commits. The
  test (complete) was committed while its subject, `app.ts`, was still being
  edited by concurrent agents and stayed uncommitted. Static guards read the
  *checkout*, not the working tree that was verified.
- **Rule:** **a test rides in the same commit as the code it guards** — never
  split them, even to satisfy a "commit now" nudge. If the code can't be
  committed yet, the test waits with it.

### 2. A user interrupt silently cancelled an entire just-spawned agent fleet
- **Symptom:** four subagents spawned in one message; the user sent an FYI
  moments later; all four were stopped, and `SendMessage` to them returned
  "stopped by the user".
- **Root cause:** interrupting the main turn cancels background subagents that
  haven't been resumed. Spawning a fleet during an active back-and-forth with
  the user makes the fleet fragile.
- **Rule:** relaunch is the recovery (fold the new context into the fresh
  prompts — it made the prompts better), and every respawned agent's prompt
  must open with "run `git status`; a prior cancelled agent may have left
  partial edits — review and complete or redo coherently." That instruction
  worked verbatim this session.

### 3. Local camera/Playwright verification unavailable in the cloud sandbox
- **Symptom:** the docx agent's `camera.py` attempt died — `cdn.playwright.dev`
  403 through the proxy — despite Chromium being preinstalled.
- **Root cause:** `verify/requirements.txt` pins its own Playwright revision,
  which tries to download *its* bundled Chromium instead of using the
  environment's `/opt/pw-browsers/chromium` (`PLAYWRIGHT_BROWSERS_PATH` points
  there, but a pinned-revision mismatch triggers a re-download → blocked).
- **Rule:** in cloud sandboxes, drive the browser with
  `executablePath=/opt/pw-browsers/chromium` (or run the camera via CI:
  `camera.yml` → PNGs on the `camera-shots` branch, fetchable with git).
  **Never report visual verification that didn't produce a screenshot that was
  actually looked at** — say plainly it couldn't run, as the docx agent did.

### 4. A silent failure branch in auth cost a real user her account access
- **Symptom (field report):** "she changed her password and it says wrong."
- **Root cause:** expired/consumed recovery links put `#error=…&error_code=otp_expired`
  in the URL; supabase-js fires no distinguishable event; the app only handled
  the success shape (`type=recovery`) and rendered *nothing* — she never
  actually changed the password, and nothing told her so.
- **Rule:** every external redirect/callback surface must render explicit
  feedback for the **error** branch, and its guard test must assert the
  failure path, not just the happy path. One-time email links must be assumed
  prefetch-consumable by mail scanners.

### 5. Filter leaks reached the live feed (nursing, retail sales, MLM funnels)
- **Symptom:** "Customer Service/Sales @ Home Depot", "…Admissions Nurse",
  "LPN - Nursing Home Caregiver", Vector Marketing / Zuzick rows visible to
  the end user.
- **Root cause (two layers):** broad allowlist substrings ("intake",
  "customer service", "caregiver") admit clinical/retail titles; and the
  deliberate no-bare-acronyms rule (RN/LPN) relied on a compensating control —
  description license-hints — that demonstrably failed, with no canary to
  notice.
- **Rule:** when a filter is deliberately weakened, pair it with a **canary
  regression test using real leaked titles from the live feed**, and audit the
  published `jobs.json` (fetch via `git show origin/gh-pages:jobs.json` — the
  proxy blocks github.io but git reaches the branch) whenever filters change.

### 6. Phantom "missing CSS link" build bug that wasn't
- **Symptom:** built `web/index.html` had an inline `<style>` and an orphaned
  hashed CSS file with no `<link>` — looked like a real regression, was blamed
  on an unrelated code change.
- **Root cause:** stale outDir debris. `outDir: ../web` is outside the Vite
  project root, so builds don't empty `web/_astro`; a shared sandbox running
  many incremental builds from intermediate states accumulates artifacts that
  make the *latest* HTML look inconsistent with what's on disk.
- **Rule:** before diagnosing any build-output anomaly, `rm -rf web/_astro`
  and rebuild clean. CI is immune (fresh checkout); only long-lived sandboxes
  hit this.
