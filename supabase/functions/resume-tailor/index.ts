// Supabase Edge Function: tailor her real resume to one job posting.
//
// Deploy (after the Supabase project exists):
//   supabase functions deploy resume-tailor
//   (uses the same ANTHROPIC_API_KEY project secret as the companion)
//
// Load-bearing rule — NEVER FABRICATE:
// The model may only reorder, re-emphasize, rephrase, and select from what is
// ALREADY in her resume, plus surface transferable skills she genuinely has.
// It must not invent employers, titles, dates, certifications, metrics, or
// experience. This mirrors the app's honesty invariants — a tailored resume
// that lies gets her caught in an interview and is worse than no help at all.
//
// Security model:
// - verify_jwt is ON: only signed-in (invite-only) users reach this.
// - The Anthropic key lives in Supabase function secrets — never in the page.
// - Her resume is NOT stored server-side (it stays in the page's localStorage);
//   this function is stateless apart from a per-user rate row in `ai_usage`.

import { createClient } from "npm:@supabase/supabase-js@2";
import * as Sentry from "npm:@sentry/deno@10";

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
// Sonnet 4.6: strong instruction-following — critical here, because the one rule
// that matters most is "never invent." One user, low volume → cost is trivial.
const MODEL = "claude-sonnet-4-6";

const SYSTEM_PROMPT = `You tailor a person's REAL resume to a specific job posting.
The person is an experienced administrative / office professional in the Des
Moines metro with no college degree. Treat her as the capable, experienced
professional she is — never describe her or the role as "entry-level."

THE ONE RULE THAT OUTRANKS EVERYTHING: never invent. You may ONLY reorder,
re-emphasize, rephrase, tighten, and select from what is ALREADY present in her
resume, and surface transferable skills that her real experience genuinely
supports. Do NOT add employers, job titles, dates, certifications, degrees,
software, metrics, or accomplishments that are not in her resume. If the posting
wants something she doesn't have, leave it out — do not fabricate it. When in
doubt, keep her own wording. A tailored resume that overstates gets her caught
in an interview; honest and well-organized is the whole job.

HOW TO TAILOR:
- Lead with the experience and skills from her resume that match THIS posting.
- Mirror the posting's real language ONLY where it truthfully describes her
  existing experience.
- Keep it clean, scannable, and ATS-friendly (plain text, clear section
  headers, simple bullet points starting with strong verbs).
- Keep it honest about gaps — never paper over them with invented content.

Return your answer using the provided JSON schema:
- "resume": the full tailored resume as plain text, ready to copy.
- "changes": 3 to 6 short, plain-language bullets telling her what you emphasized
  and why, warm and encouraging (she is doing great by applying at all).
- "cover_note": a short, honest, first-person cover note (4-7 sentences) she can
  edit, drawing only on her real experience.`;

const OUTPUT_SCHEMA = {
  type: "object",
  properties: {
    resume: { type: "string" },
    changes: { type: "array", items: { type: "string" } },
    cover_note: { type: "string" },
  },
  required: ["resume", "changes", "cover_note"],
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
    jobText = String(body?.jobText ?? "").trim().slice(0, 6000);
  } catch {
    return json({ error: "bad request" }, 400);
  }
  if (resume.length < 40) {
    return json({ error: "Add your résumé in My corner first, then try again ✦" }, 400);
  }
  if (resume.length > 12000) return json({ error: "That résumé is a bit long — trim it under ~12,000 characters." }, 400);

  // Rate cap — this is the only place the paid Anthropic key is reachable from
  // this function. Insert a usage row FIRST (so the count includes this turn),
  // then refuse if too many in the last 5 minutes. FAIL CLOSED on any error.
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

  const userMsg =
    `JOB POSTING\nTitle: ${jobTitle || "(not given)"}\nEmployer: ${company || "(not given)"}\n` +
    `Description:\n${jobText || "(only the title was provided)"}\n\n` +
    `HER REAL RESUME (the only source of truth — do not add anything not here):\n${resume}`;

  const ai = await Sentry.startSpan(
    {
      op: "gen_ai.chat",
      name: `resume-tailor ${MODEL}`,
      attributes: {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": MODEL,
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
          model: MODEL,
          max_tokens: 4000,
          // Stable instructions cached; her resume rides in the user turn.
          system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
          output_config: { format: { type: "json_schema", schema: OUTPUT_SCHEMA } },
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
  if (!ai.ok) {
    console.error("anthropic error", ai.status, ai.detail);
    if (SENTRY_DSN) {
      Sentry.captureException(new Error(`anthropic ${ai.status}`), {
        tags: { upstream: "anthropic", status: String(ai.status) },
        extra: { detail: ai.detail },
      });
      await Sentry.flush(2000);
    }
    return json({ error: "the résumé helper is resting — try again in a minute" }, 502);
  }

  // output_config guarantees the first text block is valid JSON for the schema.
  let parsed: { resume?: string; changes?: string[]; cover_note?: string } | null = null;
  for (const block of ai.data.content ?? []) {
    if (block.type === "text") {
      try { parsed = JSON.parse(block.text); } catch { /* fall through */ }
      break;
    }
  }
  if (!parsed?.resume) {
    return json({ error: "I couldn't put that together cleanly — try once more?" }, 502);
  }
  return json({
    resume: parsed.resume,
    changes: Array.isArray(parsed.changes) ? parsed.changes.slice(0, 8) : [],
    cover_note: typeof parsed.cover_note === "string" ? parsed.cover_note : "",
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
