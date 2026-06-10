# Portal (Supabase) — scaffold

Multi-user portal (~5 invited users) on Supabase: shared jobs feed, per-user
**Applied** status + dates, per-job **conversation notes** (interview-prep log),
Google + email magic-link sign-in. AI chat comes **after** the portal works
(Edge Function calling the Anthropic API; key lives in Supabase secrets).

**Status: scaffold only.** Nothing here is live until the Supabase project
exists. The static PWA keeps working unchanged — the portal is additive.

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
