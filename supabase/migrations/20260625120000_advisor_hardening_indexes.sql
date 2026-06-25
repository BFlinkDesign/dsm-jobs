-- Clears the live Supabase advisor findings on project tcclohxvhmwgjrtdkkuw
-- (snapshot 2026-06-25). All statements are idempotent and low-risk.
--
-- 1) SECURITY — `public.rls_auto_enable()` is a SECURITY DEFINER *event-trigger*
--    function that the advisor flags as EXECUTE-able by `anon` and
--    `authenticated` via /rest/v1/rpc/rls_auto_enable. An event-trigger function
--    isn't meaningfully callable over PostgREST (it needs DDL event context), but
--    leaving a SECURITY DEFINER function exposed to the anon role is needless
--    surface. It is invoked by the event trigger as table owner regardless of
--    these grants, so revoking EXECUTE does not affect its real job.
revoke execute on function public.rls_auto_enable() from anon;
revoke execute on function public.rls_auto_enable() from authenticated;
revoke execute on function public.rls_auto_enable() from public;

-- 2) PERFORMANCE — covering indexes for the two foreign keys the advisor flags
--    as unindexed. Without them, a delete/update on the referenced `jobs` row
--    forces a seq scan of these tables to enforce the FK.
create index if not exists job_notes_job_id_idx
  on public.job_notes (job_id);
create index if not exists user_job_status_job_id_idx
  on public.user_job_status (job_id);
