import type { AppState, ApplicationPack, ApplicationStatus, AtsAlignment, FilterPrefs, Job, Meta, Resource, ResourceHub, ResumeDocument, ViewName } from "./types";
import {
  appendChatToLocal,
  autosave,
  clearAutosave,
  clearChatHistory,
  debouncePushNote,
  drainPendingSaves,
  initAutosave,
  loadChatHistory,
  loadPortal,
  pendingSyncCount,
  pullLegacyTables,
  pullNotes,
  pullProfile,
  pushChatMessage,
} from "./autosave";
import { currentUser, fetchGoogleAuthEnabled, friendlyAuthError, getClient, initAuth, registerPasskey, resetPasswordForEmail, signIn, signInWithGoogle, signInWithMagicLink, signInWithPasskey, signOut, signUp, supportsPasskey, updatePassword } from "./auth";
import {
  pickSaying,
  SEARCHING_LINES,
  TAILOR_LINES,
  THINKING_LINES,
} from "./rudy-sayings";
import { extractResumeFile } from "./resume";
import { getState, loadLocal, migrateLocalV1, patchState } from "./store";
import {
  SKULL,
  addDaysISO,
  ago,
  debounce,
  daysSince,
  esc,
  fmtStamp,
  relativePosted,
  safeUrl,
  todayISO,
  weekStart,
} from "./util";

const LOCKED: Record<string, boolean> = { today: true, apps: true, corner: true };
const APP_STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: "Applied",
  followed_up: "Followed up",
  interview: "Interview",
  rejected: "Not a fit",
  offer: "Offer",
  ghosted: "Quiet / no reply",
};

let jobs: Job[] = [];
let meta: Meta = { contact: "", phone: "", generated: "", hidden: 0, total: 0, safe: 0 };
let hub: ResourceHub | null = null;
let view: ViewName = "jobs";
let authed = false;
let commuteMax: number | null = null;
let isNewJobs: Record<string, boolean> = {};
const scrollByView: Partial<Record<ViewName, number>> = {};
let jobsShellReady = false;
let pullStartY = 0;
let pullReady = false;
let pullRefreshing = false;
let feedLoadFailed = false;
let rudyHistoryLoaded = false;
let tailorTimer: ReturnType<typeof setInterval> | null = null;
let filtersExpanded = false;
let lastTailorRequest: TailorRequest | null = null;
type BeforeInstallPromptEvent = Event & {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: string }>;
};
let deferredInstall: BeforeInstallPromptEvent | null = null;
let authMode: "signin" | "signup" = "signin";
interface TailorRequest { job: Job; resume: string; jobText: string }
interface TailorResult {
  resume: string;
  changes?: string[];
  cover_note?: string;
  follow_up?: string;
  ats_alignment?: Partial<AtsAlignment>;
}
type ErrorBodyLike = { error?: unknown; message?: unknown };
type ResponseLike = {
  clone?: () => ResponseLike;
  json?: () => Promise<unknown>;
  text?: () => Promise<string>;
};

const QUIZ: Array<[string, string, Array<[string, string]>]> = [
  ["kind", "What kind of work sounds best right now?", [
    ["people", "With people"], ["quiet", "Quiet & organized"], ["hands", "Keeping my hands busy"], ["care", "Caring for others"],
  ]],
  ["where", "Where would you rather be?", [
    ["out", "Out of the house"], ["home", "Working from home"], ["either", "Either is fine"],
  ]],
  ["time", "What hours fit your life?", [
    ["day", "Daytime"], ["evening", "Evenings"], ["any", "Whatever works"],
  ]],
  ["pay", "Posted pay?", [
    ["must", "Show $19+ first"], ["open", "Good jobs, listed pay or not"],
  ]],
  ["confidence", "How are you feeling about applying?", [
    ["low", "Nervous — start me easy"], ["ok", "Ready — bring it on"],
  ]],
];
const QUIZ_KEYS = ["kind", "where", "time", "pay", "confidence"] as const;
const COMMUTE_BANDS: Array<[number | null, string]> = [
  [null, "Any distance"],
  [20, "Within 20 min"],
  [30, "Within 30 min"],
  [45, "Within 45 min"],
];

// Speech state — stored in localStorage separate from AppState (device-specific opt-in)
let speechSynthOK = false;
let speakOn = false;
let spicyOn = false;
let speechVoice: SpeechSynthesisVoice | null = null;

// ── Affirmations pool (per-day rotation via dayHash) ──────────────────────
const ENC_LINES = [
  "Job ads are wish lists. If you match the core work, it is worth applying.",
  "One focused application is progress. Small steps still count.",
  "Pay not listed is a question to ask, not a reason to count yourself out.",
  "Start with the clearest match. Momentum is easier after the first step.",
  "A saved job is not a commitment. It is just a useful option to revisit.",
  "If a posting feels confusing, slow down and use the checklist.",
  "You can take this one task at a time. The app will keep track.",
  "The goal is not a perfect search. The goal is a steady one.",
  "A no from one employer is information, not a verdict.",
  "Apply before doubt turns a good match into extra work.",
  "A short, honest note is better than waiting for perfect wording.",
  "Trust the scam checks, then make the next practical move.",
  "Your experience does not need to match every bullet to matter.",
  "Send the application that is ready enough. Improve the next one.",
  "If today is busy, choose one job and save the rest.",
  "A calm pace is still a real pace.",
  "Every reviewed posting narrows the search.",
  "The best next step is usually the smallest clear one.",
  "You are allowed to ask about pay, hours, and training.",
  "Keep the search simple: review, save, apply, follow up.",
];

/** Deterministic per-day hash (same algorithm as original find_admin_jobs.py). */
function dayHash(): number {
  const d = todayISO();
  let h = 0;
  for (let i = 0; i < d.length; i++) h = (h * 31 + d.charCodeAt(i)) >>> 0;
  return h;
}

/** Returns the affirmation for today — stable all day, changes daily. */
function todayAffirmation(): string {
  return ENC_LINES[dayHash() % ENC_LINES.length];
}

/** True if this job is snoozed (until > today). Auto-expires at day boundary. */
function snoozedNow(id: string): boolean {
  const until = getState().snoozedUntil[id];
  return !!until && until > todayISO();
}

// ── Rudy's voice ──────────────────────────────────────────────────────────
// Prefer the provider-agnostic `voice` edge function (Chatterbox by default
// when REPLICATE_API_TOKEN is configured); fall back to the browser's built-in
// voice when the function or key is not set up yet. Repeated lines are cached
// so they do not re-bill.
let voiceUnconfigured = false;             // set once the function reports no key
const ttsCache = new Map<string, string>(); // text -> object URL
let rudyAudio: HTMLAudioElement | null = null;
let voiceStatusEl: HTMLElement | null = null;

function setVoiceStatus(kind: string, text: string): void {
  if (!voiceStatusEl) voiceStatusEl = $("#rudy-voice-status");
  if (!voiceStatusEl) return;
  voiceStatusEl.dataset.voiceStatus = kind;
  voiceStatusEl.textContent = text;
}

function syncVoiceIdleStatus(): void {
  if (!speakOn) {
    setVoiceStatus("off", "Voice is off.");
  } else if (!getClient()) {
    setVoiceStatus("fallback", "Sign in to use Rudy's real voice. Browser voice can still try.");
  } else if (voiceUnconfigured) {
    setVoiceStatus("fallback", "Chatterbox is not connected yet. Using this phone's browser voice.");
  } else {
    setVoiceStatus("ready", "Rudy will try her real voice first, then fall back if needed.");
  }
}

function stopRudyVoice(): void {
  try { rudyAudio?.pause(); } catch { /* no-op */ }
  rudyAudio = null;
  try { window.speechSynthesis?.cancel(); } catch { /* no-op */ }
}

/** Try the server voice. Returns false if unavailable (caller falls back). */
async function edgeSpeak(text: string): Promise<boolean> {
  const sb = getClient();
  if (!sb) {
    setVoiceStatus("fallback", "Sign in to use Rudy's real voice. Browser voice can still try.");
    return false;
  }
  if (voiceUnconfigured) {
    setVoiceStatus("fallback", "Chatterbox is not connected yet. Using this phone's browser voice.");
    return false;
  }
  try {
    let url = ttsCache.get(text);
    if (!url) {
      setVoiceStatus("checking", "Checking Rudy's real voice...");
      const { data, error } = await sb.functions.invoke("voice", { body: { mode: "tts", text } });
      if (error) {
        setVoiceStatus("fallback", "Voice service stumbled. Browser voice is still ready.");
        return false;
      }
      if (data?.unconfigured) {
        voiceUnconfigured = true;
        setVoiceStatus("fallback", "Chatterbox is not connected yet. Using this phone's browser voice.");
        return false;
      }
      if (!data?.audio) {
        setVoiceStatus("fallback", "Voice service returned no audio. Browser voice is still ready.");
        return false;
      }
      const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
      url = URL.createObjectURL(new Blob([bytes], { type: data.mime || "audio/mpeg" }));
      ttsCache.set(text, url);
    }
    stopRudyVoice();
    const a = new Audio(url);
    rudyAudio = a;
    a.addEventListener("ended", () => syncVoiceIdleStatus(), { once: true });
    setVoiceStatus("playing", "Rudy's real voice is playing.");
    await a.play();
    return true;
  } catch {
    setVoiceStatus("fallback", "Voice service stumbled. Browser voice is still ready.");
    return false;
  }
}

/** The browser's built-in voice — the fallback only. */
function synthSpeak(text: string): void {
  if (!speechSynthOK) {
    setVoiceStatus("unavailable", "Voice is not available in this browser.");
    return;
  }
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    if (speechVoice) u.voice = speechVoice;
    u.rate = 0.96; u.pitch = 1.0; u.volume = 1.0;
    u.onend = () => syncVoiceIdleStatus();
    setVoiceStatus("fallback", "Using this phone's browser voice.");
    window.speechSynthesis.speak(u);
  } catch { /* no-op */ }
}

/** Speak Rudy's text aloud if the user has the voice on. */
function speakText(text: string): void {
  if (!speakOn || !text) return;
  void (async () => {
    const ok = await edgeSpeak(text);
    if (!ok) synthSpeak(text);
  })();
}

/** How strong a lead is FOR HER — her quiz answers weigh most, then remote,
 * known/safe employer, will-train, confirmed pay, a short commute, and freshness. */
function pickScore(j: Job): number {
  const s = getState();
  let sc = forYouScore(j) * 2;                 // her own answers matter most
  if (j.remote) sc += 3;                        // she prefers remote
  if (j.trusted) sc += 2;                       // known / safer employer
  if (j.trains) sc += 2;                        // will train — good when starting over
  if (j.good) sc += 1;                          // confirmed $19+/hr
  if (!j.remote && j.commuteMin != null) {
    if (j.commuteMin <= 20) sc += 2;
    else if (j.commuteMin <= 30) sc += 1;
  }
  const days = (j.posted ? daysSince(j.posted) : 99) ?? 99;
  if (days <= 2) sc += 2; else if (days <= 7) sc += 1;
  if (s.saved[j.id]) sc += 1;                   // she already flagged interest
  return sc;
}

/** The 3 strongest leads for her right now — genuinely scored, not random.
 * Skips ones she's hidden, snoozed, or already applied to. */
function todaysPicks(): Job[] {
  const s = getState();
  const pool = jobs.filter((j) => {
    if (s.hidden[j.id]) return false;
    if (snoozedNow(j.id)) return false;
    if (s.applied[j.id]) return false;          // don't re-pitch what she already did
    if (commuteMax != null && !j.remote && j.commuteMin != null && j.commuteMin > commuteMax) return false;
    return true;
  });
  return pool
    .map((j, i) => [pickScore(j), -i, j] as const)
    .sort((a, b) => b[0] - a[0] || b[1] - a[1])
    .slice(0, 3)
    .map((x) => x[2]);
}

/** One short, warm line explaining why a pick fits HER — built from real fields. */
function pickReason(j: Job): string {
  const p = getState().profile.quiz;
  const bits: string[] = [];
  if (j.remote) bits.push("works from home");
  else if (j.commuteMin != null && j.commuteMin <= 20) bits.push(`only about ${j.commuteMin} min away`);
  else if (j.commute) bits.push(j.commute.replace("~", "about "));
  if (p.kind === "quiet" && j.category === "Office") bits.push("the calm office work she likes");
  else if (p.kind === "people" && j.category === "Customer service") bits.push("the people work she likes");
  else if ((p.kind === "care" || p.kind === "hands") && j.category === "Caregiving") bits.push("the caregiving she likes");
  if (j.trains) bits.push("they'll train her");
  if (j.good) bits.push("pays $19+/hr");
  if (j.trusted && bits.length < 2) bits.push(j.trustLabel ? `${j.trustLabel.toLowerCase()} employer` : "a known employer");
  if (!bits.length) bits.push("a solid, scam-checked lead");
  return `Why this one: ${bits.slice(0, 3).join(", ")}.`;
}

/** Update the "My apps" tab badge to show count of due follow-ups. */
function renderFollowBadge(): void {
  const s = getState();
  const today = todayISO();
  const due = Object.entries(s.followUps).filter(([, fu]) => !fu.done && fu.on && fu.on <= today).length;
  document.querySelectorAll(".tab[data-view='apps']").forEach((btn) => {
    let badge = btn.querySelector(".follow-badge") as HTMLElement | null;
    if (due > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "follow-badge";
        btn.appendChild(badge);
      }
      badge.textContent = due > 9 ? "9+" : String(due);
      badge.hidden = false;
    } else if (badge) {
      badge.hidden = true;
    }
  });
}

/** Fire browser Notification for due follow-ups (once per day). */
function maybeNotifyFollowUps(): void {
  if (typeof Notification === "undefined") return;
  if ((Notification as typeof Notification).permission !== "granted") return;
  const s = getState();
  const today = todayISO();
  if (s.followAlertDay === today) return;
  const due = Object.entries(s.followUps)
    .filter(([, fu]) => !fu.done && fu.on && fu.on <= today)
    .map(([id]) => id);
  if (!due.length) return;
  patchState((st) => { st.followAlertDay = today; });
  autosave();
  due.slice(0, 3).forEach((id, i) => {
    const fu = s.followUps[id];
    const logEntry = s.appliedLog[id];
    const title2 = logEntry?.t || "Follow up";
    const name = fu.name ? ` — ${fu.name}` : "";
    setTimeout(() => {
      try {
        new Notification("Time to follow up", {
          body: title2 + name,
          tag: `followup-${id}`,
          icon: `${import.meta.env.BASE_URL}icon-192.png`,
        });
      } catch { /* no-op */ }
    }, i * 400);
  });
}

const $ = (sel: string) => document.querySelector(sel) as HTMLElement | null;

// ---- Modal focus management (a11y) -----------------------------------------
// Trap Tab inside the open overlay and restore focus to the trigger on close,
// so keyboard / switch-control users can't tab into the page behind the modal.
let trapEl: HTMLElement | null = null;
let trapReturn: HTMLElement | null = null;

function setModalTrap(overlay: HTMLElement | null): void {
  trapReturn = document.activeElement as HTMLElement | null;
  trapEl = overlay;
}

function clearModalTrap(): void {
  trapEl = null;
  const ret = trapReturn;
  trapReturn = null;
  try { ret?.focus(); } catch { /* element may be gone */ }
}

function handleTrapTab(e: KeyboardEvent): void {
  if (e.key !== "Tab" || !trapEl) return;
  const items = [...trapEl.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )].filter((el) => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement as HTMLElement;
  if (e.shiftKey && (active === first || !trapEl.contains(active))) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !trapEl.contains(active))) {
    e.preventDefault();
    first.focus();
  }
}

let toastTimer: ReturnType<typeof setTimeout> | undefined;
function toast(msg: string, undo?: () => void): void {
  const t = $("#toast");
  if (!t) return;
  // Clear any prior timer so a new toast doesn't get hidden early by the old
  // one's countdown, and a live "Undo" toast isn't silently clobbered.
  if (toastTimer) clearTimeout(toastTimer);
  if (undo) {
    t.innerHTML = `${esc(msg)} <button type="button" class="toast-undo">Undo</button>`;
    t.querySelector(".toast-undo")?.addEventListener("click", () => {
      undo();
      if (toastTimer) clearTimeout(toastTimer);
      t.classList.remove("show");
    }, { once: true });
  } else {
    t.textContent = msg;
  }
  t.classList.add("show");
  toastTimer = setTimeout(() => t.classList.remove("show"), undo ? 5000 : 2000);
}

/** True if any popup overlay is currently open — used so two popups never stack
 * (the update modal was burying the install coach). */
function anyModalOpen(): boolean {
  const tour = document.getElementById("tour-root");
  if (tour && !tour.hidden) return true;
  return !!document.querySelector(".modal-back:not([hidden])");
}

/** Big, centered, splashy "new version" modal — one button that reloads. */
function showUpdateModal(): void {
  const back = $("#update-modal");
  const go = $("#update-go");
  if (!back || !go) { location.reload(); return; }
  back.hidden = false;
  go.addEventListener("click", () => { location.reload(); }, { once: true });
}

// ── Guided "Show me around" walkthrough. Interactive, Wispr-Flow-style: a few
// quick taps actually TUNE her feed (immediate, personal payoff), then spotlight
// coachmarks highlight the REAL on-screen controls, with motion + progress.
type TourChip = { val: string; label: string };
type TourStep =
  | { type: "intro" | "payoff" | "finish"; emoji: string; h: string; p: string }
  | { type: "ask"; key: string; emoji: string; h: string; chips: TourChip[] }
  | { type: "spot"; target: string; emoji: string; h: string; p: string; view?: ViewName };

const TOUR_STEPS: TourStep[] = [
  { type: "intro", emoji: "👋", h: "Hi — let me set this up for you",
    p: "Two quick taps and I'll tune this to the jobs that fit YOU. Then I'll point out where everything is. Tap Next when you're ready." },
  { type: "ask", key: "kind", emoji: "💭", h: "What kind of work feels right?",
    chips: [{ val: "quiet", label: "Calm, behind the scenes" }, { val: "people", label: "Helping people" }, { val: "care", label: "Caring for someone" }] },
  { type: "ask", key: "where", emoji: "🏠", h: "Where do you want to work?",
    chips: [{ val: "home", label: "From home (remote)" }, { val: "out", label: "Out in town" }, { val: "any", label: "Either is fine" }] },
  { type: "ask", key: "confidence", emoji: "🌱", h: "Starting something new?",
    chips: [{ val: "low", label: "Yes — I'd love training" }, { val: "mid", label: "A little nervous" }, { val: "high", label: "I've got this" }] },
  { type: "payoff", emoji: "✨", h: "Done — these are YOUR jobs now",
    p: "I just put your kind of work first. From here on, the list is tuned to you. Let me show you around…" },
  { type: "spot", target: "#sort-row", view: "jobs", emoji: "🔀", h: "Sort it your way",
    p: "Newest is the default. Tap 'Best match' to use what you just told me — or 'Remote first'." },
  { type: "spot", target: "#jobs-list .job-card", view: "jobs", emoji: "🗂️", h: "Tap a job to open it",
    p: "Read it, then Apply. Every single job here was scam-checked before it ever reached you." },
  { type: "spot", target: ".nav-bottom [data-view='money'], .nav-side [data-view='money']", emoji: "💚", h: "Bills tight this month?",
    p: "The Money tab has real local help — rent, food, utilities — plus free classes. One tap to call." },
  { type: "spot", target: ".nav-bottom [data-view='today'], .nav-side [data-view='today']", emoji: "⭐", h: "Just three picks a day",
    p: "Overwhelmed? The Today tab gives you three strong leads, chosen for you. That's the whole list." },
  { type: "finish", emoji: "🎉", h: "You're all set!",
    p: "Reopen this any time from the Help tab. You've got this. ✦" },
];

let tourIdx = 0;

/** First visible match of a (possibly comma-separated) selector. */
function tourTarget(sel: string): HTMLElement | null {
  for (const one of sel.split(",")) {
    const el = document.querySelector(one.trim()) as HTMLElement | null;
    if (el && el.offsetParent !== null) return el;
  }
  return document.querySelector(sel.split(",")[0].trim()) as HTMLElement | null;
}

/** Place the glowing ring over a real element and float the pop above/below it. */
function positionSpotlight(target: HTMLElement): void {
  const r = target.getBoundingClientRect();
  const pad = 8;
  const ring = $("#tour-ring");
  const pop = $("#tour-pop");
  if (ring) {
    ring.hidden = false;
    ring.style.top = `${r.top - pad}px`;
    ring.style.left = `${r.left - pad}px`;
    ring.style.width = `${r.width + pad * 2}px`;
    ring.style.height = `${r.height + pad * 2}px`;
  }
  if (pop) {
    pop.classList.add("anchored");
    const popH = pop.offsetHeight || 230;
    const below = r.bottom + popH + 26 < window.innerHeight;
    pop.style.top = below ? `${r.bottom + 18}px` : `${Math.max(14, r.top - popH - 18)}px`;
    pop.style.left = "50%";
    pop.style.transform = "translateX(-50%)";
  }
}

function centerPop(): void {
  const ring = $("#tour-ring");
  if (ring) ring.hidden = true;
  const pop = $("#tour-pop");
  if (pop) {
    pop.classList.remove("anchored");
    pop.style.top = "50%";
    pop.style.left = "50%";
    pop.style.transform = "translate(-50%, -50%)";
  }
}

function renderTour(): void {
  const s = TOUR_STEPS[tourIdx];
  const root = $("#tour-root");
  const pop = $("#tour-pop");
  const dim = $("#tour-dim");
  if (!s || !root || !pop) return;

  const set = (sel: string, txt: string) => { const e = $(sel); if (e) e.textContent = txt; };
  set("#tour-emoji", s.emoji);
  set("#tour-h", s.h);
  const bar = $("#tour-progress-bar");
  if (bar) bar.style.width = `${((tourIdx + 1) / TOUR_STEPS.length) * 100}%`;
  const back = $("#tour-back");
  if (back) back.hidden = tourIdx === 0;

  // Chips (interactive personalization)
  const chips = $("#tour-chips");
  const para = $("#tour-p");
  if (s.type === "ask") {
    const cur = getState().profile.quiz[s.key];
    if (chips) {
      chips.innerHTML = s.chips.map((c) =>
        `<button type="button" class="tour-chip${cur === c.val ? " on" : ""}" data-tour-chip="${esc(c.val)}">${esc(c.label)}</button>`).join("");
      chips.hidden = false;
    }
    if (para) { para.hidden = true; para.textContent = ""; }
  } else {
    if (chips) { chips.hidden = true; chips.innerHTML = ""; }
    if (para) { para.hidden = false; para.textContent = s.p; }
  }

  const next = $("#tour-next");
  if (next) {
    next.classList.toggle("ghosty", s.type === "ask");
    next.textContent = s.type === "ask" ? "Skip" : (s.type === "finish" ? "Start looking ✦" : "Next");
  }

  // Re-trigger the entrance fade
  pop.classList.remove("pop-in");
  void pop.offsetWidth;
  pop.classList.add("pop-in");

  if (s.type === "spot") {
    if (s.view) setView(s.view);
    if (dim) dim.style.background = "transparent";   // the ring's shadow dims
    setTimeout(() => {
      const tgt = tourTarget(s.target);
      if (!tgt) { if (dim) dim.style.background = ""; centerPop(); return; }
      tgt.scrollIntoView({ block: "center", behavior: "auto" });
      setTimeout(() => positionSpotlight(tgt), 70);
    }, 50);
  } else {
    if (dim) dim.style.background = "";
    centerPop();
    if (s.type === "payoff") {
      patchState((st) => { st.filters.sortBy = "match"; });
      autosave();
      setView("jobs");
    }
  }
}

function startTour(): void {
  tourIdx = 0;
  const root = $("#tour-root");
  if (!root) return;
  root.hidden = false;
  document.body.style.overflow = "hidden";
  localStorage.setItem("tour-seen", "1");
  renderTour();
}
function closeTour(): void {
  const root = $("#tour-root");
  if (root) root.hidden = true;
  document.body.style.overflow = "";
}
function tourNext(): void {
  if (tourIdx >= TOUR_STEPS.length - 1) { closeTour(); return; }
  tourIdx += 1;
  renderTour();
}
function tourBack(): void {
  if (tourIdx > 0) { tourIdx -= 1; renderTour(); }
}
/** A personalization chip was tapped — save it and glide to the next step. */
function tourPickChip(val: string): void {
  const s = TOUR_STEPS[tourIdx];
  if (!s || s.type !== "ask") return;
  patchState((st) => { st.profile.quiz[s.key] = val; });
  autosave();
  renderTour();                 // show it selected
  setTimeout(tourNext, 300);    // then advance with a beat
}

function jobCategories(): string[] {
  return [...new Set(jobs.map((j) => j.category).filter(Boolean))].sort();
}

// Baseline of job ids seen on a PRIOR visit, captured once at app start. Held
// for the whole session so an in-session refresh (pull-to-refresh, online
// event, feed retry) re-marks "New" against the same baseline instead of
// wiping the badges the first refresh would otherwise clear.
let seenBaseline: Set<string> | null = null;
function markJobsSeen(): void {
  if (seenBaseline === null) seenBaseline = new Set(getState().seen);
  const firstVisit = seenBaseline.size === 0;
  isNewJobs = {};
  for (const j of jobs) {
    if (!firstVisit && !seenBaseline.has(j.id)) isNewJobs[j.id] = true;
  }
  patchState((s) => { s.seen = jobs.map((j) => j.id); });
}

function updateStaleBanner(): void {
  const el = document.getElementById("stale-banner");
  if (!el || !meta.generated) return;
  const age = daysSince(meta.generated);
  if (age != null && age >= 3) {
    el.hidden = false;
    el.innerHTML = `These jobs are from <b>${esc(String(meta.generated).slice(0, 10))}</b>. Open with internet to get today's list.`;
  } else {
    el.hidden = true;
  }
}

function updateOfflineBanner(): void {
  const el = document.getElementById("offline-banner");
  if (el) el.hidden = navigator.onLine;
}

async function updateSyncBanner(): Promise<void> {
  const el = document.getElementById("sync-banner");
  if (!el) return;
  const pending = await pendingSyncCount();
  if (pending <= 0) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent = navigator.onLine
    ? `Syncing ${pending} saved change${pending === 1 ? "" : "s"} to her account...`
    : `${pending} saved change${pending === 1 ? "" : "s"} safe on this phone. They will sync when internet is back.`;
}

function updateConnectionBanners(): void {
  updateOfflineBanner();
  void updateSyncBanner();
}

function syncFilterChips(): void {
  const f = getState().filters;
  $("#filter-pay")?.classList.toggle("on", f.filterPay);
  document.querySelectorAll("#filter-remote .chip").forEach((btn) => {
    btn.classList.toggle("on", btn.getAttribute("data-remote") === f.filterRemote);
  });
  document.querySelectorAll("#sort-row .chip").forEach((btn) => {
    btn.classList.toggle("on", btn.getAttribute("data-sort") === (f.sortBy || "newest"));
  });
  document.querySelectorAll("#filter-commute .chip").forEach((btn) => {
    const v = btn.getAttribute("data-commute");
    const m = v === "any" ? null : Number(v);
    btn.classList.toggle("on", commuteMax === m);
  });
  const map: Array<[string, boolean]> = [
    ["filter-train", f.filterTrain],
    ["filter-trusted", f.filterTrusted],
    ["filter-saved", f.filterSaved],
    ["filter-applied", f.filterApplied],
    ["filter-show-hidden", f.showHidden],
  ];
  for (const [id, on] of map) {
    document.getElementById(id)?.classList.toggle("on", on);
  }
  const catSel = document.getElementById("filter-category") as HTMLSelectElement | null;
  if (catSel) catSel.value = f.filterCategory;
}

function activeFilterCount(): number {
  const f = getState().filters;
  return [
    f.filterTrain,
    f.filterPay,
    f.filterTrusted,
    f.filterSaved,
    f.filterApplied,
    f.showHidden,
    f.filterRemote !== "all",
    !!f.filterCategory,
    !!f.searchQ,
    commuteMax != null,
  ].filter(Boolean).length;
}

function updateJobsListOnly(): void {
  const listEl = document.getElementById("jobs-list");
  const countEl = document.getElementById("jobs-count");
  if (!listEl) {
    renderJobsMain();
    return;
  }
  const list = sortJobs(filteredJobs());
  listEl.innerHTML = list.map(jobCard).join("") || "<p class='job-meta'>No jobs match — try widening filters.</p>";
  if (countEl) {
    countEl.textContent = `${list.length} safe job${list.length === 1 ? "" : "s"} · updated ${meta.generated}`;
  }
  syncFilterChips();
}

function refreshJobsView(): void {
  if (view !== "jobs") {
    render();
    return;
  }
  if (!jobsShellReady) renderJobsMain();
  else updateJobsListOnly();
}

function callScriptHtml(j: Job, appliedOn: string): string {
  const when = ago(appliedOn) || "recently";
  return `<details class="script"><summary>What do I say if I call?</summary>` +
    `<blockquote>&ldquo;Hi! My name is ____. I applied for the ${esc(j.title)} job ${esc(when)}, ` +
    `and I wanted to check if it's still open and if you need anything else from me. Thank you!&rdquo;</blockquote>` +
    `<p class="job-meta" style="margin-top:6px">That's the whole call. Short is perfect.</p></details>`;
}

function jobFromAppliedLog(id: string, entry: AppState["appliedLog"][string]): Job {
  return {
    id,
    title: entry.t || "Job no longer listed",
    company: entry.c || "Saved application",
    location: "Tracked from earlier job list",
    pay: "Pay not listed",
    payNum: 0,
    remote: false,
    trusted: false,
    trustLabel: "",
    good: false,
    tagLabel: "",
    posted: entry.d || "",
    url: entry.u || "",
    category: "tracked",
    commute: "",
    commuteMin: null,
    about: "This job is no longer in today's feed, but the application, notes, pack, and follow-up stay here.",
    descFull: "",
    trains: false,
    contactPhone: "",
    contactEmail: "",
    contactName: "",
  };
}

function trackedApplicationJobs(): Job[] {
  const s = getState();
  const byId = new Map(jobs.map((j) => [j.id, j]));
  const ids = new Set([...Object.keys(s.appliedLog), ...Object.keys(s.applied)]);
  return [...ids]
    .filter((id) => !!s.applied[id])
    .sort((a, b) => {
      const aEntry = s.appliedLog[a];
      const bEntry = s.appliedLog[b];
      return ((bEntry?.ts || bEntry?.d || "")).localeCompare(aEntry?.ts || aEntry?.d || "");
    })
    .map((id) => byId.get(id) ?? jobFromAppliedLog(id, s.appliedLog[id] ?? { t: "", c: "", d: "", u: "" }));
}

function appStatusValue(id: string): ApplicationStatus {
  const raw = getState().applicationStatus[id];
  return raw && APP_STATUS_LABELS[raw] ? raw : "applied";
}

function statusStopsFollowUps(status: ApplicationStatus): boolean {
  return status === "followed_up" || status === "interview" || status === "rejected" || status === "offer" || status === "ghosted";
}

function statusOptions(id: string): string {
  const current = appStatusValue(id);
  return (Object.keys(APP_STATUS_LABELS) as ApplicationStatus[])
    .map((status) => `<option value="${esc(status)}"${status === current ? " selected" : ""}>${esc(APP_STATUS_LABELS[status])}</option>`)
    .join("");
}

function isLocked(v: ViewName): boolean {
  return !!LOCKED[v] && !authed;
}

function setView(v: ViewName): void {
  scrollByView[view] = window.scrollY;
  view = v;
  document.querySelectorAll(".tab").forEach((b) => {
    b.setAttribute("aria-current", String(b.getAttribute("data-view") === v));
  });
  render();
  const y = scrollByView[v] ?? 0;
  window.scrollTo({ top: y, behavior: y > 0 ? "auto" : "smooth" });
}

function quizComplete(): boolean {
  const q = getState().profile.quiz;
  return QUIZ_KEYS.every((k) => !!q[k]);
}

function newDocumentId(): string {
  return `doc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function documentStats(text: string): string {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const chars = text.length;
  return `${words} word${words === 1 ? "" : "s"} · ${chars.toLocaleString()} character${chars === 1 ? "" : "s"}`;
}

function setActiveResumeText(profile: AppState["profile"], text: string): void {
  profile.resume = text;
  if (!text.trim()) return;
  let doc = profile.documents.find((item) => item.id === profile.activeDocumentId);
  const ts = new Date().toISOString();
  if (!doc) {
    doc = {
      id: newDocumentId(),
      name: "Pasted résumé",
      text,
      source: "paste",
      createdAt: ts,
      updatedAt: ts,
    };
    profile.documents.unshift(doc);
    profile.activeDocumentId = doc.id;
    return;
  }
  doc.text = text;
  doc.updatedAt = ts;
  if (!doc.name.trim()) doc.name = "Pasted résumé";
}

function addResumeDocument(profile: AppState["profile"], name: string, text: string, source: ResumeDocument["source"]): ResumeDocument {
  const ts = new Date().toISOString();
  const doc: ResumeDocument = {
    id: newDocumentId(),
    name: name.trim() || "Résumé",
    text,
    source,
    createdAt: ts,
    updatedAt: ts,
  };
  profile.documents = [doc, ...profile.documents];
  profile.activeDocumentId = doc.id;
  profile.resume = text;
  return doc;
}

function selectResumeDocument(profile: AppState["profile"], id: string): ResumeDocument | null {
  const doc = profile.documents.find((item) => item.id === id);
  if (!doc) return null;
  profile.activeDocumentId = doc.id;
  profile.resume = doc.text;
  return doc;
}

function removeResumeDocument(profile: AppState["profile"], id: string): ResumeDocument | null {
  const doc = profile.documents.find((item) => item.id === id);
  if (!doc) return null;
  profile.documents = profile.documents.filter((item) => item.id !== id);
  if (profile.activeDocumentId === id) {
    const next = profile.documents[0];
    profile.activeDocumentId = next?.id ?? "";
    profile.resume = next?.text ?? "";
  }
  return doc;
}

function renderResumeDocuments(docs: ResumeDocument[], activeId: string): string {
  if (!docs.length) {
    return `<p class="field-hint doc-empty">No documents saved yet. Upload a file or paste text below.</p>`;
  }
  return `<div class="doc-list" aria-label="Saved résumé documents">
    ${docs.map((doc) => {
    const active = doc.id === activeId;
    const preview = doc.text.trim().slice(0, 260);
    return `<article class="doc-item${active ? " is-active" : ""}">
        <div class="doc-main">
          <span class="doc-name">${esc(doc.name)}</span>
          <span class="doc-meta">${esc(documentStats(doc.text))}${active ? " · selected" : ""}</span>
          ${preview ? `<p class="doc-preview">${esc(preview)}${doc.text.length > preview.length ? "..." : ""}</p>` : ""}
        </div>
        <div class="doc-actions">
          ${active ? `<span class="badge-safe">Selected</span>` : `<button type="button" class="btn btn-ghost btn-sm" data-doc-active="${esc(doc.id)}">Use this</button>`}
          <button type="button" class="btn btn-ghost btn-sm" data-doc-delete="${esc(doc.id)}">Delete</button>
        </div>
      </article>`;
  }).join("")}
  </div>`;
}

/** Quiz answers gently float matching jobs upward — never buries low scorers. */
function forYouScore(j: Job): number {
  const p = getState().profile.quiz;
  let s = 0;
  if (p.kind) {
    const k = j.category || "";
    if (p.kind === "people" && k === "Customer service") s += 2;
    if (p.kind === "quiet" && k === "Office") s += 2;
    if (p.kind === "hands" && k === "Caregiving") s += 2;
    if (p.kind === "care" && k === "Caregiving") s += 2;
  }
  if (p.where === "home" && j.remote) s += 2;
  if (p.where === "out" && !j.remote) s += 1;
  if (p.confidence === "low" && j.trains) s += 2;
  if (p.pay === "must" && j.good) s += 1;
  return s;
}

/** Sort options shown to her above the list. "Newest" is the default. */
const SORT_MODES: Array<[FilterPrefs["sortBy"], string]> = [
  ["newest", "Newest"],
  ["match", "Best match"],
  ["remote", "Remote first"],
  ["commute", "Closest"],
  ["pay", "Pay listed"],
];

/** Order the visible list by her chosen sort mode (default newest-first). */
function sortJobs(list: Job[]): Job[] {
  const mode = getState().filters.sortBy || "newest";
  const byNewest = (a: Job, b: Job) => (b.posted || "").localeCompare(a.posted || "");
  const arr = list.slice();
  if (mode === "match") {
    // Her quiz answers float matching work up; remote and freshness break ties.
    return arr
      .map((j, i) => [forYouScore(j), j.remote ? 1 : 0, -i, j] as const)
      .sort((a, b) => b[0] - a[0] || b[1] - a[1] || a[2] - b[2])
      .map((x) => x[3]);
  }
  if (mode === "remote") {
    return arr.sort((a, b) => (Number(b.remote) - Number(a.remote)) || byNewest(a, b));
  }
  if (mode === "commute") {
    const key = (j: Job) => (j.remote ? -1 : j.commuteMin ?? 999);
    return arr.sort((a, b) => key(a) - key(b) || byNewest(a, b));
  }
  if (mode === "pay") {
    // Opt-in only — never the default, so 'Pay not listed' leads aren't buried.
    return arr.sort((a, b) => (Number(b.good) - Number(a.good)) || (b.payNum - a.payNum) || byNewest(a, b));
  }
  return arr.sort(byNewest);
}

function authRedirectUrl(): string {
  return `${window.location.origin}${import.meta.env.BASE_URL}`;
}

function filteredJobs(): Job[] {
  const s = getState();
  const f = s.filters;
  return jobs.filter((j) => {
    if (!f.showHidden) {
      if (s.hidden[j.id]) return false;
      if (snoozedNow(j.id)) return false;
    } else if (!s.hidden[j.id] && !snoozedNow(j.id)) {
      return false;
    }
    if (f.filterRemote === "local" && j.remote) return false;
    if (f.filterRemote === "remote" && !j.remote) return false;
    if (f.filterPay && !j.good) return false;
    if (f.filterTrain && !j.trains) return false;
    if (f.filterTrusted && !j.trusted) return false;
    if (f.filterSaved && !s.saved[j.id]) return false;
    if (f.filterApplied && !s.applied[j.id]) return false;
    if (f.filterCategory && j.category !== f.filterCategory) return false;
    if (commuteMax != null && !j.remote && j.commuteMin != null && j.commuteMin > commuteMax) return false;
    if (f.searchQ) {
      const hay = `${j.title} ${j.company} ${j.location}`.toLowerCase();
      if (!hay.includes(f.searchQ.toLowerCase())) return false;
    }
    return true;
  });
}

function followUpHtml(j: Job): string {
  const fu = getState().followUps[j.id];
  if (!getState().applied[j.id] || !fu) return "";
  const appliedDate = getState().appliedLog[j.id]?.d || todayISO();
  const appliedDays = daysSince(appliedDate);
  const status = appStatusValue(j.id);
  let html = `<div class="job-actions follow-block">`;
  html += `<label class="field-hint app-status-label">Status <select class="field app-status-field" data-app-status="${esc(j.id)}">${statusOptions(j.id)}</select></label>`;
  if (fu.done) {
    html += `<span class="badge-safe">${esc(APP_STATUS_LABELS[status])} ✓</span>`;
    html += `<button type="button" class="btn btn-ghost btn-sm" data-follow-undo="${esc(j.id)}">Resume reminders</button>`;
  } else {
    const phone = fu.phone || j.contactPhone;
    const email = fu.email || j.contactEmail;
    if (phone) html += `<a class="btn btn-call" href="tel:${esc(phone)}">Call</a>`;
    if (email) html += `<a class="btn btn-email" href="mailto:${esc(email)}">Email</a>`;
    html += `<span class="field-hint">Remind:</span>`;
    for (const d of [3, 5, 7]) {
      const on = fu.on === addDaysISO(appliedDate, d);
      html += `<button type="button" class="chip${on ? " on" : ""}" data-remind="${esc(j.id)}" data-days="${d}">${d}d</button>`;
    }
    html += `<button type="button" class="btn btn-primary btn-sm" data-follow-done="${esc(j.id)}" style="margin-top:8px">I followed up ✓</button>`;
    if (appliedDays != null && appliedDays >= 5) {
      html += `<div class="nudge" style="margin-top:8px">You applied ${esc(ago(appliedDate))} — a quick call shows you're serious.${callScriptHtml(j, appliedDate)}</div>`;
    }
  }
  html += `<details class="follow-edit" style="margin-top:8px"><summary>Edit contact</summary>`;
  html += `<label class="field-hint follow-date-label">Follow up on <input class="field follow-field" data-follow-date="${esc(j.id)}" type="date" value="${esc(fu.on)}" min="${esc(todayISO())}" /></label>`;
  html += `<input class="field follow-field" data-follow-name="${esc(j.id)}" placeholder="Contact name" value="${esc(fu.name)}" />`;
  html += `<input class="field follow-field" data-follow-phone="${esc(j.id)}" type="tel" placeholder="Phone" value="${esc(fu.phone)}" />`;
  html += `<input class="field follow-field" data-follow-email="${esc(j.id)}" type="email" placeholder="Email" value="${esc(fu.email)}" />`;
  html += `</details></div>`;
  return html;
}

function jobCard(j: Job): string {
  const s = getState();
  const applied = !!s.applied[j.id];
  const isHidden = !!s.hidden[j.id];
  const isSnoozed = snoozedNow(j.id);
  const payCls = j.good ? "pay-tag good" : "pay-tag";
  const trust = j.trusted ? `<span class="badge-safe">✓ ${esc(j.trustLabel || "Verified")}</span>` : "";
  const loc = j.remote ? "Remote" : esc(j.location);
  const commute = j.commute ? ` · ${esc(j.commute)}` : "";
  const hasResume = !!s.profile.resume.trim();
  const hasPack = !!s.applicationPacks[j.id];
  const shareBtn =
    typeof navigator !== "undefined" && typeof navigator.share === "function"
      ? `<button type="button" class="btn btn-ghost btn-sm" data-share="${esc(j.id)}">Share</button>`
      : "";
  return `<article class="card card-glitter job-card${isHidden ? " card-hidden" : ""}" data-id="${esc(j.id)}" data-verified="${j.trusted ? "1" : "0"}" data-pay="${j.good ? "1" : "0"}">
    <h3>${esc(j.title)}${isNewJobs[j.id] ? '<span class="newtag">New</span>' : ""}${j.trains ? '<span class="traintag">✦ Will train</span>' : ""}</h3>
    <div class="job-meta">${esc(j.company)} · ${loc}${commute}</div>
    <div><span class="${payCls}">${esc(j.pay)}</span> ${trust}</div>
    <div class="job-meta">${esc(relativePosted(j.posted))}</div>
    ${j.about ? `<p class="job-meta">${esc(j.about)}</p>` : ""}
    <div class="job-actions">
      ${applied
    ? `<span class="badge-safe">Applied</span><button type="button" class="btn btn-ghost btn-sm" data-unapply="${esc(j.id)}">Undo applied</button>`
    : authed
      ? `<button type="button" class="btn btn-primary" data-apply="${esc(j.id)}">Mark applied</button>`
      : `<button type="button" class="btn btn-ghost" data-needs-auth>Sign in to apply</button>`}
      ${authed ? `<button type="button" class="btn btn-ghost" data-save="${esc(j.id)}">${s.saved[j.id] ? "Saved ✓" : "Save"}</button>` : ""}
      ${authed && j.url ? `<a class="btn btn-ghost" href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener">Apply ↗</a>` : ""}
      ${authed && hasResume ? `<button type="button" class="btn btn-ghost" data-tailor="${esc(j.id)}">Rudy tailor résumé</button>` : ""}
      ${authed && hasPack ? `<button type="button" class="btn btn-ghost" data-pack="${esc(j.id)}">Open pack</button>` : ""}
      <button type="button" class="btn btn-ghost btn-sm" data-snooze="${esc(j.id)}">${isSnoozed ? "👁 Napping" : "Not today"}</button>
      <button type="button" class="btn btn-ghost btn-sm" data-hide="${esc(j.id)}">${isHidden ? "Unhide" : "Hide"}</button>
      ${shareBtn}
    </div>
    ${followUpHtml(j)}
    ${authed ? `<textarea class="notes-area" rows="2" placeholder="Notes (just for her)…" data-note="${esc(j.id)}">${esc(s.notes[j.id] || "")}</textarea>` : ""}
  </article>`;
}

function renderJobsMain(): void {
  const host = $("#view-host");
  if (!host) return;
  const f = getState().filters;
  const list = sortJobs(filteredJobs());
  const cats = jobCategories();
  const activeFilters = activeFilterCount();
  jobsShellReady = true;
  host.innerHTML = `
    <div class="search-row">
      <input class="search" type="search" placeholder="Search jobs…" value="${esc(f.searchQ)}" id="job-search" autocomplete="off" enterkeyhint="search" />
    </div>
    <p class="filter-label" style="margin-top:4px">Sort</p>
    <div class="chip-row sort-row" id="sort-row">
      ${SORT_MODES.map(([m, label]) =>
    `<button type="button" class="chip${(f.sortBy || "newest") === m ? " on" : ""}" data-sort="${m}">${esc(label)}</button>`,
  ).join("")}
    </div>
    <button type="button" class="filter-toggle" id="filter-toggle" aria-expanded="${filtersExpanded ? "true" : "false"}" aria-controls="filter-panel">
      <span>Filters${activeFilters ? ` (${activeFilters})` : ""}</span>
      <span aria-hidden="true">${filtersExpanded ? "Hide" : "Show"}</span>
    </button>
    <div id="filter-panel" class="filter-panel${filtersExpanded ? "" : " is-collapsed"}">
      <p class="filter-label">Filter</p>
      <div class="chip-row" id="filter-extra">
        <button type="button" class="chip${f.filterTrain ? " on" : ""}" id="filter-train">Will train ✦</button>
        <button type="button" class="chip${f.filterPay ? " on" : ""}" id="filter-pay">$19+/hr</button>
        <button type="button" class="chip${f.filterTrusted ? " on" : ""}" id="filter-trusted">Verified employer</button>
        <button type="button" class="chip${f.filterSaved ? " on" : ""}" id="filter-saved">Saved</button>
        <button type="button" class="chip${f.filterApplied ? " on" : ""}" id="filter-applied">Applied</button>
        <button type="button" class="chip${f.showHidden ? " on" : ""}" id="filter-show-hidden">Hidden</button>
      </div>
      ${cats.length ? `<p class="filter-label">Category</p>
      <select class="field" id="filter-category" style="margin-bottom:8px">
        <option value="">All categories</option>
        ${cats.map((c) => `<option value="${esc(c)}"${f.filterCategory === c ? " selected" : ""}>${esc(c)}</option>`).join("")}
      </select>` : ""}
      <p class="filter-label">Job type</p>
      <div class="chip-row" id="filter-remote">
        <button type="button" class="chip${f.filterRemote === "all" ? " on" : ""}" data-remote="all">All</button>
        <button type="button" class="chip${f.filterRemote === "local" ? " on" : ""}" data-remote="local">In person</button>
        <button type="button" class="chip${f.filterRemote === "remote" ? " on" : ""}" data-remote="remote">Remote</button>
      </div>
      <p class="filter-label">How far she'll drive from Grimes</p>
      <div class="chip-row" id="filter-commute">
        ${COMMUTE_BANDS.map(([m, label]) =>
    `<button type="button" class="chip${commuteMax === m ? " on" : ""}" data-commute="${m ?? "any"}">${esc(label)}</button>`,
  ).join("")}
      </div>
    </div>
    <p class="job-meta" id="jobs-count" style="margin-top:1rem">${list.length} safe job${list.length === 1 ? "" : "s"} · updated ${esc(meta.generated)}</p>
    <div class="jobs-grid" id="jobs-list">${list.map(jobCard).join("") || "<p class='job-meta'>No jobs match — try widening filters.</p>"}</div>
    <p class="field-hint" style="margin-top:1rem">We checked ${meta.total} postings and hid ${meta.hidden} that looked like scams.</p>
    ${feedLoadFailed ? `<button type="button" class="btn btn-primary" id="feed-retry" style="margin-top:12px">Try loading jobs again</button>` : ""}
    ${(() => {
      const s = getState();
      const nHidden = Object.keys(s.hidden).length;
      const nSnoozed = Object.keys(s.snoozedUntil).filter(id => snoozedNow(id)).length;
      const total = nHidden + nSnoozed;
      if (!total) return "";
      return `<button type="button" class="btn btn-ghost" id="toggle-hidden" style="margin-top:8px">${f.showHidden ? "Hide hidden/snoozed" : `Show ${total} hidden/snoozed`}</button>`;
    })()}
  `;
  updateStaleBanner();
}

/** Crisis lines rendered on every lock screen — MUST be reachable without login. */
const CRISIS_LOCK = `<p class="lock-crisis">Need help right now? <a href="tel:988"><b>988</b></a> (call/text, free, 24/7) · Your Life Iowa <a href="tel:8555818111">855-581-8111</a> · Iowa Warm Line <a href="tel:8447759276">844-775-9276</a>. Always free, no account needed.</p>`;

function renderLock(sub: string): string {
  return `<div class="lock-screen card card-glitter">
    ${SKULL}
    <h2>Free account required</h2>
    <p>${esc(sub)}</p>
    <button type="button" class="btn btn-primary" data-lock-signin>Create free account</button>
    <p class="field-hint" style="margin-top:1rem">Everything saves instantly — no save buttons.</p>
    ${CRISIS_LOCK}
  </div>`;
}

function renderToday(): void {
  const host = $("#view-host");
  if (!host) return;
  if (isLocked("today")) {
    host.innerHTML = renderLock("Today's gentle shortlist is hers once she signs in — a tiny doable list, not the whole wall.");
    return;
  }
  const picks = todaysPicks();
  const s = getState();
  const affirmation = s.coachOff ? "" : `<p class="affirmation">${esc(todayAffirmation())}</p>`;
  host.innerHTML = `
    <div class="card card-glitter">
      <h2 class="view-title">Today</h2>
      ${affirmation}
      <p class="job-meta" style="margin-top:${affirmation ? "8px" : "0"}">Three leads picked for her — closest fit first. No pressure to apply to all three.</p>
      ${!s.coachOff ? `<button type="button" class="btn btn-ghost" id="coach-off-btn" style="margin-top:8px;font-size:var(--text-xs)">Turn off affirmations</button>` : `<button type="button" class="btn btn-ghost" id="coach-on-btn" style="margin-top:8px;font-size:var(--text-xs)">Turn on affirmations</button>`}
    </div>
    <div class="jobs-grid">${picks.length
      ? picks.map((j) => `<div class="pick-wrap"><p class="pick-reason">${esc(pickReason(j))}</p>${jobCard(j)}</div>`).join("")
      : "<p class='job-meta'>You've worked through today's list — genuinely well done. New jobs arrive every morning.</p>"}</div>
  `;
}

function renderApps(): void {
  const host = $("#view-host");
  if (!host) return;
  if (isLocked("apps")) {
    host.innerHTML = renderLock("Her applied list, follow-up reminders, and call/email buttons sync to her account automatically.");
    return;
  }
  const s = getState();
  const applied = trackedApplicationJobs();
  const ws = weekStart();
  const appsThisWeek = Object.values(s.appliedLog).filter((e) => (e.d || "") >= ws).length;
  const weekMsg = appsThisWeek >= 3
    ? `${appsThisWeek} application${appsThisWeek === 1 ? "" : "s"} this week — that covers the 3 Iowa asks for. ✦`
    : `${appsThisWeek} application${appsThisWeek === 1 ? "" : "s"} this week — Iowa asks for 4 work-search activities a week, ≥3 applications.`;
  const today = todayISO();
  const dueEntries = Object.entries(s.followUps).filter(([, fu]) => !fu.done && fu.on && fu.on <= today);
  const dueBanner = dueEntries.length
    ? `<div class="follow-alert"><b>${dueEntries.length} follow-up${dueEntries.length === 1 ? "" : "s"} due</b> — tap Call or Email on the job below.</div>`
    : "";
  const canNotify = typeof Notification !== "undefined";
  const notifPerm = canNotify ? (Notification as typeof Notification).permission : "denied";
  const notifyBtn = canNotify && notifPerm !== "granted" && Object.keys(s.applied).length > 0
    ? `<button type="button" class="btn btn-ghost" id="notifybtn" style="margin-top:8px" ${notifPerm === "denied" ? "disabled title='Blocked in phone settings'" : ""}>
        ${notifPerm === "denied" ? "Reminders blocked — enable in phone settings" : "Turn on phone reminders for follow-ups"}
       </button>`
    : "";
  host.innerHTML = `
    <div class="card">
      <h2 class="view-title">My applications</h2>
      ${dueBanner}
      <p class="job-meta">${applied.length} tracked — tap <b>I followed up ✓</b> when she's done so reminders stop.</p>
      <p class="job-meta" style="margin-top:8px">${weekMsg}</p>
      ${notifyBtn}
      <button type="button" class="btn btn-ghost" id="print-log" style="margin-top:12px">Print work-search log</button>
    </div>
    <div class="jobs-grid">${applied.length ? applied.map(jobCard).join("") : "<p class='job-meta'>Nothing marked applied yet. Tap <b>Mark applied</b> on a job she likes.</p>"}</div>
  `;
  renderFollowBadge();
  maybeNotifyFollowUps();
}

function renderCorner(): void {
  const host = $("#view-host");
  if (!host) return;
  if (isLocked("corner")) {
    host.innerHTML = renderLock("Rudy, résumé tailoring, and her saved details live here — all remembered automatically after sign-in.");
    return;
  }
  const p = getState().profile;
  const base = import.meta.env.BASE_URL;
  const h = new Date().getHours();
  const part = h < 5 ? "You're up late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  const pref = p.preferredName.trim();
  const quizDone = quizComplete();
  const quizHtml = QUIZ.map(([key, question, opts]) => `
    <div class="quiz-q">${esc(question)}</div>
    <div class="chip-row quiz-opts">
      ${opts.map(([val, label]) => {
    const on = p.quiz[key] === val;
    return `<button type="button" class="chip qopt${on ? " on" : ""}" data-quiz="${esc(key)}" data-quiz-val="${esc(val)}">${esc(label)}</button>`;
  }).join("")}
    </div>`).join("");
  host.innerHTML = `
    <div class="card card-glitter">
      <h2 class="view-title">${part}${pref ? `, ${esc(pref)}` : ""}</h2>
      <p class="job-meta">This page is just for you — no job list, no pressure.</p>
    </div>
    <div class="card card-glitter rudy-row">
      <img class="rudy-avatar" src="${base}rudy.jpg" alt="" width="56" height="56" />
      <div>
        <h2 class="view-title">Rudy</h2>
        <p class="job-meta">Your emotional support cow — calm check-ins, remembers you, helps with the search.</p>
        <button type="button" class="btn btn-primary" id="open-rudy" style="margin-top:12px">Talk to Rudy</button>
      </div>
    </div>
    <div class="card">
      <h3 class="section-title">Her name</h3>
      <input class="field" id="pf-preferred" placeholder="Preferred name" value="${esc(p.preferredName)}" />
      <p class="field-hint">Saves as she types</p>
      <input class="field" id="pf-legal" placeholder="Legal name (for logs)" value="${esc(p.legalName)}" />
    </div>
    <div class="card">
      <h3 class="section-title">About her (tunes the Jobs feed)</h3>
      <p class="job-meta">Answer a few easy questions and the Jobs page starts putting her kind of work first.</p>
      ${quizHtml}
      ${quizDone ? `<p class="field-hint quiz-done">Got it. The Jobs page now puts her kind of work first. ✦</p>` : ""}
    </div>
    <div class="card">
      <h3 class="section-title">Résumé</h3>
      <button type="button" class="btn btn-ghost" id="upload-resume" style="margin-bottom:8px">Add .docx, .pdf, .txt, or .md</button>
      <p class="field-hint" id="resume-msg"></p>
      ${renderResumeDocuments(p.documents, p.activeDocumentId)}
      <textarea class="field" id="pf-resume" rows="6" placeholder="Paste résumé — auto-saved">${esc(p.resume)}</textarea>
      <p class="field-hint">Rudy tailors from the selected résumé. Pasted changes save to that selected document.</p>
    </div>
  `;
}

function renderHelp(): void {
  const host = $("#view-host");
  if (!host) return;
  host.innerHTML = `
    <div class="card card-glitter">
      <h2 class="view-title">Help &amp; how-to</h2>
      <p class="job-meta">New here, or want a refresher? Take the quick tour — it walks you through the whole app, one tap at a time.</p>
      <button type="button" class="btn update-go" id="tour-start" style="margin-top:12px">✦ Show me around</button>
    </div>
    <div class="card">
      <h3 class="section-title">What each tab does</h3>
      <ul class="job-meta help-list">
        <li><b>Jobs</b> — safe jobs, newest first. Tap <b>Sort</b> to change the order (Newest, Best match, Remote first, Closest).</li>
        <li><b>Today</b> — your 3 best picks for the day, each with why it fits.</li>
        <li><b>Apps</b> — everything you applied to, plus follow-up reminders.</li>
        <li><b>My corner</b> — your résumé + <b>Rudy</b>, who tailors it to a job and answers questions.</li>
        <li><b>Money</b> — local help with rent, food, and bills, plus <b>free</b> classes to earn more.</li>
      </ul>
    </div>
    <div class="card">
      <h3 class="section-title">How you stay safe</h3>
      <ul class="job-meta help-list">
        <li>Every job is scam-checked before you see it — scams are <b>removed</b>, not just labeled.</li>
        <li>"Pay not listed" is normal and often a great lead — just ask when you apply.</li>
        <li>Real employers never ask you to pay, buy gift cards, wire money, or interview off-app. If someone does, it's a scam — stop.</li>
        <li>Worried about money this month? The <b>Money</b> tab has real local help — the fastest start is a free call to 2-1-1.</li>
      </ul>
    </div>
    <div class="crisis">
      <b>Need help right now?</b>
      <p class="job-meta crisis-links">
        <a href="tel:988"><b>988</b></a> — call or text, free, 24/7 (Suicide &amp; Crisis Lifeline)<br>
        <a href="tel:8555818111"><b>855-581-8111</b></a> Your Life Iowa ·
        <a href="sms:8558958398">text 855-895-8398</a>, free, 24/7<br>
        <a href="tel:8447759276"><b>844-775-9276</b></a> Iowa Warm Line — just want someone kind to talk to? That's what this one is for.
      </p>
      <p class="field-hint" style="margin-top:8px">All free. No insurance needed. More help (food, rent, free clinics): dial <b>2-1-1</b> or <a href="https://www.211iowa.org" target="_blank" rel="noopener">211iowa.org</a>.</p>
    </div>
  `;
}

/** A dialable tel: href from a human-typed phone string ("(515) 244-0198"). */
function telHref(phone: string): string {
  const digits = (phone || "").replace(/[^0-9+]/g, "");
  return digits ? `tel:${digits}` : "";
}

/** One help resource as a card: what it is, who it's for, a call button + link,
 * and an optional "what to say" script she can read aloud. */
function resourceCard(r: Resource): string {
  const tel = r.phone ? telHref(r.phone) : "";
  const link = r.url ? safeUrl(r.url) : "";
  return `<article class="card resource-card">
    <h3 class="section-title">${esc(r.name)}</h3>
    <p class="job-meta">${esc(r.what)}</p>
    ${r.who ? `<p class="field-hint">Who it's for: ${esc(r.who)}</p>` : ""}
    ${r.time ? `<p class="field-hint">How long: ${esc(r.time)}</p>` : ""}
    ${r.how ? `<p class="field-hint">${esc(r.how)}</p>` : ""}
    ${r.whatToSay ? `<details class="script"><summary>What to say</summary><blockquote>&ldquo;${esc(r.whatToSay)}&rdquo;</blockquote></details>` : ""}
    <div class="job-actions" style="margin-top:8px">
      ${tel ? `<a class="btn btn-primary btn-sm" href="${esc(tel)}">Call ${esc(r.phone ?? "")}</a>` : ""}
      ${link ? `<a class="btn btn-ghost btn-sm" href="${esc(link)}" target="_blank" rel="noopener">Open website</a>` : ""}
    </div>
  </article>`;
}

/** "Money & help" tab — verified local survival resources + free upskilling.
 * Fully usable without an account (no lock); fails soft to the 211 anchor. */
function renderMoney(): void {
  const host = $("#view-host");
  if (!host) return;
  const h = hub;
  if (!h) {
    host.innerHTML = `<div class="card card-glitter">
      <h2 class="view-title">Money &amp; help</h2>
      <p class="job-meta">One free call connects to nearly every local program — rent, utilities, food, child care, and more. In Iowa, dial <a href="tel:211"><b>2-1-1</b></a> (free, 24/7) or visit <a href="https://www.211iowa.org" target="_blank" rel="noopener">211iowa.org</a>.</p>
      <a class="btn btn-primary" href="tel:211" style="margin-top:8px">Call 2-1-1 — free, 24/7</a>
    </div>`;
    return;
  }
  const start = h.startHere ? `<div class="card card-glitter">
    <h3 class="section-title">${esc(h.startHere.title)}</h3>
    <p class="job-meta">${esc(h.startHere.body)}</p>
    ${h.startHere.phone ? `<a class="btn btn-primary" href="${esc(telHref(h.startHere.phone))}" style="margin-top:8px">Call ${esc(h.startHere.phone)}</a>` : ""}
  </div>` : "";
  const sections = h.sections.map((sec) => `<section class="resource-section">
    <h3 class="section-head">${esc(sec.title)}</h3>
    ${sec.subtitle ? `<p class="job-meta section-sub">${esc(sec.subtitle)}</p>` : ""}
    ${sec.resources.map(resourceCard).join("")}
  </section>`).join("");
  const skills = h.skills && h.skills.length ? `<section class="resource-section">
    <h3 class="section-head">Free skills that pay off</h3>
    ${h.skillsIntro ? `<p class="job-meta section-sub">${esc(h.skillsIntro)}</p>` : ""}
    ${h.skills.map(resourceCard).join("")}
  </section>` : "";
  const safety = h.safetyNote ? `<div class="crisis"><b>Stay safe</b><p class="job-meta">${esc(h.safetyNote)}</p></div>` : "";
  host.innerHTML = `
    <div class="card card-glitter"><h2 class="view-title">Money &amp; help</h2><p class="job-meta">${esc(h.intro)}</p></div>
    ${start}${sections}${skills}${safety}
    ${h.updated ? `<p class="field-hint" style="margin-top:8px">Resource list reviewed ${esc(h.updated)}. Programs change — calling to confirm is always smart.</p>` : ""}
  `;
}

function render(): void {
  switch (view) {
    case "jobs": renderJobsMain(); break;
    case "today": renderToday(); break;
    case "apps": renderApps(); break;
    case "corner": renderCorner(); break;
    case "help": renderHelp(); break;
    case "money": renderMoney(); break;
  }
  document.body.classList.toggle("authed", authed);
  renderFollowBadge();
}

/** Build and trigger the Iowa printable work-search log. */
function printWorkLog(): void {
  const s = getState();
  let legal = s.profile.legalName.trim();
  if (!legal) {
    const entered = window.prompt(
      "Your legal name for the work-search log (for unemployment or court — you can change it later in My corner):",
      ""
    );
    if (entered?.trim()) {
      legal = entered.trim();
      patchState((st) => { st.profile.legalName = legal; });
      autosave();
    }
  }
  const ws = weekStart();
  // Sort most recent first
  const entries = Object.entries(s.appliedLog)
    .map(([id, e]) => ({ id, ...e }))
    .sort((a, b) => (a.d < b.d ? 1 : -1));

  const rows = entries.map((r) => {
    // Fall back to live job list if the entry has no title/company captured
    const liveJob = jobs.find((j) => j.id === r.id);
    const title = r.t || liveJob?.title || "(job no longer listed)";
    const co = r.c || liveJob?.company || "";
    const stamp = esc(fmtStamp(r.ts, r.d));
    return `<tr><td>${stamp}</td><td>${esc(title)}</td><td>${esc(co)}</td><td>Online application</td><td></td></tr>`;
  });

  const wl = document.getElementById("print-worklog");
  if (!wl) return;
  wl.innerHTML = `
    <h1>Work-Search Log</h1>
    <p>Name: ${legal ? esc(legal) : "______________________"} &nbsp;&nbsp; Week of ${esc(ws)} (Sunday&ndash;Saturday)<br>
    Iowa asks for 4 reemployment activities per week; at least 3 must be job applications.</p>
    <table>
      <tr><th>Logged (date &amp; time)</th><th>Position</th><th>Employer</th><th>How</th><th>Result / notes</th></tr>
      ${rows.join("") || "<tr><td colspan='5'>(no applications logged yet)</td></tr>"}
    </table>
  `;
  wl.hidden = false;
  window.print();
}

function handleViewClick(e: Event): void {
  const t = (e.target as HTMLElement).closest(
    "[data-needs-auth], [data-lock-signin], [data-apply], [data-unapply], [data-save], [data-remind], [data-remote], [data-commute], [data-sort], #filter-toggle, #filter-pay, #filter-train, #filter-trusted, #filter-saved, #filter-applied, #filter-show-hidden, #feed-retry, [data-follow-done], [data-follow-undo], [data-doc-active], [data-doc-delete], [data-tailor], [data-pack], [data-share], #open-rudy, #tour-start, #print-log, [data-hide], [data-snooze], #toggle-hidden, #notifybtn, #coach-off-btn, #coach-on-btn, #upload-resume, .qopt"
  ) as HTMLElement | null;
  if (!t) return;

  if (t.matches("[data-needs-auth], [data-lock-signin]")) {
    openAuth();
    return;
  }
  if (t.classList.contains("qopt")) {
    const key = t.getAttribute("data-quiz");
    const val = t.getAttribute("data-quiz-val");
    if (!key || !val) return;
    patchState((s) => {
      if (s.profile.quiz[key] === val) delete s.profile.quiz[key];
      else s.profile.quiz[key] = val;
    });
    autosave();
    if (view === "jobs") refreshJobsView();
    else renderCorner();
    return;
  }
  if (t.id === "open-rudy") {
    openRudy();
    return;
  }
  if (t.id === "tour-start") {
    startTour();
    return;
  }
  if (t.id === "print-log") {
    printWorkLog();
    return;
  }
  if (t.id === "feed-retry") {
    void loadFeed().then((ok) => { if (ok) render(); });
    return;
  }
  if (t.id === "filter-toggle") {
    filtersExpanded = !filtersExpanded;
    try { localStorage.setItem("dsm-jobs-filters-expanded", filtersExpanded ? "1" : "0"); } catch { /* quota */ }
    renderJobsMain();
    return;
  }
  if (t.id === "filter-pay" || t.id === "filter-train" || t.id === "filter-trusted" || t.id === "filter-saved" || t.id === "filter-applied" || t.id === "filter-show-hidden") {
    const keyMap: Record<string, "filterPay" | "filterTrain" | "filterTrusted" | "filterSaved" | "filterApplied" | "showHidden"> = {
      "filter-pay": "filterPay",
      "filter-train": "filterTrain",
      "filter-trusted": "filterTrusted",
      "filter-saved": "filterSaved",
      "filter-applied": "filterApplied",
      "filter-show-hidden": "showHidden",
    };
    const key = keyMap[t.id];
    if (key) {
      patchState((s) => { s.filters[key] = !s.filters[key]; });
      autosave();
      refreshJobsView();
    }
    return;
  }
  if (t.hasAttribute("data-sort")) {
    const m = t.getAttribute("data-sort") as FilterPrefs["sortBy"];
    patchState((s) => { s.filters.sortBy = m; });
    autosave();
    refreshJobsView();
    return;
  }
  if (t.hasAttribute("data-remote")) {
    const remote = t.getAttribute("data-remote") as "all" | "local" | "remote";
    patchState((s) => { s.filters.filterRemote = remote; });
    autosave();
    refreshJobsView();
    return;
  }
  if (t.hasAttribute("data-commute")) {
    const v = t.getAttribute("data-commute");
    commuteMax = v === "any" ? null : Number(v);
    patchState((s) => { s.commuteRadius = commuteMax; });
    autosave();
    refreshJobsView();
    return;
  }
  if (t.hasAttribute("data-follow-done")) {
    const id = t.getAttribute("data-follow-done")!;
    patchState((s) => {
      const fu = s.followUps[id];
      if (fu) fu.done = true;
      s.applicationStatus[id] = "followed_up";
    });
    autosave();
    render();
    toast("Follow-up marked done ✦");
    return;
  }
  if (t.hasAttribute("data-follow-undo")) {
    const id = t.getAttribute("data-follow-undo")!;
    patchState((s) => {
      const fu = s.followUps[id];
      if (fu) fu.done = false;
      s.applicationStatus[id] = "applied";
    });
    autosave();
    render();
    return;
  }
  if (t.hasAttribute("data-apply")) {
    const id = t.getAttribute("data-apply")!;
    const job = jobs.find((x) => x.id === id);
    const prev = {
      applied: !!getState().applied[id],
      log: getState().appliedLog[id] ? { ...getState().appliedLog[id] } : undefined,
      fu: getState().followUps[id] ? { ...getState().followUps[id] } : undefined,
      status: getState().applicationStatus[id],
    };
    patchState((s) => {
      s.applied[id] = true;
      if (!s.appliedLog[id]) {
        s.appliedLog[id] = {
          t: job?.title || "",
          c: job?.company || "",
          d: todayISO(),
          u: job?.url || "",
          ts: new Date().toISOString(),
        };
      } else {
        s.appliedLog[id].d = todayISO();
        if (!s.appliedLog[id].ts) s.appliedLog[id].ts = new Date().toISOString();
      }
      if (!s.followUps[id]) {
        s.followUps[id] = {
          name: job?.contactName || "",
          phone: job?.contactPhone || "",
          email: job?.contactEmail || "",
          on: addDaysISO(todayISO(), 5),
          done: false,
        };
      }
      if (!s.applicationStatus[id]) s.applicationStatus[id] = "applied";
    });
    autosave();
    if (typeof navigator.vibrate === "function") navigator.vibrate(50);
    render();
    toast("Marked applied ✦", () => {
      patchState((s) => {
        if (!prev.applied) delete s.applied[id];
        if (prev.log) s.appliedLog[id] = prev.log;
        else delete s.appliedLog[id];
        if (prev.fu) s.followUps[id] = prev.fu;
        else delete s.followUps[id];
        if (prev.status) s.applicationStatus[id] = prev.status;
        else delete s.applicationStatus[id];
      });
      autosave();
      render();
    });
    return;
  }
  if (t.hasAttribute("data-unapply")) {
    const id = t.getAttribute("data-unapply")!;
    const prev = {
      applied: !!getState().applied[id],
      log: getState().appliedLog[id] ? { ...getState().appliedLog[id] } : undefined,
      fu: getState().followUps[id] ? { ...getState().followUps[id] } : undefined,
      status: getState().applicationStatus[id],
    };
    patchState((s) => {
      delete s.applied[id];
      delete s.appliedLog[id];
      delete s.followUps[id];
      delete s.applicationStatus[id];
    });
    autosave();
    render();
    toast("Applied status removed", () => {
      patchState((s) => {
        if (prev.applied) s.applied[id] = true;
        if (prev.log) s.appliedLog[id] = prev.log;
        if (prev.fu) s.followUps[id] = prev.fu;
        if (prev.status) s.applicationStatus[id] = prev.status;
        else if (prev.applied) s.applicationStatus[id] = "applied";
      });
      autosave();
      render();
    });
    return;
  }
  if (t.hasAttribute("data-save")) {
    const id = t.getAttribute("data-save")!;
    patchState((s) => { s.saved[id] = !s.saved[id]; });
    autosave();
    render();
    return;
  }
  if (t.hasAttribute("data-remind")) {
    const id = t.getAttribute("data-remind")!;
    const days = Number(t.getAttribute("data-days"));
    patchState((s) => {
      const fu = s.followUps[id];
      if (fu) {
        fu.on = addDaysISO(s.appliedLog[id]?.d || todayISO(), days);
        fu.done = false;
        s.applicationStatus[id] = "applied";
      }
    });
    autosave();
    render();
    return;
  }
  if (t.id === "upload-resume") {
    (document.getElementById("resume-file") as HTMLInputElement | null)?.click();
    return;
  }
  if (t.hasAttribute("data-doc-active")) {
    const id = t.getAttribute("data-doc-active")!;
    let selectedName = "";
    patchState((s) => {
      selectedName = selectResumeDocument(s.profile, id)?.name ?? "";
    });
    autosave();
    renderCorner();
    if (selectedName) toast(`Using ${selectedName}`);
    return;
  }
  if (t.hasAttribute("data-doc-delete")) {
    const id = t.getAttribute("data-doc-delete")!;
    const prev = {
      profile: JSON.parse(JSON.stringify(getState().profile)) as AppState["profile"],
    };
    let deletedName = "";
    patchState((s) => {
      deletedName = removeResumeDocument(s.profile, id)?.name ?? "";
    });
    if (!deletedName) return;
    autosave();
    renderCorner();
    toast("Document deleted", () => {
      patchState((s) => { s.profile = prev.profile; });
      autosave();
      renderCorner();
    });
    return;
  }
  if (t.hasAttribute("data-share")) {
    const id = t.getAttribute("data-share")!;
    const job = jobs.find((x) => x.id === id);
    if (job && typeof navigator.share === "function") {
      const url = safeUrl(job.url);
      void navigator.share({
        title: `${job.title} at ${job.company}`,
        ...(url ? { url } : {}),
      }).catch(() => {});
    }
    return;
  }
  if (t.hasAttribute("data-tailor")) {
    const id = t.getAttribute("data-tailor")!;
    const job = jobs.find((x) => x.id === id);
    if (job) openTailor(job);
    return;
  }
  if (t.hasAttribute("data-pack")) {
    const id = t.getAttribute("data-pack")!;
    const job = jobs.find((x) => x.id === id);
    if (job) openApplicationPack(job);
    return;
  }
  if (t.hasAttribute("data-hide")) {
    const id = t.getAttribute("data-hide")!;
    const wasHidden = !!getState().hidden[id];
    const wasSnoozed = getState().snoozedUntil[id];
    patchState((s) => {
      if (s.hidden[id]) {
        delete s.hidden[id];
      } else {
        s.hidden[id] = true;
        delete s.snoozedUntil[id];
      }
    });
    autosave();
    render();
    if (!wasHidden) {
      toast("Job hidden", () => {
        patchState((s) => {
          delete s.hidden[id];
          if (wasSnoozed) s.snoozedUntil[id] = wasSnoozed;
        });
        autosave();
        render();
      });
    }
    return;
  }
  if (t.hasAttribute("data-snooze")) {
    const id = t.getAttribute("data-snooze")!;
    patchState((s) => {
      if (snoozedNow(id)) {
        delete s.snoozedUntil[id];
      } else {
        // Snooze until tomorrow
        const tomorrow = addDaysISO(todayISO(), 1);
        s.snoozedUntil[id] = tomorrow;
      }
    });
    autosave();
    render();
    return;
  }
  if (t.id === "toggle-hidden") {
    patchState((s) => { s.filters.showHidden = !s.filters.showHidden; });
    autosave();
    refreshJobsView();
    return;
  }
  if (t.id === "notifybtn") {
    if (typeof Notification !== "undefined" && (Notification as typeof Notification).permission !== "denied") {
      void Notification.requestPermission().then(() => { renderApps(); });
    }
    return;
  }
  if (t.id === "coach-off-btn") {
    patchState((s) => { s.coachOff = true; });
    autosave();
    renderToday();
    return;
  }
  if (t.id === "coach-on-btn") {
    patchState((s) => { s.coachOff = false; });
    autosave();
    renderToday();
    return;
  }
}

const onProfileInput = debounce((e: Event) => {
  const t = e.target as HTMLElement;
  if (t.id === "pf-preferred") patchState((s) => { s.profile.preferredName = (t as HTMLInputElement).value; });
  else if (t.id === "pf-legal") patchState((s) => { s.profile.legalName = (t as HTMLInputElement).value; });
  else if (t.id === "pf-resume") patchState((s) => { setActiveResumeText(s.profile, (t as HTMLTextAreaElement).value); });
  else return;
  autosave();
}, 400);

const onSearchInput = debounce((e: Event) => {
  const q = (e.target as HTMLInputElement).value;
  patchState((s) => { s.filters.searchQ = q; });
  autosave();
  refreshJobsView();
}, 200);

function bindViewHost(): void {
  const host = $("#view-host");
  if (!host || host.dataset.bound) return;
  host.dataset.bound = "1";
  host.addEventListener("click", handleViewClick);
  const handleField = (e: Event) => {
    const t = e.target as HTMLElement;
    if (t.id === "job-search") { onSearchInput(e); return; }
    if (t.hasAttribute("data-note")) {
      const jobId = t.getAttribute("data-note")!;
      const body = (t as HTMLTextAreaElement).value;
      patchState((s) => { s.notes[jobId] = body; });
      debouncePushNote(jobId);
      return;
    }
    if (t.hasAttribute("data-follow-name")) {
      const id = t.getAttribute("data-follow-name")!;
      patchState((s) => { const fu = s.followUps[id]; if (fu) fu.name = (t as HTMLInputElement).value; });
      autosave();
      return;
    }
    if (t.hasAttribute("data-follow-phone")) {
      const id = t.getAttribute("data-follow-phone")!;
      patchState((s) => { const fu = s.followUps[id]; if (fu) fu.phone = (t as HTMLInputElement).value; });
      autosave();
      return;
    }
    if (t.hasAttribute("data-follow-email")) {
      const id = t.getAttribute("data-follow-email")!;
      patchState((s) => { const fu = s.followUps[id]; if (fu) fu.email = (t as HTMLInputElement).value; });
      autosave();
      return;
    }
    if (t.hasAttribute("data-app-status")) {
      const id = t.getAttribute("data-app-status")!;
      const value = (t as HTMLSelectElement).value as ApplicationStatus;
      if (!APP_STATUS_LABELS[value]) return;
      patchState((s) => {
        s.applicationStatus[id] = value;
        const fu = s.followUps[id];
        if (fu) fu.done = statusStopsFollowUps(value);
      });
      autosave();
      renderFollowBadge();
      return;
    }
    if (t.hasAttribute("data-follow-date")) {
      const id = t.getAttribute("data-follow-date")!;
      const value = (t as HTMLInputElement).value;
      patchState((s) => {
        const fu = s.followUps[id];
        if (fu && value) {
          fu.on = value;
          fu.done = false;
        }
      });
      autosave();
      refreshJobsView();
      renderFollowBadge();
      return;
    }
    if (t.id === "filter-category") {
      patchState((s) => { s.filters.filterCategory = (t as HTMLSelectElement).value; });
      autosave();
      refreshJobsView();
      return;
    }
    onProfileInput(e);
  };
  host.addEventListener("input", handleField);
  host.addEventListener("change", handleField);
}

function setAuthMsg(text: string, isErr = false): void {
  const el = $("#auth-msg");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("auth-err", isErr);
}

function setForgotMsg(text: string, isErr = false): void {
  const el = $("#auth-forgot-msg");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("auth-err", isErr);
}

function setRecoverMsg(text: string, isErr = false): void {
  const el = $("#auth-recmsg");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("auth-err", isErr);
}

function setPasswordFieldVisible(input: HTMLInputElement, toggle: HTMLButtonElement, visible: boolean): void {
  input.type = visible ? "text" : "password";
  toggle.setAttribute("aria-label", visible ? "Hide password" : "Show password");
  toggle.setAttribute("aria-pressed", visible ? "true" : "false");
  toggle.querySelector<SVGElement>(".field-password-icon--show")?.classList.toggle("is-hidden", visible);
  toggle.querySelector<SVGElement>(".field-password-icon--hide")?.classList.toggle("is-hidden", !visible);
}

function resetAllPasswordVisibility(): void {
  document.querySelectorAll<HTMLInputElement>("#auth-modal .field-password .field").forEach((input) => {
    const toggle = input.closest(".field-password")?.querySelector<HTMLButtonElement>(".field-password-toggle");
    if (toggle) setPasswordFieldVisible(input, toggle, false);
  });
}

function wirePasswordToggles(): void {
  $("#auth-modal")?.addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>(".field-password-toggle");
    if (!btn) return;
    const input = btn.closest(".field-password")?.querySelector<HTMLInputElement>(".field");
    if (!input) return;
    const visible = input.type === "password";
    setPasswordFieldVisible(input, btn, visible);
  });
}

function setAuthMode(mode: "signin" | "signup"): void {
  authMode = mode;
  const title = $("#auth-title");
  const sub = $("#auth-sub");
  const signInBtn = $("#auth-signin");
  const signUpBtn = $("#auth-signup");
  const forgot = $("#auth-forgot");
  const toggle = $("#auth-toggle");
  const passEl = document.getElementById("auth-pass") as HTMLInputElement | null;
  if (title) title.textContent = mode === "signup" ? "Create your account ✦" : "Welcome back ✦";
  if (sub) {
    sub.textContent = mode === "signup"
      ? "Make an account so her jobs, notes and chats are saved and follow her."
      : "Sign in so her jobs, notes and chats follow her to any device.";
  }
  if (signInBtn) signInBtn.textContent = mode === "signup" ? "Create account" : "Sign in";
  if (signUpBtn) signUpBtn.hidden = mode === "signup";
  if (forgot) forgot.hidden = mode === "signup";
  if (toggle) toggle.textContent = mode === "signup" ? "Already have an account? Sign in" : "New here? Create an account";
  if (passEl) {
    passEl.hidden = false;
    passEl.setAttribute("autocomplete", mode === "signup" ? "new-password" : "current-password");
    passEl.placeholder = mode === "signup" ? "Choose a password (8+ characters)" : "Password";
  }
  setAuthMsg("");
}

function showAuthMain(): void {
  const main = $("#auth-main");
  const forgotPanel = $("#auth-forgot-panel");
  const recover = $("#auth-recover");
  if (main) main.hidden = false;
  if (forgotPanel) forgotPanel.hidden = true;
  if (recover) recover.hidden = true;
  setForgotMsg("");
  setRecoverMsg("");
}

function showAuthForgot(): void {
  const main = $("#auth-main");
  const forgotPanel = $("#auth-forgot-panel");
  const recover = $("#auth-recover");
  const signInEmail = (document.getElementById("auth-email") as HTMLInputElement | null)?.value.trim() ?? "";
  const forgotEmail = document.getElementById("auth-forgot-email") as HTMLInputElement | null;
  if (main) main.hidden = true;
  if (forgotPanel) forgotPanel.hidden = false;
  if (recover) recover.hidden = true;
  if (forgotEmail && signInEmail && !forgotEmail.value.trim()) forgotEmail.value = signInEmail;
  setAuthMsg("");
  setForgotMsg("");
  setTimeout(() => { forgotEmail?.focus(); }, 50);
}

function showAuthRecover(): void {
  const main = $("#auth-main");
  const forgotPanel = $("#auth-forgot-panel");
  const recover = $("#auth-recover");
  if (main) main.hidden = true;
  if (forgotPanel) forgotPanel.hidden = true;
  if (recover) recover.hidden = false;
  setAuthMsg("");
  setForgotMsg("");
  setTimeout(() => { (document.getElementById("auth-newpass") as HTMLInputElement | null)?.focus(); }, 50);
}

function isPasswordRecoveryHash(): boolean {
  if (typeof window === "undefined") return false;
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) return false;
  const params = new URLSearchParams(hash);
  return params.get("type") === "recovery";
}

function renderCallButton(): void {
  const btn = $("#call-btn");
  if (!btn) return;
  const rawWho = meta.contact?.trim() || "Brady";
  const who = /^me$/i.test(rawWho) ? "Brady" : rawWho;
  if (meta.phone?.trim()) {
    const tel = meta.phone.replace(/[^0-9+]/g, "");
    btn.setAttribute("href", `tel:${tel}`);
    btn.textContent = `Something feels wrong? Call ${who}`;
    btn.hidden = false;
  } else {
    btn.removeAttribute("href");
    btn.textContent = `Something feels wrong? Ask ${who} before you reply`;
    btn.hidden = false;
  }
}

function offerPasskeyNudge(userId: string): void {
  const sb = getClient();
  if (!sb || !supportsPasskey(sb)) return;
  const key = `pk_offered:${userId}`;
  if (localStorage.getItem(key)) return;
  localStorage.setItem(key, "1");
  toast("Add Face ID for instant sign-in next time?");
  void registerPasskey(sb).then((err) => {
    if (!err) toast("Face ID ready ✦");
  });
}

function openAuth(): void {
  const back = $("#auth-modal");
  if (back) {
    showAuthMain();
    setAuthMode("signin");
    back.hidden = false;
    document.body.style.overflow = "hidden";
    setModalTrap(back);
    setTimeout(() => { (document.getElementById("auth-email") as HTMLInputElement | null)?.focus(); }, 50);
  }
}

function closeAuth(): void {
  const back = $("#auth-modal");
  if (back) back.hidden = true;
  document.body.style.overflow = "";
  clearModalTrap();
  resetAllPasswordVisibility();
}

function openRudy(): void {
  if (!authed) return openAuth();
  const ov = $("#rudy-overlay");
  if (ov) { ov.hidden = false; document.body.style.overflow = "hidden"; setModalTrap(ov); }
  void renderRudyLog();
  setTimeout(() => { ($("#rudy-input") as HTMLInputElement | null)?.focus(); }, 50);
}

function closeRudy(): void {
  const ov = $("#rudy-overlay");
  if (ov) { ov.hidden = true; document.body.style.overflow = ""; clearModalTrap(); }
  stopRudyVoice();
  closeRudyMemory();
}

/** Human label for a saved quiz value, e.g. kind="home" -> "Working from home".
 * Falls back to the raw value if it's not one of the known quiz options — this
 * happens for a value the companion itself saved (e.g. a free-text `notes`). */
function quizValueLabel(key: string, val: string): string {
  const entry = QUIZ.find(([k]) => k === key);
  const opt = entry?.[2].find(([v]) => v === val);
  return opt ? opt[1] : val;
}

/** "What Rudy remembers" — a plain-language list of every fact Rudy actually
 * has about her (preference flags the quiz/chat set, her saved résumé
 * documents, and her recent chat history), each with a one-tap way to forget
 * it. Nothing here is inferred; it mirrors exactly what companion/grounding.ts
 * sends the model and what the chat log holds. */
function renderRudyMemory(): void {
  const body = $("#rudy-memory-body");
  if (!body) return;
  const p = getState().profile;
  const quizEntries = Object.entries(p.quiz).filter(([, v]) => !!v);

  const prefsHtml = quizEntries.length
    ? `<div class="doc-list" aria-label="Preferences Rudy has picked up">
        ${quizEntries.map(([key, val]) => {
      const entry = QUIZ.find(([k]) => k === key);
      const question = entry?.[1] ?? key;
      return `<article class="doc-item">
            <div class="doc-main">
              <span class="doc-name">${esc(question)}</span>
              <span class="doc-meta">${esc(quizValueLabel(key, val))}</span>
            </div>
            <div class="doc-actions">
              <button type="button" class="btn btn-ghost btn-sm" data-mem-forget-quiz="${esc(key)}">Forget</button>
            </div>
          </article>`;
    }).join("")}
      </div>`
    : `<p class="field-hint doc-empty">No preferences saved yet — answer a few questions in My corner, or just tell Rudy in chat.</p>`;

  const docsHtml = p.documents.length
    ? `<div class="doc-list" aria-label="Résumé documents Rudy can read from">
        ${p.documents.map((doc) => {
      const active = doc.id === p.activeDocumentId;
      return `<article class="doc-item${active ? " is-active" : ""}">
            <div class="doc-main">
              <span class="doc-name">${esc(doc.name)}</span>
              <span class="doc-meta">${esc(documentStats(doc.text))}${active ? " · selected for tailoring" : ""}</span>
            </div>
            <div class="doc-actions">
              <button type="button" class="btn btn-ghost btn-sm" data-mem-forget-doc="${esc(doc.id)}">Delete</button>
            </div>
          </article>`;
    }).join("")}
      </div>`
    : `<p class="field-hint doc-empty">No résumé saved yet — add one in My corner and Rudy can read it.</p>`;

  body.innerHTML = `
    <section class="mem-section">
      <h4 class="section-title">Preferences she's told Rudy</h4>
      ${prefsHtml}
    </section>
    <section class="mem-section">
      <h4 class="section-title">Saved résumé</h4>
      ${docsHtml}
    </section>
    <section class="mem-section">
      <h4 class="section-title">Chat history</h4>
      <p class="field-hint" id="rudy-memory-chat-count">Checking...</p>
      <button type="button" class="btn btn-ghost btn-sm" id="rudy-memory-clear-chat">Clear conversation history</button>
    </section>
  `;

  void loadChatHistory().then((msgs) => {
    const el = $("#rudy-memory-chat-count");
    if (!el) return;
    el.textContent = msgs.length
      ? `Rudy can see your last ${msgs.length} message${msgs.length === 1 ? "" : "s"} to keep the conversation going.`
      : "No chat history saved yet.";
  });
}

function openRudyMemory(): void {
  const log = $("#rudy-log");
  const prompts = $(".rudy-prompts") as HTMLElement | null;
  const mem = $("#rudy-memory");
  const btn = $("#rudy-memory-open");
  if (log) log.hidden = true;
  if (prompts) prompts.hidden = true;
  if (mem) mem.hidden = false;
  if (btn) btn.setAttribute("aria-pressed", "true");
  renderRudyMemory();
}

function closeRudyMemory(): void {
  const log = $("#rudy-log");
  const prompts = $(".rudy-prompts") as HTMLElement | null;
  const mem = $("#rudy-memory");
  const btn = $("#rudy-memory-open");
  if (log) log.hidden = false;
  if (prompts) prompts.hidden = false;
  if (mem) mem.hidden = true;
  if (btn) btn.setAttribute("aria-pressed", "false");
}

async function renderRudyLog(): Promise<void> {
  const log = $("#rudy-log");
  if (!log) return;
  if (rudyHistoryLoaded) return;
  rudyHistoryLoaded = true;
  const msgs = await loadChatHistory();
  log.innerHTML = "";
  if (!msgs.length) {
    const p = getState().profile;
    const name = (p.preferredName || p.legalName).trim();
    const greeting = name ? `Hi ${esc(name)} —` : "Hi —";
    log.insertAdjacentHTML("beforeend", `<div class="bubble ai">${greeting} I'm Rudy 🐄. No pressure today. Tell me how you're doing, ask for a tiny next step, or tap a job and I'll help you talk it through. Moo means I'm in your corner.</div>`);
  } else {
    for (const m of msgs) {
      const cls = m.role === "user" ? "me" : "ai";
      log.insertAdjacentHTML("beforeend", `<div class="bubble ${cls}">${esc(m.body)}</div>`);
    }
  }
  log.scrollTop = log.scrollHeight;
}

async function sendRudy(): Promise<void> {
  const inp = $("#rudy-input") as HTMLInputElement | null;
  const log = $("#rudy-log");
  if (!inp || !log || !inp.value.trim()) return;
  const msg = inp.value.trim();
  inp.value = "";
  log.insertAdjacentHTML("beforeend", `<div class="bubble me">${esc(msg)}</div>`);
  const thinkingBubble = document.createElement("div");
  thinkingBubble.className = "bubble ai think";
  thinkingBubble.textContent = pickSaying(THINKING_LINES);
  log.appendChild(thinkingBubble);
  log.scrollTop = log.scrollHeight;

  // Persist user message to Supabase + localStorage
  void pushChatMessage("user", msg);
  appendChatToLocal("user", msg);

  const sb = getClient();
  if (!sb) {
    thinkingBubble.classList.remove("think");
    thinkingBubble.textContent = "No connection right now — I'll be right here when you're back online. 💜";
    log.scrollTop = log.scrollHeight;
    return;
  }
  try {
    const { data, error } = await sb.functions.invoke("companion", { body: { message: msg, spicy: spicyOn } });
    const reply = (error?.message ? null : (data?.reply as string)) || "I'm here with you. Try again in a moment. 💜";
    thinkingBubble.classList.remove("think");
    thinkingBubble.textContent = reply;
    // Persist Rudy's reply
    void pushChatMessage("assistant", reply);
    appendChatToLocal("assistant", reply);
    speakText(reply);
  } catch {
    thinkingBubble.classList.remove("think");
    thinkingBubble.textContent = "Connection blip — you're still safe. Try again. 💜";
  }
  log.scrollTop = log.scrollHeight;
}

function tailorLabel(job: Job): string {
  return `${job.title} at ${job.company}`;
}

function tailorLoaderHTML(job: Job): string {
  return `<section class="tailor-load" aria-label="Rudy is tailoring this résumé">
    <div class="tailor-load-head">
      <p class="tailor-kicker">Rudy is tailoring</p>
      <h3>${esc(job.title)}</h3>
      <p>${esc(job.company)}</p>
    </div>
    <div class="tailor-route" aria-hidden="true">
      <span class="route-track"></span><span class="route-stop route-stop-a"></span><span class="route-stop route-stop-b"></span><span class="route-stop route-stop-c"></span>
    </div>
    <div class="tailor-bar" id="tailor-meter" role="progressbar" aria-label="Résumé tailoring progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i id="tailor-progress"></i></div>
    <div class="tailor-stage" id="tailor-stage" role="status" aria-live="polite">${esc(pickSaying(TAILOR_LINES))}</div>
    <p class="tailor-time">Usually takes 15-30 seconds. Keep this open; nothing is submitted for you.</p>
    <ul class="tailor-trust">
      <li>Uses only her saved résumé text and this job posting.</li>
      <li>Checks the draft for made-up details before showing it.</li>
      <li>You choose what to copy, download, or ignore.</li>
    </ul>
  </section>`;
}

function startTailorLoader(job: Job): void {
  const body = $("#tailor-body");
  if (!body) return;
  body.innerHTML = tailorLoaderHTML(job);
  const fill = document.getElementById("tailor-progress");
  const meter = document.getElementById("tailor-meter");
  const msg = document.getElementById("tailor-stage");
  const stages = [
    "Reading the résumé and this posting side by side...",
    "Matching real experience to the job requirements...",
    "Rudy is side-eyeing the buzzwords and cutting fluff...",
    "Checking the draft for made-up details...",
    "Getting copy buttons ready...",
  ];
  const t0 = Date.now();
  const DUR = 9000;
  if (tailorTimer) clearInterval(tailorTimer);
  tailorTimer = setInterval(() => {
    const el = Date.now() - t0;
    const pct = Math.min(94, (el / DUR) * 94);
    if (fill) fill.style.width = `${pct.toFixed(1)}%`;
    if (meter) meter.setAttribute("aria-valuenow", Math.round(pct).toString());
    if (msg) {
      const i = Math.min(stages.length - 1, Math.floor(el / (DUR / stages.length)));
      msg.textContent = stages[i];
    }
  }, 180);
}

function stopTailorLoader(): void {
  if (tailorTimer) {
    clearInterval(tailorTimer);
    tailorTimer = null;
  }
  const fill = document.getElementById("tailor-progress");
  const meter = document.getElementById("tailor-meter");
  if (fill) fill.style.width = "100%";
  if (meter) meter.setAttribute("aria-valuenow", "100");
}

function openTailor(job: Job): void {
  if (!authed) return openAuth();
  const resume = getState().profile.resume.trim();
  if (!resume) {
    toast("Paste her résumé in My corner first");
    setView("corner");
    return;
  }
  const modal = $("#tailor-modal");
  const body = $("#tailor-body");
  if (!modal || !body) return;
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  setModalTrap(modal);
  const full = (job.descFull || "").trim();
  if (full.length >= 200) {
    startTailorLoader(job);
    void runTailor(job, resume, full);
  } else {
    renderTailorPaste(job, resume);
  }
}

function closeTailor(): void {
  const modal = $("#tailor-modal");
  if (modal) modal.hidden = true;
  document.body.style.overflow = "";
  clearModalTrap();
  stopTailorLoader();
}

function openApplicationPack(job: Job): void {
  const pack = getState().applicationPacks[job.id];
  if (!pack) {
    toast("No saved pack for this job yet");
    return;
  }
  const modal = $("#tailor-modal");
  if (!modal) return;
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  setModalTrap(modal);
  renderTailorResult(job, packToTailorResult(pack));
}

function renderTailorPaste(job: Job, resume: string, initialText = ""): void {
  const body = $("#tailor-body");
  if (!body) return;
  body.innerHTML = `
    <div class="tailor-paste-panel">
      <p class="tailor-kicker">More detail helps</p>
      <h3>${esc(job.title)}</h3>
      <p class="job-meta">${esc(job.company)} does not have a full posting saved here yet.</p>
      <p class="job-meta">Paste the full posting if you have it. Rudy can still use the title and company, but the result is better with the real requirements.</p>
      <textarea class="field" id="tailor-paste" rows="6" placeholder="Paste job posting text here...">${esc(initialText)}</textarea>
      <p class="field-hint" id="tailor-paste-msg" aria-live="polite"></p>
      <div class="tailor-actions">
        <button type="button" class="btn btn-primary" id="tailor-run-paste">Tailor with pasted posting</button>
        <button type="button" class="btn btn-ghost" id="tailor-run-title">Use title only</button>
      </div>
    </div>
  `;
  $("#tailor-run-paste")?.addEventListener("click", () => {
    const pasteEl = document.getElementById("tailor-paste") as HTMLTextAreaElement | null;
    const paste = pasteEl?.value.trim() || "";
    if (paste.length < 80) {
      const msg = document.getElementById("tailor-paste-msg");
      if (msg) msg.textContent = "Paste more of the posting, or choose Use title only.";
      pasteEl?.focus();
      return;
    }
    startTailorLoader(job);
    void runTailor(job, resume, paste);
  });
  $("#tailor-run-title")?.addEventListener("click", () => {
    startTailorLoader(job);
    void runTailor(job, resume, "");
  });
}

function normalizeTailorError(message?: string): string {
  return Array.from((message || "").toLowerCase().normalize("NFD"))
    .filter((ch) => {
      const code = ch.charCodeAt(0);
      return code < 0x0300 || code > 0x036f;
    })
    .join("");
}

function messageFromTailorErrorBody(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return undefined;
  const body = value as ErrorBodyLike;
  if (typeof body.error === "string") return body.error;
  if (typeof body.message === "string") return body.message;
  return undefined;
}

async function extractTailorErrorMessage(dataError: unknown, error: unknown): Promise<string | undefined> {
  const fromData = messageFromTailorErrorBody(dataError);
  if (fromData) return fromData;
  const maybeError = error as { message?: unknown; context?: unknown } | null;
  const context = maybeError?.context as ResponseLike | undefined;
  if (context?.json) {
    try {
      const source = context.clone?.() ?? context;
      const fromBody = messageFromTailorErrorBody(await source.json?.());
      if (fromBody) return fromBody;
    } catch { /* fall through to text / message */ }
  }
  if (context?.text) {
    try {
      const source = context.clone?.() ?? context;
      const text = (await source.text?.())?.trim();
      if (text) return text;
    } catch { /* fall through to message */ }
  }
  return typeof maybeError?.message === "string" ? maybeError.message : undefined;
}

function friendlyTailorError(message?: string): string {
  const lower = normalizeTailorError(message);
  if (lower.includes("auth") || lower.includes("jwt") || lower.includes("session")) {
    return "Your sign-in needs a refresh. Sign in again, then try this tailor.";
  }
  if (lower.includes("resume") || lower.includes("too short")) {
    return "The résumé text looks too short to tailor well. Check My corner, then try again.";
  }
  if (lower.includes("timeout") || lower.includes("network") || lower.includes("fetch")) {
    return "The connection stalled before Rudy finished. Nothing was changed.";
  }
  return "Rudy couldn't finish this version. Nothing was changed.";
}

function renderTailorError(job: Job, message: string): void {
  const body = $("#tailor-body");
  if (!body) return;
  body.innerHTML = `
    <div class="tailor-error" role="alert">
      <p class="tailor-kicker">Tailor paused</p>
      <h3>${esc(tailorLabel(job))}</h3>
      <p>${esc(message)}</p>
      <p class="field-hint">You can retry the same request or paste the full job posting before trying again.</p>
      <div class="tailor-actions">
        <button type="button" class="btn btn-primary" data-tailor-retry>Try again</button>
        <button type="button" class="btn btn-ghost" data-tailor-edit>Edit posting text</button>
      </div>
    </div>
  `;
  body.querySelector("[data-tailor-retry]")?.addEventListener("click", () => {
    if (!lastTailorRequest) return;
    startTailorLoader(lastTailorRequest.job);
    void runTailor(lastTailorRequest.job, lastTailorRequest.resume, lastTailorRequest.jobText);
  });
  body.querySelector("[data-tailor-edit]")?.addEventListener("click", () => {
    if (!lastTailorRequest) return;
    renderTailorPaste(lastTailorRequest.job, lastTailorRequest.resume, lastTailorRequest.jobText);
  });
}

function cleanPackList(items: unknown, limit: number): string[] {
  if (!Array.isArray(items)) return [];
  return items.map((x) => String(x).trim()).filter(Boolean).slice(0, limit);
}

function fallbackFollowUp(job: Job): string {
  return `Hello, I applied for the ${job.title} position at ${job.company} and wanted to follow up. I am still interested and would be glad to answer any questions about my experience. Thank you for your time.`;
}

function normalizeAtsAlignment(value: TailorResult["ats_alignment"]): AtsAlignment {
  return {
    strong_matches: cleanPackList(value?.strong_matches, 6),
    suggested_keywords: cleanPackList(value?.suggested_keywords, 6),
    note: typeof value?.note === "string" && value.note.trim()
      ? value.note.trim()
      : "Use the strongest truthful matches. Do not add keywords unless her résumé actually supports them.",
  };
}

function buildApplicationPack(job: Job, data: TailorResult): ApplicationPack {
  const createdAt = new Date().toISOString();
  return {
    id: `${job.id}:${createdAt}`,
    jobId: job.id,
    jobTitle: job.title,
    company: job.company,
    createdAt,
    resume: data.resume,
    coverNote: data.cover_note || "",
    followUp: data.follow_up || fallbackFollowUp(job),
    changes: cleanPackList(data.changes, 8),
    ats: normalizeAtsAlignment(data.ats_alignment),
  };
}

function packToTailorResult(pack: ApplicationPack): TailorResult {
  return {
    resume: pack.resume,
    changes: pack.changes,
    cover_note: pack.coverNote,
    follow_up: pack.followUp,
    ats_alignment: pack.ats,
  };
}

function saveApplicationPack(pack: ApplicationPack): void {
  patchState((s) => {
    s.applicationPacks[pack.jobId] = pack;
  });
  autosave();
  toast("Application pack saved ✦");
  render();
}

function renderTailorResult(job: Job, data: TailorResult): void {
  const body = $("#tailor-body");
  if (!body) return;
  const changes = (Array.isArray(data.changes) ? data.changes : [])
    .map((x) => String(x).trim())
    .filter(Boolean)
    .slice(0, 6);
  const pack = buildApplicationPack(job, data);
  const ats = pack.ats;
  const saved = !!getState().applicationPacks[job.id];
  body.innerHTML = `
    <div class="tailor-result-head">
      <p class="tailor-kicker">Ready to review</p>
      <h3>${esc(tailorLabel(job))}</h3>
      <p class="job-meta">Rudy tailored this into an application pack using her real experience. Review before copying.</p>
    </div>
    <div class="tailor-changes">
      <h3 class="section-title">What Rudy changed</h3>
      ${changes.length
    ? `<ul>${changes.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
    : `<p class="field-hint">No change summary came back. Read the text before using it.</p>`}
    </div>
    <div class="tailor-result-actions">
      <button type="button" class="btn btn-primary" data-copy="both">Copy both</button>
      <button type="button" class="btn btn-ghost" data-download>Download</button>
      <button type="button" class="btn btn-ghost" data-save-pack>${saved ? "Saved pack" : "Save application pack"}</button>
    </div>
    <div class="tailor-block">
      <h3 class="section-title">Résumé</h3>
      <textarea class="field tailor-ta" id="tailor-resume" rows="8" readonly>${esc(data.resume)}</textarea>
      <button type="button" class="btn btn-ghost" data-copy="resume">Copy résumé</button>
    </div>
    ${data.cover_note ? `
    <div class="tailor-block">
      <h3 class="section-title">Cover note</h3>
      <textarea class="field tailor-ta" id="tailor-cover" rows="5" readonly>${esc(data.cover_note)}</textarea>
      <button type="button" class="btn btn-ghost" data-copy="cover">Copy cover note</button>
    </div>` : ""}
    <div class="tailor-block">
      <h3 class="section-title">Follow-up message</h3>
      <textarea class="field tailor-ta" id="tailor-follow-up" rows="4" readonly>${esc(pack.followUp)}</textarea>
      <button type="button" class="btn btn-ghost" data-copy="follow">Copy follow-up</button>
    </div>
    <div class="tailor-changes tailor-ats">
      <h3 class="section-title">ATS alignment</h3>
      ${ats.strong_matches.length
    ? `<p class="field-hint"><b>Strong matches:</b> ${ats.strong_matches.map(esc).join(", ")}</p>`
    : `<p class="field-hint"><b>Strong matches:</b> Review manually before applying.</p>`}
      ${ats.suggested_keywords.length
    ? `<p class="field-hint"><b>Use carefully:</b> ${ats.suggested_keywords.map(esc).join(", ")}</p>`
    : ""}
      <p class="field-hint">${esc(ats.note)}</p>
    </div>
  `;
  const downloadText = [
    data.resume,
    data.cover_note ? `=== COVER NOTE ===\n\n${data.cover_note}` : "",
    `=== FOLLOW-UP MESSAGE ===\n\n${pack.followUp}`,
    `=== WHAT CHANGED ===\n\n${changes.map((x) => `- ${x}`).join("\n") || "Review manually."}`,
    `=== ATS ALIGNMENT ===\n\nStrong matches: ${ats.strong_matches.join(", ") || "Review manually."}\nUse carefully: ${ats.suggested_keywords.join(", ") || "None listed."}\n${ats.note}`,
  ].filter(Boolean).join("\n\n\n");
  body.querySelector("[data-save-pack]")?.addEventListener("click", () => {
    saveApplicationPack(pack);
    const btn = body.querySelector("[data-save-pack]") as HTMLButtonElement | null;
    if (btn) {
      btn.textContent = "Saved pack";
      btn.classList.add("is-done");
    }
  });
  body.querySelectorAll("[data-download]").forEach((btn) => btn.addEventListener("click", () => {
    const blob = new Blob([downloadText], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${job.company.replace(/\W+/g, "-").slice(0, 30)}-resume.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Downloaded ✦");
  }));
  body.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const which = btn.getAttribute("data-copy");
      let text = data.resume;
      if (which === "cover") text = data.cover_note || "";
      else if (which === "follow") text = pack.followUp;
      else if (which === "both") text = downloadText;
      const button = btn as HTMLButtonElement;
      const original = button.textContent || "Copy";
      try {
        await navigator.clipboard?.writeText(text);
        button.textContent = "Copied";
        button.classList.add("is-done");
        toast("Copied");
        setTimeout(() => {
          button.textContent = original;
          button.classList.remove("is-done");
        }, 1400);
      } catch {
        toast("Copy failed — select text manually");
      }
    });
  });
}

async function runTailor(job: Job, resume: string, jobText: string): Promise<void> {
  const sb = getClient();
  const body = $("#tailor-body");
  lastTailorRequest = { job, resume, jobText };
  if (!sb || !body) {
    stopTailorLoader();
    if (body) renderTailorError(job, "Sign in to tailor. That keeps her résumé private and saved to her account.");
    return;
  }
  try {
    const { data, error } = await sb.functions.invoke("resume-tailor", {
      body: { resume, jobTitle: job.title, company: job.company, jobText },
    });
    stopTailorLoader();
    if (error || !data?.resume) {
      renderTailorError(job, friendlyTailorError(await extractTailorErrorMessage(data?.error, error)));
      return;
    }
    renderTailorResult(job, data as TailorResult);
  } catch {
    stopTailorLoader();
    renderTailorError(job, "No connection right now. Nothing was changed; try again when the connection is back.");
  }
}

async function refreshAuth(): Promise<void> {
  const sb = getClient();
  if (!sb) { authed = false; clearAutosave(); render(); return; }
  const user = await currentUser(sb);
  const wasAuthed = authed;
  authed = !!user;
  const btn = $("#acct-btn");
  if (btn) btn.classList.toggle("in", authed);
  if (user) {
    initAutosave(sb, user.id, toast);
    await pullProfile();
    await pullLegacyTables();
    await pullNotes();
    autosave();
    await drainPendingSaves();
    await updateSyncBanner();
    commuteMax = getState().commuteRadius;
    rudyHistoryLoaded = false;
    if (!wasAuthed) offerPasskeyNudge(user.id);
  } else {
    clearAutosave();
    await updateSyncBanner();
  }
  render();
}

async function loadFeed(): Promise<boolean> {
  const base = import.meta.env.BASE_URL;
  feedLoadFailed = false;
  try {
    const [jobsR, metaR] = await Promise.all([
      fetch(`${base}jobs.json`),
      fetch(`${base}meta.json`),
    ]);
    if (!jobsR.ok || !metaR.ok) {
      throw new Error(`Feed unavailable (${jobsR.status}/${metaR.status})`);
    }
    jobs = (await jobsR.json()) as Job[];
    meta = (await metaR.json()) as Meta;
    // Resources hub is optional — a missing/!ok resources.json never breaks jobs.
    try {
      const rR = await fetch(`${base}resources.json`);
      if (rR.ok) hub = (await rR.json()) as ResourceHub;
    } catch { /* resources optional */ }
    markJobsSeen();
    updateStaleBanner();
    const gen = $("#meta-generated");
    if (gen) gen.textContent = `${meta.safe} safe jobs · scam-checked · ${meta.generated}`;
    renderCallButton();
    return true;
  } catch {
    feedLoadFailed = true;
    const gen = $("#meta-generated");
    if (gen) gen.textContent = "Couldn't load today's jobs — check your connection and refresh.";
    return false;
  }
}

function maybeIosInstallCoach(): void {
  const nav = navigator as Navigator & { standalone?: boolean };
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  if (!isIOS || nav.standalone) return;
  if (localStorage.getItem("ios-install-shown")) return;
  // Wait for a clear screen so it never stacks under the update modal or the
  // first-run tour (which is exactly what buried it before). Retry a few times.
  let tries = 0;
  const tryShow = (): void => {
    if (localStorage.getItem("ios-install-shown")) return;
    if (anyModalOpen()) {
      if (tries++ < 20) setTimeout(tryShow, 1500);
      return;
    }
    localStorage.setItem("ios-install-shown", "1");
    const m = $("#ios-install-modal");
    if (m) m.hidden = false;
  };
  setTimeout(tryShow, 1600);
}

function wirePullToRefresh(): void {
  const host = $("#view-host");
  if (!host) return;
  host.addEventListener("touchstart", (e) => {
    if (view !== "jobs" || window.scrollY > 8) return;
    pullStartY = e.touches[0]?.clientY ?? 0;
    pullReady = false;
  }, { passive: true });
  host.addEventListener("touchmove", (e) => {
    if (view !== "jobs" || window.scrollY > 8 || pullRefreshing) return;
    const dy = (e.touches[0]?.clientY ?? 0) - pullStartY;
    if (dy > 70 && !pullReady) {
      pullReady = true;
      try { navigator.vibrate?.(10); } catch { /* unsupported */ }
      toast("Release to refresh jobs");
    }
  }, { passive: true });
  host.addEventListener("touchend", (e) => {
    if (view !== "jobs" || window.scrollY > 8 || pullRefreshing) return;
    const dy = (e.changedTouches[0]?.clientY ?? 0) - pullStartY;
    if (dy > 90) {
      pullRefreshing = true;
      toast("Refreshing jobs…");
      void loadFeed()
        .then((ok) => {
          if (ok) {
            render();
            toast("Jobs refreshed");
          } else {
            toast("Couldn't refresh jobs — check connection");
          }
        })
        .finally(() => {
          pullReady = false;
          pullRefreshing = false;
        });
    } else {
      pullReady = false;
    }
  }, { passive: true });
  host.addEventListener("touchcancel", () => {
    pullReady = false;
    pullRefreshing = false;
  }, { passive: true });
}

async function boot(): Promise<void> {
  loadLocal();
  migrateLocalV1();
  try {
    const savedFiltersExpanded = localStorage.getItem("dsm-jobs-filters-expanded");
    if (savedFiltersExpanded != null) filtersExpanded = savedFiltersExpanded !== "0";
  } catch { /* quota */ }
  commuteMax = getState().commuteRadius;
  bindViewHost();
  updateConnectionBanners();
  window.addEventListener("dsm-jobs-outbox-change", () => { void updateSyncBanner(); });
  window.addEventListener("online", () => {
    updateConnectionBanners();
    void drainPendingSaves().then(() => updateSyncBanner());
    void loadFeed().then((ok) => { if (ok) render(); });
  });
  window.addEventListener("offline", updateConnectionBanners);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Tab") { handleTrapTab(e); return; }
    if (e.key === "Escape") {
      if ($("#auth-modal") && !($("#auth-modal") as HTMLElement).hidden) closeAuth();
      if ($("#ios-install-modal") && !($("#ios-install-modal") as HTMLElement).hidden) {
        ($("#ios-install-modal") as HTMLElement).hidden = true;
      }
    }
  });

  const genLoading = $("#meta-generated");
  if (genLoading) genLoading.textContent = pickSaying(SEARCHING_LINES);
  const ok = await loadFeed();
  if (!ok) {
    const host = $("#view-host");
    if (host) {
      host.innerHTML = `<div class="card"><p class="job-meta">Jobs didn't load. Try again in a moment — your saved work on this phone is still here.</p><button type="button" class="btn btn-primary" id="feed-retry" style="margin-top:12px">Try again</button></div>`;
    }
  }

  document.querySelectorAll(".tab").forEach((b) => {
    b.addEventListener("click", () => setView(b.getAttribute("data-view") as ViewName));
  });

  $("#acct-btn")?.addEventListener("click", () => (authed ? signOut(getClient()!).then(refreshAuth) : openAuth()));
  $("#auth-close")?.addEventListener("click", closeAuth);
  wirePasswordToggles();
  $("#auth-modal")?.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).id === "auth-modal") closeAuth();
  });
  $("#auth-toggle")?.addEventListener("click", () => {
    setAuthMode(authMode === "signin" ? "signup" : "signin");
  });
  $("#auth-forgot")?.addEventListener("click", () => {
    showAuthForgot();
  });
  $("#auth-forgot-back")?.addEventListener("click", () => {
    showAuthMain();
    setAuthMode("signin");
  });
  $("#auth-send-reset")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    const email = (document.getElementById("auth-forgot-email") as HTMLInputElement).value.trim();
    if (!email) {
      setForgotMsg("Enter your email address.", true);
      return;
    }
    if (!/.+@.+\..+/.test(email)) {
      setForgotMsg("That doesn't look like an email address.", true);
      return;
    }
    setForgotMsg("Sending a reset link…");
    const err = await resetPasswordForEmail(sb, email, authRedirectUrl());
    setForgotMsg(
      err ? friendlyAuthError(err) : "If that email is on file, check your inbox for a reset link.",
      !!err,
    );
  });
  $("#auth-passkey")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    setAuthMsg("Waiting for Face ID / fingerprint…");
    const err = await signInWithPasskey(sb);
    if (err) setAuthMsg(friendlyAuthError(err), true);
  });
  $("#auth-setpass")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    const np = (document.getElementById("auth-newpass") as HTMLInputElement).value;
    const confirm = (document.getElementById("auth-newpass-confirm") as HTMLInputElement).value;
    if (np.length < 8) {
      setRecoverMsg("At least 8 characters, please.", true);
      return;
    }
    if (np !== confirm) {
      setRecoverMsg("Passwords don't match — try again.", true);
      return;
    }
    setRecoverMsg("Saving…");
    const err = await updatePassword(sb, np);
    if (err) {
      setRecoverMsg(friendlyAuthError(err), true);
      return;
    }
    setRecoverMsg("Done! You're signed in.");
    if (window.location.hash) {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    setTimeout(async () => {
      closeAuth();
      await refreshAuth();
      toast("Password updated — welcome back");
    }, 900);
  });
  $("#auth-signin")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    const email = (document.getElementById("auth-email") as HTMLInputElement).value.trim();
    const pass = (document.getElementById("auth-pass") as HTMLInputElement).value;
    if (!/.+@.+\..+/.test(email)) {
      setAuthMsg("That doesn't look like an email address.", true);
      return;
    }
    if (pass.length < 8) {
      setAuthMsg("Password needs at least 8 characters.", true);
      return;
    }
    setAuthMsg(authMode === "signup" ? "Creating your account…" : "Signing you in…");
    const err = authMode === "signup"
      ? await signUp(sb, email, pass, authRedirectUrl())
      : await signIn(sb, email, pass);
    if (err) setAuthMsg(friendlyAuthError(err), true);
    else {
      closeAuth();
      await refreshAuth();
      toast(authMode === "signup" ? "Account created — everything saves automatically" : "Welcome back");
    }
  });
  $("#auth-signup")?.addEventListener("click", () => {
    setAuthMode("signup");
  });
  $("#auth-magic")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    const email = (document.getElementById("auth-email") as HTMLInputElement).value.trim();
    if (!/.+@.+\..+/.test(email)) {
      setAuthMsg("Enter your email first.", true);
      return;
    }
    setAuthMsg("Sending sign-in link…");
    const err = await signInWithMagicLink(sb, email, authRedirectUrl());
    setAuthMsg(err ? friendlyAuthError(err) : "Check your inbox for a sign-in link.", !!err);
  });
  $("#auth-google")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    setAuthMsg("Opening Google…");
    const err = await signInWithGoogle(sb, authRedirectUrl());
    if (err) {
      setAuthMsg(/provider|not enabled|unsupported/i.test(err)
        ? "Google sign-in isn't set up yet — use email and password for now."
        : friendlyAuthError(err), true);
    }
  });

  const resumeFile = document.getElementById("resume-file") as HTMLInputElement | null;
  resumeFile?.addEventListener("change", () => {
    const file = resumeFile.files?.[0];
    if (!file) return;
    const msgEl = document.getElementById("resume-msg");
    if (file.size > 8 * 1024 * 1024) {
      if (msgEl) msgEl.textContent = "That file's quite large — try a smaller one, or paste the text.";
      resumeFile.value = "";
      return;
    }
    if (msgEl) {
      msgEl.innerHTML = `<span class="resume-readout">Reading ${esc(file.name)}<i aria-hidden="true"></i></span>`;
    }
    void extractResumeFile(file).then((text) => {
      text = (text || "").trim();
      if (text.length < 40) {
        if (msgEl) msgEl.textContent = "I couldn't find readable text in that (a scanned PDF, maybe?). Paste your résumé below instead.";
        resumeFile.value = "";
        return;
      }
      let docName = file.name;
      patchState((s) => {
        docName = addResumeDocument(s.profile, file.name, text, "upload").name;
      });
      autosave();
      renderCorner();
      const freshMsg = document.getElementById("resume-msg");
      if (freshMsg) freshMsg.textContent = `Loaded ${docName} ✦ — saved automatically.`;
      toast("Résumé document added ✦");
      resumeFile.value = "";
    }).catch((err: Error) => {
      if (msgEl) msgEl.textContent = err?.message || "I couldn't read that file — try paste instead.";
      resumeFile.value = "";
    });
  });

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstall = e as BeforeInstallPromptEvent;
    const b = document.getElementById("install-btn");
    if (b) b.hidden = false;
  });
  window.addEventListener("appinstalled", () => {
    const b = document.getElementById("install-btn");
    if (b) b.hidden = true;
    deferredInstall = null;
  });
  $("#install-btn")?.addEventListener("click", () => {
    if (!deferredInstall) return;
    void deferredInstall.prompt();
    void deferredInstall.userChoice.finally(() => {
      deferredInstall = null;
      const b = document.getElementById("install-btn");
      if (b) b.hidden = true;
    });
  });

  $("#rudy-close")?.addEventListener("click", closeRudy);
  $("#rudy-memory-open")?.addEventListener("click", openRudyMemory);
  $("#rudy-memory-close")?.addEventListener("click", closeRudyMemory);
  $("#rudy-memory")?.addEventListener("click", (e) => {
    const t = (e.target as HTMLElement).closest(
      "[data-mem-forget-quiz], [data-mem-forget-doc], #rudy-memory-clear-chat"
    ) as HTMLElement | null;
    if (!t) return;
    if (t.id === "rudy-memory-clear-chat") {
      void clearChatHistory().then(() => {
        toast("Chat history cleared");
        rudyHistoryLoaded = false;
        renderRudyMemory();
      });
      return;
    }
    const quizKey = t.getAttribute("data-mem-forget-quiz");
    if (quizKey) {
      patchState((s) => { delete s.profile.quiz[quizKey]; });
      autosave();
      toast("Forgotten");
      renderRudyMemory();
      return;
    }
    const docId = t.getAttribute("data-mem-forget-doc");
    if (docId) {
      patchState((s) => { removeResumeDocument(s.profile, docId); });
      autosave();
      toast("Résumé deleted");
      renderRudyMemory();
      if (view === "corner") renderCorner();
      return;
    }
  });
  $("#rudy-send")?.addEventListener("click", () => { void sendRudy(); });
  $("#rudy-input")?.addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Enter") void sendRudy();
  });
  document.querySelectorAll<HTMLElement>("[data-rudy-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const prompt = btn.dataset.rudyPrompt?.trim();
      const inp = $("#rudy-input") as HTMLInputElement | null;
      if (!prompt || !inp) return;
      inp.value = prompt;
      void sendRudy();
    });
  });
  spicyOn = localStorage.getItem("rudySpicy") === "1";
  const spicyBtn = $("#rudy-spicy");
  const syncSpicy = (): void => {
    if (!spicyBtn) return;
    spicyBtn.setAttribute("aria-pressed", spicyOn ? "true" : "false");
    spicyBtn.title = spicyOn ? "Spicy mode is on" : "Spicy mode is off";
    spicyBtn.classList.toggle("on", spicyOn);
  };
  if (spicyBtn) {
    syncSpicy();
    spicyBtn.addEventListener("click", () => {
      spicyOn = !spicyOn;
      localStorage.setItem("rudySpicy", spicyOn ? "1" : "0");
      syncSpicy();
      toast(spicyOn ? "Spicy Rudy on" : "Spicy Rudy off");
    });
  }

  // ── Voice OUT: "Rudy reads replies aloud" toggle. Voice uses the server
  // edge function first, with browser speech as fallback, so keep the toggle
  // even when SpeechSynthesis is missing. ────────────────────────────────────
  speechSynthOK = "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
  speakOn = localStorage.getItem("rudySpeak") === "1";
  const pickVoice = (): void => {
    if (!speechSynthOK) return;
    try {
      const vs = window.speechSynthesis.getVoices() || [];
      const pref = ["Samantha", "Google US English", "Microsoft Aria", "Microsoft Jenny", "Victoria", "Karen", "Moira"];
      for (const name of pref) {
        const v = vs.find((x) => x.name === name);
        if (v) { speechVoice = v; return; }
      }
      speechVoice = vs.find((x) => /en[-_]US/i.test(x.lang) && /female|woman/i.test(x.name))
        ?? vs.find((x) => /^en/i.test(x.lang)) ?? vs[0] ?? null;
    } catch { /* no-op */ }
  };
  if (speechSynthOK) {
    pickVoice();
    try { window.speechSynthesis.onvoiceschanged = pickVoice; } catch { /* no-op */ }
  }
  const spkBtn = $("#rudy-spk");
  const spkState = $("#rudy-spk-state");
  voiceStatusEl = $("#rudy-voice-status");
  const syncSpeaker = (): void => {
    if (!spkBtn) return;
    spkBtn.setAttribute("aria-pressed", speakOn ? "true" : "false");
    if (spkState) spkState.textContent = speakOn ? "On" : "Off";
    syncVoiceIdleStatus();
  };
  if (spkBtn) {
    syncSpeaker();
    spkBtn.addEventListener("click", () => {
      speakOn = !speakOn;
      localStorage.setItem("rudySpeak", speakOn ? "1" : "0");
      syncSpeaker();
      if (!speakOn) stopRudyVoice();
      else toast("Rudy will read replies aloud");
    });
  }

  // ── Voice IN: tap the mic to talk to Rudy. Records with MediaRecorder and
  // transcribes via the `voice` edge function, which works on iOS unlike the
  // old SpeechRecognition path. Hidden where the mic API is unavailable. ─────
  const micBtn = $("#rudy-mic");
  const listenEl = $("#rudy-listen");
  const micOK = !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
  if (micBtn && micOK) {
    micBtn.hidden = false;
    let rec: MediaRecorder | null = null;
    let chunks: BlobPart[] = [];
    let stream: MediaStream | null = null;
    let autostop: ReturnType<typeof setTimeout> | undefined;
    let recording = false;
    const setRec = (on: boolean): void => {
      recording = on;
      micBtn.classList.toggle("on", on);
      micBtn.setAttribute("aria-pressed", on ? "true" : "false");
      if (listenEl) listenEl.hidden = !on;
    };
    const endStream = (): void => {
      if (autostop) clearTimeout(autostop);
      try { stream?.getTracks().forEach((t) => t.stop()); } catch { /* no-op */ }
      stream = null;
    };
    const stop = (): void => {
      if (autostop) clearTimeout(autostop);
      try { if (rec && rec.state === "recording") rec.stop(); } catch { /* no-op */ }
    };
    const transcribe = async (blob: Blob): Promise<void> => {
      const sb = getClient();
      if (!sb || !blob.size) return;
      const b64 = await new Promise<string>((res) => {
        const fr = new FileReader();
        fr.onload = () => res(String(fr.result).split(",")[1] || "");
        fr.onerror = () => res("");
        fr.readAsDataURL(blob);
      });
      if (!b64) return;
      try {
        const { data, error } = await sb.functions.invoke("voice", { body: { mode: "stt", audio: b64, mime: blob.type } });
        if (error || data?.unconfigured || !data?.text) {
          toast(data?.unconfigured ? "Voice typing isn't set up yet" : "Didn't catch that — try typing");
          return;
        }
        const inp = $("#rudy-input") as HTMLInputElement | null;
        if (inp) inp.value = data.text;
        void sendRudy();
      } catch { toast("Didn't catch that — try typing"); }
    };
    micBtn.addEventListener("click", async () => {
      if (recording) { stop(); return; }
      stopRudyVoice();
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch { toast("Let the app use your mic to talk to Rudy"); return; }
      chunks = [];
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm"
        : MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "";
      try {
        rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      } catch { rec = new MediaRecorder(stream); }
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = () => {
        setRec(false);
        const blob = new Blob(chunks, { type: rec?.mimeType || "audio/webm" });
        endStream();
        void transcribe(blob);
      };
      setRec(true);
      rec.start();
      autostop = setTimeout(stop, 15000);   // never record forever
    });
  } else if (micBtn) {
    micBtn.hidden = true;
  }

  // ── Follow-up notification on visibility change ──────────────────────────
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      renderFollowBadge();
      maybeNotifyFollowUps();
    }
  });

  $("#tailor-close")?.addEventListener("click", closeTailor);
  $("#tailor-modal")?.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).id === "tailor-modal") closeTailor();
  });

  const portal = await loadPortal();
  await initAuth(portal);
  if (await fetchGoogleAuthEnabled(portal)) {
    const gBtn = document.getElementById("auth-google");
    if (gBtn) gBtn.hidden = false;
  }
  const sb = getClient();
  if (sb) {
    const pkBtn = $("#auth-passkey");
    const pkDiv = $("#auth-pk-div");
    if (!supportsPasskey(sb)) {
      if (pkBtn) pkBtn.hidden = true;
      if (pkDiv) pkDiv.hidden = true;
    }
    sb.auth.onAuthStateChange((event) => {
      if (event === "PASSWORD_RECOVERY") {
        const back = $("#auth-modal");
        if (back) back.hidden = false;
        showAuthRecover();
        return;
      }
      void refreshAuth();
    });
    if (isPasswordRecoveryHash()) {
      const back = $("#auth-modal");
      if (back) back.hidden = false;
      showAuthRecover();
    }
  }
  await refreshAuth();
  render();
  wirePullToRefresh();
  maybeIosInstallCoach();
  $("#ios-install-close")?.addEventListener("click", () => { const m = $("#ios-install-modal"); if (m) m.hidden = true; });
  $("#ios-install-ok")?.addEventListener("click", () => { const m = $("#ios-install-modal"); if (m) m.hidden = true; });

  // Guided walkthrough controls + first-run auto-show (sequenced so it never
  // stacks under another popup).
  $("#tour-next")?.addEventListener("click", tourNext);
  $("#tour-back")?.addEventListener("click", tourBack);
  $("#tour-skip")?.addEventListener("click", closeTour);
  $("#tour-chips")?.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest("[data-tour-chip]") as HTMLElement | null;
    if (b) tourPickChip(b.getAttribute("data-tour-chip") || "");
  });
  window.addEventListener("resize", () => {
    const root = $("#tour-root");
    if (root && !root.hidden) renderTour();
  });
  if (!localStorage.getItem("tour-seen")) {
    setTimeout(() => { if (!anyModalOpen()) startTour(); }, 900);
  }

  const base = import.meta.env.BASE_URL;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(`${base}sw.js`).then((reg) => {
      reg.addEventListener("updatefound", () => {
        const nw = reg.installing;
        nw?.addEventListener("statechange", () => {
          if (nw.state === "installed" && navigator.serviceWorker.controller) {
            showUpdateModal();
          }
        });
      });
    }).catch(() => {});
  }
}

boot();
