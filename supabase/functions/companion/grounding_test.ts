// Executable tests for the anti-confabulation grounding (knownFacts). These run
// the REAL function — they are not source greps. They prove the load-bearing
// behavior of PR #89: the baseline availability truth is injected every turn, a
// stored time/availability value can never leak into the facts block, and legacy
// profile shapes still render.
import { assert, assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import { knownFacts } from "./grounding.ts";

const BASELINE = "availability: she needs DAYTIME hours";

Deno.test("baseline availability is injected with a null profile", () => {
  const out = knownFacts(null);
  assertStringIncludes(out, BASELINE);
  assertStringIncludes(out, "NOT open on hours for in-person work");
  assertStringIncludes(out, "[set by Brady]");
  assertStringIncludes(out, "KNOWN FACTS about her");
});

Deno.test("baseline is the ONLY fact with an empty profile", () => {
  const out = knownFacts({});
  assertStringIncludes(out, BASELINE);
  // One bullet only: the baseline. (Each rendered fact begins with "\n- ".)
  assertEquals(out.split("\n- ").length, 2);
});

Deno.test("time and confidence are NEVER rendered — availability is baseline-only", () => {
  const out = knownFacts({
    time: { v: "evening", src: "confirmed-in-chat" },
    confidence: 0.9,
    kind: { v: "people", src: "confirmed-in-chat" },
  });
  assert(!out.includes("evening"), "a stored 'time' must never reach the facts block");
  assert(!out.toLowerCase().includes("confidence"), "'confidence' must never be rendered");
  // a legitimately stated fact still renders, source-tagged
  assertStringIncludes(out, "kind of work she likes: people [confirmed-in-chat]");
  // and the only availability statement is still the baseline
  assertStringIncludes(out, BASELINE);
});

Deno.test("backward-compat: legacy scalar profile values still render", () => {
  const out = knownFacts({ where: "home", notes: "" });
  assertStringIncludes(out, "in-person vs remote: home [earlier note]");
  assert(!out.includes("note: "), "empty values produce no bullet");
});

Deno.test("source-tagged object form uses v + src", () => {
  const out = knownFacts({ pay: { v: "must", src: "confirmed-in-chat" } });
  assertStringIncludes(out, "pay priority: must [confirmed-in-chat]");
});
