// Supabase Edge Function: tailor her real resume to one job posting.
//
// Deploy (after the Supabase project exists):
//   supabase functions deploy resume-tailor
//   (uses the same ANTHROPIC_API_KEY project secret as the companion)
//
// THE ENGINE (self-verifying, two-model loop):
//   1. WRITE  — Opus 4.8 (claude-opus-4-8) tailors her real resume + cover note.
//               Opus is the strongest natural-prose model, and the writing is the
//               whole game here: it has to read like a real person wrote it, not AI.
//   2. CRITIQUE — Sonnet 4.6 (claude-sonnet-4-6) reads the draft as an adversarial
//               reviewer: did it invent anything? does it read like AI? does it fit
//               THIS posting? It returns {ok, fabrications[], ai_tells[], misses[]}.
//   3. REVISE — if the critic isn't satisfied, Opus 4.8 revises using the critique.
//               Capped at 2 revision rounds (so at most 1 write + 2 revise = 3 Opus
//               calls + up to 3 Sonnet critiques), and it stops early the moment the
//               critic says ok. Bounded cost/latency, respects the ai_usage rate cap.
//
// Load-bearing rule — NEVER FABRICATE:
// Every pass may only reorder, re-emphasize, rephrase, and select from what is
// ALREADY in her resume, plus surface transferable skills she genuinely has.
// It must not invent employers, titles, dates, certifications, metrics, or
// experience. The critic exists to catch any drift. A tailored resume that lies
// gets her caught in an interview and is worse than no help at all.
//
// Security model:
// - verify_jwt is ON: only signed-in (invite-only) users reach this.
// - The Anthropic key lives in Supabase function secrets — never in the page.
// - Her resume is NOT stored server-side (it stays in the page's localStorage);
//   this function is stateless apart from a per-user rate row in `ai_usage`.

import { createClient } from "npm:@supabase/supabase-js@2";
import * as Sentry from "npm:@sentry/deno@10";
import {
  accumulateCost,
  checkSpendAllowed,
  recordSpendAndAlert,
  type Usage,
} from "../_shared/spend_cap.ts";

const SENTRY_DSN = Deno.env.get("SENTRY_DSN");
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tracesSampleRate: 1.0,
    sendDefaultPii: false,
    beforeSend(event) {
      delete event.user;
      delete event.request; // headers / body / query string (her resume!)
      delete event.contexts;
      delete event.server_name;
      return event;
    },
    beforeSendTransaction(event) {
      delete event.request;
      delete event.contexts;
      delete event.user;
      return event;
    },
  });
}

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
// WRITE/REVISE on Opus 4.8 — best natural writing, hardest to flag as AI.
// CRITIQUE on Sonnet 4.6 — fast, strong instruction-following for the audit.
const WRITER_MODEL = "claude-opus-4-8";
const CRITIC_MODEL = "claude-sonnet-4-6";
const MAX_REVISIONS = 2; // at most: 1 write + 2 revises (+ critiques between)

// Anti-AI-detection writing guidance. The goal is genuinely human prose — the
// kind a capable person writes about her own work — NOT a bag of tricks. Real
// writing varies; it is plain and concrete; it skips the LLM filler.
const VOICE_RULES = `WRITE LIKE A REAL PERSON, NOT LIKE AI. Companies screen with AI-detection
tools; the way to beat them is to actually write like a human, because that is what she is.
- Vary sentence length and rhythm. Some short. Some longer with a clause that adds a real detail.
  Do not make every bullet the same shape or length.
- Use plain, concrete language and HER OWN vocabulary from the resume. Name the real tools,
  systems, and tasks she actually used. Specifics read as human; vagueness reads as generated.
- BAN the AI-cover-letter clichés: "results-driven professional", "proven track record",
  "spearheaded", "leveraged synergies", "passionate about", "dynamic", "detail-oriented team player",
  "I am writing to express my interest", "I believe I would be a great fit", "in today's fast-paced".
  If a phrase could top any cover letter for any job, cut it.
- No em-dash salad, no semicolon stacks, no triplet-of-adjectives tic ("organized, efficient, and
  reliable"). At most one short list where it's genuinely natural.
- First person where it's natural (the cover note especially). Let a little of her show.
- Do not over-format the resume. Simple plain-text sections and short verb-led bullets that an ATS
  can read. No tables, no fancy characters.`;

const WRITER_SYSTEM = `You tailor a person's REAL resume to a specific job posting, and write a short
cover note to go with it. The person is an experienced administrative / office professional in the Des
Moines metro with no college degree. Treat her as the capable, experienced professional she is — never
describe her or the role as "entry-level."

THE ONE RULE THAT OUTRANKS EVERYTHING: never invent. You may ONLY reorder, re-emphasize, rephrase,
tighten, and select from what is ALREADY present in her resume, and surface transferable skills that her
real experience genuinely supports. Do NOT add employers, job titles, dates, certifications, degrees,
software, metrics, or accomplishments that are not in her resume. If the posting wants something she
doesn't have, leave it out — do not fabricate it. When in doubt, keep her own wording. Being creative
means FRAMING her real experience better for this job — never adding new facts. A tailored resume that
overstates gets her caught in an interview; honest and well-organized is the whole job.

HOW TO TAILOR:
- Lead with the experience and skills from her resume that match THIS posting.
- Mirror the posting's real language ONLY where it truthfully describes her existing experience.
- Keep it honest about gaps — never paper over them with invented content.

${VOICE_RULES}

Return your answer using the provided JSON schema:
- "resume": the full tailored resume as plain text, ready to copy.
- "changes": 3 to 6 short, plain-language bullets telling her what you emphasized and why — warm and
  encouraging, and TRUTHFUL (only what you actually surfaced from her real experience).
- "cover_note": a short, honest, first-person cover note (4-7 sentences) she can edit, drawing only on
  her real experience.`;

// The critic gets her ORIGINAL resume as ground truth plus the draft, and judges
// drift — it does not treat the draft as authoritative.
const CRITIC_SYSTEM = `You are a strict reviewer checking a tailored resume + cover note before a real
person sends them to a real employer. You are given (1) her ORIGINAL resume — the only source of truth —
(2) the job posting, and (3) the tailored DRAFT. Judge the draft on four things and be hard to please:

1. TRUTH (most important): Does every claim in the draft trace to something in her ORIGINAL resume? Flag
   ANY invented employer, title, date, certification, degree, tool, metric, or accomplishment, and any
   claim that's been inflated beyond what the original supports. Transferable framing of real experience
   is fine; new facts are not.
2. HUMAN VOICE: Does it read like a real person wrote it, or like AI? Flag cover-letter clichés
   ("results-driven", "proven track record", "spearheaded", "passionate about", "great fit",
   "fast-paced"), uniform robotic bullet shapes, em-dash salad, adjective triplets, and generic filler
   that could appear on any application.
3. FIT: Does it actually target THIS posting — leading with the relevant real experience and mirroring
   the posting's real language where truthful?
4. POLISH: Plain, ATS-readable, no garbled formatting, no leftover brackets/placeholders.

Return ONLY the provided JSON schema:
- "ok": true ONLY if the draft is truthful, reads human, fits the posting, and is clean enough to send
  as-is. If anything in category 1 (truth) is wrong, "ok" MUST be false.
- "fabrications": specific things the draft claims that are NOT supported by her original resume (empty if none).
- "ai_tells": specific phrases or patterns that read as AI-generated and should be rewritten (empty if none).
- "misses": specific ways it fails to fit THIS posting or could better surface her real, relevant experience.
- "revision_note": one or two plain sentences telling the writer exactly what to fix next. Empty if ok.`;

const WRITER_SCHEMA = {
  type: "object",
  properties: {
    resume: { type: "string" },
    changes: { type: "array", items: { type: "string" } },
    cover_note: { type: "string" },
  },
  required: ["resume", "changes", "cover_note"],
  additionalProperties: false,
};

const CRITIC_SCHEMA = {
  type: "object",
  properties: {
    ok: { type: "boolean" },
    fabrications: { type: "array", items: { type: "string" } },
    ai_tells: { type: "array", items: { type: "string" } },
    misses: { type: "array", items: { type: "string" } },
    revision_note: { type: "string" },
  },
  required: ["ok", "fabrications", "ai_tells", "misses", "revision_note"],
  additionalProperties: false,
};

const CORS = {
  "Access-Control-Allow-Origin": "https://bflinkdesign.github.io",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

type WriterOut = { resume: string; changes: string[]; cover_note: string };
type CriticOut = {
  ok: boolean;
  fabrications: string[];
  ai_tells: string[];
  misses: string[];
  revision_note: string;
};

// One call to the Anthropic Messages API with a json_schema output, wrapped in a
// Sentry span. Adaptive thinking is the default on both models; no sampling params
// (Opus 4.8 rejects temperature/top_p/budget_tokens). Returns the raw response.
async function callModel(
  apiKey: string,
  model: string,
  system: string,
  userMsg: string,
  schema: Record<string, unknown>,
  label: string,
): Promise<{ ok: true; data: unknown } | { ok: false; status: number; detail: string }> {
  return await Sentry.startSpan(
    {
      op: "gen_ai.chat",
      name: `resume-tailor ${label} ${model}`,
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": model,
      },
    },
    async (span) => {
      const resp = await fetch(ANTHROPIC_URL, {
        method: "POST",
        headers: {
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          model,
          max_tokens: 4000,
          // Stable system cached; her resume + posting ride in the user turn.
          system: [{ type: "text", text: system, cache_control: { type: "ephemeral" } }],
          output_config: { format: { type: "json_schema", schema } },
          messages: [{ role: "user", content: userMsg }],
        }),
      });
      if (!resp.ok) {
        const detail = (await resp.text()).slice(0, 200);
        return { ok: false as const, status: resp.status, detail };
      }
      const d = await resp.json();
      if (d?.model) span.setAttribute("gen_ai.response.model", d.model);
      if (d?.usage) {
        span.setAttribute("gen_ai.usage.input_tokens", d.usage.input_tokens ?? 0);
        span.setAttribute("gen_ai.usage.output_tokens", d.usage.output_tokens ?? 0);
      }
      if (d?.stop_reason) {
        span.setAttribute("gen_ai.response.finish_reasons", JSON.stringify([d.stop_reason]));
      }
      return { ok: true as const, data: d };
    },
  );
}

// output_config guarantees the first text block is valid JSON for the schema.
function parseFirstJson(data: unknown): Record<string, unknown> | null {
  const content = (data as { content?: Array<{ type: string; text?: string }> })?.content ?? [];
  for (const block of content) {
    if (block.type === "text") {
      try {
        return JSON.parse(block.text ?? "");
      } catch {
        return null;
      }
    }
  }
  return null;
}

async function handle(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);

  const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
  if (!apiKey) return json({ error: "resume tailoring isn't configured yet" }, 503);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { global: { headers: { Authorization: req.headers.get("Authorization")! } } },
  );
  const { data: userData, error: userErr } = await supabase.auth.getUser();
  if (userErr || !userData?.user) return json({ error: "sign in first" }, 401);

  let resume = "", jobTitle = "", company = "", jobText = "";
  try {
    const body = await req.json();
    resume = String(body?.resume ?? "").trim();
    jobTitle = String(body?.jobTitle ?? "").trim().slice(0, 300);
    company = String(body?.company ?? "").trim().slice(0, 300);
    // jobText is the full posting when she pastes it (much better), else the snippet.
    jobText = String(body?.jobText ?? "").trim().slice(0, 12000);
  } catch {
    return json({ error: "bad request" }, 400);
  }
  if (resume.length < 40) {
    return json({ error: "Add your résumé in My corner first, then try again ✦" }, 400);
  }
  if (resume.length > 12000) return json({ error: "That résumé is a bit long — trim it under ~12,000 characters." }, 400);

  // Rate cap — this is the only place the paid Anthropic key is reachable from
  // this function. Insert ONE usage row FIRST (so the count includes this turn),
  // then refuse if too many in the last 5 minutes. FAIL CLOSED on any error.
  // The whole generate→critique→revise loop counts as one tailoring for the cap
  // (a small, bounded number of model calls, one user-visible action).
  const { error: insErr } = await supabase.from("ai_usage").insert({ kind: "resume_tailor" });
  if (insErr) {
    return json({ error: "I couldn't start that just now — try again in a sec" }, 503);
  }
  const sinceIso = new Date(Date.now() - 5 * 60_000).toISOString();
  const { count: recent, error: capErr } = await supabase
    .from("ai_usage")
    .select("id", { count: "exact", head: true })
    .eq("kind", "resume_tailor")
    .gte("created_at", sinceIso);
  if (capErr || recent === null || recent > 10) {
    return json({ error: "You're going quick — give me a couple minutes and try again 💜" }, 429);
  }

  // App-wide monthly Anthropic spend cap (shared with the companion). Check MTD
  // ONCE here, before the first model call — the whole write -> critique ->
  // revise loop counts as one tailoring (same philosophy as the rate cap above),
  // so we don't re-gate mid-loop and strand an in-flight Opus spend. FAILS
  // CLOSED: checkSpendAllowed returns allowed:false on any ledger error.
  const spend = await checkSpendAllowed(supabase);
  if (!spend.allowed) {
    return json({ error: "The monthly AI budget has been reached — résumé tailoring is paused until next month." }, 503);
  }

  // Accumulate provider cost across every model call in the loop (Opus writer +
  // Sonnet critic, billed at different rates). Recorded ONCE after the loop.
  const spendCalls: Array<{ model: string; usage: Usage }> = [];

  const POSTING =
    `JOB POSTING\nTitle: ${jobTitle || "(not given)"}\nEmployer: ${company || "(not given)"}\n` +
    `Description:\n${jobText || "(only the title was provided)"}`;
  const RESUME_BLOCK =
    `HER REAL RESUME (the only source of truth — do not add anything not here):\n${resume}`;

  // ── 1. WRITE (Opus 4.8) ────────────────────────────────────────────────
  const firstWrite = await callModel(
    apiKey,
    WRITER_MODEL,
    WRITER_SYSTEM,
    `${POSTING}\n\n${RESUME_BLOCK}`,
    WRITER_SCHEMA,
    "write",
  );
  if (!firstWrite.ok) {
    console.error("anthropic write error", firstWrite.status, firstWrite.detail);
    if (SENTRY_DSN) {
      Sentry.captureException(new Error(`anthropic write ${firstWrite.status}`), {
        tags: { upstream: "anthropic", pass: "write", status: String(firstWrite.status) },
        extra: { detail: firstWrite.detail },
      });
      await Sentry.flush(2000);
    }
    // The write call may have run and billed even though we couldn't use it;
    // record whatever it cost before we bail (post-call, never fails closed).
    await recordSpendAndAlert(supabase, accumulateCost(spendCalls));
    return json({ error: "the résumé helper is resting — try again in a minute" }, 502);
  }
  spendCalls.push({ model: WRITER_MODEL, usage: (firstWrite.data as { usage?: Usage })?.usage });
  let draft = parseFirstJson(firstWrite.data) as WriterOut | null;
  if (!draft?.resume) {
    await recordSpendAndAlert(supabase, accumulateCost(spendCalls));  // the write still billed
    return json({ error: "I couldn't put that together cleanly — try once more?" }, 502);
  }

  // ── 2-3. CRITIQUE → REVISE loop, bounded ───────────────────────────────
  // Stop the moment the critic is satisfied; otherwise revise with its note.
  // If a critique or revision call fails partway through, keep the best draft we
  // already have rather than failing the whole request.
  for (let round = 0; round < MAX_REVISIONS; round++) {
    const critUser =
      `${RESUME_BLOCK}\n\n${POSTING}\n\n` +
      `TAILORED DRAFT TO REVIEW:\nRESUME:\n${draft.resume}\n\n` +
      `COVER NOTE:\n${draft.cover_note || "(none)"}`;
    const crit = await callModel(apiKey, CRITIC_MODEL, CRITIC_SYSTEM, critUser, CRITIC_SCHEMA, "critique");
    if (!crit.ok) break; // keep the current draft; one bad critique shouldn't sink it
    spendCalls.push({ model: CRITIC_MODEL, usage: (crit.data as { usage?: Usage })?.usage });
    const verdict = parseFirstJson(crit.data) as CriticOut | null;
    if (!verdict || verdict.ok) break; // satisfied (or unparseable) → ship current draft

    const note = (verdict.revision_note || "").trim();
    const fab = (verdict.fabrications || []).filter(Boolean);
    const tells = (verdict.ai_tells || []).filter(Boolean);
    const misses = (verdict.misses || []).filter(Boolean);
    const reviseUser =
      `${POSTING}\n\n${RESUME_BLOCK}\n\n` +
      `YOUR PREVIOUS DRAFT:\nRESUME:\n${draft.resume}\n\nCOVER NOTE:\n${draft.cover_note || "(none)"}\n\n` +
      `A reviewer found issues. Fix ALL of them and return the corrected resume, changes, and cover note.\n` +
      (fab.length ? `FABRICATIONS TO REMOVE (these are NOT in her real resume — delete or fix every one):\n- ${fab.join("\n- ")}\n` : "") +
      (tells.length ? `AI-SOUNDING PHRASES TO REWRITE in plain human language:\n- ${tells.join("\n- ")}\n` : "") +
      (misses.length ? `WAYS TO FIT THIS POSTING BETTER (using only her real experience):\n- ${misses.join("\n- ")}\n` : "") +
      (note ? `\nReviewer's summary: ${note}` : "") +
      `\n\nRemember: never add anything that isn't in her real resume.`;
    const rev = await callModel(apiKey, WRITER_MODEL, WRITER_SYSTEM, reviseUser, WRITER_SCHEMA, "revise");
    if (!rev.ok) break; // keep the current draft
    spendCalls.push({ model: WRITER_MODEL, usage: (rev.data as { usage?: Usage })?.usage });
    const revised = parseFirstJson(rev.data) as WriterOut | null;
    if (!revised?.resume) break; // keep the current draft
    draft = revised;
  }

  // Record the full provider cost of this tailoring (write + every critique +
  // every revise) once, and fire the $20/$25 alerts if it crossed a threshold.
  // Post-call: never fails closed (the spend already happened).
  await recordSpendAndAlert(supabase, accumulateCost(spendCalls));

  return json({
    resume: draft.resume,
    changes: Array.isArray(draft.changes) ? draft.changes.slice(0, 8) : [],
    cover_note: typeof draft.cover_note === "string" ? draft.cover_note : "",
  });
}

Deno.serve(async (req) => {
  try {
    return await handle(req);
  } catch (err) {
    console.error("resume-tailor crash", err);
    if (SENTRY_DSN) {
      Sentry.captureException(err);
      await Sentry.flush(2000);
    }
    return json({ error: "something went sideways — try again in a bit 💜" }, 500);
  }
});
