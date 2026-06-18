/* JS execution smoke test — a vision-substitute that runs in CI (no browser).
 *
 * The unit tests prove the Python logic; the camera (verify/camera.py) proves
 * the rendered reality but needs real Chrome. This harness fills the gap in
 * between: it actually EXECUTES the generated page's inline app script with
 * lightweight DOM stubs and drives every view (Jobs / Today / Apps / My corner),
 * so a runtime ReferenceError/TypeError — e.g. calling a function that was
 * deleted in a refactor — fails loudly here instead of silently shipping a
 * broken tab. (This is the exact class of bug that motivated invariant #7.)
 *
 * Usage: node verify/js_smoke.js path/to/index.html
 * Exit 0 + "SMOKE OK" on success; exit 1 + "SMOKE FAIL: ..." on any error.
 */
const fs = require("fs"), vm = require("vm");

const file = process.argv[2];
if (!file) { console.log("SMOKE FAIL: no HTML path given"); process.exit(1); }
const html = fs.readFileSync(file, "utf8");

// Pull the inline app script (skip external <script src=...> like the CDN).
const re = /<script(\b[^>]*)>([\s\S]*?)<\/script>/g;
let m, scripts = [];
while ((m = re.exec(html))) { if (/\bsrc\s*=/.test(m[1] || "")) continue; scripts.push(m[2]); }
if (!scripts.length) { console.log("SMOKE FAIL: no inline script found"); process.exit(1); }
scripts.sort((a, b) => b.length - a.length);
const appJs = scripts[0];   // the big app script

// A chainable no-op proxy for anything we don't model explicitly.
function stub() {
  const f = function () { return stub(); };
  return new Proxy(f, {
    get(t, p) {
      if (p === Symbol.toPrimitive) return () => "";
      if (p === Symbol.iterator) return function* () {};
      if (p === "length") return 0;
      return stub();
    },
    set() { return true; }, apply() { return stub(); }, construct() { return stub(); },
  });
}
function elStub() {
  const o = {
    style: {}, dataset: {}, value: "", textContent: "", innerHTML: "", hidden: false,
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    appendChild() {}, append() {}, insertBefore() {}, addEventListener() {}, removeEventListener() {},
    querySelector() { return elStub(); }, querySelectorAll() { return []; }, closest() { return elStub(); },
    focus() {}, blur() {}, remove() {}, cloneNode() { return elStub(); }, scrollIntoView() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
  };
  return new Proxy(o, {
    get(t, p) { if (p in t) return t[p]; if (p === Symbol.toPrimitive) return () => ""; return stub(); },
    set() { return true; },
  });
}
const store = {};
const sb = {
  console, Math, JSON, Date, Array, Object, String, Number, Boolean, RegExp, Set, Map, WeakMap,
  Promise, Symbol, isNaN, parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  requestAnimationFrame: () => 0, addEventListener: () => {}, removeEventListener: () => {},
  navigator: { vibrate: () => {}, serviceWorker: { register: () => ({ catch: () => {} }) },
    clipboard: { writeText: () => Promise.resolve() } },
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  location: { origin: "http://x", pathname: "/", href: "http://x/" },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  scrollTo: () => {}, print: () => {}, prompt: () => null, alert: () => {}, PublicKeyCredential: undefined,
};
sb.document = {
  getElementById: () => elStub(), querySelector: () => elStub(), querySelectorAll: () => [],
  createElement: () => elStub(), addEventListener: () => {}, body: elStub(), documentElement: elStub(), head: elStub(),
};
sb.window = sb; sb.self = sb; sb.globalThis = sb;

// Minimal Supabase stub so the portal init() actually runs (with PORTAL set):
// getSession resolves SYNCHRONOUSLY with a signed-in session, so showIn() and the
// signed-in account/chat wiring execute during this synchronous run and any
// ReferenceError surfaces here. Query builders are chainable synchronous thenables.
function syncThenable(value){
  const api = {
    then(onF){ if(onF) onF(value); return syncThenable(value); },
    catch(){ return syncThenable(value); },
    finally(fn){ if(fn) fn(); return syncThenable(value); },
  };
  for(const m of ["select","insert","update","upsert","delete","eq","gte","lte","order","limit","maybeSingle","single","or","in","is"])
    api[m] = () => syncThenable(value);
  return api;
}
const fakeSession = { data: { session: { user: { id: "u1", email: "her@example.com" } } } };
const fakeAuth = {
  signInWithPassword: () => syncThenable({ data: {}, error: null }),
  signUp: () => syncThenable({ data: { user: null, session: null }, error: null }),
  signInWithOtp: () => syncThenable({ error: null }),
  signInWithPasskey: () => syncThenable({ error: null }),
  registerPasskey: () => syncThenable({ error: null }),
  signInWithOAuth: () => syncThenable({ error: null }),
  resetPasswordForEmail: () => syncThenable({ error: null }),
  updateUser: () => syncThenable({ error: null }),
  signOut: () => syncThenable({ error: null }),
  getSession: () => syncThenable(fakeSession),
  onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
};
sb.supabase = {
  createClient: () => ({
    auth: fakeAuth,
    from: () => syncThenable({ data: [], error: null, count: 0 }),
    functions: { invoke: () => syncThenable({ data: {}, error: null }) },
  }),
};

// Execute, then drive every view so view-specific functions actually run.
const wrapped = appJs +
  "\n;try{if(typeof setView==='function'){setView('today');setView('apps');setView('corner');setView('help');setView('jobs');}}catch(e){throw e;}";
try {
  vm.runInNewContext(wrapped, sb, { timeout: 8000 });
  console.log("SMOKE OK: app JS executed; all views rendered with no ReferenceError/TypeError");
} catch (e) {
  console.log("SMOKE FAIL: " + e.name + ": " + e.message);
  process.exit(1);
}
