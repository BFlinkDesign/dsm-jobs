# Chatterbox Voice Launch Architecture

Date: 2026-07-01

## Objective

Launch Rudy voice as a trust-first, provider-agnostic voice layer with
Chatterbox as the default TTS backend, without losing user data, leaking
secrets, or claiming live readiness before the live artifact is exercised.

## Source Of Truth

- Source contract: `supabase/functions/voice/index.ts`
- Auth gate: `supabase/config.toml`, `[functions.voice] verify_jwt = true`
- Client integration: `app/src/scripts/app.ts`, `app/src/pages/index.astro`
- Data-preservation gate: `scripts/snapshot_supabase.py`
- Preservation runbook: `docs/SUPABASE-PRESERVATION-RUNBOOK.md`
- Official CLI contract: Supabase CLI `functions deploy`, `secrets list`, and
  `secrets set` docs queried through Context7 on 2026-07-01.

## Trust Model

- VERIFIED: real signed-in browser invokes live `voice`, receives audio from
  the configured provider, plays it, and evidence is captured.
- ASSERTED: source/config says Chatterbox will be selected when
  `REPLICATE_API_TOKEN` exists, but live secret/deploy proof is missing.
- DRAFT: future barge-in/WebRTC/action-log work.
- BLOCKED: any Supabase mutation when the production snapshot gate fails.

## Phase 0 - Worktree Hygiene And Freeze Line

Goal: keep work isolated and preserve dirty local state.

Actions:
- Use the clean pickup worktree, not the stale dirty checkout.
- Keep the original dirty checkout untouched.
- Stage by file name only; never `git add -A`.
- Kill only processes this loop starts.

Gate:
- `git status --short --branch`
- Verify no unexpected generated files are tracked.

## Phase 1 - Source Contract Lock

Goal: make the code contract unambiguous.

Actions:
- Keep `voice` provider-agnostic.
- TTS selection order: forced `VOICE_TTS`, then `REPLICATE_API_TOKEN` to
  Chatterbox, then HF, Cloudflare, ElevenLabs, else `{ unconfigured: true }`.
- Client calls `voice` through Supabase Functions and falls back to browser
  speech if unconfigured.
- Remove stale client/config copy that says ElevenLabs is the primary voice.

Gates:
- Static guard asserts Chatterbox default selector.
- Static guard asserts no stale ElevenLabs copy in the client/config surface.
- Deno check/lint/test over all edge functions.

## Phase 2 - Snapshot Gate

Goal: prove data/settings are safe before live Supabase work.

Actions:
- Load Supabase env through the approved local mechanism, without printing
  values.
- Run `python scripts/snapshot_supabase.py`.
- Run `python scripts/verify_supabase_schema.py --require-full`.

Gate:
- Snapshot manifest exists with auth/users, auth settings, and app tables.
- Required table counts are present.
- Schema verifier exits 0.

Failure rule:
- If `SUPABASE_URL`, service key, DB password, or pooler host is missing, stop
  live Supabase work. Continue only source-safe work.

## Phase 3 - Secret Provisioning

Goal: configure Chatterbox without exposing secrets.

Actions:
- Check secret names only:
  `supabase secrets list --project-ref tcclohxvhmwgjrtdkkuw`
- If absent, set `REPLICATE_API_TOKEN` using a secure local env file or shell
  environment, not chat.
- Optional tuning: `CHATTERBOX_MODEL`, `CHATTERBOX_VOICE_URL`,
  `CHATTERBOX_EXAGGERATION`, `CHATTERBOX_CFG`, `CHATTERBOX_TEMPERATURE`.
- Do not store provider credentials in GitHub repo secrets unless CI genuinely
  needs them. Runtime provider keys belong in Supabase Edge Function secrets.

Gate:
- Secret-name list shows `REPLICATE_API_TOKEN`.
- No secret values printed, committed, or written to docs.

## Phase 4 - Deploy And Canary

Goal: prove the deployed live function matches source and keeps auth enforced.

Actions:
- Deploy only the voice function:
  `supabase functions deploy voice --project-ref tcclohxvhmwgjrtdkkuw`
- Confirm unauthenticated calls are rejected by platform JWT verification.
- Confirm signed-in calls reach the function.

Gates:
- Deploy exits 0.
- Unauthenticated request cannot get TTS audio.
- Signed-in canary returns `audio` and a valid `mime`, not `{ unconfigured: true }`.

## Phase 5 - Browser Playback Proof

Goal: verify value, not just deployment.

Actions:
- Open the live GitHub Pages app.
- Sign in with a real allowed account.
- Enable "Rudy reads replies aloud".
- Ask a short prompt.
- Verify playback occurs through returned audio, not browser fallback.
- Test mic transcription if STT provider is configured.

Gates:
- Browser console has no voice errors.
- Network call to `voice` succeeds.
- UI still works if the provider returns failure.
- Evidence captured: screenshot plus exact command/test output.

## Phase 6 - Observability And Cost Guard

Goal: make failures visible without leaking private text/audio.

Actions:
- Preserve Sentry PII stripping in `voice`.
- Add lightweight user-facing fallback copy only if needed.
- Future: provider latency/cost counters without raw text/audio.

Gates:
- Error bodies do not expose provider token values or full private content.
- Max TTS chars and STT bytes remain enforced.

## Loop Contract

Each loop follows:

1. Artifact: name the file/function/deployed endpoint being changed.
2. Source: identify the canonical source of truth.
3. Gate: run the smallest meaningful command.
4. Evidence: record exact exit status and key output.
5. Gap: mark anything not live-verified as ASSERTED or BLOCKED.

No phase may promote to VERIFIED without a fresh gate in the current loop.
