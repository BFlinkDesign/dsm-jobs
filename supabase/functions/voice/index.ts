// Supabase Edge Function: "voice" — Rudy's real voice, via ElevenLabs.
//
// Two modes on one POST endpoint:
//   { mode: "tts", text }                  -> { audio: <base64 mp3>, mime }
//   { mode: "stt", audio: <base64>, mime } -> { text }
//
// Why this exists: the browser's built-in speechSynthesis sounds robotic, and
// its SpeechRecognition (mic) is unsupported on iOS Safari/PWA — which is the
// only device she uses. ElevenLabs gives a warm voice for TTS and works for
// speech-to-text on iOS (the client records with MediaRecorder and sends the
// audio here). The API key never touches the page.
//
// Deploy (after the Supabase project exists):
//   supabase functions deploy voice
//   supabase secrets set ELEVENLABS_API_KEY=<key>          # masked dialog, never chat
//   supabase secrets set ELEVENLABS_VOICE_ID=<voiceId>     # optional; warm default below
//   supabase secrets set ELEVENLABS_MODEL_ID=<modelId>     # optional; turbo default below
//
// Security:
// - verify_jwt is ON (Supabase default): only signed-in users reach this.
// - ELEVENLABS_API_KEY lives in function secrets, never in the page.
// - If the key is unset, every call returns { unconfigured: true } with HTTP 200
//   so the client falls back to the old browser voice instead of erroring.

import * as Sentry from "npm:@sentry/deno@10";

const SENTRY_DSN = Deno.env.get("SENTRY_DSN");
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tracesSampleRate: 0,
    sendDefaultPii: false,
    beforeSend(event) {
      delete event.user;
      delete event.request;   // never let audio/text leave the isolate
      delete event.contexts;
      delete event.server_name;
      return event;
    },
  });
}

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

// A warm, gentle default (ElevenLabs "Rachel"). Override with ELEVENLABS_VOICE_ID
// to any voice in the account — pick whatever sounds most like Rudy.
const DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM";
// Turbo v2.5: low latency + low cost, good for short chat replies.
const DEFAULT_MODEL_ID = "eleven_turbo_v2_5";

// Cost guards (a single user, but never let one call run away):
const MAX_TTS_CHARS = 1500;
const MAX_STT_BYTES = 6 * 1024 * 1024;   // ~6 MB of audio (well past a short clip)

function b64encode(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

// Returns Uint8Array<ArrayBuffer> (not the default ArrayBufferLike) so the
// bytes are accepted directly as a BlobPart under Deno's strict lib.
function b64decode(b64: string): Uint8Array<ArrayBuffer> {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function tts(text: string, key: string): Promise<Response> {
  const clean = (text || "").trim().slice(0, MAX_TTS_CHARS);
  if (!clean) return json({ error: "empty_text" }, 400);
  const voiceId = Deno.env.get("ELEVENLABS_VOICE_ID") || DEFAULT_VOICE_ID;
  const modelId = Deno.env.get("ELEVENLABS_MODEL_ID") || DEFAULT_MODEL_ID;
  const resp = await fetch(
    `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}?output_format=mp3_44100_128`,
    {
      method: "POST",
      headers: { "xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg" },
      body: JSON.stringify({
        text: clean,
        model_id: modelId,
        // Warm + steady: a touch more stability, gentle style.
        voice_settings: { stability: 0.45, similarity_boost: 0.75, style: 0.25, use_speaker_boost: true },
      }),
    },
  );
  if (!resp.ok) {
    const detail = (await resp.text()).slice(0, 200);
    return json({ error: "tts_failed", status: resp.status, detail }, 502);
  }
  const audio = b64encode(await resp.arrayBuffer());
  return json({ audio, mime: "audio/mpeg" });
}

async function stt(audioB64: string, mime: string, key: string): Promise<Response> {
  const bytes = b64decode(audioB64 || "");
  if (!bytes.length) return json({ error: "empty_audio" }, 400);
  if (bytes.length > MAX_STT_BYTES) return json({ error: "audio_too_large" }, 413);
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: mime || "audio/webm" }), "clip");
  form.append("model_id", "scribe_v1");
  const resp = await fetch("https://api.elevenlabs.io/v1/speech-to-text", {
    method: "POST",
    headers: { "xi-api-key": key },
    body: form,
  });
  if (!resp.ok) {
    const detail = (await resp.text()).slice(0, 200);
    return json({ error: "stt_failed", status: resp.status, detail }, 502);
  }
  const data = await resp.json();
  return json({ text: String(data?.text || "").trim() });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const key = Deno.env.get("ELEVENLABS_API_KEY");
  // No key yet -> tell the client to use its fallback voice. Not an error.
  if (!key) return json({ unconfigured: true });

  let body: { mode?: string; text?: string; audio?: string; mime?: string };
  try {
    body = await req.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }

  try {
    if (body.mode === "tts") return await tts(body.text || "", key);
    if (body.mode === "stt") return await stt(body.audio || "", body.mime || "", key);
    return json({ error: "unknown_mode" }, 400);
  } catch (err) {
    if (SENTRY_DSN) Sentry.captureException(err);
    return json({ error: "voice_error" }, 500);
  }
});
