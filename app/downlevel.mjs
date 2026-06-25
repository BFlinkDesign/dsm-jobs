// Post-build: down-level the emitted client bundles to ES2019.
//
// Why this exists: Vite's `build.target` does NOT reach Astro's client
// <script> bundles, so ES2021 logical-assignment (`||=`/`&&=`) shipped to
// production and crashed older mobile browsers at PARSE time — the whole app
// failed to initialize (Sentry DSM-JOBS-2: "SyntaxError: Unexpected token '='"
// at `n||=Promise`). A green build/CI never caught it because the syntax is
// valid on modern browsers.
//
// esbuild reliably lowers `||=`/`&&=`/`??`/`?.` when given an old target, so we
// run it over every built `_astro/*.js` file. Idempotent; safe to re-run.
import { readdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { transform } from "esbuild";

const TARGET = "es2019";
const dir = resolve(process.cwd(), "..", "web", "_astro");

let files;
try {
  files = (await readdir(dir)).filter((f) => f.endsWith(".js"));
} catch (err) {
  console.error(`[downlevel] could not read ${dir}: ${err.message}`);
  process.exit(1);
}

let changed = 0;
for (const f of files) {
  const p = resolve(dir, f);
  const src = await readFile(p, "utf8");
  const out = await transform(src, { target: TARGET, loader: "js", minify: true });
  if (out.code !== src) {
    await writeFile(p, out.code);
    changed += 1;
  }
}
console.log(`[downlevel] ${TARGET}: lowered ${changed}/${files.length} bundle(s)`);
