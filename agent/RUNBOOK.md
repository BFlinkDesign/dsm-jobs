# RUNBOOK.md — remaining work, briefed for Sonnet-5 (medium effort)

Each item below is a **self-contained brief**: a Sonnet-medium session (or
subagent) should be able to execute it without re-deriving context. Rules that
apply to every item:

- Read `CLAUDE.md` (invariants), `agent/ERRORS.md` (rules), `docs/DESIGN-BRIEF.md`
  (any UI work) before starting.
- Work on a fresh branch off `main` → PR → the owner squash-merges.
- Gate before any PR: `python -m ruff check find_admin_jobs.py tests` ·
  `python -m pytest -q --timeout=60` · `cd app && npm run build` · and for UI,
  the camera (dispatch `camera.yml` on your branch, shots land on
  `camera-shots`; LOOK at them).
- A test rides in the same commit as the code it guards. Never claim a visual
  result without a screenshot you actually viewed.
- **Escalate to a frontier model instead of grinding** when: the fix spans >3
  subsystems, requires a data-model/auth-flow decision, or two honest attempts
  failed. Say so in the PR instead of shipping a guess.

---

## P0-1 · Recovery-flow E2E camera scenario
**Why:** the live lockout bug lived exactly where no rendered test looks.
**Files:** `verify/camera.py` (+ maybe `app/src/scripts/app.ts` test hooks).
**Do:** add a camera check that loads the app with a synthetic
`#error=access_denied&error_code=otp_expired` hash and asserts the rendered
DOM shows the forgot-password panel + the "link expired" message; and a second
pass with `#access_token=…&type=recovery` (fake token) asserting the
set-new-password form renders. No real Supabase needed — this is render-layer.
**Gate:** camera goes 8→10 checks green; screenshots show both states.

## P0-2 · Published-feed canary (weekly)
**Why:** the nursing/retail leak sat in production until a human noticed.
**Files:** `scripts/` (new `audit_published_feed.py`), `.github/workflows/source-health.yml`.
**Do:** script fetches `origin/gh-pages:jobs.json` via git (github.io is not
always reachable; git is), greps titles/companies for clinical/retail/sales/
MLM tokens (import the same lists from `find_admin_jobs.py` — no duplication),
exits nonzero with the offending rows printed. Wire into `source-health.yml`
weekly; on failure the existing monitor labels an `auto-fix` issue.
**Gate:** run locally against the current feed → must pass post-#172; unit
test with a poisoned fixture row → must fail.

## P1-1 · Pin Node for the JS/CSS guard tests in main CI
**Why:** `test_js_smoke.py` / `test_css_lint.py` silently skip or run against
whatever node the runner ships; pytest runs before `setup-node` in `ci.yml`.
**Files:** `.github/workflows/ci.yml`.
**Do:** move the `setup-node` step above the pytest step (keep 22.12), and
install `verify/css` toolchain there so the css-lint test stops skipping in
the backend job (or accept the dedicated css job as the gate and instead make
the skip loud: emit a workflow notice). Workflow changes go to human review by
design — auto-merge-guard will hold it; that's correct.
**Gate:** CI run shows the docx round-trip and css-lint tests RAN (not skipped).

## P1-2 · `web/` static-asset drift check
**Why:** `web/sw.js` sat 10 days stale, missing the push handler.
**Files:** `tests/` (new tiny test) — NOT a workflow change.
**Do:** pytest asserting `web/sw.js == app/public/sw.js` (and manifest/icons
byte-equality). Fails loudly with "run: cp app/public/sw.js web/sw.js".
**Gate:** test passes on current tree; mutate a byte → fails.

## P1-3 · Direct-apply resolver observability
**Why:** ~150 nightly outbound resolutions with zero visibility; silent decay
(e.g. an ATS starts blocking the UA) would erode the feature invisibly.
**Files:** `find_admin_jobs.py` (scan summary print), `.github/workflows/scan.yml`
(nothing secret — counts only).
**Do:** count attempted/resolved/failed in the resolution pass; print one
summary line ("direct-apply: 42/117 resolved"). Extend the health monitor to
flag when resolved-rate drops below ~10% across 3 consecutive scans.
**Gate:** mock-safe (no counts printed in `--mock`); unit test the counter.

## P2-1 · Recover rejected Workday tenants (MercyOne, Hy-Vee)
**Why:** big local employers currently absent; they were dropped for parse/
noise reasons, not policy (see rejected list in `providers.py`).
**Do:** re-probe each tenant's CxS endpoint live; fix pagination/facet params;
each must survive the metro+admin filters with >0 real rows before wiring in
(CLAUDE.md rule: add a board only after confirming live rows). Fail-soft.
**Gate:** provider unit tests with recorded fixtures; live probe evidence in
the PR description.

## P2-2 · License-hint growth loop
**Why:** `requires_license_or_cert()` missed the LPN row; title excludes are
the backstop, but the description layer should learn from every miss.
**Do:** when any leak is found, add BOTH the title canary and the description
phrase that should have caught it (`LICENSE_CERT_HINTS`). Backfill from the
LPN leak: add "current lpn", "lpn in good standing", "iowa lpn" style phrasings
with softener-window tests (don't drop "CNA preferred but not required").
**Gate:** existing softener tests stay green.

## P0-3 · Job import (paste a job from anywhere → real card)
**Status:** briefed, build was killed by the account spend limit mid-run.
**Spec:** new edge function `supabase/functions/job-import` cloned from the
resume-tailor pattern (verify_jwt=true + getUser(), origin-locked CORS,
spend-cap, json_schema LLM output). Input `{url?|text?}`; URL mode fetches
server-side behind a strict SSRF guard (https only, no IP-literal hosts,
1.5MB/10s caps); extraction schema forbids invented pay (`pay_stated` bool —
invariant #1 extends to imports); the function ports SCAM_HARD_FLAGS +
scam_blocklist names and returns `{rejected:true}` with NO job data on a hit
(invariant #3: removed, not labeled). Client: "Add a job you found ✦" on the
Jobs tab → modal with paste button (no auto clipboard read) → preview card →
saves to `state.importedJobs` synced via the profile blob; renders through
the normal card path with a "You found this one" marker; esc()/safeUrl()
everywhere; undo/delete. Deno tests (blocklist reject, gift-card reject,
clean pass, stated/unstated pay) + static guards.

## P0-4 · Alignment engine (Rudy interview + ignore-that-learns)
**Status:** briefed, build was killed by the account spend limit mid-run.
**Spec:** deterministic, transparent, NO ML. (a) Chips-first skippable
resumable interview (~6 steps: commute ceiling, categories, schedule
dealbreakers, stated-pay floor, never-want free text) hosted in Rudy's voice
→ `preferences` in AppState + profile blob. (b) "Not for me" on cards → one
why-chip (Too far / Wrong kind / Pay / Schedule / Just no); 1 = hint, 3 same
reason+dimension = auto-rule with visible dismissible notice + per-rule undo
and a "What Rudy's learned" list (Corner tab) with reset-all. Rules must
never bury "Pay not listed" rows (invariant #2) and never touch the scam
shield. Extract pure logic to `app/src/scripts/alignment.ts` for testability.

## P1-4 · Design overhaul — "professional spooky, components SCREAM real"
**Status:** direction set; execution needs a fresh session (spend limit).
**Direction (from the design lead pass):** the app's gravity well is
"near-black + one neon accent" — escape it with *materials*, not more color:
candlelit warmth layered over deep blacks (2-3 stacked translucent glows, not
flat #hex), engraved-line ornament on cards (0.5px double-rule borders like
mourning jewelry), moth-wing iridescence ONLY on the signature element (the
bat swarm + one accent per view), real texture (subtle paper/vellum grain on
panels), type with actual display presence (a serif display for headings —
self-hosted WOFF2, LCP ≤2.5s budget). Motion: one easing family, 150-250ms,
reduced-motion clean. Every DESIGN-BRIEF acceptance criterion is a gate;
camera + screenshots at 390px, WCAG AA proven by numbers.
**WebGL/Three.js verdict (owner asked):** NO for this app — R3F/three.js is
~150KB+ gzipped before a single scene, iPad Safari WebGL contexts die under
memory pressure in PWAs, and the audience is a stressed user on battery.
Frontier-feel here = materials, texture, and motion discipline (above), which
cost ~0KB. Reconsider only for an optional easter-egg scene, lazy-loaded.

## Operator-only (no code — the owner does these)
1. **Email templates (do first — powers the 6-digit codes):** Dashboard →
   Authentication → Email Templates → paste `docs/email-templates/magic-link.html`
   and `recovery.html` (subjects in each file's first comment). Then send
   yourself a code from the app and confirm the digits render and the iPad
   autofills them from Mail.
2. **Both "incorrect password" cases predate the fixes** — the old password
   is still the active one (the change never completed). Easiest path now:
   sign in with a 6-digit code, then set a fresh password in Corner, or just
   live on codes/passkeys.
3. **Passkeys:** enable in Supabase dashboard → sign in once on her iPad →
   accept Face ID enrollment. (Passkeys sync via iCloud Keychain — that's the
   cross-device story.)
4. **Custom sender ("from Rudy, not supabase"):** needs a domain you own
   (~$10/yr; free TLDs are spam-scored — worse than the default, don't).
   Then: Resend account (one likely exists — `RESEND_API_KEY` already powers
   spend-cap mail) → verify domain (3 DNS records) → Dashboard → Auth → SMTP:
   host `smtp.resend.com`, sender "Rudy at DSM Jobs <rudy@yourdomain>". Until
   then the branded templates already fix subject/body.
5. **Check Auth logs** (Dashboard → Logs → Auth) for the two accounts'
   failed-login history if you want the paper trail.
6. **Google OAuth** client (optional; button auto-appears once enabled).
