# Plan — iPad/iPhone PWA redesign (wireframe-first, Relume++ process)

**Date:** 2026-06-22  
**Status:** PARTIALLY SHIPPED — Astro goth shell live on branch; see `docs/HANDOFF.md` for session closure. **Open:** collapsible `FilterSheet`, iPad layouts, camera per-device matrix.
**Goal:** Calm-premium mobile product for one end user — scam-vetted jobs, Rudy companion, résumé tailor — optimized for **iPhone + iPad**, built with a real design process and maintainable front-end.

---

## 1. Problem frame

| Constraint | Implication |
|---|---|
| One stressed phone/tablet user | IA must be obvious; no hunt-and-peck |
| Zero scam tolerance | Vetting stays server-side; UI never “warns and shows” |
| Installed PWA at fixed URL | `https://bflinkdesign.github.io/dsm-jobs/` must not break |
| Rudy + operator graphics | Character is a first-class asset, not emoji/SVG hack |
| Operator-maintained | Design tokens + component library; no CSS-in-Python |

**Out of scope:** Desktop-first layout, .NET rewrite, multi-tenant SaaS, Webflow runtime lock-in.

---

## 2. Device strategy (iPhone + iPad)

### 2.1 Primary viewports (design + camera)

| Device | Logical width | Design frame (Figma) | Notes |
|---|---|---|---|
| iPhone SE / mini | 375 | iPhone 13 mini | Minimum tap-target floor |
| iPhone Pro | 390 | iPhone 15 Pro | **Primary design target** |
| iPhone Pro Max | 430 | iPhone 15 Pro Max | Longer scroll; same column |
| iPad mini | 744 | iPad mini 6 | 2-col job grid optional |
| iPad Air / Pro 11" | 820 | iPad Pro 11" | Split layouts start here |
| iPad Pro 12.9" | 1024 | iPad Pro 12.9" | Max content width cap |

### 2.2 Layout rules

- **Phone:** single column, bottom tab bar (5 tabs), full-bleed cards, `env(safe-area-inset-*)` on header + tab bar.
- **iPad portrait:** same IA; content `max-width: 720px` centered OR 2-column job grid with filters in sticky left rail (decision D4).
- **iPad landscape:** prefer **sidebar nav** (icons + labels) + main content — bottom bar feels wrong at 1024×768.
- **Orientation:** change `manifest.webmanifest` from `portrait` → `any` once iPad layouts ship.
- **Touch:** 44×44pt minimum; 48×48 for primary CTAs (Apply, Call, Talk to Rudy).

### 2.3 Performance budget (mobile p75)

- LCP ≤ 2.5s on iPhone over LTE
- INP ≤ 200ms
- Total JS ≤ 80kb gzip (islands only where needed)
- Fonts: max 2 families, `font-display: swap`, preload display face only
- Rudy `rudy.jpg` ≤ 60kb WebP/AVIF + JPEG fallback

---

## 3. Design process (Relume++ — wireframe to ship)

Relume’s strength: **sitemap → wireframe → style → components → handoff**. We adopt that sequence but **do not** ship on Webflow — Figma is source of truth; Astro/Vite is implementation.

```
┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────┐   ┌─────────────┐
│ 0. Discover │ → │ 1. Sitemap   │ → │ 2. Wireframe│ → │ 3. Tokens    │ → │ 4. Hi-fi    │
│  (FigJam)   │   │  (Relume/    │   │  (Figma low │   │  (Variables  │   │  (Figma +   │
│             │   │   manual)    │   │   fi kit)   │   │   + Type)    │   │  Rudy art)  │
└─────────────┘   └──────────────┘   └─────────────┘   └──────────────┘   └─────────────┘
       │                                                                    │
       ▼                                                                    ▼
┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────┐
│ 5. Prototype│ → │ 6. Design QA │ → │ 7. Build    │ → │ 8. Camera   │
│  (Figma +   │   │  (checklist + │   │  (Astro +   │   │  (Playwright│
│   motion)   │   │   stakeholder)│   │   tokens)   │   │   per device)│
└─────────────┘   └──────────────┘   └─────────────┘   └──────────────┘
```

### Phase 0 — Discovery (1–2 sessions, FigJam)

**Tools:** FigJam (or Miro), existing `docs/superpowers/` specs as input.

**Deliverables:**
- `docs/design/00-persona-jobs-to-be-done.md` — one user, three anxieties (scam, unqualified, overwhelm)
- `docs/design/01-user-flows.fig` — 6 critical flows (see §4)
- Emotional tone board: **calm premium editorial** — not agency showcase, not purple job-board cliché

**Exit criteria:** Flows agreed before pixels.

### Phase 1 — Sitemap & IA (Relume-assisted)

**Tools:**
- **Relume Site Builder** — paste brief → AI sitemap → export to Figma (accelerator, not gospel)
- Manual pass in FigJam to align with safety invariants

**Deliverable:** `docs/design/02-sitemap.md` + Figma page **「Sitemap」**

**Proposed IA (5 tabs + 2 overlays):**

| Route / tab | Purpose |
|---|---|
| **Jobs** (default) | Scam-checked feed, search, filters, commute radius |
| **Today** | Low-pressure daily note + affirmation (no job list pressure) |
| **My apps** | Applied / saved / hidden + **follow-up** Call/Email/remind |
| **My corner** | Account, résumé, About me quiz, **Rudy launcher** |
| **Help** | Safety rules, crisis lines, how vetting works |
| **Lock** (signed out) | Perks + sign-in; crisis lines always visible |
| **Rudy overlay** | Full-screen companion (modal, not a tab) |
| **Résumé tailor** | Sheet/modal from job card or corner |

**Exit criteria:** Every screen has a one-line job; no orphan pages.

### Phase 2 — Wireframes (low-fi, mobile-first)

**Tools:**
- **Relume Figma Kit** (wireframe components) OR **FigJam → Figma** frames
- **Figma device frames:** iPhone 15 Pro + iPad Pro 11" minimum

**Deliverable:** Figma page **「Wireframes — Lo-fi」** — grayscale only, no brand color yet.

**Wireframe set (minimum frames):**

1. Jobs — list + collapsed filters  
2. Jobs — expanded filter sheet  
3. Job card — states: pay listed / unlisted / verified employer  
4. Job card — applied (Call, Email, remind chips)  
5. My apps — empty / with follow-ups  
6. My corner — signed out teaser / signed in  
7. Rudy — intro card + full-screen chat (empty + with bubbles)  
8. Résumé tailor — paste + one-tap from full description  
9. Lock screen  
10. Help + crisis block  
11. iPad — Jobs 2-col + sidebar nav (landscape)  

**Exit criteria:** Stakeholder sign-off on IA and tap paths; camera shot list derived from frames.

### Phase 3 — Design system / tokens

**Tools:**
- **Figma Variables** (collections: primitive → semantic → component)
- **Tokens Studio** plugin → export `design/tokens.json`
- Reference: Atkinson Hyperlegible (body) + one editorial serif (Fraunces, Newsreader, or Instrument Serif — pick in hi-fi)

**Deliverable:**
- Figma page **「Design system」**
- `design/tokens.json` → CSS via Style Dictionary or Tailwind `@theme`

**Token groups:**
- Color: surface stack (paper/card/surface/elevated), ink primary/secondary, accent (restrained), semantic (safe/hidden/crisis)
- Type: display / body / caption / mono; scale 15–28 mobile
- Space: 4px base grid
- Radius: card 16, button 12, sheet 20 top
- Shadow: layered (no neon glow)
- Motion: duration 120/200/260ms; easing; `reduced` variants documented

**Exit criteria:** Contrast WCAG AA on all text pairs; tap targets annotated.

### Phase 4 — Hi-fi visual design

**Tools:** Figma; operator **`web/rudy.jpg`** + future expression variants in `design/assets/rudy/`

**Deliverable:** Figma page **「Hi-fi — iPhone」** + **「Hi-fi — iPad」**

**Must show:**
- Rudy circular avatar with subtle ring (character art visible)
- Job cards: trust badge, commute chip, pay invariant #1 copy
- Editorial headline treatment (not gradient WordArt)
- Bottom tab bar + iPad sidebar variant

**Exit criteria:** Side-by-side with tone board; “calm premium” not “tacky.”

### Phase 5 — Prototype & motion

**Tools:** Figma interactive prototype; optional **Motion One** spec doc for dev.

**Flows to prototype:**
- Install PWA hint → Jobs → expand card → Apply (gated) → sign in  
- Apply job → My apps → follow-up remind  
- Corner → Talk to Rudy → voice/text → crisis path  
- Tailor résumé from job with full description  

**Motion principles:**
- Sheet rise 260ms; tab cross-fade; Rudy overlay slide-up
- `prefers-reduced-motion: reduce` → instant or opacity-only
- No infinite decorative animation on job list

**Exit criteria:** Tap-through without confusion on iPhone frame.

### Phase 6 — Design QA (pre-build)

**Checklist:**
- [ ] Crisis lines on Lock + Help + Rudy footer  
- [ ] No guessed wage as number in any mock state  
- [ ] Scam-hidden jobs absent from all frames  
- [ ] iPad landscape has reachable primary actions  
- [ ] Rudy art legible at 40px and 54px diameters  

**Tool:** Figma inspect + export redlines to `docs/design/03-redlines.md`

### Phase 7 — Build (see §5)

### Phase 8 — Camera verification

Extend `verify/camera.py` with device profiles:

| Shot ID | Viewport | Frame |
|---|---|---|
| `iphone-jobs` | 390×844 | Jobs list |
| `iphone-corner-rudy` | 390×844 | Corner + Rudy card |
| `iphone-rudy-chat` | 390×844 | Rudy overlay |
| `iphone-apps-followup` | 390×844 | My apps with reminders |
| `ipad-jobs-portrait` | 820×1180 | 2-col or centered |
| `ipad-jobs-landscape` | 1180×820 | Sidebar nav |

Compare against Figma exports (perceptual diff optional later).

---

## 4. Critical user flows (traceability)

| ID | Flow | Success metric |
|---|---|---|
| F1 | Open app → see only safe jobs | No scam cards; header shows checked count |
| F2 | Filter by commute + remote | Chips persist; remote always passes |
| F3 | Apply → save contact → remind | Call/Email native; 3/5/7-day chip |
| F4 | Tailor résumé from job | Full `descFull` one-tap; no fabrication |
| F5 | Talk to Rudy when stressed | Grounded replies; crisis → 988 + YLI |
| F6 | Signed out → understand value | Lock perks; crisis visible; no dead Google button |

---

## 5. Technical architecture (implementation)

### 5.1 Repo layout (target)

```
dsm-jobs/
├── find_admin_jobs.py          # scanner only → emits data
├── data/
│   └── jobs.json               # built artifact (safe jobs + meta)
├── app/                        # NEW Astro (or Vite) front-end
│   ├── src/
│   │   ├── components/         # JobCard, TabBar, RudyOverlay, …
│   │   ├── layouts/            # AppShell.astro
│   │   ├── pages/              # index (SPA shell) or MPA islands
│   │   ├── styles/
│   │   │   └── tokens.css      # generated from design/tokens.json
│   │   └── scripts/            # client TS (filter, storage, portal)
│   ├── public/
│   │   ├── rudy.jpg
│   │   ├── manifest.webmanifest
│   │   └── sw.js
│   └── astro.config.mjs
├── design/
│   ├── tokens.json
│   ├── figma/                  # exported PDF/PNG reference (not auto-code)
│   └── assets/rudy/
├── supabase/functions/         # unchanged: companion, resume-tailor
└── verify/
    └── camera.py               # multi-viewport shots
```

### 5.2 Build & deploy pipeline

1. CI: `python find_admin_jobs.py` (live keys) → `data/jobs.json` + audit CSV  
2. CI: `cd app && npm ci && npm run build` → `app/dist/`  
3. CD: push `app/dist/` to `gh-pages` (same URL)  
4. Pre-publish gate: camera 8+ checks per viewport  

### 5.3 Preserved invariants

1. No guessed wage as number  
2. Pay unlisted is normal  
3. Scams hidden, not labeled  
4. Attainability filter (server)  
5. XSS: `esc()` / `safeUrl()` in all dynamic render paths  
6. Rudy: only stored facts; crisis routing  
7. Vision-verify before deploy  

### 5.4 iPad-specific implementation

- CSS: `@media (min-width: 744px)` — 2-col job grid, wider gutters  
- CSS: `@media (min-width: 1024px) and (orientation: landscape)` — `.nav-sidebar` replaces `.nav-bottom`  
- JS: `matchMedia` syncs active tab; no separate routes required (SPA shell)  
- PWA: `"display": "standalone"`, `"orientation": "any"`  

---

## 6. Component inventory (Figma ↔ code)

| Component | Variants | Notes |
|---|---|---|
| `AppShell` | phone / ipad-sidebar | Safe areas, meta, SW register |
| `TabBar` / `SideNav` | 5 items | Jobs default |
| `ScamBadge` | — | Header + card trust |
| `JobCard` | listed-pay / unlisted / applied | Apply CTA gated |
| `FilterSheet` | collapsed / open | Labeled rows (Filter / Type / Commute) |
| `FollowUpBar` | call / email / remind | Applied state only |
| `LockScreen` | — | Crisis always |
| `RudyCard` | signed-out / signed-in | `rudy.jpg` avatar |
| `RudyOverlay` | chat / listening / speaking | Full-screen modal |
| `ResumeCard` | empty / uploaded | Corner |
| `TailorSheet` | paste / one-tap | From job `descFull` |
| `CrisisStrip` | compact / expanded | Help, Lock, Rudy footer |

---

## 7. Phased delivery (PRs)

| Phase | PR scope | Design gate | Ship risk |
|---|---|---|---|
| **P0** | This plan + Figma project + sitemap/wireframes | Stakeholder sign wireframes | None |
| **P1** | `design/tokens.json` + token CSS + Astro scaffold + Jobs view from `jobs.json` | Hi-fi Jobs iPhone approved | Preview branch |
| **P2** | iPad layouts + sidebar nav + camera viewports | Hi-fi iPad approved | Preview |
| **P3** | My apps (follow-ups) + corner + auth shell | Wireflow F3 | Preview |
| **P4** | Rudy overlay + `rudy.jpg` + rename | Rudy frames approved | Preview |
| **P5** | Résumé tailor UI wired to edge fn | F4 | Preview |
| **P6** | PWA manifest/sw + cutover `gh-pages` same URL | Camera all green | **Production** |

Each PR: camera screenshots attached; no merge without vision pass.

---

## 8. Tooling stack (recommended)

| Job | Tool | Why |
|---|---|---|
| Sitemap AI | **Relume** (export to Figma) | Fast IA; edit manually after |
| Wireframes | **Relume Figma kit** or **FigJam** | Mobile component vocabulary |
| Hi-fi + tokens | **Figma** + **Variables** + **Tokens Studio** | Industry standard handoff |
| OSS alternative | **Penpot** | If Figma seat cost matters |
| Font pairing | Google Fonts or self-host | Atkinson + one serif |
| Image opt | Squoosh / `sharp` CLI | WebP/AVIF for Rudy |
| Build | **Astro 5** + **Tailwind v4** | Static, islands, tokens |
| Client logic | TypeScript (vanilla or petite-vue) | No heavy React runtime |
| Motion | **Motion One** (optional) | Small, PWA-safe |
| QA | **verify/camera.py** + Playwright | Deterministic pixels |
| Design drift | Figma PNG export vs camera diff | Future: perceptual hash |

**Not recommended:** Webflow runtime, Locofy/Anima code export, Framer as production host, Blazor WASM.

---

## 9. Test strategy

| Layer | Tests |
|---|---|
| Scanner | Existing `pytest` — unchanged logic |
| Tokens | CSS parse via Lightning CSS (existing `verify/css/`) |
| Components | Vitest + happy-dom for filter/sort/esc |
| Invariants | `tests/test_find_admin_jobs.py` ported to app helpers |
| Rudy / AI | Deno tests + weekly behavioral eval (credit-aware skip) |
| Visual | Camera per §3 Phase 8 |
| iPad | Two viewports minimum in CI |

---

## 10. Open decisions (confirm before P1 build)

| ID | Question | Recommendation |
|---|---|---|
| **D1** | iPad job list: **centered single column** vs **2-column grid**? | 2-col from 744px — more “frontier” on tablet |
| **D2** | Editorial serif: **Fraunces** vs **Newsreader** vs **Instrument Serif**? | Newsreader — calm, readable at 17px |
| **D3** | Accent direction: **oxblood** vs **muted plum** vs **warm clay**? | Warm clay + charcoal — not purple, not red-aggressive |
| **D4** | Relume subscription for sitemap sprint? | One month OK if it speeds IA; don’t bind export to Webflow |

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Figma ↔ code drift | Tokens JSON single source; camera diff |
| iPad landscape nav regression | Dedicated camera shots; QA checklist |
| LCP from fonts + Rudy | Preload one font; AVIF avatar |
| Rebuild stalls mid-flight | P1 ships Jobs-only preview; old PWA until P6 |
| 69 stale branches confuse | Delete merged branches after P6 |

---

## 12. Immediate next actions (operator)

1. Create Figma file: **「dsm-jobs — 2026 mobile redesign」**  
2. Run Relume sitemap from brief in §1 (paste → edit → export)  
3. Wireframe iPhone frames 1–10 (§3 Phase 2)  
4. Confirm D1–D4  
5. Open P0 PR: add `design/` folder + this plan + empty Figma link in README  

---

*End of plan.*
