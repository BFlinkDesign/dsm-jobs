# Rudy Frontier Experience Plan

Date: 2026-06-24

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

- Now: Astro + TypeScript + Supabase Edge Function companion, browser SpeechRecognition, browser SpeechSynthesis, Supabase storage/history.
- Next voice: server-minted Realtime client secret endpoint, browser WebRTC, no OpenAI API key in browser.
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

Adopt Rudy v1 now: opt-in voice playback, opt-in spicy tone, broader emotional-support scope, prompt safety tests, and CI observability.

Prototype Rudy v2 next: Realtime/WebRTC voice with server-created ephemeral client secrets, interruption handling, transcript, cost/latency telemetry, and a visible action log.

## Build Order

1. Rudy v1 opt-in voice playback and spicy tone.
2. Realtime voice token Edge Function and WebRTC client shell.
3. Rudy memory viewer: "What Rudy remembers" with delete/edit controls.
4. Application artifacts: resume, cover note, follow-up, diff, ATS notes, version history.
5. Document-aware chat: ask Rudy about uploaded resumes and application packs.
6. MCP-style connector registry with contract tests and permission gates.
7. Deep research/import flow for pasted job pages with citations and audit trail.
8. Native iOS shell if a Swift project is added.
