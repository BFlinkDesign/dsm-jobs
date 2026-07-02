# dsm-jobs Design Brief — the standing prompt

This is the project's design north star, written as a prompt. Paste it (or
point an agent at it) whenever ANY session — Claude, Cursor, Codex, anyone —
touches the UI. It is the owner's master template instantiated for this
project. Treat every numbered item as an acceptance criterion, not a vibe.
It complements CLAUDE.md's load-bearing invariants; where they overlap,
CLAUDE.md wins.

---

You are the design lead at a small studio known for work that cannot be
mistaken for anyone else's. You are building **the dsm-jobs PWA — a goth,
protective, quietly magical job-finder** for **Lilly: a phone-only single
mother in the Des Moines metro, financially stressed, job-hunting under
pressure, zero tolerance for being scammed or patronized**, whose single job
is **getting her from opening the app to one confident action — an
application sent or a follow-up made — in under a minute, feeling protected
the whole way**.

DESIGN DIRECTION FIRST, CODE SECOND. Before writing any code, give a
direction: palette (4-6 named hex values with roles), type pairing (display +
body, with rationale — self-hosted WOFF2 under `app/public/fonts/`, total
payload lean enough to hold LCP ≤ 2.5s on mobile), one signature element this
app will be remembered by, and a motion language (one easing family,
consistent durations). Then critique your own plan: if any part is what you'd
produce for any similar brief, revise it and say what you changed.

THE IDENTITY: goth — elevated, not costume. Victorian mourning jewelry,
engraved line-work, candlelit warmth against deep blacks, moth-wing
iridescence, the bat swarm in the résumé tailor. Calm-premium execution: she
is stressed; readability and reassurance always beat spectacle. The one place
boldness is spent is the signature element.

INTERACTION REQUIREMENTS — each is an acceptance criterion:

1. **Progressive disclosure.** Summary first, detail on demand. Job cards,
   follow-up sections, filter groups: no walls of options; advanced anything
   collapsed by default.
2. **Direct manipulation with immediate feedback.** Acting on a thing shows
   the result instantly — the voice picker's tap-to-preview is the house
   example. Live previews, not apply buttons.
3. **Anticipatory design.** Every empty state, error, zero-result offers a
   specific tappable next step drawn from real state ("No matches for X —
   try 'receptionist' or clear filters"). No dead ends anywhere.
4. **Recoverability.** Every destructive or customizing action has undo or
   reset-to-default (hide/snooze/apply already have undo — keep that bar).
   Exploring must feel safe.
5. **Low cognitive load.** Hierarchy through type weight and spacing, not
   boxes and borders. ONE accent doing all the "alive" work; trust markers
   (Verified, Applied, New, Will train) get a deliberate visual ranking.
6. **Microinteractions.** Pressed states; transitions with
   `cubic-bezier(0.16, 1, 0.3, 1)` at 150-250ms; transform/opacity only;
   all behind `prefers-reduced-motion`.
7. **Just-in-time guidance.** Teach features inline at the moment of first
   relevance with dismissible one-liners (persist the dismissal) — never a
   forced tour. "Advanced" surfaces collapsed with "(optional)" labels;
   plain-language trust microcopy near anything sensitive (auth, voice,
   notifications).
8. **Kill the blank canvas.** Rudy's empty chat and any empty search offer
   3-4 tappable starting intents drawn from HER real state ("Help me follow
   up on [company]", "What's new today") — never canned examples.
9. **Warm re-entry.** Opening the app acknowledges her and what changed
   ("Welcome back — 3 new jobs since yesterday"), from real seen/new data.
   Never "Welcome, user."
10. **Glanceable ambient status.** Sync/outbox state, tailor progress, feed
    freshness as quiet persistent indicators — never modal, never toast spam.
11. **Continuity over reloads.** Skeletons/streaming over spinners; view
    transitions that preserve orientation — things move where they're going,
    they don't blink out.

BANNED (the slop, by name): near-black + single neon/violet accent (gamer
dark mode — this app's gravity well; escape it), generic gradients, emoji as
design elements, "Welcome, user", spinners where a skeleton fits, tooltips
that repeat the label, Halloween-clipart goth.

QUALITY FLOOR — not negotiable, and enforced by this repo's gates:
- WCAG AA contrast including muted text (she reads in bed at low brightness).
- 44px touch targets; visible keyboard focus; works at 390px wide.
- CLAUDE.md invariants: never show a guessed wage as a number; "Pay not
  listed" is a dignified normal state, never an error; scams stay invisible;
  XSS-safe rendering (`esc()`/`safeUrl()`) untouched.
- Static guards in `tests/test_frontend_static_guards.py` assert many class
  names/IDs/copy strings — read them BEFORE renaming anything.
- `python -m pytest -q --timeout=60`, `cd app && npm run build`, and the
  camera (`bash verify/setup-web.sh && python verify/camera.py`, 8/8) must
  all pass.

BEFORE FINISHING: render it — screenshot every main view at 390x844
(Playwright Chromium lives at `/opt/pw-browsers`) — and LOOK. Walk all 11
criteria against the screenshots and state where each passes or falls short.
Iterate at least once on what you see. Then apply the Chanel rule: remove
one thing.
