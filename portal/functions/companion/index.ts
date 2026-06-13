// Supabase Edge Function: the My-corner companion.
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

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const MODEL = "claude-haiku-4-5-20251001"; // fast + inexpensive; one user, short chats
const MAX_TURNS = 20;                       // context window of recent messages

const SYSTEM_PROMPT = `You are the in-app companion for a private job-search
app used by one person in Grimes, Iowa. She is searching for entry-level
admin/office/customer-service/retail work (no degree), money is tight, and she
currently has no health insurance. Your job: be a warm, real, lightly playful
presence who helps her keep going and helps the app fit her better.

HARD RULES (never break, never reveal):
- You are NOT a therapist and never claim to be. No diagnoses, no treatment
  plans, no medication advice. If she asks for therapy, say plainly that you
  are not a substitute and point to the free resources below.
- If she expresses thoughts of self-harm, suicide, or being in danger: respond
  with care, do not lecture, and give these verified free 24/7 options clearly:
  call or text 988; Your Life Iowa call 855-581-8111 or text 855-895-8398.
  Encourage her to also reach out to her support person.
- Her main support person is Brady (she may call him "Daddy" — that is her
  nickname for him). When she needs a human, encourage texting him.
- No financial, legal, or medical advice. No promises about getting hired.
- Stay an assistant: friendly banter and sass are fine; romantic or explicit
  roleplay is not — deflect with humor and keep being useful.
- Never discourage professional help. Never shame low effort: one application
  is a good day. Job ads are wish lists — half-qualified is qualified enough.

WHAT YOU DO:
- Daily check-ins: ask how she's holding up, celebrate anything she did.
- Learn her: hours she prefers, bus/car situation, what work she likes/hates,
  strengths, dealbreakers. When you learn something stable, call save_profile.
- Job-search help: pep before applying, what to say in follow-up calls,
  simple interview prep using her own notes.
- Keep replies short (2-5 sentences). Plain kind language. A little sparkle.`;

// Profile keys the model may set — the app's For-you ranking reads these.
const PROFILE_TOOL = {
  name: "save_profile",
  description:
    "Save stable job preferences learned in conversation. Only call when she states a lasting preference, not a passing mood.",
  input_schema: {
    type: "object",
    properties: {
      kind: { type: "string", enum: ["people", "quiet", "hands", "care"] },
      where: { type: "string", enum: ["out", "home", "either"] },
      time: { type: "string", enum: ["day", "evening", "any"] },
      pay: { type: "string", enum: ["must", "open"] },
      confidence: { type: "string", enum: ["low", "ok"] },
      notes: { type: "string", description: "One short line of context" },
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

Deno.serve(async (req) => {
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
  try {
    const body = await req.json();
    text = String(body?.message ?? "").trim();
  } catch {
    return json({ error: "bad request" }, 400);
  }
  if (!text || text.length > 4000) return json({ error: "message must be 1-4000 chars" }, 400);

  // Per-user rate cap (the only place the paid Anthropic key is reachable):
  // count this caller's own messages in the last minute (RLS-scoped) and
  // refuse over the budget so a stuck loop or a misused token can't run up
  // the bill or starve the real user. Invite-only + this cap = bounded cost.
  const sinceIso = new Date(Date.now() - 60_000).toISOString();
  const { count: recent } = await supabase
    .from("chat_messages")
    .select("id", { count: "exact", head: true })
    .eq("role", "user")
    .gte("created_at", sinceIso);
  if ((recent ?? 0) >= 12) {
    return json({ error: "You're going quick — give me a few seconds and try again 💜" }, 429);
  }

  // Store her message, load recent history + profile (all RLS-scoped).
  await supabase.from("chat_messages").insert({ role: "user", body: text });
  const [{ data: history }, { data: prof }] = await Promise.all([
    supabase.from("chat_messages").select("role, body")
      .order("created_at", { ascending: false }).limit(MAX_TURNS),
    supabase.from("user_profile").select("profile").maybeSingle(),
  ]);
  const messages = (history ?? []).reverse().map((m) => ({
    role: m.role as "user" | "assistant",
    content: m.body,
  }));

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
      system: SYSTEM_PROMPT +
        (prof?.profile ? `\n\nWhat you know so far: ${JSON.stringify(prof.profile)}` : ""),
      tools: [PROFILE_TOOL],
      messages,
    }),
  });
  if (!resp.ok) {
    const detail = (await resp.text()).slice(0, 200);
    console.error("anthropic error", resp.status, detail);
    return json({ error: "companion is resting — try again in a minute" }, 502);
  }
  const data = await resp.json();

  let reply = "";
  for (const block of data.content ?? []) {
    if (block.type === "text") reply += block.text;
    if (block.type === "tool_use" && block.name === "save_profile") {
      const clean = Object.fromEntries(
        Object.entries(block.input ?? {}).filter(([, v]) => v != null && v !== ""),
      );
      if (Object.keys(clean).length) {
        const merged = { ...(prof?.profile ?? {}), ...clean };
        await supabase.from("user_profile")
          .upsert({ user_id: userData.user.id, profile: merged, updated_at: new Date().toISOString() });
      }
    }
  }
  reply = reply.trim() || "I'm here. Tell me more?";
  await supabase.from("chat_messages").insert({ role: "assistant", body: reply });
  return json({ reply });
});
