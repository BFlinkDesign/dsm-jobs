// Executable tests for document/job grounding (resumeContextBlock,
// jobContextBlock, documentContextBlocks). These run the REAL functions —
// not source greps — mirroring grounding_test.ts's pattern. They prove:
// (1) a plain chat turn (no active doc/job) adds NOTHING to the prompt,
// (2) an active résumé document rides along, truncated at a sane cap,
// (3) an active job rides along preferring the full posting over the blurb,
// (4) the anti-confabulation instruction text is present in both blocks so a
//     future edit can't silently drop the "say plainly you don't know" rule.
import { assert, assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import {
  documentContextBlocks,
  jobContextBlock,
  MAX_JOB_TEXT_CHARS,
  MAX_RESUME_CHARS,
  resumeContextBlock,
} from "./doc_context.ts";

Deno.test("no active document or job => no context blocks at all", () => {
  assertEquals(documentContextBlocks(null, null), []);
  assertEquals(documentContextBlocks(undefined, undefined), []);
  assertEquals(documentContextBlocks({ name: "x", text: "" }, { title: "" }), []);
});

Deno.test("resumeContextBlock is empty when text is missing or blank", () => {
  assertEquals(resumeContextBlock(null), "");
  assertEquals(resumeContextBlock({ name: "Résumé" }), "");
  assertEquals(resumeContextBlock({ name: "Résumé", text: "   " }), "");
});

Deno.test("resumeContextBlock includes the doc name and full text under the cap", () => {
  const out = resumeContextBlock({ name: "Office résumé", text: "Answered phones. Managed calendars." });
  assertStringIncludes(out, "Office résumé");
  assertStringIncludes(out, "Answered phones. Managed calendars.");
  assertStringIncludes(out, "the ONLY source of truth about her résumé");
  assertStringIncludes(out, "don't see that in her saved résumé rather than guessing");
});

Deno.test("resumeContextBlock defaults the name and truncates oversized text", () => {
  const big = "x".repeat(MAX_RESUME_CHARS + 500);
  const out = resumeContextBlock({ text: big });
  assertStringIncludes(out, '"Résumé"');
  assertStringIncludes(out, "[...document truncated for length...]");
  assert(out.length < big.length + 500, "truncated block must be meaningfully smaller than the raw oversized input");
});

Deno.test("jobContextBlock is empty when nothing about the job is given", () => {
  assertEquals(jobContextBlock(null), "");
  assertEquals(jobContextBlock({}), "");
  assertEquals(jobContextBlock({ title: "", company: "", descFull: "", about: "" }), "");
});

Deno.test("jobContextBlock prefers descFull over about, and includes anti-guess pay instruction", () => {
  const out = jobContextBlock({
    title: "Office Assistant",
    company: "Acme Co",
    descFull: "Full posting text with duties and pay range $18-20/hr.",
    about: "short blurb",
  });
  assertStringIncludes(out, "Office Assistant");
  assertStringIncludes(out, "Acme Co");
  assertStringIncludes(out, "Full posting text with duties and pay range $18-20/hr.");
  assert(!out.includes("short blurb"), "descFull should win over the shorter about blurb when both are present");
  assertStringIncludes(out, "say plainly it's not listed rather than guessing a number");
});

Deno.test("jobContextBlock falls back to about when descFull is absent", () => {
  const out = jobContextBlock({ title: "Receptionist", company: "Beta Inc", about: "Front desk role, answers phones." });
  assertStringIncludes(out, "Front desk role, answers phones.");
});

Deno.test("jobContextBlock still grounds title/company only, without inventing a description", () => {
  const out = jobContextBlock({ title: "Data Entry Clerk", company: "Gamma LLC" });
  assertStringIncludes(out, "Data Entry Clerk");
  assertStringIncludes(out, "Gamma LLC");
  assertStringIncludes(out, "no posting text");
});

Deno.test("jobContextBlock truncates an oversized posting", () => {
  const big = "y".repeat(MAX_JOB_TEXT_CHARS + 500);
  const out = jobContextBlock({ title: "Clerk", descFull: big });
  assertStringIncludes(out, "[...posting truncated for length...]");
});

Deno.test("documentContextBlocks returns both blocks in order when both are active", () => {
  const blocks = documentContextBlocks(
    { name: "Main résumé", text: "Ten years of admin experience." },
    { title: "Admin Assistant", company: "Delta Co", descFull: "Assist the office manager daily." },
  );
  assertEquals(blocks.length, 2);
  assertStringIncludes(blocks[0], "Main résumé");
  assertStringIncludes(blocks[1], "Admin Assistant");
});

Deno.test("documentContextBlocks returns only the résumé block when no job is active", () => {
  const blocks = documentContextBlocks({ name: "Résumé", text: "Real experience here." }, null);
  assertEquals(blocks.length, 1);
  assertStringIncludes(blocks[0], "Real experience here.");
});
