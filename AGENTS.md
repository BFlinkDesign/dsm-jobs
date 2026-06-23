# AGENTS.md — agent orientation (read this first)

This repo is a **phone/tablet PWA** that finds vetted, entry-level admin/office-adjacent jobs in the Des Moines metro (+ remote), hides scams before the user sees them, and adds leverage UX (apply tracking, follow-ups, Rudy, résumé tailor). One Python scanner publishes JSON feeds; the **Astro app** is the canonical UI. Built for one end user (Lilly): no desktop assumption, zero scam tolerance, realistic entry-level roles only.

## Canonical paths

| Path | Role |
|------|------|
| `app/` | **UI source** — Astro pages, TypeScript (`app/src/scripts/`), styles, public assets |
| `find_admin_jobs.py` | **Scanner** — APIs, filters, scam shield, mock/live pipeline |
| `web/` | **Build output** — `cd app && npm run build` (overwrites scan HTML; do not hand-edit for features) |
| `portal/` | Supabase schema, RLS, operator runbook |
| `verify/camera.py` | Deterministic visual verifier (Playwright Chromium) |
| `docs/HANDOFF.md` | **Full session handoff** — gaps, verify steps, Lilly onboarding |

## Read first

1. **`docs/HANDOFF.md`** — honest status, shipped vs not done, verify commands.
2. **`CLAUDE.md`** — architecture, invariants, **Shipped** / **Planned** sections (source of truth for dates and roadmap).

Do not duplicate HANDOFF here; use it for detail.

## This session shipped (2026-06-22)

See **`docs/HANDOFF.md`** for the full list. Headline items:

- **Follow-up done** — mark followed up, undo, editable contacts, call script on 5+ day nudge
- **Filters in `AppState`** — search, pay, train, verified, saved, applied, hidden, category, commute → localStorage + Supabase `user_profile` blob
- **Magic link** sign-in; passkey/Face ID wired in code (dashboard enable still operator)
- **`health.yml`** — live check expects "Jobs for you" (not "Job Board")
- **Companion → Rudy** in edge function prompt (`supabase/functions/companion/index.ts`)
- **Leverage UX** — undo apply/hide, scroll restore, SW update toast, offline/retry, pull-to-refresh, iOS install coach, stale feed banner, tailor download, haptics
- **Goth Astro shell** — tokens, Rudy sayings, five-tab PWA (earlier commits on branch)

Key touchpoints: `app/src/scripts/app.ts`, `store.ts`, `autosave.ts`, `auth.ts`, `types.ts`, `index.astro`.

## Known limitations — do not claim done

| Gap | Notes |
|-----|--------|
| **Collapsible filters** | Legacy `#filtertoggle` **not** ported; Jobs tab shows filters expanded |
| **Web Push** | Not built; follow-ups use in-app `Notification` (iOS PWA weak when app closed) |
| **Resend / Google OAuth / passkeys** | External Supabase/dashboard + secrets; code may be ready but not E2E on her device |
| **`user_job_status` table** | Migrate-only; ongoing state lives in **profile blob** |
| **Email reliability** | Default Supabase mailer; magic link/reset may hit spam without Resend |

Product invariants still apply: **never show guessed wages as numbers**; **scams hidden, not labeled**; XSS-safe rendering (`esc`, `safeUrl`).

## Product direction

Optimize for **unfair advantage for Lilly** — leverage UX (sync, reminders, Rudy, tailor, polish), not a minimal MVP. Every change should respect the three hard constraints in `CLAUDE.md`: mobile-only, scam-safe, attainable entry-level.

## Verify before ship

Never deploy a **`--mock`** build to `gh-pages`.

```bash
python find_admin_jobs.py --mock
cd app && npm run build
python -m pytest -q --timeout=60
bash verify/setup-web.sh && python verify/camera.py
```

Confirm UI with the **camera** or screenshots — do not rely on grepping HTML alone.

## Local dev

- Scanner mock populates `app/public/jobs.json` (and meta/portal JSON).
- Build: `cd app && npm run build` → `web/`.
- Serve with base path: **`http://127.0.0.1:8137/dsm-jobs/`** (junction or copy under `dsm-jobs/`; `file://` will not run the SW).

```bash
python -m http.server 8137 --directory web --bind 127.0.0.1
```

Live site: https://bflinkdesign.github.io/dsm-jobs/ (repo rename breaks installed PWA URL).

## Commands cheat sheet

| Task | Command |
|------|---------|
| Mock scan | `python find_admin_jobs.py --mock` |
| Live scan | `python find_admin_jobs.py --contact "Name"` (needs `.env`) |
| Lint/tests | `python -m ruff check find_admin_jobs.py tests`; `python -m pytest -q --timeout=60` |
| Astro build | `cd app && npm run build` |

When in doubt: **HANDOFF.md** for session truth, **CLAUDE.md** for system design and invariants.