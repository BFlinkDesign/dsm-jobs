// Supabase Edge Function: "voice" — Rudy's real voice. Provider-agnostic so you
// can use a paid OR a free/open backend; it picks by whichever key you set.
//
// Two modes on one POST endpoint:
//   { mode: "tts", text, voice? }           -> { audio: <base64>, mime }
//   { mode: "stt", audio: <base64>, mime }  -> { text }
//
// `voice` is an OPTIONAL short preset id from a small server-side allowlist
// (see VOICE_PRESETS below) — never a raw provider value. The client can only
// ever pick one of a handful of curated names; it cannot inject arbitrary
// model params, sample URLs, or provider voice IDs this way. Omitting `voice`
// (or sending an unknown one) resolves to "warm", which sends byte-for-byte
// the same request every provider sent before presets existed — nothing
// already deployed changes behavior.
//
// Why: the browser's speechSynthesis is robotic and its SpeechRecognition (mic)
// is unsupported on iOS — her only device. This sends short clips/replies to a
// real voice service. The key never touches the page; if NO key is set, every
// call returns { unconfigured: true } (HTTP 200) and the client falls back to
// the old browser voice — so nothing breaks before you configure a provider.
//
// ── Pick a provider by setting its secret(s) ───────────────────────────────
// Rudy's voice (TTS) — open source first:
//   Chatterbox (Resemble AI, MIT — SoTA open TTS, benchmarks ahead of
//   ElevenLabs; served via Replicate). THIS IS THE DEFAULT VOICE:
//     supabase secrets set REPLICATE_API_TOKEN=<token>
//     [CHATTERBOX_VOICE_URL=<5s+ sample to clone Rudy's voice>]
//     [CHATTERBOX_EXAGGERATION=0.5] [CHATTERBOX_CFG=0.5] [CHATTERBOX_MODEL=resemble-ai/chatterbox]
//   Hugging Face Kokoro-82M (also open; may need an Inference Endpoint URL):
//     supabase secrets set HF_TOKEN=<token>   [HF_TTS_URL=<endpoint>]
//   Cloudflare Workers AI MeloTTS (free tier, one account also covers STT):
//     supabase secrets set CLOUDFLARE_ACCOUNT_ID=<id> CLOUDFLARE_API_TOKEN=<token>
//   ElevenLabs (paid; lowest priority — only used if it's the only TTS key):
//     supabase secrets set ELEVENLABS_API_KEY=<key> [ELEVENLABS_VOICE_ID=<id>]
//
// ── Voice picker (client-chosen preset: warm | bright | calm | spark) ──────
//   The 4 presets are hardcoded (see VOICE_PRESETS) so nothing needs setting
//   up to enable the picker. Optional per-preset overrides once you've
//   recorded/confirmed real values, e.g.:
//     CHATTERBOX_VOICE_URL_BRIGHT=<distinct clone sample for "bright">
//     ELEVENLABS_VOICE_ID_CALM=<confirmed voice id for "calm">
//   Unset overrides just use the built-in preset value (or the shared
//   CHATTERBOX_VOICE_URL sample for Chatterbox presets without their own).
//
// Mic typing (STT):
//   Groq Whisper (free, very fast): supabase secrets set GROQ_API_KEY=<key>
//   Cloudflare Whisper: same CLOUDFLARE_* keys as above.
//   ElevenLabs scribe_v1: same ELEVENLABS_API_KEY.
//
// Force a choice (else auto by key presence): VOICE_TTS / VOICE_STT =
//   chatterbox | huggingface | cloudflare | elevenlabs   (tts)
//   groq | cloudflare | elevenlabs                       (stt)
//
// Deploy: supabase functions deploy voice   (verify_jwt on — signed-in only)

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

const env = (k: string): string => Deno.env.get(k) || "";

// Cost guards — one user, but never let one call run away.
const MAX_TTS_CHARS = 1500;
const MAX_STT_BYTES = 6 * 1024 * 1024;   // ~6 MB

function b64encode(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  return btoa(bin);
}

// Uint8Array<ArrayBuffer> (not the default ArrayBufferLike) so the bytes are a
// valid BlobPart under Deno's strict lib.
function b64decode(b64: string): Uint8Array<ArrayBuffer> {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// ── Voice presets (server-side allowlist) ──────────────────────────────────
// The ONLY voice-selection surface the client can reach. `voice` in the
// request body is matched against this map's keys only; anything else (or
// absent) becomes "warm". This is what makes the picker safe: the client
// sends a short label, never a model param, sample URL, or provider voice id.
type ChatterboxTune = { exaggeration: number; cfgWeight: number; temperature: number };
type VoicePreset = {
  label: string;
  chatterbox: ChatterboxTune;
  elevenlabsVoiceId: string;   // known-good premade ElevenLabs voice id
  kokoroVoice?: string;        // Kokoro-82M named voice (best-effort, see ttsHuggingFace)
};

const DEFAULT_VOICE = "warm";

const VOICE_PRESETS: Record<string, VoicePreset> = {
  // "warm" MUST stay numerically identical to the pre-preset hardcoded
  // defaults below (0.5 / 0.5 / 0.8, ElevenLabs "Rachel") — it is the
  // fallback for every existing caller that never sends `voice` at all.
  warm: {
    label: "Warm",
    chatterbox: { exaggeration: 0.5, cfgWeight: 0.5, temperature: 0.8 },
    elevenlabsVoiceId: "21m00Tcm4TlvDq8ikWAM",   // "Rachel" — existing default
    kokoroVoice: "af_heart",
  },
  bright: {
    label: "Bright",
    chatterbox: { exaggeration: 0.7, cfgWeight: 0.4, temperature: 0.9 },
    elevenlabsVoiceId: "EXAVITQu4vr4xnSDxMaL",   // "Bella" — energetic premade voice
    kokoroVoice: "af_bella",
  },
  calm: {
    label: "Calm",
    chatterbox: { exaggeration: 0.3, cfgWeight: 0.6, temperature: 0.6 },
    elevenlabsVoiceId: "ErXwobaYiN019PkySvjV",   // "Antoni" — steady, lower-energy premade voice
    kokoroVoice: "am_michael",
  },
  spark: {
    label: "Spark",
    chatterbox: { exaggeration: 0.8, cfgWeight: 0.35, temperature: 1.0 },
    elevenlabsVoiceId: "MF3mGyEYCl7XYWbV9V6O",   // "Elli" — playful premade voice
    kokoroVoice: "af_nicole",
  },
};

/** Map an untrusted request-body value to a known preset id, defaulting safely. */
function resolveVoiceId(raw: unknown): string {
  const id = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  return Object.prototype.hasOwnProperty.call(VOICE_PRESETS, id) ? id : DEFAULT_VOICE;
}

const cfReady = (): boolean => !!(env("CLOUDFLARE_ACCOUNT_ID") && env("CLOUDFLARE_API_TOKEN"));

function ttsProvider(): string {
  const forced = env("VOICE_TTS").toLowerCase();
  if (forced) return forced;
  if (env("REPLICATE_API_TOKEN")) return "chatterbox";   // open default (Resemble AI Chatterbox)
  if (env("HF_TOKEN")) return "huggingface";
  if (cfReady()) return "cloudflare";
  if (env("ELEVENLABS_API_KEY")) return "elevenlabs";    // paid fallback, lowest priority
  return "";
}

function sttProvider(): string {
  const forced = env("VOICE_STT").toLowerCase();
  if (forced) return forced;
  if (env("GROQ_API_KEY")) return "groq";
  if (env("ELEVENLABS_API_KEY")) return "elevenlabs";
  if (cfReady()) return "cloudflare";
  return "";
}

// ── TTS providers ──────────────────────────────────────────────────────────

async function ttsElevenLabs(text: string, voiceId = DEFAULT_VOICE): Promise<Response> {
  // "warm" keeps the exact pre-preset lookup (env override, else hardcoded
  // "Rachel") so a request without `voice` is byte-for-byte unchanged. Any
  // other preset uses its curated id, optionally overridden per-preset via
  // ELEVENLABS_VOICE_ID_<NAME> once real sample ids are confirmed live.
  const elId = voiceId === DEFAULT_VOICE
    ? (env("ELEVENLABS_VOICE_ID") || VOICE_PRESETS[DEFAULT_VOICE].elevenlabsVoiceId)
    : (env(`ELEVENLABS_VOICE_ID_${voiceId.toUpperCase()}`) || VOICE_PRESETS[voiceId].elevenlabsVoiceId);
  const modelId = env("ELEVENLABS_MODEL_ID") || "eleven_turbo_v2_5";
  const r = await fetch(
    `https://api.elevenlabs.io/v1/text-to-speech/${elId}?output_format=mp3_44100_128`,
    {
      method: "POST",
      headers: { "xi-api-key": env("ELEVENLABS_API_KEY"), "Content-Type": "application/json", "Accept": "audio/mpeg" },
      body: JSON.stringify({
        text,
        model_id: modelId,
        voice_settings: { stability: 0.45, similarity_boost: 0.75, style: 0.25, use_speaker_boost: true },
      }),
    },
  );
  if (!r.ok) return json({ error: "tts_failed", provider: "elevenlabs", status: r.status, detail: (await r.text()).slice(0, 200) }, 502);
  return json({ audio: b64encode(await r.arrayBuffer()), mime: "audio/mpeg" });
}

async function ttsCloudflare(text: string): Promise<Response> {
  const acct = env("CLOUDFLARE_ACCOUNT_ID");
  const r = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${acct}/ai/run/@cf/myshell-ai/melotts`,
    {
      method: "POST",
      headers: { "Authorization": `Bearer ${env("CLOUDFLARE_API_TOKEN")}`, "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text, lang: "en" }),
    },
  );
  if (!r.ok) return json({ error: "tts_failed", provider: "cloudflare", status: r.status, detail: (await r.text()).slice(0, 200) }, 502);
  const data = await r.json();
  const audio = data?.result?.audio;   // MeloTTS returns base64 mp3
  if (!audio) return json({ error: "tts_no_audio", provider: "cloudflare" }, 502);
  return json({ audio, mime: "audio/mpeg" });
}

async function ttsHuggingFace(text: string, voiceId = DEFAULT_VOICE): Promise<Response> {
  // Kokoro-82M (Apache-2.0) — the best open voice. Serverless support varies, so
  // HF_TTS_URL lets you point at a dedicated Inference Endpoint.
  const url = env("HF_TTS_URL")
    || `https://api-inference.huggingface.co/models/${env("HF_TTS_MODEL") || "hexgrad/Kokoro-82M"}`;
  // "warm" sends the exact same body as before presets existed. For other
  // presets we *additionally* send Kokoro's documented voice-name field
  // (kokoro's own API/pipeline accepts a `voice` string, e.g. "af_bella",
  // "am_michael" — see hexgrad/kokoro README). The stock HF serverless
  // Inference API for a standard text-to-speech pipeline does not read a
  // custom `voice` parameter and will just ignore it — that's the intended
  // "fall back to default silently" behavior on a dedicated endpoint that
  // doesn't implement voice selection.
  const preset = VOICE_PRESETS[voiceId];
  const body: Record<string, unknown> = { inputs: text };
  if (voiceId !== DEFAULT_VOICE && preset?.kokoroVoice) {
    body.parameters = { voice: preset.kokoroVoice };
  }
  const r = await fetch(url, {
    method: "POST",
    headers: { "Authorization": `Bearer ${env("HF_TOKEN")}`, "Content-Type": "application/json", "Accept": "audio/wav" },
    body: JSON.stringify(body),
  });
  if (!r.ok) return json({ error: "tts_failed", provider: "huggingface", status: r.status, detail: (await r.text()).slice(0, 200) }, 502);
  const mime = r.headers.get("content-type") || "audio/wav";
  return json({ audio: b64encode(await r.arrayBuffer()), mime });
}

async function ttsChatterbox(text: string, voiceId = DEFAULT_VOICE): Promise<Response> {
  // Chatterbox (Resemble AI, MIT) via Replicate. Async predictions API: we ask
  // it to wait inline (Prefer: wait, up to 60s) and poll if it isn't done yet.
  const token = env("REPLICATE_API_TOKEN");
  const model = env("CHATTERBOX_MODEL") || "resemble-ai/chatterbox";
  const num = (k: string, d: number): number => {
    const v = parseFloat(env(k));
    return Number.isFinite(v) ? v : d;
  };
  let input: Record<string, unknown>;
  let voiceSample: string;
  if (voiceId === DEFAULT_VOICE) {
    // Byte-identical to the pre-preset request: same env keys, same defaults.
    input = {
      prompt: text,
      exaggeration: num("CHATTERBOX_EXAGGERATION", 0.5),
      cfg_weight: num("CHATTERBOX_CFG", 0.5),
      temperature: num("CHATTERBOX_TEMPERATURE", 0.8),
    };
    voiceSample = env("CHATTERBOX_VOICE_URL");
  } else {
    // Other presets use their own tuned exaggeration/cfg_weight/temperature
    // combo. A distinct clone sample per preset is optional
    // (CHATTERBOX_VOICE_URL_<NAME>); until one is set we fall back to the
    // single shared sample, so the voice still differs by tuning alone.
    const preset = VOICE_PRESETS[voiceId].chatterbox;
    input = {
      prompt: text,
      exaggeration: preset.exaggeration,
      cfg_weight: preset.cfgWeight,
      temperature: preset.temperature,
    };
    voiceSample = env(`CHATTERBOX_VOICE_URL_${voiceId.toUpperCase()}`) || env("CHATTERBOX_VOICE_URL");
  }
  if (voiceSample) input.audio_prompt = voiceSample;   // clone Rudy's voice from a sample

  const create = await fetch(`https://api.replicate.com/v1/models/${model}/predictions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      "Prefer": "wait",
    },
    body: JSON.stringify({ input }),
  });
  if (!create.ok) return json({ error: "tts_failed", provider: "chatterbox", status: create.status, detail: (await create.text()).slice(0, 200) }, 502);

  let pred = await create.json();
  const terminal = (s: string): boolean => s === "succeeded" || s === "failed" || s === "canceled";
  const getUrl: string | undefined = pred?.urls?.get;
  let tries = 0;
  while (getUrl && pred?.status && !terminal(pred.status) && tries < 30) {
    await new Promise((r) => setTimeout(r, 1500));
    const poll = await fetch(getUrl, { headers: { "Authorization": `Bearer ${token}` } });
    if (!poll.ok) break;
    pred = await poll.json();
    tries++;
  }
  if (pred?.status !== "succeeded") {
    return json({ error: "tts_failed", provider: "chatterbox", status: pred?.status || "unknown", detail: String(pred?.error || "").slice(0, 200) }, 502);
  }

  const out = Array.isArray(pred.output) ? pred.output[0] : pred.output;   // URL to a .wav
  if (!out || typeof out !== "string") return json({ error: "tts_no_audio", provider: "chatterbox" }, 502);
  const audioRes = await fetch(out);
  if (!audioRes.ok) return json({ error: "tts_fetch_failed", provider: "chatterbox", status: audioRes.status }, 502);
  const mime = audioRes.headers.get("content-type") || "audio/wav";
  return json({ audio: b64encode(await audioRes.arrayBuffer()), mime });
}

async function doTTS(text: string, voiceRaw?: unknown): Promise<Response> {
  const clean = (text || "").trim().slice(0, MAX_TTS_CHARS);
  if (!clean) return json({ error: "empty_text" }, 400);
  const voiceId = resolveVoiceId(voiceRaw);
  switch (ttsProvider()) {
    case "chatterbox": return await ttsChatterbox(clean, voiceId);
    case "elevenlabs": return await ttsElevenLabs(clean, voiceId);
    // Cloudflare MeloTTS's public API only takes { prompt, lang } — it has no
    // per-voice parameter, so every preset silently falls back to its one
    // default voice here, per spec.
    case "cloudflare": return await ttsCloudflare(clean);
    case "huggingface": return await ttsHuggingFace(clean, voiceId);
    default: return json({ unconfigured: true });
  }
}

// ── STT providers ──────────────────────────────────────────────────────────

async function sttGroq(bytes: Uint8Array<ArrayBuffer>, mime: string): Promise<Response> {
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: mime || "audio/webm" }), "clip");
  form.append("model", env("GROQ_STT_MODEL") || "whisper-large-v3-turbo");
  form.append("response_format", "json");
  const r = await fetch("https://api.groq.com/openai/v1/audio/transcriptions", {
    method: "POST",
    headers: { "Authorization": `Bearer ${env("GROQ_API_KEY")}` },
    body: form,
  });
  if (!r.ok) return json({ error: "stt_failed", provider: "groq", status: r.status, detail: (await r.text()).slice(0, 200) }, 502);
  const data = await r.json();
  return json({ text: String(data?.text || "").trim() });
}

async function sttCloudflare(bytes: Uint8Array<ArrayBuffer>): Promise<Response> {
  const acct = env("CLOUDFLARE_ACCOUNT_ID");
  const r = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${acct}/ai/run/@cf/openai/whisper`,
    {
      method: "POST",
      headers: { "Authorization": `Bearer ${env("CLOUDFLARE_API_TOKEN")}`, "Content-Type": "application/octet-stream" },
      body: bytes,
    },
  );
  if (!r.ok) return json({ error: "stt_failed", provider: "cloudflare", status: r.status, detail: (await r.text()).slice(0, 200) }, 502);
  const data = await r.json();
  return json({ text: String(data?.result?.text || "").trim() });
}

async function sttElevenLabs(bytes: Uint8Array<ArrayBuffer>, mime: string): Promise<Response> {
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: mime || "audio/webm" }), "clip");
  form.append("model_id", "scribe_v1");
  const r = await fetch("https://api.elevenlabs.io/v1/speech-to-text", {
    method: "POST",
    headers: { "xi-api-key": env("ELEVENLABS_API_KEY") },
    body: form,
  });
  if (!r.ok) return json({ error: "stt_failed", provider: "elevenlabs", status: r.status, detail: (await r.text()).slice(0, 200) }, 502);
  const data = await r.json();
  return json({ text: String(data?.text || "").trim() });
}

async function doSTT(audioB64: string, mime: string): Promise<Response> {
  const bytes = b64decode(audioB64 || "");
  if (!bytes.length) return json({ error: "empty_audio" }, 400);
  if (bytes.length > MAX_STT_BYTES) return json({ error: "audio_too_large" }, 413);
  switch (sttProvider()) {
    case "groq": return await sttGroq(bytes, mime);
    case "cloudflare": return await sttCloudflare(bytes);
    case "elevenlabs": return await sttElevenLabs(bytes, mime);
    default: return json({ unconfigured: true });
  }
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  let body: { mode?: string; text?: string; audio?: string; mime?: string; voice?: string };
  try {
    body = await req.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }

  try {
    if (body.mode === "tts") return await doTTS(body.text || "", body.voice);
    if (body.mode === "stt") return await doSTT(body.audio || "", body.mime || "");
    return json({ error: "unknown_mode" }, 400);
  } catch (err) {
    if (SENTRY_DSN) Sentry.captureException(err);
    return json({ error: "voice_error" }, 500);
  }
});
