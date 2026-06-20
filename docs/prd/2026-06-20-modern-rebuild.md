# PRD — dsm-jobs modern rebuild (Astro · Tailwind · Vercel Workflow DevKit)

**Date:** 2026-06-20 · **Status:** DRAFT — decisions D1–D3 must be confirmed before build.

## 1. Problem & goal
The PWA works, but (a) it doesn't read "pro" (tacky gradient title, hand-rolled hex, no design system) and (b) the front-end is generated as a giant HTML string inside `find_admin_jobs.py`, which fights design iteration. Rebuild the UI on a modern, maintainable stack to a **calm-premium phone-app** bar, and modernize the AI backend to **durable workflows** with multi-provider routing and a spend cap — **without breaking the live installed PWA.**

## 2. Users & hard constraints
- **One end user:** phone-only single mom, stressed, zero scam tolerance; daytime/remote jobs. Target look = **calm premium**, NOT an agency showcase.
- **Her PWA is installed at `https://bflinkdesign.github.io/dsm-jobs/` — the production URL MUST NOT change** (a host/domain change breaks her installed app).
- Public repo (no secrets committed). IT-managed EDR/WDAC host → heavy installs + deploys run in **WSL/CI/sandbox**, never bare-installed.
- Perf budget: LCP ≤ 2.5s, INP ≤ 200ms (mobile p75); `prefers-reduced-motion` respected; WCAG contrast/tap-targets.
- Preserve load-bearing invariants: no guessed wage shown as a number; scams hidden not labeled; daytime gate (remote-exempt); XSS-safe rendering; Rudy states only stored facts (no confabulation); verify rendered reality with vision (invariant #7).

## 3. Target architecture
- **Front-end:** **Astro (islands) + Tailwind** → **static build → GitHub Pages at the same URL** (`base: '/dsm-jobs'`). Interactive islands only where needed (search/filter, Rudy chat). Tasteful, reduced-motion-safe motion.
- **Job data:** `find_admin_jobs.py` keeps ALL scam/filter/salary logic but emits **`jobs.json`** (instead of HTML). Astro consumes it at build time.
- **AI backend (durable):** **Vercel Workflow DevKit** — `DurableAgent` for **Rudy** (companion) and a **résumé workflow** (Opus-write → Sonnet-critique loop, retryable + streamed). Existing pieces become *steps* inside these workflows: grounded-memory facts (#89), the $25/$20 **spend cap** (#90) as a pre-step gate, and **multi-provider routing** (D — Anthropic/Gemini/Grok via model strings / AI gateway).
- **Auth + DB:** **Supabase unchanged** (email auth, RLS, `user_profile`, `chat_messages`, `ai_spend_ledger`).
- **Topology:** Pages (front-end) + Vercel (durable AI) + Supabase (auth/db). Front-end calls Vercel workflow endpoints over CORS.

## 4. Test strategy (TDD)
Write the failing test first, then implement.
- **Steps = plain functions** → unit-test directly (`"use step"` is a no-op without the compiler).
- **Durable workflows** → `@workflow/vitest` integration tests (hooks/sleep/retries): e.g. *"résumé workflow runs the critic ≤ 2 rounds and stops when clean"*, *"spend cap refuses at $25 and fails closed on ledger error"*, *"Rudy never asserts a fact not in KNOWN FACTS"*.
- **Python logic** → existing pytest suite (unchanged).
- **Front-end** → component tests + the **camera vision-verify** (render + screenshot) carried forward.

## 5. Phased delivery (each a PR, preview-deployed, prod URL untouched until P4 cutover)
- **P0** — this PRD + decisions locked + provisioning.
- **P1** — Astro+Tailwind scaffold + **design system** (tokens/type scale/motion) + **jobs view** reading `jobs.json` → preview + pixel-verify. (Kills the tacky title here.)
- **P2** — My corner + **Rudy DurableAgent** + auth + the real RUDY portrait.
- **P3** — **résumé durable workflow** + multi-provider (D) + spend-cap gate, all TDD.
- **P4** — PWA manifest/sw + full camera/pixel verify + **cutover to gh-pages (same URL)**.

## 6. Provisioning needed (USER — I can't do these from the EDR host)
- **Vercel account + project** (for Workflow DevKit) — login/CLI is yours.
- **Provider keys**: Anthropic (have) + **Gemini + Grok** for D → Vercel/Supabase secrets, never committed.
- I run `npm create astro` / `npm i workflow tailwindcss` etc. in **WSL/CI**, not bare on the host.

## 7. Reshuffle of in-flight work
- **#89 (grounding)** and **#90 (spend cap)** → absorbed as **steps inside the durable workflows** (logic preserved, re-homed). Keep the branches as the reference implementation.
- **#91 (goth theme)** → **superseded** by this rebuild → close it. The **RUDY portrait asset carries forward**.

## 8. Open decisions — CONFIRM before build
- **D1.** Add **Vercel** as the AI-workflow backend (alongside Supabase auth/db + Pages front-end)? *(rec: yes — it's what the Workflow DevKit needs, and it keeps her URL.)*
- **D2.** Confirm **calm-premium** look (not the Relume showcase).
- **D3.** OK to **absorb #89/#90 into the durable workflows and close #91**?
