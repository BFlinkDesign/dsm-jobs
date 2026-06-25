# PRD — dsm-jobs modern rebuild (lean: Astro static + solo-Claude backend)

**Date:** 2026-06-20 · **Status:** DRAFT — confirm D2/D3 before build.

## 1. Problem & goal
The PWA works but doesn't read "pro" (tacky gradient title, hand-rolled hex, no design system), and the front-end is a giant HTML string inside `find_admin_jobs.py`, which fights iteration. Rebuild the UI on a modern, maintainable stack to a **calm-premium phone-app** bar — **$0 platform cost, single best-in-class AI provider, nothing that breaks her installed PWA.**

## 2. Users & hard constraints
- **One end user:** phone-only single mom, stressed, zero scam tolerance; daytime/remote jobs. Look = **calm premium**, not an agency showcase.
- **Her PWA is installed at `https://bflinkdesign.github.io/dsm-jobs/` — the production URL MUST NOT change.**
- Public repo (no secrets committed). EDR/WDAC host → installs/builds run in a **cloud dev env / CI / sandbox**, not bare on the laptop.
- Perf: LCP ≤ 2.5s, INP ≤ 200ms (mobile p75); `prefers-reduced-motion`; WCAG.
- Preserve invariants: no guessed wage as a number; scams hidden; daytime gate (remote-exempt); XSS-safe; Rudy states only stored facts; verify rendered reality with vision (#7).

## 3. Target architecture (lean — no Vercel, no OpenRouter, no multi-provider)
- **Front-end:** **Astro (islands) + Tailwind** → **static → GitHub Pages, same URL** (`base:'/dsm-jobs'`). Free. Interactive islands only for search/filter + Rudy chat. Tasteful, reduced-motion-safe motion. *(This is where the "pro" look comes from — design craft, not the host.)*
- **Job data:** `find_admin_jobs.py` keeps all scam/filter/salary logic but emits **`jobs.json`**; Astro consumes it at build.
- **AI backend:** the **existing Supabase edge functions**, **SOLO provider = Anthropic Claude** — **Opus 4.8** (résumé writer), **Sonnet 4.6** (Rudy + the résumé critic). Keeps grounded-memory (#89) + the $25/$20 spend cap (#90). No durable-workflow platform — one user, short chats, the functions are plenty. Solo Claude = top quality, **no drop**.
- **Auth + DB:** **Supabase**, unchanged.
- **Cost:** Pages (free) + Supabase (free tier) + Anthropic tokens (capped at $25). **~$0 fixed.**

## 4. Separate track (NOT this app): your mcp-gateway
Gemini / Grok / other provider keys + secret-centralization live in **your personal MCP gateway for your own multi-model agent work** — distinct from the dsm-jobs app, which stays solo-Claude. Tracked separately; out of scope for this PRD.

## 5. Test strategy (TDD)
Failing test first, then implement.
- Python logic → existing **pytest** suite.
- Edge-function behavior → unit tests for the grounding (Rudy states only stored facts), the spend-cap gate (refuse at $25, fail closed).
- Front-end → component tests + the **camera vision-verify** (render + screenshot) carried forward.

## 6. Phased delivery (each a PR, preview-deployed, prod URL untouched until P4)
- **P0** — this PRD.
- **P1** — Astro+Tailwind scaffold + **design system** (tokens/type/motion) + **jobs view** from `jobs.json` → preview + pixel-verify. *(Kills the tacky title.)*
- **P2** — My corner + **Rudy** (real RUDY portrait) + auth, against the existing solo-Claude function.
- **P3** — résumé UI + the existing Opus/Sonnet loop wired + spend-cap/grounding verified.
- **P4** — PWA manifest/sw + full camera verify + **cutover to gh-pages (same URL).**

## 7. Provisioning needed (USER)
- A **cloud dev env** for the build (Claude Code on the web / container) — the EDR host can't cleanly `npm create astro` (WSL package network is firewalled; bare-host installs are the anti-pattern).
- `RESEND_API_KEY` + alert addresses (for the spend-cap email).
- **Nothing else** — no Vercel, no Gemini/Grok, no OpenRouter for the app.

## 8. In-flight reshuffle
- **#89 (grounding)** + **#90 (spend cap)** → stay in the Supabase functions (merge when ready).
- **#91 (goth theme)** → superseded by this rebuild → **close it**; the **RUDY portrait asset carries forward**.

## 9. Open decisions — CONFIRM
- **D2.** Confirm **calm-premium** look (not showcase).
- **D3.** OK to **close #91** (mascot carries into the rebuild)?
