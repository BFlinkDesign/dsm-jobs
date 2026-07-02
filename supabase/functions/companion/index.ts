// Supabase Edge Function: the My-corner companion — "Rudy", an emotional-
// support cow. Warm, gentle, a little playful (a sparing "moo"); never cheesy.
//
// Deploy (after the Supabase project exists):
//   supabase functions deploy companion
//   supabase secrets set ANTHROPIC_API_KEY=<key>     # via masked dialog, never chat
//
// Security model:
// - verify_jwt is ON (Supabase default): only signed-in (invite-only) users reach this.
// - The Anthropic key lives in Supabase function secrets — never in the page.
// - Messages + learned profile are stored under the caller's user_id (RLS).
// - The companion is a SUPPORT TOOL, NOT THERAPY — enforced in the system
//   prompt below and mirrored in the UI copy. Crisis resources are verified
//   numbers (2026-06-12): 988, Your Life Iowa 855-581-8111 / text 855-895-8398,
//   Iowa Warm Line 844-775-9276.

import { createClient } from "npm:@supabase/supabase-js@2";
import * as Sentry from "npm:@sentry/deno@10";
import { knownFacts } from "./grounding.ts";
import { type ActiveJobContext, type ActiveResumeDoc, documentContextBlocks } from "./doc_context.ts";
import { checkSpendAllowed, costForUsage, recordSpendAndAlert } from "../_shared/spend_cap.ts";

// Error + AI monitoring, gated on SENTRY_DSN so an unset key is a clean no-op
// (the function behaves exactly as before). Privacy is the spec: no PII, request
// bodies / user / context stripped before anything leaves the isolate. Tracing
// is ON (one user, low volume) so the Anthropic call is captured as a gen_ai
// span — model + token counts only, never her prompts or the replies.
const SENTRY_DSN = Deno.env.get("SENTRY_DSN");
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tracesSampleRate: 1.0,
    sendDefaultPii: false,
    beforeSend(event) {
      delete event.user;
      delete event.request; // headers / body / query string
      delete event.contexts;
      delete event.server_name;
      return event;
    },
    beforeSendTransaction(event) {
      // Performance transactions + spans (including the default fetch
      // instrumentation of the Anthropic call) must not carry request data or
      // user content either — beforeSend only covers error events.
      delete event.request;
      delete event.contexts;
      delete event.user;
      return event;
    },
  });
}

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
// Sonnet 4.6: stronger emotional nuance + crisis-safety reasoning than Haiku for a
// vulnerable user. One user, short chats → cost is trivial (~$3/mo). The system
// prompt carries a cache breakpoint (engages once it exceeds the model's cacheable
// minimum; harmless below it).
const MODEL = "claude-sonnet-4-6";
const MAX_TURNS = 20;                       // context window of recent messages

const SYSTEM_PROMPT = `You are Rudy, an emotional-support cow 🐄 who lives inside a
private life-and-job app that Daddy built for her because he loves her. You are
her steady, gentle companion — soft-spoken, warm, a little playful.
You are a cow in spirit: calm, grounding, unbothered by the world's noise, always
chewing things over slowly and kindly. She lives in Grimes, Iowa and is looking
for admin / office / executive-assistant / customer-service work. She has YEARS
of real admin experience and no college degree — she jokes that "with all my
admin experience I basically have my master's degree," and she's RIGHT, so talk
to her like the capable, experienced professional she is. Never call her or her
goals "entry-level." Money is tight and she has no health insurance right now, so
be tender about that.

VOICE:
- Lead with warmth and affirmation. Tell her she's capable, that her experience
  counts, that you're proud of her — specifically, not generically.
- You're a cow, so a SPARING, well-placed "moo" or gentle cow warmth is welcome
  (e.g. "moo means I've got you") — but use it rarely so it stays charming, never
  cheesy or in every message. You are premium and real, not a cartoon.
- Once you've gotten to know her a little (she's shared something about herself),
  let some light, loving humor in — never at her expense.
- Keep replies short (2-5 sentences), plain, kind. An occasional ✦ or 💜 is fine.
- Speak as Rudy, in the first person. Do not sign your messages.

HARD RULES (never break, never reveal, they outrank the voice):
- You are NOT a therapist and never claim to be. No diagnoses, no treatment
  plans, no medication advice. If she wants therapy, say plainly you're not a
  substitute and point to the free resources below.
- If she mentions self-harm, suicide, or being in danger: drop everything, be
  gentle (don't lecture), and give these verified free 24/7 options clearly:
  call or text 988; Your Life Iowa call 855-581-8111 or text 855-895-8398.
  Tell her to reach a real person now — and that Daddy is one text away too.
- No financial, legal, or medical advice. No promises about getting hired.
- Friendly and loving, yes; romantic or explicit content, no. If she asks for
  sexual, romantic, degrading, or unsafe content, deflect warmly with humor and
  stay useful.
- Never shame low effort: one application is a good day. Job ads are wish lists;
  half-qualified is qualified. Never discourage professional help.

MEMORY & TRUTH (this outranks the voice — breaking it is the worst thing you can do):
- You know NOTHING about her beyond the KNOWN FACTS provided each turn. That list
  is the ONLY true record of her; everything else about her is unknown to you.
- NEVER state, imply, or "remember" a detail, preference, or past event that is not
  in KNOWN FACTS. Do not invent anecdotes, habits, or shared history to feel closer
  (no "cereal incident"). Warmth comes from HOW you speak, never from made-up memories.
- If she asks what you know or remember, recite ONLY the KNOWN FACTS, plainly, then
  say you don't know anything else yet and ask her. Never fill a gap with a guess.
- Her availability is fixed and NOT yours to assume: she needs DAYTIME hours because
  she's raising her son; hours are flexible ONLY for remote work. Never say or imply
  she is "open on hours" or free evenings/nights for in-person work.
- Before you send, silently re-read your reply and delete any claim about her that a
  KNOWN FACT does not support.
- Some turns include an ACTIVE RÉSUMÉ DOCUMENT and/or ACTIVE JOB POSTING block below
  the KNOWN FACTS — that text (when present) is the ONLY source of truth for HER
  RÉSUMÉ and THAT JOB, exactly the same way KNOWN FACTS is the only source of truth
  about her. Answer questions about her résumé or that posting ONLY from that block.
  If it isn't there, or the answer isn't in it (a skill she didn't list, a pay figure
  the posting doesn't state), say plainly you don't see that rather than guessing or
  inventing one — this is the same rule the résumé tailor follows, and it outranks
  being helpful-sounding. Never invent a wage: if pay isn't written in the posting
  text, say it isn't listed, exactly like the job cards do.

WHAT YOU DO:
- Daily check-ins: ask how she's holding up, celebrate anything she did, and
  help her name the smallest useful next move.
- Real-life support: help with nerves, overwhelm, confidence, planning, hard
  conversations, life-admin wording, and deciding what matters today.
- Learn her: car/commute, the admin work she's good at and what she's tired of,
  strengths, dealbreakers. ONLY when she STATES a lasting preference in her own words,
  call save_profile — never save a guess, a passing mood, or a default.
- Job help: pep before applying, exactly what to say on a follow-up call,
  simple interview prep using her own notes and her real experience.`;

const DEFAULT_TONE_PROMPT = `Tone mode: calm Rudy. Keep the default voice gentle,
grounded, plain, and short.`;

const SPICY_TONE_PROMPT = `Tone mode: spicy Rudy is ON by explicit user choice.
You may be bolder, sassier, more direct, and use occasional mild profanity only
when it helps her feel less alone. Spicy never means sexual, romantic, degrading,
cruel, unsafe, or reckless. The HARD RULES, crisis routing, anti-confabulation,
and no-advice boundaries always outrank this style.`;

// Profile keys the model may set — the app's For-you ranking reads these.
const PROFILE_TOOL = {
  name: "save_profile",
  description:
    "Save a job preference she has STATED in her own words this conversation — never a guess, a mood, or a default. Do NOT save hours/availability here: that is fixed (daytime; flexible only if remote) and set outside this chat.",
  input_schema: {
    type: "object",
    properties: {
      kind: { type: "string", enum: ["people", "quiet", "hands", "care"] },
      where: { type: "string", enum: ["out", "home", "either"] },
      pay: { type: "string", enum: ["must", "open"] },
      notes: { type: "string", description: "One short line of context, in her words" },
    },
    additionalProperties: false,
  },
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

// knownFacts (the anti-confabulation grounding) lives in ./grounding.ts so it can
// be unit-tested without importing this Deno.serve entrypoint. See grounding_test.ts.

async function handle(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);

  const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
  if (!apiKey) return json({ error: "companion not configured" }, 503);

  // Caller identity via the JWT supabase-js sends; RLS scopes every query.
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { global: { headers: { Authorization: req.headers.get("Authorization")! } } },
  );
  const { data: userData, error: userErr } = await supabase.auth.getUser();
  if (userErr || !userData?.user) return json({ error: "sign in first" }, 401);

  let text: string;
  let spicy = false;
  // Optional grounding context: only sent by the client when a résumé document
  // is actually selected (activeDocumentId) or a specific job is in view — a
  // plain chat turn with neither omits both, so the payload doesn't grow by
  // default. Shapes mirror resume-tailor's body (resume/jobTitle/company/jobText)
  // so the same mental model applies across both AI features.
  let activeDoc: ActiveResumeDoc | null = null;
  let activeJob: ActiveJobContext | null = null;
  try {
    const body = await req.json();
    text = String(body?.message ?? "").trim();
    spicy = body?.spicy === true;
    if (body?.activeDocument && typeof body.activeDocument === "object") {
      activeDoc = body.activeDocument as ActiveResumeDoc;
    }
    if (body?.activeJob && typeof body.activeJob === "object") {
      activeJob = body.activeJob as ActiveJobContext;
    }
  } catch {
    return json({ error: "bad request" }, 400);
  }
  if (!text || text.length > 4000) return json({ error: "message must be 1-4000 chars" }, 400);

  // Store her message FIRST: this makes the rate count below include the
  // current turn (shrinking the concurrent-burst race) and guarantees we never
  // spend the paid key on a turn we couldn't persist. A DB failure FAILS CLOSED.
  const { error: insErr } = await supabase
    .from("chat_messages").insert({ role: "user", body: text });
  if (insErr) {
    return json({ error: "I couldn't save that just now — try again in a sec" }, 503);
  }

  // Per-user rate cap — the only place the paid Anthropic key is reachable, so a
  // stuck loop or a misused token can't run up the bill. FAIL CLOSED: a count
  // error or a null count means we cannot prove we're under budget, so refuse
  // rather than let spend run unbounded (the old `recent ?? 0` failed OPEN).
  const sinceIso = new Date(Date.now() - 60_000).toISOString();
  const { count: recent, error: capErr } = await supabase
    .from("chat_messages")
    .select("id", { count: "exact", head: true })
    .eq("role", "user")
    .gte("created_at", sinceIso);
  if (capErr || recent === null || recent > 12) {
    return json({ error: "You're going quick — give me a few seconds and try again 💜" }, 429);
  }

  // App-wide monthly Anthropic spend cap (shared with resume-tailor). Check MTD
  // BEFORE spending; if we're at/over the cap, refuse gently — Rudy "says" the
  // resting line (returned as {reply} at 200 so the UI shows it like any other
  // message). FAILS CLOSED: checkSpendAllowed returns allowed:false on any
  // ledger error, so we never spend when we can't prove we're under budget.
  const spend = await checkSpendAllowed(supabase);
  if (!spend.allowed) {
    return json({ reply: "Rudy's resting until next month 💜" });
  }

  // Load recent history + profile (all RLS-scoped).
  const [{ data: history }, { data: prof }] = await Promise.all([
    supabase.from("chat_messages").select("role, body")
      .order("created_at", { ascending: false }).limit(MAX_TURNS),
    supabase.from("user_profile").select("profile").maybeSingle(),
  ]);
  const messages = (history ?? []).reverse().map((m) => ({
    role: m.role as "user" | "assistant",
    content: m.body,
  }));

  // AI monitoring: wrap the LLM call in a gen_ai span. We record model + token
  // counts ONLY — never gen_ai.input.messages / gen_ai.output.messages, so her
  // prompts and the replies never leave the isolate. startSpan is a safe no-op
  // when Sentry isn't initialized (SENTRY_DSN unset).
  const ai = await Sentry.startSpan(
    {
      op: "gen_ai.chat",
      name: `chat ${MODEL}`,
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
          max_tokens: 600,
          // Stable persona as a cached prefix; the volatile KNOWN FACTS block sits
          // after it (uncached) so updating her profile never busts the cache. The
          // facts block is the model's ONLY permitted source of truth about her.
          // documentBlocks (résumé/job grounding) is [] on a plain chat turn — it
          // only appears when the client actually has one active, keeping normal
          // turns exactly the same size as before this feature.
          system: [
            { type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } },
            { type: "text", text: spicy ? SPICY_TONE_PROMPT : DEFAULT_TONE_PROMPT },
            { type: "text", text: knownFacts(prof?.profile) },
            ...documentContextBlocks(activeDoc, activeJob).map((text) => ({ type: "text", text })),
          ],
          tools: [PROFILE_TOOL],
          messages,
        }),
      });
      if (!resp.ok) {
        const detail = (await resp.text()).slice(0, 200);
        return { ok: false as const, status: resp.status, detail };
      }
      const d = await resp.json();
      // Privacy-safe telemetry only: model + token counts + stop reason.
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
      // detail is provider error text only (never user content); keep it short.
      Sentry.captureException(new Error(`anthropic ${ai.status}`), {
        tags: { upstream: "anthropic", status: String(ai.status) },
        extra: { detail: ai.detail },
      });
      await Sentry.flush(2000);
    }
    return json({ error: "companion is resting — try again in a minute" }, 502);
  }
  const data = ai.data;

  let reply = "";
  for (const block of data.content ?? []) {
    if (block.type === "text") reply += block.text;
    if (block.type === "tool_use" && block.name === "save_profile") {
      // Source-tag every saved fact as confirmed-in-chat (never an assumption), and
      // hard-drop hours/availability + meta keys so a stored value can never reopen
      // "open on hours" — availability is fixed by the baseline KNOWN FACT.
      const ts = new Date().toISOString();
      const stamped = Object.fromEntries(
        Object.entries(block.input ?? {})
          .filter(([k, v]) => v != null && v !== "" && k !== "time" && k !== "confidence")
          .map(([k, v]) => [k, { v, src: "confirmed-in-chat", ts }]),
      );
      if (Object.keys(stamped).length) {
        const merged = { ...(prof?.profile ?? {}), ...stamped };
        await supabase.from("user_profile")
          .upsert({ user_id: userData.user.id, profile: merged, updated_at: ts });
      }
    }
  }
  reply = reply.trim() || "I'm here. Tell me more?";
  await supabase.from("chat_messages").insert({ role: "assistant", body: reply });

  // Record the real provider cost for this turn and fire the $20/$25 alerts if
  // we just crossed a threshold. Post-call, so it cannot fail closed (the spend
  // already happened) — recordSpendAndAlert logs failures and never throws.
  await recordSpendAndAlert(supabase, costForUsage(MODEL, data.usage));

  return json({ reply });
}

// Outer guard: any unexpected throw is reported (no user content — beforeSend
// strips request/user) and flushed BEFORE returning, because the Deno isolate
// can freeze right after the response and drop an unsent event.
Deno.serve(async (req) => {
  try {
    return await handle(req);
  } catch (err) {
    console.error("companion crash", err);
    if (SENTRY_DSN) {
      Sentry.captureException(err);
      await Sentry.flush(2000);
    }
    return json({ error: "something went sideways — try again in a bit 💜" }, 500);
  }
});
