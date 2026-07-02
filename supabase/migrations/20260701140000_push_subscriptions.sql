-- Web Push subscriptions for follow-up reminders (CLAUDE.md "Planned / next" —
-- iOS PWA `Notification` only fires while the app is open/foregrounded; a
-- phone-only user who closed the app is the NORMAL state, so follow-up nudges
-- need a real OS push, which requires a server-held subscription per device.
--
-- Apply: paste into the Supabase SQL editor (or `supabase db query`) on the
-- project, then run the advisors (Dashboard -> Advisors, or `supabase db advisors`).
-- This file is human-reviewed before it is applied — see AGENTS.md "Supabase
-- operating rules" (snapshot first, no direct apply from an agent session).
--
-- One row per subscribed device/browser (a user may have >1 — e.g. her phone
-- and a tablet), keyed by the push endpoint URL itself (globally unique per
-- the Push API spec) so re-subscribing the same device upserts cleanly.
-- Follows the same "own row only" RLS shape as public.user_job_status.
create table public.push_subscriptions (
  endpoint    text primary key,
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  p256dh      text not null,                 -- subscription.keys.p256dh
  auth_key    text not null,                 -- subscription.keys.auth
  user_agent  text,                          -- best-effort debugging aid only
  created_at  timestamptz not null default now(),
  last_seen   timestamptz not null default now()
);

alter table public.push_subscriptions enable row level security;

-- No SELECT policy for `authenticated`: a user never needs to read her own
-- subscription rows back (the browser already knows its own subscription via
-- the Push API), and the sender edge function uses the service role, which
-- bypasses RLS. Keeping SELECT closed means a stolen anon/publishable key
-- can't enumerate endpoint/key material for other users even if a bug ever
-- granted broader access.
create policy "own push subscription: insert"
  on public.push_subscriptions for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

create policy "own push subscription: update"
  on public.push_subscriptions for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "own push subscription: delete"
  on public.push_subscriptions for delete
  to authenticated
  using ((select auth.uid()) = user_id);

-- Data API exposure (required since 2026-04-28 for new tables). Deliberately
-- no `select` grant — see the RLS comment above. Deliberately nothing granted
-- to anon: push subscribing requires sign-in, same as every other user table.
grant insert, update, delete on public.push_subscriptions to authenticated;

-- Access path for the sender edge function (service role bypasses RLS, but
-- still benefits from an index instead of a table scan across ~5 users).
create index push_subscriptions_user_idx on public.push_subscriptions (user_id);
