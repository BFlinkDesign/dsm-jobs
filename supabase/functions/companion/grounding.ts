// Anti-confabulation grounding for the companion (Ruby), extracted from index.ts
// so it can be unit-tested WITHOUT importing the function's Deno.serve entrypoint
// (importing index.ts would start the HTTP server). Supabase bundles sibling files
// in a function directory, so this deploys with companion unchanged.
//
// knownFacts builds the ONLY record of her the model may use: a baseline truth
// (set by Brady, not assumable by the model) plus facts she actually stated, each
// source-tagged. `time`/availability is governed ONLY by the baseline line, so a
// stale stored value can never reopen "open on hours".
export function knownFacts(profile: Record<string, unknown> | null | undefined): string {
  const lines = [
    "- availability: she needs DAYTIME hours (she is raising her son); flexible ONLY if the job is remote. She is NOT open on hours for in-person work. [set by Brady]",
  ];
  const labels: Record<string, string> = {
    kind: "kind of work she likes",
    where: "in-person vs remote",
    pay: "pay priority",
    notes: "note",
  };
  for (const [k, raw] of Object.entries(profile ?? {})) {
    if (k === "time" || k === "confidence") continue; // availability is baseline-only
    const isObj = raw !== null && typeof raw === "object";
    const v = isObj && "v" in (raw as object) ? (raw as { v: unknown }).v : raw;
    const src = isObj && "src" in (raw as object) ? (raw as { src: string }).src : "earlier note";
    if (v == null || v === "") continue;
    lines.push(`- ${labels[k] ?? k}: ${v} [${src}]`);
  }
  return "KNOWN FACTS about her — the ONLY things you actually know about her. " +
    "If something is not on this list, you do NOT know it:\n" + lines.join("\n");
}
