-- AI spend cap: a HARD month-to-date cap on the deployed app's Anthropic spend,
-- shared across BOTH paid edge functions (companion + resume-tailor).
--
-- Apply: paste into the Supabase SQL editor (or `supabase db query`) on the
-- project, then run the advisors (Dashboard -> Advisors, or `supabase db advisors`).
-- This file is human-reviewed before it is applied.
--
-- WHY SECURITY DEFINER: the edge functions run under the *caller's* JWT (RLS),
-- so they cannot reach a shared, app-wide ledger row with a direct RLS table
-- write — every user would need to see/own it, which defeats the point. Instead
-- the ledger table is RLS-locked with NO policies and NO grants, and the only
-- way in is this SECURITY DEFINER function (owned by postgres, runs as owner,
-- bypasses RLS). This mirrors how `public.jobs` is service-role-only, but tighter.

-- ── ai_spend_ledger: one row per calendar month (UTC) ──────────────────────
-- cost_usd accumulates provider cost across both functions. The alerted_* flags
-- make the warning/stop emails fire EXACTLY ONCE per month (idempotent dedup):
-- record_ai_spend flips them the first time MTD crosses each threshold.
create table public.ai_spend_ledger (
  year_month text primary key,                  -- 'YYYY-MM' in UTC
  cost_usd   numeric not null default 0,
  alerted_20 boolean not null default false,    -- $20 warning email sent
  alerted_25 boolean not null default false,    -- $25 stop email sent
  updated_at timestamptz not null default now()
);

-- RLS on, but DELIBERATELY no policies and no grants to authenticated/anon.
-- The table is unreachable via the Data API; only the SECURITY DEFINER function
-- below (running as the table owner) can read or write it.
alter table public.ai_spend_ledger enable row level security;

-- ── record_ai_spend(cost_usd) ──────────────────────────────────────────────
-- Atomically adds cost_usd to the current UTC month's row (creating it if
-- needed) and returns:
--   mtd        - the new month-to-date total AFTER adding cost_usd
--   fire_warn  - true ONLY on the call that first pushes MTD >= $20 (else false)
--   fire_stop  - true ONLY on the call that first pushes MTD >= $25 (else false)
--
-- A bare numeric return can't support "email once": every call after the
-- crossing also has mtd >= threshold, so the caller could never tell "just
-- crossed" from "already over". Returning the flip signal — computed and the
-- flag persisted in the same atomic statement — is what makes the emails fire
-- exactly once. The function is the single writer, so the flag flip and the
-- cost add can't race.
--
-- Pass cost_usd = 0 to read current MTD without spending (used by the pre-call
-- gate): 0 can never cross a threshold, so fire_warn / fire_stop come back false.
create or replace function public.record_ai_spend(cost_usd numeric)
returns table (mtd numeric, fire_warn boolean, fire_stop boolean)
language plpgsql
security definer
set search_path = public, pg_temp     -- hardening: pin search_path under DEFINER
as $$
declare
  ym         text := to_char(now() at time zone 'utc', 'YYYY-MM');
  was_20     boolean;
  was_25     boolean;
  new_total  numeric;
begin
  if cost_usd is null or cost_usd < 0 then
    raise exception 'cost_usd must be a non-negative number';
  end if;

  -- Single atomic upsert: add the cost, return prior flag state + new total.
  -- ON CONFLICT handles the existing-row case; the RETURNING gives us the
  -- post-update total and the flags AS THEY WERE before we set them below.
  insert into public.ai_spend_ledger as l (year_month, cost_usd, updated_at)
  values (ym, cost_usd, now())
  on conflict (year_month) do update
    set cost_usd   = l.cost_usd + excluded.cost_usd,
        updated_at = now()
  returning l.cost_usd, l.alerted_20, l.alerted_25
    into new_total, was_20, was_25;

  -- A threshold "fires" only when it was NOT already alerted AND the new total
  -- is at/over it. Persist the flip so it never fires twice.
  fire_warn := (not was_20) and (new_total >= 20.00);
  fire_stop := (not was_25) and (new_total >= 25.00);

  if fire_warn or fire_stop then
    update public.ai_spend_ledger
      set alerted_20 = alerted_20 or fire_warn,
          alerted_25 = alerted_25 or fire_stop,
          updated_at = now()
      where year_month = ym;
  end if;

  mtd := new_total;
  return next;
end;
$$;

-- Revoke the implicit PUBLIC execute grant FIRST. Postgres grants EXECUTE to
-- PUBLIC by default on every CREATE FUNCTION, and Supabase exposes public-schema
-- functions over the PostgREST RPC endpoint (/rest/v1/rpc/record_ai_spend) to the
-- unauthenticated `anon` role (whose key ships in the client bundle). Without this
-- revoke, an anonymous caller could POST {"cost_usd": 9999} and trip the sticky
-- $25 stop — a DoS that pauses all AI features for the month. A later GRANT only
-- ADDS a grantee; it does not remove the PUBLIC one, so the revoke is required.
revoke execute on function public.record_ai_spend(numeric) from public;

-- Let signed-in users invoke it (the edge functions call it under the user JWT).
-- EXECUTE is all they get — they still cannot touch the table directly.
grant execute on function public.record_ai_spend(numeric) to authenticated;
