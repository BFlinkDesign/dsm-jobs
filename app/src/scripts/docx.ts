/**
 * Zero-dependency minimal .docx writer (mirrors the hand-rolled docx *reader*
 * in resume.ts — same "no libraries, just DataView/TextEncoder + the ZIP
 * spec" style). The app only depends on astro + supabase-js; pulling in a
 * docx-writing package for one button isn't worth it.
 *
 * Why this exists: the tailored résumé used to download as a .txt file, but
 * the City of Des Moines job portal (and most ATS upload widgets) reject
 * plain text — it only accepts .doc/.docx/.xls/.xlsx/.pdf/.gif/.tiff/.tif/
 * .jpeg/.jpg/.htm/.html/.wpd/.wp. A minimal, valid .docx clears that gate.
 *
 * buildDocx() emits just enough OOXML for Word/Pages/Google Docs (and our
 * own docxToText() reader in resume.ts) to open it: [Content_Types].xml,
 * _rels/.rels, and word/document.xml, zipped with STORED (uncompressed)
 * entries so there's no deflate implementation to carry.
 */

function xmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

// One line of the résumé -> the runs inside a single <w:p>. Tabs become
// <w:tab/> runs (matches how resume.ts's docxXmlToText turns <w:tab/> back
// into "\t"); an empty line becomes a self-closing <w:p/> (which
// docxXmlToText also maps back to a bare "\n").
function lineToRunsXml(line: string): string {
  if (line === "") return "";
  const segments = line.split("\t");
  const runs: string[] = [];
  segments.forEach((seg, i) => {
    if (seg !== "") runs.push(`<w:r><w:t xml:space="preserve">${xmlEscape(seg)}</w:t></w:r>`);
    if (i < segments.length - 1) runs.push("<w:r><w:tab/></w:r>");
  });
  return runs.join("");
}

function textToParagraphsXml(text: string): string {
  return text
    .split("\n")
    .map((line) => {
      const runs = lineToRunsXml(line);
      return runs ? `<w:p>${runs}</w:p>` : "<w:p/>";
    })
    .join("");
}

function buildDocumentXml(text: string): string {
  const body = textToParagraphsXml(text);
  return (
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">` +
    `<w:body>${body}` +
    `<w:sectPr>` +
    `<w:pgSz w:w="12240" w:h="15840"/>` +
    `<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>` +
    `</w:sectPr>` +
    `</w:body>` +
    `</w:document>`
  );
}

const CONTENT_TYPES_XML =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
  `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
  `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
  `<Default Extension="xml" ContentType="application/xml"/>` +
  `<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>` +
  `</Types>`;

const RELS_XML =
  `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
  `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
  `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>` +
  `</Relationships>`;

// ---- minimal ZIP (STORED entries only — no deflate to implement) ----

type ZipEntry = { name: string; data: Uint8Array };

function u16(n: number): Uint8Array<ArrayBuffer> {
  return new Uint8Array([n & 0xff, (n >>> 8) & 0xff]);
}

function u32(n: number): Uint8Array<ArrayBuffer> {
  return new Uint8Array([n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff]);
}

function concatAll(chunks: Uint8Array[]): Uint8Array<ArrayBuffer> {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

let crcTable: Uint32Array | null = null;
function getCrcTable(): Uint32Array {
  if (crcTable) return crcTable;
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  crcTable = table;
  return table;
}

function crc32(bytes: Uint8Array): number {
  const table = getCrcTable();
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = table[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

// DOS date/time packed fields used by the local + central-directory headers.
function dosDateTime(d: Date): { time: number; date: number } {
  const time = ((d.getHours() & 0x1f) << 11) | ((d.getMinutes() & 0x3f) << 5) | ((d.getSeconds() >> 1) & 0x1f);
  const year = Math.max(0, d.getFullYear() - 1980);
  const date = ((year & 0x7f) << 9) | (((d.getMonth() + 1) & 0xf) << 5) | (d.getDate() & 0x1f);
  return { time, date };
}

function zipStore(entries: ZipEntry[]): Uint8Array<ArrayBuffer> {
  const { time, date } = dosDateTime(new Date());
  const UTF8_FLAG = 0x0800; // general-purpose bit 11: names are UTF-8
  const localChunks: Uint8Array[] = [];
  const centralChunks: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBytes = new TextEncoder().encode(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    const localHeader = concatAll([
      u32(0x04034b50),
      u16(20), // version needed to extract
      u16(UTF8_FLAG),
      u16(0), // method: stored
      u16(time),
      u16(date),
      u32(crc),
      u32(size), // compressed size == size (stored)
      u32(size), // uncompressed size
      u16(nameBytes.length),
      u16(0), // extra field length
      nameBytes,
    ]);
    localChunks.push(localHeader, entry.data);

    const centralHeader = concatAll([
      u32(0x02014b50),
      u16(20), // version made by
      u16(20), // version needed to extract
      u16(UTF8_FLAG),
      u16(0), // method: stored
      u16(time),
      u16(date),
      u32(crc),
      u32(size),
      u32(size),
      u16(nameBytes.length),
      u16(0), // extra field length
      u16(0), // file comment length
      u16(0), // disk number start
      u16(0), // internal file attributes
      u32(0), // external file attributes
      u32(offset), // relative offset of local header
      nameBytes,
    ]);
    centralChunks.push(centralHeader);

    offset += localHeader.length + entry.data.length;
  }

  const centralDir = concatAll(centralChunks);
  const centralDirOffset = offset;
  const eocd = concatAll([
    u32(0x06054b50),
    u16(0), // disk number
    u16(0), // disk where central directory starts
    u16(entries.length), // central-directory records on this disk
    u16(entries.length), // total central-directory records
    u32(centralDir.length),
    u32(centralDirOffset),
    u16(0), // comment length
  ]);

  return concatAll([...localChunks, centralDir, eocd]);
}

/** Build a minimal, valid .docx from plain text (one paragraph per line). */
export function buildDocx(text: string): Uint8Array<ArrayBuffer> {
  const documentXml = buildDocumentXml(text);
  const entries: ZipEntry[] = [
    { name: "[Content_Types].xml", data: new TextEncoder().encode(CONTENT_TYPES_XML) },
    { name: "_rels/.rels", data: new TextEncoder().encode(RELS_XML) },
    { name: "word/document.xml", data: new TextEncoder().encode(documentXml) },
  ];
  return zipStore(entries);
}

/** Convenience wrapper for the download-button handler. */
export function buildDocxBlob(text: string): Blob {
  return new Blob([buildDocx(text)], {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
}
