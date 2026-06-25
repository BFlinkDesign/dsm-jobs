# Supabase Preservation Runbook

This app already has real user state. Supabase work is authorized, but data loss
is not. The live site must stay usable until a replacement is seeded, verified,
and cut over cleanly.

## Non-negotiables

- Preserve accounts, preferences, notes, chats, saved/applied/hidden status,
  follow-ups, AI usage, job rows, auth settings, redirect URLs, provider config,
  and Edge Function behavior.
- Snapshot before every Supabase setting, schema, RLS, auth, function, or data
  operation:

  ```powershell
  python scripts/snapshot_supabase.py
  python scripts/verify_supabase_schema.py --require-full
  ```

- Do not cut over to a new backend/project/auth setup until it is seeded from the
  latest production snapshot.
- Do not accept count-only validation. Verify primary keys and payloads.
- If a gate fails, do not publish. The last good `gh-pages` build stays live.

## Seeded Cutover Checklist

1. Create a fresh snapshot and record its path.
2. Create or migrate the target schema without pointing production traffic at it.
3. Seed tables in dependency order:
   - auth/users or identity mapping
   - `jobs`
   - `user_profile`
   - `chat_messages`
   - `job_notes`
   - `user_job_status`
   - `ai_usage`
4. Preserve user IDs wherever possible. If an auth system forces new user IDs,
   create an explicit old-to-new user map and apply it to every user-owned table.
5. Verify key-for-key and payload-for-payload against the snapshot. Document any
   intentional field transforms.
6. Smoke test the live app against the target backend with a real account.
7. Cut over only after verification is clean.
8. Keep the old backend and snapshot available through the first successful
   post-cutover health check.

## Current Network Reality

`auth.supabase.io` is blocked from the Eagle network, but `api.supabase.com` and
the project API domain are reachable. Use Management API/PostgREST scripts from
this machine. Dashboard-only tasks can be done off-network, but should be
mirrored back into scripts or documented config immediately.
