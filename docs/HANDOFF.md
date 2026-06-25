# Session handoff — 2026-06-22 (Goth Astro + audit fixes)

**Branch:** `cursor/goth-redesign-execute-9fce` (or current working branch)  
**Live URL:** https://bflinkdesign.github.io/dsm-jobs/  
**End user:** Lilly (phone/tablet only)  
**Product goal:** Unfair advantage in job search — not minimal MVP. Scam safety + leverage UX.

---

## Executive summary

This session moved the PWA from “strong beta” toward **production-complete sync and leverage UX**. Three read-only audits mapped gaps (frontier features, subconscious polish, operational sync). A full “build all gaps” subagent hit API limits twice; fixes were completed in the parent session.

**Honest status after this session:** Core loop is shippable (feed, auth, apply/save/hide, Rudy, tailor, follow-ups with **done** state). External config still needed for reliable email (Resend), Google OAuth, Web Push on iOS, and passkeys enabled in Supabase dashboard. **Collapsible filters** have since been ported to Astro (`#filter-toggle` / `.filter-panel.is-collapsed`, collapsed by default) — the "always expanded" note below is superseded.

---

## What we answered (no code)

| Question | Answer |
|---|---|
| Face ID / fingerprint ready? | **Wired in code** (`auth.ts` passkey experimental). Needs Supabase passkeys ON + one email sign-in to enroll. Not E2E-tested on her iPhone this session. |
| PWA auto-update? | **No reinstall.** Network-first `jobs.json` / `index.html`; hashed `_astro/*` bundles. SW uses `skipWaiting` + `clients.claim`. Toast prompts reload when new SW installs. Tab left open hours may need close/reopen. |
| Everything synced, no stubs? | **Was NO** before fixes. **Closer now** — see “Shipped this session”. Still no Web Push; `user_job_status` is migrate-only. |
| Collapsing filters? | **Ported (done).** Astro has `#filter-toggle` / `.filter-panel.is-collapsed` with `filtersExpanded` persisted to localStorage, collapsed by default. |

---

## Shipped this session (code)

### Sync & correctness
- **Follow-up `done`** — “I followed up ✓” clears due badges/notifications; undo “Mark not done”
- **Editable follow-up contacts** (name/phone/email) + call script on 5+ day nudge
- **Filters in `AppState`** — search, pay, train, verified, saved, applied, hidden, category, commute — localStorage + Supabase `user_profile.profile` blob
- **`followAlertDay` + `seen`** synced to cloud; **“New”** badges on jobs not seen last visit
- Legacy-style filter chips ported (Will train, Verified, Saved, Applied, Hidden, category dropdown)

### Auth & ops
- **Magic link** — “Email me a sign-in link” (`signInWithOtp`)
- **`health.yml`** — checks `"Jobs for you"` not `"Job Board"`
- **`portal/README.md`** — Astro is canonical shipped UI; profile blob sync documented
- **Companion** — system prompt **Ruby → Rudy** (`supabase/functions/companion/index.ts`)

### Leverage UX
- Undo toasts (apply, hide)
- Per-tab scroll restoration
- SW “New version — tap to refresh” toast
- Offline banner, feed retry, pull-to-refresh on Jobs
- Auth modal: Escape, focus email, scroll lock
- Haptic on apply; tailor **download**; iOS **Add to Home Screen** coach
- Stale feed banner (3+ days); résumé upload copy fix

### Key files
- `app/src/scripts/app.ts`, `types.ts`, `store.ts`, `autosave.ts`, `auth.ts`, `util.ts`
- `app/src/pages/index.astro`, `app/src/styles/app.css`
- `.github/workflows/health.yml`

---

## Not done / external only

| Item | Owner |
|---|---|
| Resend SMTP on Supabase | Operator (dashboard) |
| Google OAuth client + Supabase provider | Operator |
| Passkeys enabled in Supabase Auth | Operator |
| Web Push + VAPID + `notificationclick` | Code + secrets |
| ~~Collapsible filter panel~~ | **Done** — ported to Astro (`#filter-toggle`). |
| `user_job_status` ongoing writes | Deferred — profile blob is source of truth |
| Astro CSS in `verify/css/lint_css.py` CI gate | CI |
| Camera gates for authed flows / forgot-password | verify/ |

---

## Architecture reminder

```
find_admin_jobs.py --mock|--contact
  → app/public/{jobs,meta,portal}.json
  → cd app && npm run build  → web/
  → gh-pages deploy (scan.yml)
```

- **Canonical UI:** `app/src/scripts/*.ts` + `index.astro` — **not** `APP_TEMPLATE` JS (still generated during scan then overwritten).
- **Local serve:** junction `local-serve/dsm-jobs/` → `web/`; open `http://127.0.0.1:8137/dsm-jobs/` (base path required).

---

## Verify before merge/deploy

```bash
python find_admin_jobs.py --mock
cd app && npm run build
python -m pytest -q --timeout=60
bash verify/setup-web.sh && python verify/camera.py   # 8/8; do NOT deploy mock data to gh-pages
```

---

## Lilly onboarding (operator)

1. Add to Home Screen (iOS: Share → Add; coach modal shows once).
2. Create account (email/password) or magic link when mail works.
3. After first sign-in, accept Face ID enrollment if prompted.
4. Mark applied → set follow-up → **tap “I followed up ✓”** when done (stops nagging).
5. Filters: all visible on Jobs tab today — collapse coming.

---

## Recommended PR

**Title:** `feat(app): sync fixes, leverage UX, and audit gap closure`

**Summary bullets:**
- Follow-up done + contact edit; filters/search in AppState + Supabase sync
- Magic link, health monitor fix, Rudy naming, PWA update/offline polish
- Document handoff; CLAUDE.md Astro architecture update

**Test plan:**
- [ ] `npm run build` + pytest + camera 8/8
- [ ] Sign in → apply → follow up done → badge clears
- [ ] Filter chips persist after refresh
- [ ] Magic link sends (if SMTP configured)

---

## Audit agents (reference)

Session used background audits; parent implemented code after subagent API limits:
- Frontier gap analysis
- Subconscious UX audit  
- Operational sync audit
- Build-all (failed → resumed in parent)
