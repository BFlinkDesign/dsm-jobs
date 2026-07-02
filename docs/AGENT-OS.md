# AGENT-OS — the doctrine

Portable: drop this file into any repo (written to be copied into PROMPTOS).
Not a manual — a set of **laws with receipts**, each proven by a real incident,
plus the procedures they compile into. The premise most people get wrong:
agent output quality is not a property of the model. It is a property of the
**system around the model** — briefs, gates, ledgers, and cadence. Build the
system and cheap models produce elite work; skip it and frontier models
produce confident garbage.

---

## Part I · The Laws

**1. Quality = min(brief, gate) — never max(model).**
A frontier model with a vague ask underperforms a mid-tier model with a
sharp brief and a hard gate. Spend on the brief and the gate first; the model
tier is the *last* knob. *Receipt: four mid-tier agents shipped a five-front
release green on first pass — because every brief carried a pre-done
diagnosis to file:line and literal verification commands.*

**2. Silence is the only fatal error.**
Audit any real incident list and one shape repeats: not wrong output —
**missing signal**. A skipped test that looks like a pass. An error branch
that renders nothing. A filter bypass with no canary. A CI job dead for ten
days with no one noticing. Loud failures cost minutes; silent ones cost
users. Therefore: every branch renders feedback, every skip announces itself,
every monitor watches failure signatures (not just success markers), every
weakened control gets a canary. *Receipt: a real user was locked out of her
account by an unhandled error hash that showed her a blank sign-in screen.*

**3. The repo is the brain; sessions are cache.**
Sessions die — interrupts, context limits, reclaimed containers. Anything
that lives only in a conversation is already lost. Decisions, failures,
risks, and pending work live in **files in the repo** (see Part V); a session
is a stateless worker that reads them, acts, and writes back. Design test:
*if this session vanished right now, could a cold session resume in five
minutes?* If no, you're storing state in the wrong place.

**4. Shrink, then route.**
The routing question is not "which model can do this?" — it's "**how small
can I make this before routing it?**" A task that needs a frontier model is
usually a task that hasn't been decomposed yet. The router's real job is to
make the expensive model unnecessary. Frontier time goes to the two things
that don't decompose: judgment under ambiguity, and verification of claims.

**5. Buy verification instead of intelligence.**
Generation is expensive and fallible; checking is cheap and mechanical.
Whenever you can turn "trust the model" into "check the output" — a
round-trip test, a CRC, a screenshot, a canary grep — do it and downgrade
the generator. This is the whole economic engine: **weak model + strong
gate ≥ strong model + no gate**, at a fraction of the cost. *Receipt: a
zero-dependency .docx writer was proven not by reading the code but by
parsing its output back through the app's own reader and validating CRCs
with an independent tool.*

**6. Merged ≠ live ≠ verified ≠ watched.**
Four different states, casually conflated as "done." Code lands (merged),
deploys (live), demonstrably works in production (verified), and will
*complain on its own* when it stops working (watched). A task is finished at
**watched**, not before. *Receipt: "Web Push shipped" sat in the docs as
"planned" while the deployed service worker was missing the push handler —
merged and even live, but never verified, and watched by no one.*

**7. Every failure buys a rule — exactly once.**
A failure you paid for is an asset: symptom → root cause → rule, appended to
the ledger, read by every future session. The only real waste is re-paying.
Corollary: a rule nobody reads is not a rule — wire ledger-reading into the
project's entry point (CLAUDE.md/AGENTS.md says "read ERRORS.md first").

**8. Trust is a ladder, not a switch.**
Autonomy is granted per-capability, one rung at a time:
`observe → propose → act-behind-gate → act-and-report`.
A capability climbs on track record (N clean executions at the current rung)
and drops one rung on any incident. Never grant "full autonomy" as a blanket
— grant *merge authority*, *deploy authority*, *spend authority* separately,
each with its own gate. *Receipt: merge authority was granted here only
after a full release cycle of gated, verified PRs — and it still carries the
act-and-report obligation.*

**9. Evidence beats theory.**
Before designing any fix, fetch the artifact that actually failed: the live
feed row, the real CI log, the actual redirect contract of the library, the
bytes on the deployed branch. Ten minutes of evidence kills hours of
plausible-but-wrong engineering. *Receipts: the "filter bug" was diagnosed
from the leaked production rows themselves; a "missing CSS link regression"
dissolved entirely once a clean rebuild proved it was stale build debris.*

**10. Design for the interrupt.**
Assume any agent can be killed at any moment mid-work (users interrupt,
sessions crash, fleets get cancelled). Crash-only design: checkpoint commits
at every verified state; every brief opens with "check `git status` — a
prior agent may have left partial work"; file-ownership slicing so a dead
agent never corrupts a live one's files. Recovery must be *relaunch*, never
*reconstruct*.

---

## Part II · The routing algorithm

Don't classify the task — run this procedure:

1. **Is it judgment or volume?** Judgment (ambiguity, architecture, security,
   root-cause on weird evidence, anything user-facing-risky) → frontier,
   effort high. Everything else, continue.
2. **Shrink it.** Decompose until each piece has: known files, known
   approach, checkable outcome. What remains undecomposable goes back to 1.
3. **Write the brief** (Part III). If you can't write the gate line, the
   piece isn't ready — back to 2.
4. **Route by residual risk, not difficulty:** touches auth/payments/deploy
   or hard-to-reverse state → workhorse + frontier review of the diff;
   mechanical + fully gated → cheapest tier, effort low; default → workhorse,
   effort medium.
5. **Escalation is part of the brief:** the executor must stop and hand up
   when scope exceeds the brief, two honest attempts fail, or evidence
   contradicts the diagnosis. An executor that guesses past its brief is a
   bug in *your* brief.
6. **De-escalate yourself:** if the frontier model is doing something a brief
   could fully specify, stop and write the brief. Kickoff budget spent on
   build work is the most common silent overspend.

## Part III · The brief compiler

A brief is compiled intent: source = what you want, target = a spec a
cheaper model executes deterministically. Eight mandatory clauses:

1. **Diagnosis** — root cause, file:line, evidence. Never make the executor
   rediscover what you know.
2. **Scope** — change this; do NOT touch that.
3. **Gates** — the literal commands that must pass, plus any render-and-look
   duty ("screenshot it and LOOK; iterate until it reads right").
4. **Style anchors** — "mirror the pattern at `<file:line>`".
5. **Environment traps** — sandbox quirks, shared tree, stale caches,
   network policy. (This clause prevents ~half of all executor failures.)
6. **Commit policy** — usually "do not commit/push; report."
7. **Escalation triggers** — the specific conditions to stop and hand up.
8. **Report shape** — verdict / changes / operator-actions, separated;
   raw findings, not narrative.

**Brief lint** (run mentally before spawning): Could a competent stranger
execute this with zero questions? Does every claim in it carry evidence?
Is the gate objective? Is failure loud? If any answer is no — fix the brief,
not the model tier.

## Part IV · The gate stack

Order gates cheapest-first; each layer catches what the previous can't:

1. **Static** — lint, typecheck, compile.
2. **Unit/regression** — including a **canary from every real incident**
   (the actual leaked row, the actual error hash — never synthetic
   approximations of a failure you have the real bytes for).
3. **Round-trip** — generated artifacts parsed back by an independent
   reader (ideally the system's own consumer + one third-party validator).
4. **Build** — full production build, from a **clean** output dir (stale
   debris fakes regressions; clean-room before diagnosing any build anomaly).
5. **Rendered** — a real browser screenshot that a decision-maker actually
   viewed. String-matching HTML is not seeing. No screenshot → say "not
   visually verified," never imply otherwise.
6. **Live** — after deploy, verify the deployed bytes (fetch the production
   branch/URL) and the user-visible behavior.
7. **Watched** — a standing monitor greps production for the failure class
   this change addressed. This is the gate that never un-runs.

Integration rule: executors run their *own* gates; the integrator re-runs
the **full** stack once. And the iron commit rule: **a test rides in the
same commit as the code it guards** — splitting them is how "verified
locally" becomes red CI.

## Part V · Ledgers (the system of record)

Four files, tiny on purpose, wired into the repo's entry point:

- **ERRORS.md** — append-only: symptom → root cause → **rule**. Read before
  every session.
- **GAPS.md** — risks nobody hit yet, each with a closing action and a
  priority. The difference between "quietly wrong" and "on the roadmap."
- **RUNBOOK.md** — pending work pre-compiled into Part-III briefs, so any
  cold session (any tier) can execute immediately.
- **CLAUDE.md / AGENTS.md** — invariants, commands, architecture, and the
  pointer that makes the other three get read.

Plus standing templates for recurring judgment (design brief, decision
matrix). Masters live in one place (PROMPTOS); projects instantiate.

## Part VI · Concurrency, interrupts, economics

- **Slice by file ownership.** Same file only with disjoint regions;
  integrator owns final state. Worktrees only when slices genuinely collide.
- **Checkpoint at every verified state.** Ephemeral environments reclaim;
  uncommitted verified work is unverified work you'll redo.
- **Fleet math:** wall-clock = slowest chain, not sum of stages — pipeline,
  don't barrier, unless a stage truly needs *all* prior results.
- **Bookend ratio:** healthy systems spend ~10–20% of tokens on frontier
  bookends (diagnose/brief/verify) and the rest on executors. If frontier
  share creeps up, briefs have gotten lazy; if it hits ~0%, nobody's
  verifying and Law 1 is about to collect.
- **Don't re-derive.** A decision made once is written down (Part V) and
  cited, not re-litigated. Re-explaining context across long chats is the
  invisible budget leak.

## Part VII · The cadence

- **Heartbeats:** scheduled workflows (daily scan, weekly source-health,
  health monitor) are the durable watchers — sessions die, cron doesn't.
- **Canary sweeps:** periodically grep *production output* for every failure
  class ever seen. New incident → new canary, same day.
- **Deploy watch:** after every merge, follow the pipeline to **live** and
  check the deployed bytes. Automate the watch (a one-shot poller), don't
  vibe it.
- **Retro on pause:** at every natural pause — what broke (→ERRORS), what's
  quietly wrong (→GAPS), what process change is reversible enough to just do
  now. Implement the reversible ones immediately; queue the rest as briefs.

## Part VIII · Named anti-patterns (the tells)

- **The Confident Ghost** — "done!" with no gate output attached. Demand the
  evidence or treat it as not done.
- **The Split Commit** — test and code landing separately. Red CI incoming.
- **The Polite Skip** — a test that skips quietly in the one environment
  where it matters. Make skips loud or make them run.
- **The Happy-Path Callback** — an integration handling only success shapes.
  The error branch is where users get hurt (Law 2).
- **The Stale Cache Panic** — debugging "regressions" in a dirty long-lived
  environment. Clean-room first (Law 9).
- **The Re-derivation Loop** — a session reasoning its way back to a decision
  already in the ledger. Cite, don't re-think.
- **The Blanket Grant** — "just do everything autonomously." Autonomy without
  per-capability gates is how one bad day undoes a year of trust (Law 8).
- **The Framework Reflex** — reaching for orchestration software when the
  bottleneck is brief quality. The doctrine is the framework; add software
  only when a recurring task's gates deserve a script.

## Part IX · Bootstrap (new repo, ten minutes)

1. Copy this file in. Create empty ERRORS.md / GAPS.md / RUNBOOK.md.
2. Add the entry-point pointer: "read the ledgers before working."
3. Stand up the gate stack floor: lint + tests + build in CI, on every PR.
4. Grant autonomy rung by rung as track record accrues (Law 8).
5. First real failure → first ERRORS entry + first canary. The system is
   now learning. Everything after that is compounding.
