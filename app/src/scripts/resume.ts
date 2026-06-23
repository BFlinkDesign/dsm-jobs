/** Résumé file upload: .docx / .pdf / .md / .txt → plain text (ported from APP_TEMPLATE). */

async function inflateRaw(bytes: Uint8Array): Promise<Uint8Array> {
  const ds = new DecompressionStream("deflate-raw");
  const s = new Response(bytes).body!.pipeThrough(ds);
  return new Uint8Array(await new Response(s).arrayBuffer());
}

function docxXmlToText(xml: string): string {
  let s = xml
    .replace(/<w:tab\b[^>]*\/?>/g, "\t")
    .replace(/<\/w:p>/g, "\n")
    .replace(/<w:p\b[^>]*\/>/g, "\n")
    .replace(/<w:br\b[^>]*\/?>/g, "\n")
    .replace(/<[^>]+>/g, "");
  s = s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
  return s.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

async function docxToText(buf: ArrayBuffer): Promise<string> {
  const u8 = new Uint8Array(buf);
  const dv = new DataView(buf);
  let eocd = -1;
  for (let i = u8.length - 22; i >= 0; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("That doesn't look like a .docx file.");
  const cdOff = dv.getUint32(eocd + 16, true);
  const cnt = dv.getUint16(eocd + 10, true);
  let p = cdOff;
  let t: { method: number; compSize: number; localOff: number } | null = null;
  for (let n = 0; n < cnt; n++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true);
    const compSize = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true);
    const extraLen = dv.getUint16(p + 30, true);
    const cmtLen = dv.getUint16(p + 32, true);
    const localOff = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nameLen));
    if (name === "word/document.xml") {
      t = { method, compSize, localOff };
      break;
    }
    p += 46 + nameLen + extraLen + cmtLen;
  }
  if (!t) throw new Error("Couldn't read the text in that .docx.");
  const lh = t.localOff;
  if (dv.getUint32(lh, true) !== 0x04034b50) throw new Error("That .docx looks damaged.");
  const dstart = lh + 30 + dv.getUint16(lh + 26, true) + dv.getUint16(lh + 28, true);
  const comp = u8.subarray(dstart, dstart + t.compSize);
  let xmlBytes: Uint8Array;
  if (t.method === 0) xmlBytes = comp;
  else if (t.method === 8) xmlBytes = await inflateRaw(comp);
  else throw new Error("Unsupported compression in that .docx.");
  return docxXmlToText(new TextDecoder().decode(xmlBytes));
}

const PDFJS_VER = "4.7.76";
const PDFJS_SRI = "sha384-qgyx6GmMWoI003drRr62DU41/67b3n7M2G0EXu2WhaOsBqONtHyay9Vw4aIivyOX";
const PDFJS_WORKER_SRI =
  "sha384-ATeT9bCTw1LFxZRSxFHBli/+35MHo/faKiXDlvCvxK2ENYquq3OIA9RkrOW44G/L";

let pdfjs: {
  getDocument: (opts: { data: ArrayBuffer }) => {
    promise: Promise<{
      numPages: number;
      getPage: (n: number) => Promise<{ getTextContent: () => Promise<{ items: Array<{ str?: string }> }> }>;
    }>;
  };
  GlobalWorkerOptions: { workerSrc: string };
} | null = null;

async function verifiedBlobUrl(url: string, sri: string): Promise<string> {
  const resp = await fetch(url, { integrity: sri, mode: "cors", credentials: "omit" });
  if (!resp.ok) throw new Error("Couldn't load the PDF reader.");
  return URL.createObjectURL(await resp.blob());
}

async function loadPdfjs(): Promise<NonNullable<typeof pdfjs>> {
  if (pdfjs) return pdfjs;
  const base = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VER}/build/`;
  const libUrl = await verifiedBlobUrl(`${base}pdf.min.mjs`, PDFJS_SRI);
  const workerUrl = await verifiedBlobUrl(`${base}pdf.worker.min.mjs`, PDFJS_WORKER_SRI);
  const lib = await import(/* @vite-ignore */ libUrl);
  lib.GlobalWorkerOptions.workerSrc = workerUrl;
  pdfjs = lib;
  return lib;
}

async function pdfToText(buf: ArrayBuffer): Promise<string> {
  const lib = await loadPdfjs();
  const pdf = await lib.getDocument({ data: buf }).promise;
  const out: string[] = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const tc = await page.getTextContent();
    out.push(tc.items.map((it) => ("str" in it ? it.str : "")).join(" "));
  }
  return out.join("\n\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

export async function extractResumeFile(file: File): Promise<string> {
  const name = (file.name || "").toLowerCase();
  if (
    name.endsWith(".txt") ||
    name.endsWith(".md") ||
    name.endsWith(".markdown") ||
    file.type === "text/plain" ||
    file.type === "text/markdown"
  ) {
    return (await file.text()).trim();
  }
  if (name.endsWith(".docx")) return docxToText(await file.arrayBuffer());
  if (name.endsWith(".pdf") || file.type === "application/pdf") return pdfToText(await file.arrayBuffer());
  if (name.endsWith(".doc")) {
    throw new Error("Old .doc files aren't supported — save it as .docx, or paste the text.");
  }
  throw new Error("Use a .docx, .pdf, .md, or .txt file — or paste the text below.");
}
