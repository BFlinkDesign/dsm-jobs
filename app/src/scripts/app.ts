import type { Job, Meta, ViewName } from "./types";
import { autosave, clearAutosave, initAutosave, loadPortal, pullProfile } from "./autosave";
import { currentUser, getClient, initAuth, signIn, signOut, signUp } from "./auth";
import { getState, loadLocal, patchState } from "./store";
import {
  SKULL,
  addDaysISO,
  debounce,
  esc,
  relativePosted,
  safeUrl,
  todayISO,
} from "./util";

const LOCKED: Record<string, boolean> = { today: true, apps: true, corner: true };

let jobs: Job[] = [];
let meta: Meta = { contact: "", phone: "", generated: "", hidden: 0, total: 0, safe: 0 };
let view: ViewName = "jobs";
let authed = false;
let searchQ = "";
let filterRemote: "all" | "local" | "remote" = "all";
let filterPay = false;
let commuteMax: number | null = null;
let tailorJob: Job | null = null;
let tailorData: { resume: string; cover_note?: string } | null = null;

const $ = (sel: string) => document.querySelector(sel) as HTMLElement | null;

function toast(msg: string): void {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2000);
}

function isLocked(v: ViewName): boolean {
  return !!LOCKED[v] && !authed;
}

function setView(v: ViewName): void {
  view = v;
  document.querySelectorAll(".tab").forEach((b) => {
    b.setAttribute("aria-current", String(b.getAttribute("data-view") === v));
  });
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function filteredJobs(): Job[] {
  const s = getState();
  return jobs.filter((j) => {
    if (s.hidden[j.id]) return false;
    if (filterRemote === "local" && j.remote) return false;
    if (filterRemote === "remote" && !j.remote) return false;
    if (filterPay && !j.good) return false;
    if (commuteMax != null && !j.remote && j.commuteMin != null && j.commuteMin > commuteMax) return false;
    if (searchQ) {
      const hay = `${j.title} ${j.company} ${j.location}`.toLowerCase();
      if (!hay.includes(searchQ.toLowerCase())) return false;
    }
    return true;
  });
}

function followUpHtml(j: Job): string {
  const fu = getState().followUps[j.id];
  if (!getState().applied[j.id] || !fu) return "";
  const phone = fu.phone || j.contactPhone;
  const email = fu.email || j.contactEmail;
  let html = `<div class="job-actions follow-block">`;
  if (phone) html += `<a class="btn btn-call" href="tel:${esc(phone)}">Call</a>`;
  if (email) html += `<a class="btn btn-email" href="mailto:${esc(email)}">Email</a>`;
  html += `<span class="field-hint">Remind:</span>`;
  for (const d of [3, 5, 7]) {
    const on = fu.on === addDaysISO(getState().appliedLog[j.id] || todayISO(), d);
    html += `<button type="button" class="chip${on ? " on" : ""}" data-remind="${esc(j.id)}" data-days="${d}">${d}d</button>`;
  }
  html += `</div>`;
  return html;
}

function jobCard(j: Job): string {
  const s = getState();
  const applied = !!s.applied[j.id];
  const payCls = j.good ? "pay-tag good" : "pay-tag";
  const trust = j.trusted ? `<span class="badge-safe">✓ ${esc(j.trustLabel || "Verified")}</span>` : "";
  const loc = j.remote ? "Remote" : esc(j.location);
  const commute = j.commute ? ` · ${esc(j.commute)}` : "";
  const hasResume = !!s.profile.resume.trim();
  return `<article class="card card-glitter job-card" data-id="${esc(j.id)}">
    <h3>${esc(j.title)}</h3>
    <div class="job-meta">${esc(j.company)} · ${loc}${commute}</div>
    <div><span class="${payCls}">${esc(j.pay)}</span> ${trust}</div>
    <div class="job-meta">${esc(relativePosted(j.posted))}</div>
    ${j.about ? `<p class="job-meta">${esc(j.about)}</p>` : ""}
    <div class="job-actions">
      ${applied
    ? `<span class="badge-safe">Applied</span>`
    : authed
      ? `<button type="button" class="btn btn-primary" data-apply="${esc(j.id)}">Mark applied</button>`
      : `<button type="button" class="btn btn-ghost" data-needs-auth>Sign in to apply</button>`}
      ${authed ? `<button type="button" class="btn btn-ghost" data-save="${esc(j.id)}">${s.saved[j.id] ? "Saved ✓" : "Save"}</button>` : ""}
      ${authed && j.url ? `<a class="btn btn-ghost" href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener">Apply ↗</a>` : ""}
      ${authed && hasResume ? `<button type="button" class="btn btn-ghost" data-tailor="${esc(j.id)}">✦ Tailor résumé</button>` : ""}
    </div>
    ${followUpHtml(j)}
  </article>`;
}

function renderJobsMain(): void {
  const host = $("#view-host");
  if (!host) return;
  const list = filteredJobs();
  host.innerHTML = `
    <div class="search-row">
      <input class="search" type="search" placeholder="Search jobs…" value="${esc(searchQ)}" id="job-search" autocomplete="off" />
    </div>
    <p class="filter-label">Job type</p>
    <div class="chip-row" id="filter-remote">
      <button type="button" class="chip${filterRemote === "all" ? " on" : ""}" data-remote="all">All</button>
      <button type="button" class="chip${filterRemote === "local" ? " on" : ""}" data-remote="local">In person</button>
      <button type="button" class="chip${filterRemote === "remote" ? " on" : ""}" data-remote="remote">Remote</button>
    </div>
    <p class="filter-label">Pay</p>
    <div class="chip-row">
      <button type="button" class="chip${filterPay ? " on" : ""}" id="filter-pay">$19+ stated only</button>
    </div>
    <p class="filter-label">How far she'll drive from Grimes</p>
    <div class="chip-row" id="filter-commute">
      ${[null, 20, 30, 45].map((m) =>
    `<button type="button" class="chip${commuteMax === m ? " on" : ""}" data-commute="${m ?? "any"}">${m ? `${m} min` : "Any"}</button>`,
  ).join("")}
    </div>
    <p class="job-meta" style="margin-top:1rem">${list.length} safe job${list.length === 1 ? "" : "s"} · updated ${esc(meta.generated)}</p>
    <div class="jobs-grid" id="jobs-list">${list.map(jobCard).join("") || "<p class='job-meta'>No jobs match — try widening filters.</p>"}</div>
    <p class="field-hint" style="margin-top:1rem">We checked ${meta.total} postings and hid ${meta.hidden} that looked like scams.</p>
  `;
}

function renderLock(sub: string): string {
  return `<div class="lock-screen card card-glitter">
    ${SKULL}
    <h2>Free account required</h2>
    <p>${esc(sub)}</p>
    <button type="button" class="btn btn-primary" data-lock-signin>Create free account</button>
    <p class="field-hint" style="margin-top:1rem">Everything saves instantly — no save buttons.</p>
  </div>`;
}

function renderToday(): void {
  const host = $("#view-host");
  if (!host) return;
  if (isLocked("today")) {
    host.innerHTML = renderLock("Today's gentle shortlist is hers once she signs in — a tiny doable list, not the whole wall.");
    return;
  }
  const picks = filteredJobs().slice(0, 3);
  host.innerHTML = `
    <div class="card card-glitter">
      <h2 class="view-title">Today</h2>
      <p class="job-meta">Three doable leads — no pressure to apply to all of them.</p>
    </div>
    <div class="jobs-grid">${picks.map(jobCard).join("")}</div>
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
  const applied = jobs.filter((j) => s.applied[j.id]);
  host.innerHTML = `
    <div class="card"><h2 class="view-title">My applications</h2>
    <p class="job-meta">${applied.length} tracked — reminders appear when it's time to follow up.</p></div>
    <div class="jobs-grid">${applied.length ? applied.map(jobCard).join("") : "<p class='job-meta'>Nothing marked applied yet. Tap <b>Mark applied</b> on a job she likes.</p>"}</div>
  `;
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
  host.innerHTML = `
    <div class="card card-glitter rudy-row">
      <img class="rudy-avatar" src="${base}rudy.jpg" alt="" width="56" height="56" />
      <div>
        <h2 class="view-title">Rudy</h2>
        <p class="job-meta">Her emotional support cow — calm check-ins, remembers her, helps with the search.</p>
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
      <h3 class="section-title">Résumé</h3>
      <textarea class="field" id="pf-resume" rows="6" placeholder="Paste résumé — auto-saved">${esc(p.resume)}</textarea>
      <p class="field-hint">Tailor from any job card once text is here.</p>
    </div>
  `;
}

function renderHelp(): void {
  const host = $("#view-host");
  if (!host) return;
  host.innerHTML = `
    <div class="card">
      <h2 class="view-title">How she stays safe</h2>
      <ul class="job-meta help-list">
        <li>Every job here was scam-checked before she sees it.</li>
        <li>If pay isn't listed, that's normal — ask when she applies.</li>
        <li>Real employers don't ask for gift cards, wire transfers, or upfront fees.</li>
      </ul>
    </div>
    <div class="crisis">
      <b>Need help right now?</b>
      <p class="job-meta crisis-links">
        <a href="tel:988">988</a> Suicide & Crisis Lifeline ·
        <a href="tel:8555818111">855-581-8111</a> Your Life Iowa ·
        <a href="tel:8558958398">855-895-8398</a> en español
      </p>
    </div>
  `;
}

function render(): void {
  switch (view) {
    case "jobs": renderJobsMain(); break;
    case "today": renderToday(); break;
    case "apps": renderApps(); break;
    case "corner": renderCorner(); break;
    case "help": renderHelp(); break;
  }
  document.body.classList.toggle("authed", authed);
}

function handleViewClick(e: Event): void {
  const t = (e.target as HTMLElement).closest("[data-needs-auth], [data-lock-signin], [data-apply], [data-save], [data-remind], [data-remote], [data-commute], #filter-pay, [data-tailor], #open-rudy") as HTMLElement | null;
  if (!t) return;

  if (t.matches("[data-needs-auth], [data-lock-signin]")) {
    openAuth();
    return;
  }
  if (t.id === "open-rudy") {
    openRudy();
    return;
  }
  if (t.id === "filter-pay") {
    filterPay = !filterPay;
    renderJobsMain();
    return;
  }
  if (t.hasAttribute("data-remote")) {
    filterRemote = t.getAttribute("data-remote") as typeof filterRemote;
    autosave();
    renderJobsMain();
    return;
  }
  if (t.hasAttribute("data-commute")) {
    const v = t.getAttribute("data-commute");
    commuteMax = v === "any" ? null : Number(v);
    patchState((s) => { s.commuteRadius = commuteMax; });
    autosave();
    renderJobsMain();
    return;
  }
  if (t.hasAttribute("data-apply")) {
    const id = t.getAttribute("data-apply")!;
    const job = jobs.find((x) => x.id === id);
    patchState((s) => {
      s.applied[id] = true;
      s.appliedLog[id] = todayISO();
      if (!s.followUps[id]) {
        s.followUps[id] = {
          name: job?.contactName || "",
          phone: job?.contactPhone || "",
          email: job?.contactEmail || "",
          on: addDaysISO(todayISO(), 5),
          done: false,
        };
      }
    });
    autosave();
    render();
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
      if (fu) fu.on = addDaysISO(s.appliedLog[id] || todayISO(), days);
    });
    autosave();
    render();
    return;
  }
  if (t.hasAttribute("data-tailor")) {
    const id = t.getAttribute("data-tailor")!;
    const job = jobs.find((x) => x.id === id);
    if (job) openTailor(job);
  }
}

const onProfileInput = debounce((e: Event) => {
  const t = e.target as HTMLElement;
  if (t.id === "pf-preferred") patchState((s) => { s.profile.preferredName = (t as HTMLInputElement).value; });
  else if (t.id === "pf-legal") patchState((s) => { s.profile.legalName = (t as HTMLInputElement).value; });
  else if (t.id === "pf-resume") patchState((s) => { s.profile.resume = (t as HTMLTextAreaElement).value; });
  else return;
  autosave();
}, 400);

const onSearchInput = debounce((e: Event) => {
  searchQ = (e.target as HTMLInputElement).value;
  renderJobsMain();
}, 200);

function bindViewHost(): void {
  const host = $("#view-host");
  if (!host || host.dataset.bound) return;
  host.dataset.bound = "1";
  host.addEventListener("click", handleViewClick);
  host.addEventListener("input", (e) => {
    const t = e.target as HTMLElement;
    if (t.id === "job-search") onSearchInput(e);
    else onProfileInput(e);
  });
}

function openAuth(): void {
  const back = $("#auth-modal");
  if (back) back.hidden = false;
}

function closeAuth(): void {
  const back = $("#auth-modal");
  if (back) back.hidden = true;
}

function openRudy(): void {
  if (!authed) return openAuth();
  const ov = $("#rudy-overlay");
  if (ov) ov.hidden = false;
  renderRudyLog();
}

function closeRudy(): void {
  const ov = $("#rudy-overlay");
  if (ov) ov.hidden = true;
}

function renderRudyLog(): void {
  const log = $("#rudy-log");
  if (!log) return;
  if (!log.dataset.seeded) {
    log.innerHTML = `<div class="bubble ai">Hi — I'm Rudy. No pressure today. Tell her how she's doing, or tap a job and I'll help her talk it through. Moo means I'm in her corner.</div>`;
    log.dataset.seeded = "1";
  }
}

async function sendRudy(): Promise<void> {
  const inp = $("#rudy-input") as HTMLInputElement | null;
  const log = $("#rudy-log");
  if (!inp || !log || !inp.value.trim()) return;
  const msg = inp.value.trim();
  inp.value = "";
  log.insertAdjacentHTML("beforeend", `<div class="bubble me">${esc(msg)}</div>`);
  log.insertAdjacentHTML("beforeend", `<div class="bubble ai think" id="rudy-think">…</div>`);
  log.scrollTop = log.scrollHeight;
  const sb = getClient();
  if (!sb) {
    $("#rudy-think")!.textContent = "Sign-in sync isn't available offline — her message is saved locally.";
    return;
  }
  try {
    const { data, error } = await sb.functions.invoke("companion", { body: { message: msg } });
    const elThink = $("#rudy-think");
    if (elThink) {
      elThink.classList.remove("think");
      elThink.textContent = error?.message || (data?.reply as string) || "I'm here. Try again in a moment.";
    }
  } catch {
    $("#rudy-think")!.textContent = "Connection blip — she's still safe. Try again.";
  }
  log.scrollTop = log.scrollHeight;
}

function openTailor(job: Job): void {
  if (!authed) return openAuth();
  const resume = getState().profile.resume.trim();
  if (!resume) {
    toast("Paste her résumé in My corner first");
    setView("corner");
    return;
  }
  tailorJob = job;
  tailorData = null;
  const modal = $("#tailor-modal");
  const body = $("#tailor-body");
  if (!modal || !body) return;
  modal.hidden = false;
  const full = (job.descFull || "").trim();
  if (full.length >= 200) {
    body.innerHTML = `<p class="job-meta">Tailoring for <b>${esc(job.title)}</b> at ${esc(job.company)}…</p>`;
    runTailor(job, resume, full);
  } else {
    body.innerHTML = `
      <p class="job-meta">For <b>${esc(job.title)}</b> at ${esc(job.company)} — paste the full posting if you have it.</p>
      <textarea class="field" id="tailor-paste" rows="5" placeholder="Job posting text…"></textarea>
      <button type="button" class="btn btn-primary" id="tailor-run" style="margin-top:12px">Tailor résumé</button>
    `;
    $("#tailor-run")?.addEventListener("click", () => {
      const paste = (document.getElementById("tailor-paste") as HTMLTextAreaElement)?.value.trim() || "";
      body.innerHTML = `<p class="job-meta">Working on it…</p>`;
      runTailor(job, resume, paste);
    }, { once: true });
  }
}

function closeTailor(): void {
  const modal = $("#tailor-modal");
  if (modal) modal.hidden = true;
  tailorJob = null;
  tailorData = null;
}

function renderTailorResult(job: Job, data: { resume: string; cover_note?: string }): void {
  tailorData = data;
  const body = $("#tailor-body");
  if (!body) return;
  body.innerHTML = `
    <p class="job-meta">Tailored for <b>${esc(job.title)}</b> at ${esc(job.company)} — her real experience, rewritten to fit.</p>
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
    <button type="button" class="btn btn-primary" data-copy="both" style="margin-top:12px">Copy both</button>
  `;
  body.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const which = btn.getAttribute("data-copy");
      let text = data.resume;
      if (which === "cover") text = data.cover_note || "";
      else if (which === "both") text = data.cover_note ? `${data.resume}\n\n\n=== COVER NOTE ===\n\n${data.cover_note}` : data.resume;
      navigator.clipboard?.writeText(text).then(() => toast("Copied")).catch(() => toast("Copy failed — select text manually"));
    });
  });
}

async function runTailor(job: Job, resume: string, jobText: string): Promise<void> {
  const sb = getClient();
  const body = $("#tailor-body");
  if (!sb || !body) {
    if (body) body.innerHTML = `<p class="job-meta">Sign in to tailor — keeps her résumé private.</p>`;
    return;
  }
  try {
    const { data, error } = await sb.functions.invoke("resume-tailor", {
      body: { resume, jobTitle: job.title, company: job.company, jobText },
    });
    if (error || !data?.resume) {
      body.innerHTML = `<p class="job-meta">${esc((data?.error as string) || error?.message || "Couldn't tailor just now — try again.")}</p>`;
      return;
    }
    renderTailorResult(job, data as { resume: string; cover_note?: string });
  } catch {
    body.innerHTML = `<p class="job-meta">No connection right now — try again when she's back online.</p>`;
  }
}

async function refreshAuth(): Promise<void> {
  const sb = getClient();
  if (!sb) { authed = false; clearAutosave(); render(); return; }
  const user = await currentUser(sb);
  authed = !!user;
  const btn = $("#acct-btn");
  if (btn) btn.classList.toggle("in", authed);
  if (user) {
    initAutosave(sb, user.id, toast);
    await pullProfile();
    commuteMax = getState().commuteRadius;
  } else {
    clearAutosave();
  }
  render();
}

async function boot(): Promise<void> {
  loadLocal();
  commuteMax = getState().commuteRadius;
  bindViewHost();

  const base = import.meta.env.BASE_URL;
  const [jobsR, metaR] = await Promise.all([
    fetch(`${base}jobs.json`),
    fetch(`${base}meta.json`),
  ]);
  jobs = (await jobsR.json()) as Job[];
  meta = (await metaR.json()) as Meta;

  const gen = $("#meta-generated");
  if (gen) gen.textContent = `${meta.safe} safe jobs · scam-checked · ${meta.generated}`;

  document.querySelectorAll(".tab").forEach((b) => {
    b.addEventListener("click", () => setView(b.getAttribute("data-view") as ViewName));
  });

  $("#acct-btn")?.addEventListener("click", () => (authed ? signOut(getClient()!).then(refreshAuth) : openAuth()));
  $("#auth-close")?.addEventListener("click", closeAuth);
  $("#auth-modal")?.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).id === "auth-modal") closeAuth();
  });
  $("#auth-signin")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    const email = (document.getElementById("auth-email") as HTMLInputElement).value.trim();
    const pass = (document.getElementById("auth-pass") as HTMLInputElement).value;
    const err = await signIn(sb, email, pass);
    if (err) toast(err);
    else { closeAuth(); await refreshAuth(); toast("Welcome back"); }
  });
  $("#auth-signup")?.addEventListener("click", async () => {
    const sb = getClient();
    if (!sb) return;
    const email = (document.getElementById("auth-email") as HTMLInputElement).value.trim();
    const pass = (document.getElementById("auth-pass") as HTMLInputElement).value;
    const err = await signUp(sb, email, pass);
    if (err) toast(err);
    else { closeAuth(); await refreshAuth(); toast("Account created — everything saves automatically"); }
  });

  $("#rudy-close")?.addEventListener("click", closeRudy);
  $("#rudy-send")?.addEventListener("click", sendRudy);
  $("#rudy-input")?.addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Enter") sendRudy();
  });

  $("#tailor-close")?.addEventListener("click", closeTailor);
  $("#tailor-modal")?.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).id === "tailor-modal") closeTailor();
  });

  const portal = await loadPortal();
  await initAuth(portal);
  const sb = getClient();
  if (sb) {
    sb.auth.onAuthStateChange(() => { refreshAuth(); });
  }
  await refreshAuth();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(`${base}sw.js`).catch(() => {});
  }
}

boot();
