# Rudy Frontier Experience Plan

Date: 2026-06-24 (status refreshed 2026-06-27)

## Status update (2026-06-27)

Build-order item 1 shipped and was carried further than originally scoped;
item 4 shipped under separate feature work; item 2 shipped via a cheaper,
open-source route instead of the OpenAI Realtime path this plan originally
proposed. Details below each item. Items 3, 5 (partial), 6, 7, 8 remain open.

**Shipped beyond this plan's original scope, landed by other feature work
in the same window (not tracked as build-order items here, noted for
completeness):** personalized "Today's picks" ranking + reasons, newest-first
sort, hard exclusion filters (no food service/retail/nights/weekends),
a Money & Free-Skills resources hub, a splashy PWA update prompt, and a
rebuilt interactive coachmark walkthrough (Wispr-Flow-grade step-by-step
guidance). See `CLAUDE.md` "Shipped" log for the full list.

## Scope

Rudy is the emotional-support cow for the dsm-jobs user. Rudy is not just a job helper: Rudy should help with nerves, overwhelm, confidence, real-life wording, and the next practical move. The default experience must stay calm and safe for a stressed non-technical user. Higher-energy tone and voice playback are explicit opt-ins.

This is feature reverse engineering, not proprietary reverse engineering. We observe frontier product UX, extract primitives, map them to public APIs or open protocols, build our own controlled version, and promote only after tests, logs, and safety gates.

## Source-Grounded Primitives

- OpenAI Realtime supports browser/mobile voice through server-created short-lived client secrets and WebRTC. The main API key stays on the server. Session config can include audio, transcription, noise reduction, and turn detection.
- MCP exposes connector capabilities as discoverable tools with names, descriptions, and JSON input schemas through `tools/list`. This gives us a contract-testable connector layer.
- SwiftUI supports native pull-to-refresh via `.refreshable { await ... }`, safe-area handling, and accessibility child behavior. This repo has no Swift/iOS project today, so native iOS work belongs in a separate app or future package; the current repo can only improve PWA/mobile-web behavior.

Docs consulted through Context7:

- https://developers.openai.com/api/docs/api-reference/realtime-sessions/create-realtime-client-secret
- https://developers.openai.com/api/docs/guides/realtime-webrtc
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/server/tools.mdx
- https://developer.apple.com/documentation/swiftui/view/refreshable%28action%3A%29

## Feature Reverse Engineering Record

## Feature

Rudy voice, memory, tools, and frontier-style emotional-support chat.

## Products Observed

ChatGPT, Claude, Gemini, and MCP-based agent tooling at the public feature/API level.

## Observed Behavior

- Voice is opt-in, interruptible, and conversational.
- Memory is scoped and explainable.
- Tools are visible through user-facing action logs or confirmations.
- Tone can be warm or lively, but safety boundaries still hold.
- Advanced output appears as durable artifacts, not disposable chat only.

## Capability Primitives

- Realtime audio session with mic permission, VAD, barge-in, transcript, and audio output.
- Long-term profile memory plus per-session chat history.
- Tool registry with schemas, allowlists, and human confirmation for side effects.
- Document ingestion for resumes, job posts, and notes.
- Artifact viewer for tailored resumes, cover notes, follow-ups, diffs, and downloads.
- Evals and logs for safety, latency, cost, and regression scoring.

## Public Implementation Route

- Now: Astro + TypeScript + Supabase Edge Function companion; real TTS/STT via the provider-agnostic `voice` edge function (Chatterbox/Kokoro/MeloTTS/ElevenLabs for speech out, Groq/Cloudflare/ElevenLabs Whisper for speech in via `MediaRecorder`), falling back to browser SpeechSynthesis/SpeechRecognition only when no provider key is configured; Supabase storage/history.
- Next voice (if needed): server-minted Realtime client secret endpoint, browser WebRTC, no OpenAI API key in browser — for interruption/barge-in, which the current REST-call approach doesn't support.
- Next connector layer: MCP-style tool contracts with JSON schemas and explicit permissions.
- Native iOS parity: SwiftUI `.refreshable`, safe-area-aware layouts, accessibility grouping, and native voice session shell if/when a Swift app exists.

## MVP Behavior

- Rudy text chat stays two-way through the real companion Edge Function.
- Read-aloud is OFF by default and turns on only when the user taps it.
- Spicy tone is OFF by default and is explicitly marked as tone, not sexual content.
- Rudy can talk about jobs, nerves, life, confidence, and practical next steps.
- Rudy continues to route crisis content to the verified help resources and does not claim to be therapy.

## Test Cases

- Static guard proves `rudySpeak` only enables when localStorage is `"1"`.
- Static guard proves spicy mode is opt-in and sent to the Edge Function.
- Edge safety test proves the spicy prompt cannot delete non-sexual/safety boundaries.
- Grounding test proves the baseline known fact still renders every turn.
- CI scan test proves the daily workflow uses unbuffered logs, a bounded command timeout, and Daddy contact copy.

## Failure Modes

- Browser speech APIs may be unavailable. Hide mic/speaker controls and keep typed chat.
- Network or Edge Function failure. Keep local chat copy and show a calm retry message.
- Tone drift. Static and behavioral evals must preserve no-therapy, no-explicit-content, no-confabulation, and crisis-routing rules.
- Tool risk. No side-effecting connector action may run without visible confirmation.

## Human Gates

- No hidden tool execution.
- No production mutation by voice.
- No sending messages, applying to jobs, deleting documents, or changing account data without confirmation.
- No secret readout.
- No medical, legal, financial, or crisis advice beyond routing to verified resources.

## Promotion Decision

Adopted Rudy v1 (shipped): opt-in voice playback, opt-in spicy tone, broader emotional-support scope, prompt safety tests, and CI observability.

Rudy v2 voice shipped via a different route than originally proposed (see item 2 below) — real TTS/STT instead of browser `speechSynthesis`/`SpeechRecognition`, but through a provider-agnostic REST edge function rather than OpenAI Realtime/WebRTC. Cheaper, keeps with the project's $0-platform/open-source bias, and iOS Safari/PWA has no WebRTC-mic blocker to work around this way. Realtime/WebRTC barge-in interruption is not implemented; still a candidate if turn-taking latency becomes a real complaint.

## Build Order

1. **Shipped.** Rudy v1 opt-in voice playback and spicy tone (#124).
2. **Shipped in source, live provider proof still required.** Real TTS/STT voice, not the OpenAI Realtime/WebRTC token flow this plan proposed. `supabase/functions/voice` is a provider-agnostic edge function: TTS defaults to **Chatterbox** (Resemble AI, MIT-licensed, open-source, served via Replicate) with Hugging Face Kokoro-82M, Cloudflare MeloTTS, and ElevenLabs as fallbacks; STT defaults to Groq Whisper with Cloudflare Whisper and ElevenLabs as fallbacks (#143, #144; #144 merged 2026-07-01). Mic input moved from browser `SpeechRecognition` (unsupported on iOS Safari/PWA, her only device) to `MediaRecorder` + server-side transcription, which does work on iOS. If no provider key is configured, calls return `{ unconfigured: true }` and the client falls back to browser voice. Not done until live proof exists: Supabase secret-name check after snapshot, `voice` deploy/canary, real Chatterbox playback, mic transcription proof, interruption/barge-in, live transcript-while-speaking, cost/latency telemetry, and a visible action log.
3. **Not started.** Rudy memory viewer: "What Rudy remembers" with delete/edit controls.
4. **Shipped, as separate feature work.** Application artifacts: resume, cover note, follow-up, diff, ATS notes, version history (#121, #122, #123; résumé tailor edge function).
5. **Partially shipped.** Document-aware chat: the résumé tailor already reads the full job posting text (`descFull`) and the saved résumé to draft a tailored pack, but Rudy's general chat can't yet answer freeform questions about an uploaded résumé or an application pack outside that flow.
6. **Not started.** MCP-style connector registry with contract tests and permission gates.
7. **Not started.** Deep research/import flow for pasted job pages with citations and audit trail.
8. **Not started, still out of scope.** Native iOS shell if a Swift project is added — no Swift/iOS project exists in this repo.
