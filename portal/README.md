# Portal (Supabase) — scaffold

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

## Build order (queued)

1. Scanner upsert: `--push-supabase` flag posting rows to `jobs` (service key).
2. Web app auth + remote status/notes sync (publishable key, supabase-js via
   CDN to stay build-free; pin the version).
3. localStorage → portal import on first sign-in.
4. AI chat Edge Function (Anthropic API; per-user rate cap; AFTER the above).

## Status update (2026-06-12): 1–3 are BUILT, 4 is staged

All three build-order items now live in the app template (`find_admin_jobs.py`):
sign-in bar (magic link + Google), pull/merge/push sync with union semantics,
and the implicit localStorage import (first sync pushes everything local the
server didn't have). The scanner upsert is `--push-supabase` (transport in
`push.py`), already passed by the daily CD — it self-disables until secrets
exist. The page embeds supabase-js 2.108.1 pinned + SRI-locked, only when
`SUPABASE_URL` + `SUPABASE_PUBLISHABLE_KEY` are set at build time.

The companion (item 4) lives at `../supabase/functions/companion/index.ts`
(the Supabase-CLI standard path, deployed with `supabase functions deploy`) — see the
header comment for deploy + the guardrails baked into its system prompt
(support tool, NOT therapy; verified crisis numbers; profile learning via a
`save_profile` tool that feeds the app's For-you ranking).

### Activation checklist (one sitting, off the office network — the firewall
### geo-blocks auth.supabase.io)

1. Create the Supabase project, apply `schema.sql` (now also creates
   `user_profile` + `chat_messages`), run the Advisors.
2. Auth: disable public sign-ups (invite-only), invite emails, enable Google.
3. GitHub secrets (stdin only): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
   `SUPABASE_PUBLISHABLE_KEY` — next daily run lights up sign-in + jobs push.
4. Companion: `supabase functions deploy companion` +
   `supabase secrets set ANTHROPIC_API_KEY=...` (masked dialog, never chat).
