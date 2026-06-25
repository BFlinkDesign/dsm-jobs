-- dsm-jobs portal schema (Supabase Postgres)
-- ~5 invited users: shared jobs feed + per-user applied/saved/hidden + notes.
--
-- Apply: paste into the Supabase SQL editor (or `supabase db query`) on the
-- project, then run the advisors (Dashboard -> Advisors, or `supabase db advisors`).
-- Since 2026-04-28 new public-schema tables are NOT auto-exposed to the Data
-- API; the explicit GRANTs below are required, and RLS gates the rows.

-- ── jobs: the shared feed ──────────────────────────────────────────────────
-- Written ONLY by the scanner (service/secret key from CI or Brady's machine;
-- service role bypasses RLS). Signed-in users read it; nobody else sees it.
-- pay_text mirrors invariant #1: a predicted wage is never stored as a number.
create table public.jobs (
  id          text primary key,                 -- provider job id
  title       text not null,
  company     text not null,
  location    text,
  pay_text    text,                             -- display string only
  verdict     text check (verdict in ('meets', 'unlisted', 'below')),
  category    text,
  trains      boolean not null default false,  -- employer-stated "will train / no experience"
  trust_label text,
  commute     text,
  url         text,
  about       text,
  source      text,
  posted      date,
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now()
);

alter table public.jobs enable row level security;

create policy "signed-in users read the feed"
  on public.jobs for select
  to authenticated
  using (true);
-- No insert/update/delete policies: only the service role writes.

-- ── user_job_status: one row per (user, job) ───────────────────────────────
create table public.user_job_status (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  job_id     text not null references public.jobs (id) on delete cascade,
  applied    boolean not null default false,
  applied_on date,
  saved      boolean not null default false,
  hidden     boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (user_id, job_id)
);

alter table public.user_job_status enable row level security;

create policy "own status: select"
  on public.user_job_status for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "own status: insert"
  on public.user_job_status for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

-- UPDATE needs USING + WITH CHECK (WITH CHECK blocks reassigning user_id),
-- and silently does nothing without the SELECT policy above.
create policy "own status: update"
  on public.user_job_status for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "own status: delete"
  on public.user_job_status for delete
  to authenticated
  using ((select auth.uid()) = user_id);

-- ── job_notes: append-style conversation log per (user, job) ───────────────
-- "They ask a question and I have it down" — notes are rows, not one blob,
-- so each conversation entry keeps its timestamp for interview prep.
create table public.job_notes (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  job_id     text not null references public.jobs (id) on delete cascade,
  body       text not null check (char_length(body) between 1 and 8000),
  created_at timestamptz not null default now()
);

alter table public.job_notes enable row level security;

create policy "own notes: select"
  on public.job_notes for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "own notes: insert"
  on public.job_notes for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

create policy "own notes: update"
  on public.job_notes for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "own notes: delete"
  on public.job_notes for delete
  to authenticated
  using ((select auth.uid()) = user_id);

-- ── user_profile: quiz answers + what the companion learns (one row/user) ──
create table public.user_profile (
  user_id    uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  profile    jsonb not null default '{}'::jsonb,   -- quiz keys + companion-learned prefs
  updated_at timestamptz not null default now()
);
alter table public.user_profile enable row level security;
create policy "own profile: select" on public.user_profile for select
  to authenticated using ((select auth.uid()) = user_id);
create policy "own profile: insert" on public.user_profile for insert
  to authenticated with check ((select auth.uid()) = user_id);
create policy "own profile: update" on public.user_profile for update
  to authenticated using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

-- ── chat_messages: the companion conversation (hers; operator never reads
--    it through the app — transparency note lives in the UI) ───────────────
create table public.chat_messages (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  role       text not null check (role in ('user', 'assistant')),
  body       text not null check (char_length(body) between 1 and 8000),
  created_at timestamptz not null default now()
);
alter table public.chat_messages enable row level security;
create policy "own chat: select" on public.chat_messages for select
  to authenticated using ((select auth.uid()) = user_id);
create policy "own chat: insert" on public.chat_messages for insert
  to authenticated with check ((select auth.uid()) = user_id);
create policy "own chat: delete" on public.chat_messages for delete
  to authenticated using ((select auth.uid()) = user_id);
create index chat_messages_user_idx on public.chat_messages (user_id, created_at desc);

-- ── ai_usage: per-user rate ledger for the paid AI features ────────────────
-- One row per AI call (currently the resume tailor). The edge function inserts
-- a row, then counts the last few minutes and refuses if over budget — so a
-- stuck loop or a misused token can't run up the Anthropic bill. No body stored.
create table public.ai_usage (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  kind       text not null check (kind in ('resume_tailor')),
  created_at timestamptz not null default now()
);
alter table public.ai_usage enable row level security;
create policy "own ai_usage: select" on public.ai_usage for select
  to authenticated using ((select auth.uid()) = user_id);
create policy "own ai_usage: insert" on public.ai_usage for insert
  to authenticated with check ((select auth.uid()) = user_id);
create index ai_usage_user_idx on public.ai_usage (user_id, kind, created_at desc);

-- ── Data API exposure (required since 2026-04-28 for new tables) ───────────
grant select on public.jobs to authenticated;
grant select, insert, update, delete on public.user_job_status to authenticated;
grant select, insert, update, delete on public.job_notes to authenticated;
grant select, insert, update on public.user_profile to authenticated;
grant select, insert, delete on public.chat_messages to authenticated;
grant select, insert on public.ai_usage to authenticated;
-- Deliberately NOTHING granted to anon: the portal is invite-only.

-- ── indexes for the obvious access paths ───────────────────────────────────
create index job_notes_user_job_idx on public.job_notes (user_id, job_id, created_at desc);
create index jobs_last_seen_idx on public.jobs (last_seen desc);
