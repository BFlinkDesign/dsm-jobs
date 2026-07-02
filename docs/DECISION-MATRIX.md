# Decision matrix — the standing prompt for high-stakes choices

Companion to `docs/DESIGN-BRIEF.md` (the design master template). Paste this
(or point an agent at it) before any hard-to-reverse decision on this project:
provider/API adoption, auth flow changes, data-model migrations, paid
services, anything touching the end user's trust or installed PWA.

---

> You are a decision analyst. MY DECISION: [2–4 sentences]. OPTIONS: [2–5].
> CONSTRAINTS: [timeline/budget/team/dependencies]. TOP PRIORITIES: [3].
> Produce: (1) weighted criteria matrix — 6–8 criteria including 3 I
> overlooked, weights totaling 100%, scores 1–10, show the arithmetic
> computed by code; (2) top-3 risks per option with likelihood/impact and a
> specific mitigation; (3) 6-month and 2-year second-order effects;
> (4) reversibility score 1–10 per option with reasoning; (5) pre-mortem on
> the winner — three specific failure narratives; (6) recommendation with a
> "proceed if…" condition and a "reconsider if…" trigger, ending in one
> actionable sentence.

---

Project constants to fold into CONSTRAINTS/PRIORITIES every time:
- End user is phone/tablet-only (iPad Safari + installed PWA); zero scam
  exposure; realistic entry-level targeting (CLAUDE.md's three constraints).
- Repo is PUBLIC; runtime is stdlib-only Python + a dependency-lean Astro app.
- The installed PWA's URL must never break (repo name = URL path).
- Free-tier bias: Supabase free project, GitHub Actions, no paid APIs without
  an explicit decision.
