// Document/job grounding for the companion (Rudy), extracted like grounding.ts
// so it can be unit-tested WITHOUT importing the function's Deno.serve
// entrypoint. Supabase bundles sibling files in a function directory, so this
// deploys with companion unchanged.
//
// WHY THIS EXISTS: the résumé-tailor edge function already reads her full
// résumé + a job posting for its one-shot tailored draft, but general Rudy
// chat couldn't answer freeform questions like "does my résumé mention
// customer service?" or "what does this posting actually pay?" — this module
// builds the SAME kind of grounded context block for THAT chat, gated so it
// only rides along when a document or job is actually active in the UI (never
// unconditionally on every turn — see the payload-size note below).
//
// ANTI-CONFABULATION: this outranks the voice, same as knownFacts(). The block
// this builds is explicit that it is the ONLY source of truth for the résumé
// text / job posting text, and instructs the model to say plainly when the
// answer isn't in the provided text rather than guess or invent. That
// instruction lives here (co-located with the data it governs) AND is
// reinforced in index.ts's SYSTEM_PROMPT so it can never be dropped by only
// editing one file.

// Hard caps keep a single chat turn's payload sane: her chat history costs
// nothing extra per turn (MAX_TURNS in index.ts already bounds that), but a
// full résumé or a full job posting is much bigger than a chat line, so each
// gets its own cap, independent of and smaller than resume-tailor's caps
// (12,000 / 16,000 chars there — this is a side-channel to a chat reply, not
// the main event, so it stays leaner).
export const MAX_RESUME_CHARS = 6000;
export const MAX_JOB_TEXT_CHARS = 6000;

export type ActiveResumeDoc = {
  name?: unknown;
  text?: unknown;
};

export type ActiveJobContext = {
  title?: unknown;
  company?: unknown;
  descFull?: unknown;
  about?: unknown;
};

function truncate(s: string, max: number): { text: string; truncated: boolean } {
  const trimmed = s.trim();
  if (trimmed.length <= max) return { text: trimmed, truncated: false };
  return { text: trimmed.slice(0, max), truncated: true };
}

// Builds the résumé half of the context block, or "" if no usable document is
// active. Only a NAME + TEXT are ever read from the caller's payload — nothing
// else about the document (no ids, no other profile fields) crosses into the
// prompt.
export function resumeContextBlock(doc: ActiveResumeDoc | null | undefined): string {
  const text = typeof doc?.text === "string" ? doc.text : "";
  if (!text.trim()) return "";
  const name = typeof doc?.name === "string" && doc.name.trim() ? doc.name.trim() : "Résumé";
  const { text: body, truncated } = truncate(text, MAX_RESUME_CHARS);
  return (
    `HER ACTIVE RÉSUMÉ DOCUMENT ("${name}") — the ONLY source of truth about her résumé. ` +
    `Answer résumé questions ONLY from this text. If something isn't in it, say plainly you ` +
    `don't see that in her saved résumé rather than guessing:\n${body}` +
    (truncated ? "\n[...document truncated for length...]" : "")
  );
}

// Builds the job half of the context block, or "" if no usable job is active.
// Prefers the full posting text (descFull); falls back to the short "about"
// blurb if that's all that's available; title/company alone still grounds
// "what's this job called" without inventing a description.
export function jobContextBlock(job: ActiveJobContext | null | undefined): string {
  const title = typeof job?.title === "string" ? job.title.trim() : "";
  const company = typeof job?.company === "string" ? job.company.trim() : "";
  const full = typeof job?.descFull === "string" ? job.descFull.trim() : "";
  const about = typeof job?.about === "string" ? job.about.trim() : "";
  if (!title && !company && !full && !about) return "";
  const bodyRaw = full || about;
  const { text: body, truncated } = bodyRaw ? truncate(bodyRaw, MAX_JOB_TEXT_CHARS) : { text: "", truncated: false };
  return (
    `THE JOB POSTING SHE'S LOOKING AT — the ONLY source of truth about it. Answer questions about ` +
    `pay, duties, or requirements ONLY from this text (job ads often omit pay; if it's not stated ` +
    `here, say plainly it's not listed rather than guessing a number):\n` +
    `Title: ${title || "(not given)"}\nEmployer: ${company || "(not given)"}\n` +
    `Description:\n${body || "(only the title/employer were provided — no posting text)"}` +
    (truncated ? "\n[...posting truncated for length...]" : "")
  );
}

// Combines both halves into zero, one, or two system-prompt blocks. Returns an
// empty array when neither a résumé document nor a job is active — the
// unconditional case, so a plain chat turn's payload never grows.
export function documentContextBlocks(
  doc: ActiveResumeDoc | null | undefined,
  job: ActiveJobContext | null | undefined,
): string[] {
  const blocks: string[] = [];
  const resume = resumeContextBlock(doc);
  if (resume) blocks.push(resume);
  const jobBlock = jobContextBlock(job);
  if (jobBlock) blocks.push(jobBlock);
  return blocks;
}
