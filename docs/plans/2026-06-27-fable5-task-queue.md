# Fable 5 task queue

Prepared 2026-06-27 for hand-off once Fable 5 (`claude-fable-5`) is back online.
Whoever picks this up starts cold — no memory of the session that wrote this —
so each task below is self-contained: what it is, why it matters, where to
look, and what "done" means. Re-read `CLAUDE.md` and `AGENTS.md` first regardless;
they're the source of truth for architecture and invariants.

## Project in one paragraph

`dsm-jobs` is a job-finder PWA built for exactly one end user: a phone-only,
no-degree single mother in the Des Moines metro, financially stressed, looking
for admin/office/remote work. Three constraints override every decision:
phone/tablet only, zero scam exposure (vetting happens before she ever sees a
listing), realistic entry-level targeting. Python scanner (`find_admin_jobs.py`)
feeds JSON to an Astro PWA (`app/`) that builds into `web/` and deploys to
`gh-pages`. Rudy is her in-app AI companion (Supabase edge function
`companion`); a résumé tailor and now a voice backend (`voice` edge function)
round out the "unfair advantage" toolkit.

## Before starting anything

1. Check PR #144 (Chatterbox voice) — is it merged? If yes, skip to item 1
   below. If still open, check why (CI red? conflicts? Brady hasn't said go?)
   and resolve that first — it's the most recent work and everything after it
   assumes it landed.
2. Run `git log origin/main --oneline -20` to see what's landed since this
   doc was written — treat this list as a starting point, not gospel; verify
   each item is still actually open before working on it.
3. `python -m pytest -q --timeout=60` and `cd app && npm run build` should
   both be clean on a fresh checkout of `main`. If not, that's a P0 before
   anything else.

## 1. Verify Chatterbox voice actually works end to end

**Why:** PR #144 made Chatterbox (Resemble AI, open-source TTS via Replicate)
Rudy's default voice, replacing ElevenLabs. It was verified via CI (deno
check/lint/test) but never exercised against a real Replicate account —
CI can't do that (no token, and cost). Once Brady has set
`REPLICATE_API_TOKEN` and run `supabase functions deploy voice`, someone needs
to actually open the app, tap the Rudy read-aloud toggle, and listen.

**Where:** `supabase/functions/voice/index.ts` (the function), `app/src/scripts/app.ts`
(client — search `elevenSpeak`/`synthSpeak`/`rudyAudio`), `app/src/pages/index.astro`
(`.rudy-voicebar`, `#rudy-spk`).

**Done when:** a real chat reply plays back in Chatterbox's voice (not the
browser's robotic fallback), and the mic (`#rudy-mic`, MediaRecorder → STT)
correctly transcribes a spoken sentence. If it falls back to the browser
voice, check `ttsProvider()`/`sttProvider()` selection order in `voice/index.ts`
and confirm the secret name matches exactly what was set.

## 2. Rudy memory viewer — "What Rudy remembers"

**Why:** `docs/rudy-frontier-experience-plan.md` build-order item 3, not started.
The plan's "Human Gates" section requires memory to be scoped and explainable,
not a black box — this is the transparency feature that makes that real.

**Where:** Rudy's persisted state today is a mix of `localStorage` and the
Supabase `user_profile` blob (see `app/src/scripts/store.ts`, `autosave.ts`).
Chat history persistence: search `chatLocalKey`/`appendChatToLocal` in
`autosave.ts`/`app.ts`.

**Scope:** a view (likely inside the existing Rudy overlay, a new tab within
it) that lists what's remembered — saved résumé facts, recent chat summary,
preference flags Rudy has picked up — with a delete/edit control per item.
Nothing fancy; a stressed non-technical user needs to see it's not spying,
not have a settings-app experience.

**Done when:** a static guard test (pattern: see `tests/test_frontend_static_guards.py`
for style) confirms the viewer renders, and deleting an item actually clears
it from both localStorage and the Supabase profile blob.

## 3. Document-aware Rudy chat

**Why:** build-order item 5, partially shipped. The résumé tailor flow
(`app/src/scripts/app.ts`, search `Rudy tailor résumé`) already reads the full
job posting (`descFull`) and the saved résumé for its one-shot tailored draft.
But general Rudy chat can't answer "does my résumé mention customer service?"
or "what does this posting actually pay" outside that flow.

**Where:** `supabase/functions/companion/index.ts` (the chat prompt/tools),
`app/src/scripts/app.ts` (chat send path, search `invoke("companion"`).

**Scope:** when the user has an active/selected résumé document or is viewing
a specific job, pass that context into the companion function's prompt (with
a clear token/cost budget — see the existing spend-cap pattern in
`supabase/functions/_shared/spend_cap.ts`) so Rudy can answer questions about
it directly in chat, not just via the separate tailor button.

**Done when:** asking Rudy "what does my résumé say about X" in chat gets a
grounded answer referencing the actual saved résumé text, not a generic reply.

## 4. Web Push for follow-up reminders

**Why:** CLAUDE.md "Planned / next." Follow-up nudges (3/5/7-day chips) rely
on the in-app `Notification` API today, which is unreliable on iOS PWA once
the app is closed — and closed is the normal state for a phone-only user.

**Where:** `app/public/sw.js` (service worker — needs a `push` and
`notificationclick` handler), a new Supabase edge function or scheduled job to
actually send the push (needs VAPID keys as secrets), `app/src/scripts/app.ts`
follow-up scheduling logic (search `Follow up on`, `fu.done`).

**Done when:** a follow-up reminder arrives as a real OS push notification
with the app fully closed, tapping it opens the right job/tab, and there's a
graceful no-op if push permission was never granted (never block the
in-app fallback).

## 5. MCP-style connector registry (exploratory, lower priority)

**Why:** build-order item 6. Not urgent — no concrete connector need yet — but
worth a design spike if there's spare capacity, since `rudy-frontier-experience-plan.md`
calls for "tool registry with schemas, allowlists, and human confirmation for
side effects" before Rudy gets any side-effecting capability (e.g., "apply for
me," "email this contact").

**Done when:** a short design note (not necessarily code) proposing the
contract shape and the confirmation UX, added to `docs/plans/`.

## 6. Source coverage: CI-based ATS probe workflow

**Why:** CLAUDE.md lists MercyOne/Hy-Vee/national-tenant Workday sources as
"probed + rejected after live end-to-end testing" — but that testing happened
from environments with real internet egress. A sandboxed session may not be
able to re-probe live ATS endpoints (connection-refused under some network
policies). If that's still true when you pick this up, don't fight the
sandbox — build a `.github/workflows/*.yml` job that runs the probe from a
GitHub Actions runner (real egress) and reports pass/fail counts as a job
summary or artifact, the same way `source-health.yml` already works.

**Where:** `providers.py` (`ATS_BOARDS`, `WORKDAY_BOARDS`, `SMARTRECRUITERS_COMPANIES`,
`NEOGOV_AGENCIES`), `.github/workflows/source-health.yml` for the existing pattern.

**Done when:** a new or extended workflow can probe a candidate board and
report row counts without needing a live sandbox connection.

## 7. Housekeeping: refresh stale docs

**Why:** `docs/HANDOFF.md` (last touched 2026-06-25) and `AGENTS.md` (last
touched 2026-06-22) both predate several shipped features and contain at
least one stale claim each (e.g. AGENTS.md's "Collapsible filters not ported"
gap — CLAUDE.md confirms this **was** ported). Splitting doc-truth across
three files (`CLAUDE.md`, `AGENTS.md`, `docs/HANDOFF.md`) that drift out of
sync is a real risk for whoever reads them next.

**Done when:** `AGENTS.md`'s "Known limitations" table and `docs/HANDOFF.md`'s
"Not done / external only" table both match reality — cross-check every row
against `CLAUDE.md`'s "Shipped" log before editing, since that file is
the most consistently maintained one.

## 8. Triage the open PR queue

- **#146–149** (dependabot: astro 7.0.3, ruff 0.15.20, actions/checkout 7.0.0,
  claude-code-action 1.0.159) — routine version bumps, low risk. Confirm CI
  green, merge.
- **#145** ("unified review-gate-act pipeline," Brady's own fleet-wide
  governance push) — last seen with `mergeable_state: "dirty"`, likely
  conflicting with #150 (a similar governance baseline that already merged
  to main on 2026-06-29). Check whether #145 is now redundant/supersedable
  before spending time rebasing it — ask Brady rather than guessing, since
  it's his infra-wide rollout, not app product work.
- **#151** ("Set up Cursor Cloud dev environment + AGENTS.md notes," draft,
  external tool) — confirm with Brady whether this is wanted before touching;
  don't assume.

## What NOT to do

- Don't deploy `web/` built from `--mock` data — it overwrites the live site
  with canned jobs. Only the real `--contact` scan or the CD workflow should
  publish to `gh-pages`.
- Don't loosen the scam-shield invariants (hidden not labeled, never show a
  guessed wage as a number) for any of the above — they're load-bearing per
  `CLAUDE.md`.
- Don't touch Supabase auth/schema/RLS without a fresh
  `python scripts/snapshot_supabase.py` first (see `AGENTS.md` "Supabase
  operating rules").
