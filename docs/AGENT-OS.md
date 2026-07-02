# AGENT-OS — a portable operating system for AI-agent work

Project-agnostic. Drop this file into any repo (it was written to be copied
into PROMPTOS). It encodes one idea: **frontier models for judgment, workhorse
models for volume, deterministic gates between them.** You don't need custom
infrastructure to get elite results — you need this document followed.

The industry words for what this is (so you can search and talk shop):
**planner–executor–verifier** architecture · **orchestrator–workers** ·
**model routing / cascading** ("right-size the model to the task") ·
**context engineering** (what the model sees is the product) ·
**eval/acceptance gates** (deterministic pass/fail, not vibes) ·
**agentic harness** (the loop + tools around the model).

---

## 1 · The routing table (which model, when, what effort)

| Task shape | Tier | Effort | Why |
|---|---|---|---|
| Kickoff: audit, architecture, root-cause on a weird bug, security design, anything ambiguous | Frontier (Opus/Fable-class) | high | Judgment is the bottleneck; a wrong plan wastes every downstream token |
| Well-briefed build: implement X in files Y with gates Z | Workhorse (Sonnet-class) | medium | With a good brief, output quality converges; cost doesn't |
| Mechanical: rename, port a pattern, fixture generation, doc formatting | Fast/cheap (Haiku-class) | low | Zero judgment required |
| Verify/finalize: integrate slices, run gates, look at screenshots, write the PR story, retro | Frontier | medium–high | Catching a subtle miss here is worth 100× its cost |
| Adversarial review of a "done" claim | Workhorse ×2–3, different lenses | medium | Independent skeptics beat one genius rubber-stamping |

**Escalation triggers** (builder must stop and hand up, stated in its report):
touches >3 subsystems · requires an auth/data-model/product decision · two
honest attempts failed · evidence contradicts the brief.
**De-escalation trigger:** if you (frontier) are doing something a brief could
fully specify — you're burning kickoff budget on build work; write the brief.

## 2 · The task brief (the contract that makes cheap models good)

Every delegated task gets ALL of these. A brief missing one is not ready:

1. **Pre-done diagnosis** — files:lines, the actual root cause, evidence.
   Never make the builder rediscover what you know.
2. **Exact scope** — what to change AND what not to touch.
3. **Acceptance gates** — the literal commands that must pass, and any
   render-and-look requirement (screenshots the builder must view itself).
4. **House style pointers** — "mirror the pattern at `<file:line>`".
5. **Environment traps** — sandbox quirks, shared working tree, "run
   `git status` first; a prior agent may have left partial edits".
6. **Commit policy** — usually "do NOT commit/push; report back."
7. **Escalation triggers** — when to stop and say so instead of guessing.
8. **Report format** — root-cause verdict / changes / operator actions,
   separated. Raw data, not prose padding.

## 3 · Deterministic gates (trust nothing, verify everything)

- **Green means the machine said so:** lint + typecheck + full test suite +
  build, run by the integrator, not just claimed by the builder.
- **A test rides in the same commit as the code it guards.** Splitting them is
  how "verified locally" turns into red CI.
- **Render and look.** For anything visual, a human-viewable artifact
  (screenshot, camera run) that someone actually viewed. String-matching HTML
  is not seeing.
- **Canary tests from real failures:** every field-reported leak/bug becomes a
  regression test using the *actual* offending data.
- **Silence is not success:** a skipped test, an empty result, a monitor that
  only greps the happy path — all read as "fine" while broken. Make skips loud
  and watch failure signatures, not just success markers.
- **Clean-room rule:** before diagnosing any build-output anomaly, clean the
  output dir and rebuild. Long-lived sandboxes accumulate stale artifacts that
  fake regressions.

## 4 · Token economy (why this pattern is cheap)

- Frontier bookends are ~10–20% of tokens; workhorse volume is the rest.
- **Parallelize independent slices** (background subagents), one integrator.
  Slice by file-ownership to avoid merge collisions; shared files get
  region-disjoint edits or sequencing.
- **Don't re-derive:** decisions made once are written down (see §5) and
  referenced, not re-litigated. Long chats re-explaining context are the
  silent budget killer.
- **Checkpoint commits** in ephemeral environments — losing verified work to a
  reclaimed container costs a full rebuild.
- Give builders **targeted verification** (their own tests) and keep the
  full-suite gate at integration — running everything everywhere doubles cost
  for no signal.

## 5 · The standing-docs loop (institutional memory)

Four small files per project; agents read them before working, append after:

- **CLAUDE.md / AGENTS.md** — invariants, commands, architecture. The spec.
- **ERRORS.md** — append-only: symptom → root cause → **rule adopted**.
  Failures become policy exactly once.
- **GAPS.md** — risks nobody hit yet, each with a closing action, prioritized.
  The difference between "quietly wrong" and "on the roadmap".
- **RUNBOOK.md** — remaining work pre-briefed to §2 standard, so any session
  (any model) can pick up a task cold.

Plus standing prompt templates for recurring judgment: a **design brief**
(UI identity + acceptance criteria) and a **decision matrix** (high-stakes
choices). Instantiate per project; keep masters in PROMPTOS.

## 6 · Real-world pain points, pre-solved

| Pain | Rule |
|---|---|
| Interrupting the lead cancels its subagent fleet | Relaunch is cheap; fold the new info into better briefs; every brief starts with "check `git status` for partial prior work" |
| Two agents edit one file | Slice by file ownership; same file only with disjoint regions; integrator owns final state |
| Builder claims visual success without looking | Gate: screenshot must exist AND be viewed; "couldn't run the browser" is an acceptable honest answer, a fake pass is not |
| Sandbox can't reach the network/browser | Push verification to CI (a workflow that renders and publishes artifacts to a branch git can fetch) |
| "Done" that isn't deployed | Deployment is a step with its own gate, named in the plan; merged ≠ live |
| Users locked out by silent error branches | Every callback/redirect surface renders its failure case; tests assert the failure path |
| Model/effort choice paralysis | Use §1's table; when torn between tiers, brief quality matters more than tier — fix the brief first |
| Frontier budget exhaustion | Frontier writes briefs + verifies; if you're out of frontier budget mid-build, the workhorse can still finish against gates |

## 7 · What to build next (in priority order — mostly *don't build*)

1. **Nothing custom yet.** This document + the standing-docs loop IS the
   unified system. Frameworks (LangChain/CrewAI/AutoGen-style) add plumbing
   you don't need while briefs are the bottleneck.
2. A **PROMPTOS repo layout**: `masters/` (this file, design brief master,
   decision matrix master, brief template), `per-project/` (instantiated
   copies), `retros/` (cross-project ERRORS/GAPS rollups so lessons transfer).
3. **Eval-before-scale:** when a task type recurs (e.g. "add a provider"),
   turn its gates into a script so the workhorse self-checks.
4. Only when volume genuinely demands it: an **auto-router** — a small prompt
   that classifies incoming tasks against §1's table and emits the §2 brief
   skeleton. (That's a prompt, not software.)

## 8 · Questions you should be asking (you asked what to ask)

- "What's the **acceptance gate** for this task?" — if you can't name one, the
  task isn't ready to delegate.
- "What did the last failure **teach the system**?" — if ERRORS.md didn't
  grow, the lesson will be re-paid.
- "Is this **judgment or volume**?" — the only routing question that matters.
- "What would make this task safe for a *cheaper* model?" — usually: a better
  brief, a fixture, or a gate. Rarely: a smarter model.
- "Merged, or **live**?" — always chase the deploy.
