// Anti-confabulation grounding for the companion (Rudy), extracted from index.ts
// so it can be unit-tested WITHOUT importing the function's Deno.serve entrypoint
// (importing index.ts would start the HTTP server). Supabase bundles sibling files
// in a function directory, so this deploys with companion unchanged.
//
// knownFacts builds the ONLY record of her the model may use: a baseline truth
// (set by Daddy, not assumable by the model), facts she actually stated, and the
// saved résumé documents she explicitly provided. `time`/availability is governed
// ONLY by the baseline line, so a stale stored value can never reopen "open on
// hours".
const MAX_ACTIVE_RESUME_CHARS = 6500;
const MAX_OTHER_RESUME_CHARS = 1200;

function compactText(raw: unknown, limit: number): string {
  const text = String(raw ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  return text.slice(0, limit).trimEnd() + " ... [truncated]";
}

function savedResumeFacts(profile: Record<string, unknown> | null | undefined): string {
  const p = profile ?? {};
  const activeId = typeof p.activeDocumentId === "string" ? p.activeDocumentId : "";
  const docs = Array.isArray(p.documents) ? p.documents : [];
  const rendered: string[] = [];

  for (const rawDoc of docs.slice(0, 4)) {
    if (!rawDoc || typeof rawDoc !== "object") continue;
    const doc = rawDoc as Record<string, unknown>;
    const name = compactText(doc.name || "Saved résumé", 120);
    const text = compactText(
      doc.text,
      doc.id === activeId ? MAX_ACTIVE_RESUME_CHARS : MAX_OTHER_RESUME_CHARS,
    );
    if (!text) continue;
    const marker = doc.id === activeId ? "active" : "saved";
    rendered.push(`- ${name} (${marker}): ${text}`);
  }

  if (!rendered.length && typeof p.resume === "string" && p.resume.trim()) {
    rendered.push(`- Legacy saved résumé (active): ${compactText(p.resume, MAX_ACTIVE_RESUME_CHARS)}`);
  }

  if (!rendered.length) {
    return "SAVED RÉSUMÉ DOCUMENTS Rudy may discuss: none saved.";
  }
  return "SAVED RÉSUMÉ DOCUMENTS Rudy may discuss — use ONLY this text when answering résumé/document questions. " +
    "If the answer is not in this text, say you do not see it in the saved résumé:\n" +
    rendered.join("\n");
}

export function knownFacts(profile: Record<string, unknown> | null | undefined): string {
  const lines = [
    "- availability: she needs DAYTIME hours (she is raising her son); flexible ONLY if the job is remote. She is NOT open on hours for in-person work. [set by Daddy]",
  ];
  const labels: Record<string, string> = {
    kind: "kind of work she likes",
    where: "in-person vs remote",
    pay: "pay priority",
    notes: "note",
  };
  for (const [k, raw] of Object.entries(profile ?? {})) {
    if (
      k === "time" || k === "confidence" ||
      k === "documents" || k === "activeDocumentId" || k === "resume"
    ) continue; // availability is baseline-only; documents render in their own bounded block
    const isObj = raw !== null && typeof raw === "object";
    const v = isObj && "v" in (raw as object) ? (raw as { v: unknown }).v : raw;
    const src = isObj && "src" in (raw as object) ? (raw as { src: string }).src : "earlier note";
    if (v == null || v === "") continue;
    lines.push(`- ${labels[k] ?? k}: ${v} [${src}]`);
  }
  return "KNOWN FACTS about her — the ONLY things you actually know about her. " +
    "If something is not on this list, you do NOT know it:\n" + lines.join("\n") +
    "\n\n" + savedResumeFacts(profile);
}
