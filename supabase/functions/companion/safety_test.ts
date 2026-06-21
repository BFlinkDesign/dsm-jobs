// Structural regression guard for the companion's safety-critical content.
//
// HONEST SCOPE: this is NOT a behavioral test that Ruby actually routes a user in
// crisis to the right place — that requires a live-model eval (the behavioral half,
// tracked separately). What this DOES guarantee is that the verified crisis
// resources, the not-a-therapist boundary, the locked CORS origin, and the
// availability-drop guard cannot be silently deleted from the source by a future
// prompt edit, model swap, or refactor without CI failing loudly.
//
// It reads the source rather than importing index.ts (which calls Deno.serve at
// module load). Run with: deno test --allow-read
import { assert, assertStringIncludes } from "jsr:@std/assert@1";

const src = await Deno.readTextFile(new URL("./index.ts", import.meta.url));

Deno.test("verified crisis resources are present verbatim (988 / Your Life Iowa)", () => {
  assertStringIncludes(src, "988", "the 988 lifeline must stay in the prompt");
  assertStringIncludes(src, "855-581-8111", "Your Life Iowa call line must stay");
  assertStringIncludes(src, "855-895-8398", "Your Life Iowa text line must stay");
});

Deno.test("the not-a-therapist + no-advice boundary is present", () => {
  assertStringIncludes(src, "NOT a therapist");
  assertStringIncludes(src, "No financial, legal, or medical advice");
});

Deno.test("CORS is locked to the production origin", () => {
  assertStringIncludes(
    src,
    '"Access-Control-Allow-Origin": "https://bflinkdesign.github.io"',
  );
});

Deno.test("the save path still drops time/confidence (no availability override)", () => {
  assert(
    src.includes('k !== "time"') && src.includes('k !== "confidence"'),
    "save_profile must keep dropping time/confidence so a stored value can't reopen 'open on hours'",
  );
});
