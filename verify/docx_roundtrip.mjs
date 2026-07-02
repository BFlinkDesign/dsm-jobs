// Round-trip harness for the zero-dependency .docx writer (app/src/scripts/docx.ts)
// against the hand-rolled .docx reader (app/src/scripts/resume.ts). Run directly
// with a type-stripping node (>=22.6, tested against 22.18+): `node verify/docx_roundtrip.mjs`.
//
// Also invoked from tests/test_js_smoke.py, which skips cleanly if node is
// missing or too old to strip types.
//
// This does NOT reimplement the ZIP/XML reader — it drives the app's own
// extractResumeFile() (resume.ts) with a fake File-like object, so the test
// proves the writer and the reader actually agree, not just that the writer
// matches what the test author assumed the reader wants.

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const docxMod = await import(join(here, "..", "app", "src", "scripts", "docx.ts"));
const resumeMod = await import(join(here, "..", "app", "src", "scripts", "resume.ts"));

const { buildDocx } = docxMod;
const { extractResumeFile } = resumeMod;

function assert(cond, msg) {
  if (!cond) {
    console.error("DOCX FAIL:", msg);
    process.exit(1);
  }
}

// Same normalization the reader applies (docxXmlToText in resume.ts): strip
// trailing spaces/tabs before a newline, collapse 3+ newlines to 2, trim ends.
function readerNormalize(s) {
  return s
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// --- CRC-32 + local ZIP parsing, just to check the writer's own bytes (not
// to duplicate the docx->text logic — that stays solely in resume.ts).
function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) {
    crc ^= bytes[i];
    for (let k = 0; k < 8; k++) crc = crc & 1 ? (0xedb88320 ^ (crc >>> 1)) : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function checkZipStructure(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= 0; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  assert(eocd >= 0, "no EOCD record found");
  const cnt = dv.getUint16(eocd + 10, true);
  const cdOff = dv.getUint32(eocd + 16, true);
  assert(cnt === 3, `expected 3 zip entries, got ${cnt}`);

  let p = cdOff;
  const names = [];
  for (let n = 0; n < cnt; n++) {
    assert(dv.getUint32(p, true) === 0x02014b50, "bad central directory signature");
    const method = dv.getUint16(p + 10, true);
    assert(method === 0, `expected STORED (method 0), got ${method}`);
    const crcExpected = dv.getUint32(p + 16, true);
    const compSize = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true);
    const extraLen = dv.getUint16(p + 30, true);
    const cmtLen = dv.getUint16(p + 32, true);
    const localOff = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(p + 46, p + 46 + nameLen));
    names.push(name);

    // Validate the local header + data, and recompute the CRC ourselves.
    assert(dv.getUint32(localOff, true) === 0x04034b50, `bad local header for ${name}`);
    const lNameLen = dv.getUint16(localOff + 26, true);
    const lExtraLen = dv.getUint16(localOff + 28, true);
    const dataStart = localOff + 30 + lNameLen + lExtraLen;
    const data = bytes.subarray(dataStart, dataStart + compSize);
    const crcActual = crc32(data);
    assert(crcActual === crcExpected, `CRC mismatch for ${name}: header says ${crcExpected}, data hashes to ${crcActual}`);

    p += 46 + nameLen + extraLen + cmtLen;
  }
  assert(names.includes("word/document.xml"), "missing word/document.xml entry");
  assert(names.includes("[Content_Types].xml"), "missing [Content_Types].xml entry");
  assert(names.includes("_rels/.rels"), "missing _rels/.rels entry");
}

async function roundTrip(text) {
  const bytes = buildDocx(text);
  checkZipStructure(bytes);
  const fakeFile = {
    name: "resume.docx",
    type: "",
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  };
  const roundTripped = await extractResumeFile(fakeFile);
  const expected = readerNormalize(text);
  assert(
    roundTripped === expected,
    `round trip mismatch.\n--- expected (normalized) ---\n${JSON.stringify(expected)}\n--- got ---\n${JSON.stringify(roundTripped)}`,
  );
}

const samples = [
  "Just one line.",
  "Line one\nLine two\nLine three",
  "Leading text\n\nBlank line above, then this.",
  "Tabs:\tone\ttwo\tthree\nMore\ttabs\there",
  `Ampersand & less< greater> quote" apostrophe' backtick\``,
  "Unicode: café, naïve, 日本語, emoji ✦ — em dash, ‘smart quotes’",
  "Multiple\n\n\nblank\n\n\n\nlines collapse per the reader's own normalization",
  "",
];

for (const sample of samples) {
  await roundTrip(sample);
}

// A realistic multi-paragraph résumé shape, combined into one longer check.
const resumeLike = [
  "Jane Doe",
  "Des Moines, IA — jane@example.com",
  "",
  "EXPERIENCE",
  "Front Desk Clerk — Example Co.",
  "\tAnswered phones & greeted 50+ visitors/day",
  "\tFiled \"priority\" mail; balanced petty cash",
  "",
  "EDUCATION",
  "Diploma, Some High School",
].join("\n");
await roundTrip(resumeLike);

console.log("DOCX OK");
