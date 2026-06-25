# Spec A — Ruby memory & truthfulness (anti-confabulation)

**Date:** 2026-06-19 · **Branch:** `claude/ruby-grounded-memory`

## Problem (verified)
Ruby (the `companion` edge function) fabricates profile facts she was never told —
observed live: she claimed the user is *"open on hours"* and referenced a *"cereal
incident"* that exists nowhere in the data. Root cause is **not** missing storage
(`user_profile` + `chat_messages` + a `save_profile` tool already exist) — it's the
absence of **grounding discipline**: the prompt told her to *"learn her"* and the
model filled gaps by inventing plausible details. Separately, the quiz answers live
in client `localStorage`, not the server `user_profile` Ruby reads, so server-side
she had almost no real facts to ground on.

## Decision (research-backed, deep-research run `waifq4aqe`)
Do **not** adopt Mem0 / Zep / Letta / GraphRAG — overkill for one user, and their
accuracy benchmarks are an active vendor dispute. Fix with **prompt-level grounding +
source-tagging on the existing store**, per Anthropic's anti-hallucination guidance
(docs.anthropic.com/.../reduce-hallucinations).

## Changes (`supabase/functions/companion/index.ts`)
1. **`MEMORY & TRUTH` rules** in the system prompt: external-knowledge restriction
   ("you know nothing beyond KNOWN FACTS"), no invented anecdotes, recite-only-known +
   say "I don't know yet" then ask, and a pre-send self-audit that drops unsupported claims.
2. **`knownFacts()`** builds a source-tagged `KNOWN FACTS` block from the stored profile,
   prepended with a baseline truth set by Brady: *availability = daytime; flexible only if
   remote; never "open on hours."* This block is the model's ONLY permitted source of truth.
3. **Availability is baseline-only:** stored `time`/`confidence` are excluded from the facts
   block and from saves, so a stale value can't reopen "open on hours." `save_profile` drops
   `time`; its schema/description now forbid saving assumptions.
4. **Source-tagged saves:** `save_profile` writes `{v, src:"confirmed-in-chat", ts}`.

The job-filter day-shift gate (`find_admin_jobs.py:531`) was already correct/remote-exempt
and is unchanged — this only makes Ruby's *memory* match it.

## Out of scope (YAGNI)
No new memory framework. pgvector *semantic recall of old chats* is a future option, not
part of the truthfulness fix (recent-20 messages + facts is enough for one user).

## Verification
- Static review of the grounding block + `knownFacts()`.
- **Not runtime-tested locally** (no `deno` on this machine). Before deploy: `deno check`,
  then a manual chat check that "what do you know about me?" recites only seeded facts and
  she never asserts evening/any-hours availability for in-person work.

## Deploy (manual — not CI)
`supabase/` is excluded from auto-merge (human review). After merge:
`supabase functions deploy companion` (needs the Supabase CLI + access token; this machine
has neither, and `auth.supabase.io` is firewall-blocked).
