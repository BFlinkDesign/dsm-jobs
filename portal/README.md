# Portal (Supabase) — scaffold

**Session handoff (2026-06-22):** see `docs/HANDOFF.md` for full operator checklist.

Multi-user portal (~5 invited users) on Supabase: shared jobs feed, per-user
**Applied** status + dates, per-job **conversation notes** (interview-prep log),
Google + email magic-link sign-in. AI chat comes **after** the portal works
(Edge Function calling the Anthropic API; key lives in Supabase secrets).

**Status: LIVE as of 2026-06-16.** Project `tcclohxvhmwgjrtdkkuw`
(`https://tcclohxvhmwgjrtdkkuw.supabase.co`) — `schema.sql` applied (all 5 tables
+ RLS), **email signup/login verified end-to-end** (signup → instant session →
RLS-isolated saved work). Config that makes it work: **Site URL** =
`https://bflinkdesign.github.io/dsm-jobs/` (+ that redirect allow-listed) and
**"Confirm email" OFF** (the default Supabase mailer is too spam-prone to gate a
phone-only user's first login on). Only the **email** provider is enabled today
(Google would need a Google Cloud OAuth client; Apple is skipped — $99/yr). The
static PWA keeps working unchanged without sign-in — the portal is additive. The
setup steps below remain the canonical runbook for re-creating or auditing it.

## Setup (operator, one time)

1. **Create the Supabase project** (free tier) at https://supabase.com/dashboard.
   Region: nearest US. No code change needed for project naming.
2. **Apply `schema.sql`** in the SQL editor. Then run the **Advisors**
   (Dashboard → Advisors) and fix anything it flags.
3. **Auth → invite-only:** in Auth settings, **disable public sign-ups**
   (invite-only). Invite each user by email from Auth → Users. With sign-ups
   disabled, Google sign-in also only works for already-invited emails.
4. **Enable Google provider** (Auth → Providers → Google; needs a Google Cloud
   OAuth client — follow the dashboard's inline instructions for redirect URL).
   Magic link works out of the box with invites. (Apple Sign-In deferred —
   $99/yr Apple Developer account.)
5. **Keys:** the front end uses the **publishable** key (safe for browsers;
   `anon` is the legacy name). The scanner upload uses the **secret/service**
   key — that one NEVER ships to the browser, never enters chat, and never
   gets committed; store it via the `/add-secret` masked-dialog flow.

## Architecture decisions (locked)

- **RLS everywhere.** `jobs` is read-only for signed-in users (writes happen
  only via the service role from the scanner). `user_job_status` and
  `job_notes` are scoped to `auth.uid()` with `WITH CHECK` on updates so a row
  can't be reassigned. Nothing is granted to `anon`.
- **Explicit Data API grants** are in `schema.sql` — since 2026-04-28 new
  public-schema tables are not auto-exposed.
- **Notes are append-style rows** (timestamped conversation log), not one
  editable blob — "they asked X" entries keep their date for interview prep.
- **Invariant #1 carries over:** `jobs.pay_text` is a display string; a
  predicted wage is never stored as a number anywhere in the portal.
- **localStorage import:** on first sign-in the web app offers to import the
  device's existing `myjobs:v1` state (applied/saved/hidden/notes) into the
  user's rows, then keeps localStorage as offline cache.

## Status update (2026-06-22): Astro PWA is the shipped app

The live product is the **Astro PWA** in `app/` → built to `web/` on deploy.
Auth, profile blob sync (`user_profile`), per-job notes (`job_notes`), Rudy chat
(`chat_messages`), passkeys, magic-link sign-in, and follow-up tracking all run
through `app/src/scripts/*.ts`. The legacy `APP_TEMPLATE` HTML in
`find_admin_jobs.py` is still generated during scan but **overwritten** by the
Astro build — do not treat it as the product surface.

**Sync model:** applied/saved/hidden/snoozed/followUps/filters/commute live in
the `user_profile.profile` JSON blob (plus `job_notes` + `chat_messages` tables).
The legacy `user_job_status` table is **migrate-only** on first sign-in.

## Build order (historical)

1. Scanner upsert: `--push-supabase` flag posting rows to `jobs` (service key). **Done**
2. Web app auth + remote sync. **Done (Astro)**
3. localStorage → portal import on first sign-in. **Done**
4. AI chat Edge Function. **Done (`companion`, `resume-tailor`)**

### Activation checklist (one sitting, off the office network — the firewall
### geo-blocks auth.supabase.io)

1. Create the Supabase project, apply `schema.sql` (now also creates
   `user_profile` + `chat_messages`), run the Advisors.
2. Auth: disable public sign-ups (invite-only), invite emails, enable Google.
3. GitHub secrets (stdin only): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
   `SUPABASE_PUBLISHABLE_KEY` — next daily run lights up sign-in + jobs push.
4. Companion: `supabase functions deploy companion` +
   `supabase secrets set ANTHROPIC_API_KEY=...` (masked dialog, never chat).

## Supabase CLI (Windows operator)

Local config lives in `supabase/` (tracked): `config.toml` pins
`project_id = tcclohxvhmwgjrtdkkuw`, plus migrations and edge functions.
Link state is gitignored under `supabase/.temp/` (created by `supabase link`).

**Eagle network (CNC-1):** `supabase login` redirects to `auth.supabase.io`,
which the firewall blocks. **Skip CLI login** — the project was configured with
`.env` keys from the first build, not CLI auth. Put these in repo-root `.env`
(copy from the legacy Desktop folder if needed; never commit):

- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_PUBLISHABLE_KEY` (scanner +
  PWA build)
- `SUPABASE_ACCESS_TOKEN` (Management API — full schema verify without CLI)
- optional: `SUPABASE_DB_PASSWORD` (direct Postgres if you need it)

Schema changes: paste `portal/schema.sql` in the **dashboard SQL editor** from
a device that can reach the dashboard, or use the Management API with your
access token. `api.supabase.com` and `<ref>.supabase.co` work from Eagle; only
`auth.supabase.io` is blocked.

**Verify schema (no login):**

```powershell
.\scripts\verify_supabase_schema.ps1
```

Loads `.env` from the repo root (then
`C:\Users\Brady.EAGLE\Desktop\admin-job-finder\.env` as fallback). With
`SUPABASE_ACCESS_TOKEN` it runs read-only SQL via `api.supabase.com` (tables,
RLS flags, policy counts). With only `SUPABASE_SERVICE_KEY` it probes tables
via PostgREST (partial — RLS not checked). Publishable key alone is not enough.

**Optional CLI** (off-network or when `auth.supabase.io` is reachable):

```powershell
npm install -g supabase   # if supabase --version fails
supabase login            # opens browser → auth.supabase.io
supabase link --project-ref tcclohxvhmwgjrtdkkuw
supabase db query --linked "SELECT 1"
```

Non-interactive CLI alternative: set `SUPABASE_ACCESS_TOKEN` in `.env` or
user env (personal access token from
https://supabase.com/dashboard/account/tokens), then `supabase link` (still
needs the project database password once).

## Sentry (browser + edge monitoring)

The Astro app reads **`SENTRY_DSN`** at build time (`app/astro.config.mjs` →
`index.astro`). When unset, monitoring is a clean no-op — the app still ships.

**GitHub Actions:** set a repo **Variable** named `SENTRY_DSN` (not a secret —
the browser DSN is publishable). `.github/workflows/scan.yml` already passes
`SENTRY_DSN: ${{ vars.SENTRY_DSN }}` into the Astro build step and uses the
same value for Sentry Cron check-ins on the daily scan. Leave the variable empty
until you have a project DSN from https://sentry.io — then paste the browser
DSN (starts with `https://`) into **Settings → Secrets and variables → Actions
→ Variables**.

Edge functions (`companion`, `resume-tailor`) gate on the same `SENTRY_DSN`
Supabase secret when deployed.
