import type { AppState, AppliedEntry, ResumeDocument } from "./types";
import { defaultFilters } from "./types";

const LS = "dsm-jobs-state-v2";
const V1_KEY = "myjobs:v1";
const V1_MIGRATED = "dsm-jobs-v1-migrated";

function defaultProfile(): AppState["profile"] {
  return {
    preferredName: "",
    legalName: "",
    resume: "",
    documents: [],
    activeDocumentId: "",
    quiz: {},
  };
}

function cleanResumeDocuments(raw: unknown): ResumeDocument[] {
  if (!Array.isArray(raw)) return [];
  const docs: ResumeDocument[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const doc = item as Partial<ResumeDocument>;
    const id = typeof doc.id === "string" ? doc.id.trim() : "";
    const text = typeof doc.text === "string" ? doc.text : "";
    if (!id || !text.trim()) continue;
    if (seen.has(id)) continue;
    seen.add(id);
    const createdAt = typeof doc.createdAt === "string" ? doc.createdAt : "";
    docs.push({
      id,
      text,
      name: typeof doc.name === "string" && doc.name.trim() ? doc.name.trim() : "Résumé",
      source: doc.source === "upload" ? "upload" : "paste",
      createdAt,
      updatedAt: typeof doc.updatedAt === "string" ? doc.updatedAt : createdAt,
    });
  }
  return docs;
}

function legacyResumeDocument(text: string): ResumeDocument {
  const ts = new Date().toISOString();
  return {
    id: "legacy-resume",
    name: "Saved résumé",
    text,
    source: "paste",
    createdAt: ts,
    updatedAt: ts,
  };
}

function normalizeProfile(raw: Partial<AppState["profile"]> | undefined): AppState["profile"] {
  const base = defaultProfile();
  const profile = { ...base, ...(raw ?? {}) };
  const quiz = raw?.quiz && typeof raw.quiz === "object" ? raw.quiz : {};
  const docs = cleanResumeDocuments((raw as { documents?: unknown } | undefined)?.documents);
  const resume = typeof raw?.resume === "string" ? raw.resume : "";
  let activeDocumentId = typeof raw?.activeDocumentId === "string" ? raw.activeDocumentId : "";

  if (resume.trim()) {
    const activeDoc = activeDocumentId ? docs.find((doc) => doc.id === activeDocumentId) : undefined;
    if (activeDoc) {
      activeDoc.text = resume;
      activeDoc.updatedAt = activeDoc.updatedAt || new Date().toISOString();
    } else {
      let legacy = docs.find((doc) => doc.id === "legacy-resume");
      if (!legacy) {
        legacy = legacyResumeDocument(resume);
        docs.unshift(legacy);
      } else {
        legacy.text = resume;
        legacy.updatedAt = new Date().toISOString();
      }
      activeDocumentId = legacy.id;
    }
  }

  if (!activeDocumentId && docs.length) activeDocumentId = docs[0].id;
  const activeDoc = docs.find((doc) => doc.id === activeDocumentId);

  return {
    ...profile,
    resume: activeDoc?.text ?? resume,
    documents: docs,
    activeDocumentId,
    quiz,
  };
}

export function defaultState(): AppState {
  return {
    applied: {},
    saved: {},
    hidden: {},
    snoozedUntil: {},
    notes: {},
    followUps: {},
    appliedLog: {},
    applicationStatus: {},
    applicationPacks: {},
    followAlertDay: "",
    seen: [],
    filters: defaultFilters(),
    profile: defaultProfile(),
    commuteRadius: null,
    coachOff: false,
  };
}

let state: AppState = defaultState();

export function getState(): AppState {
  return state;
}

export function setState(patch: Partial<AppState>): void {
  const next = { ...state, ...patch };
  if (patch.profile) next.profile = normalizeProfile({ ...state.profile, ...patch.profile });
  state = next;
  persistLocal();
}

export function patchState(fn: (s: AppState) => void): void {
  fn(state);
  persistLocal();
}

export function loadLocal(): void {
  try {
    const raw = localStorage.getItem(LS);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Partial<AppState>;
    // Coerce any legacy string appliedLog entries (pre-AppliedEntry shape) to objects
    if (parsed.appliedLog) {
      for (const [id, entry] of Object.entries(parsed.appliedLog as Record<string, unknown>)) {
        if (typeof entry === "string") {
          (parsed.appliedLog as Record<string, AppliedEntry>)[id] = { t: "", c: "", d: entry, u: "" };
        }
      }
    }
    state = {
      ...defaultState(),
      ...parsed,
      filters: { ...defaultFilters(), ...parsed.filters },
      profile: normalizeProfile(parsed.profile),
    };
  } catch {
    /* ignore corrupt */
  }
}

function persistLocal(): void {
  try {
    localStorage.setItem(LS, JSON.stringify(state));
  } catch {
    /* quota */
  }
}

/**
 * One-time migration from the old `myjobs:v1` localStorage key.
 * Old shape: applied={id:dateStr}, saved=[id,...], hidden=[id,...],
 *            appliedLog={id:{t,c,d,u,ts?}}, resume:string, maxCommute:"20"|"30"|"45"
 * Runs once per device; guarded by V1_MIGRATED flag.
 */
export function migrateLocalV1(): void {
  try {
    if (localStorage.getItem(V1_MIGRATED)) return;
    const raw = localStorage.getItem(V1_KEY);
    if (!raw) { localStorage.setItem(V1_MIGRATED, "1"); return; }
    const old = JSON.parse(raw) as Record<string, unknown>;
    const patch: Partial<AppState> = {
      applied: {},
      saved: {},
      hidden: {},
      appliedLog: {},
      applicationStatus: {},
      followUps: {},
      applicationPacks: {},
      profile: defaultProfile(),
      commuteRadius: null,
    };

    // applied: {id: dateStr} → {id: true}
    if (old.applied && typeof old.applied === "object" && !Array.isArray(old.applied)) {
      for (const id of Object.keys(old.applied as Record<string, unknown>)) {
        patch.applied![id] = true;
      }
    }
    // saved: string[] → {id: true}  (was a serialised Set)
    if (Array.isArray(old.saved)) {
      for (const id of old.saved as string[]) { patch.saved![id] = true; }
    } else if (old.saved && typeof old.saved === "object") {
      Object.assign(patch.saved!, old.saved);
    }
    // hidden: string[] → {id: true}
    if (Array.isArray(old.hidden)) {
      for (const id of old.hidden as string[]) { patch.hidden![id] = true; }
    }
    // appliedLog: {id:{t,c,d,u,ts?}} — same shape, already rich
    if (old.appliedLog && typeof old.appliedLog === "object") {
      for (const [id, entry] of Object.entries(old.appliedLog as Record<string, unknown>)) {
        if (entry && typeof entry === "object") {
          const e = entry as Record<string, unknown>;
          patch.appliedLog![id] = {
            t: String(e.t ?? ""), c: String(e.c ?? ""),
            d: String(e.d ?? ""), u: String(e.u ?? ""),
            ts: e.ts ? String(e.ts) : undefined,
          };
        } else if (typeof entry === "string") {
          patch.appliedLog![id] = { t: "", c: "", d: entry, u: "" };
        }
      }
    }
    // followUps: same shape
    if (old.followUps && typeof old.followUps === "object") {
      Object.assign(patch.followUps!, old.followUps);
    }
    // applicationStatus: job id -> lifecycle state
    if (old.applicationStatus && typeof old.applicationStatus === "object" && !Array.isArray(old.applicationStatus)) {
      Object.assign(patch.applicationStatus!, old.applicationStatus);
    }
    // applicationPacks: additive v3-ish field; keep if already present in a copied profile blob
    if (old.applicationPacks && typeof old.applicationPacks === "object" && !Array.isArray(old.applicationPacks)) {
      Object.assign(patch.applicationPacks!, old.applicationPacks);
    }
    // top-level resume → profile.resume
    if (typeof old.resume === "string") patch.profile!.resume = old.resume;
    // profile.legalName / preferredName
    if (old.profile && typeof old.profile === "object") {
      const p = old.profile as Record<string, unknown>;
      if (typeof p.legalName === "string") patch.profile!.legalName = p.legalName;
      if (typeof p.preferredName === "string") patch.profile!.preferredName = p.preferredName;
      if (typeof p.resume === "string" && !patch.profile!.resume) patch.profile!.resume = p.resume;
      if (Array.isArray(p.documents)) patch.profile!.documents = cleanResumeDocuments(p.documents);
      if (typeof p.activeDocumentId === "string") patch.profile!.activeDocumentId = p.activeDocumentId;
      const quizKeys = ["kind", "where", "time", "pay", "confidence"];
      for (const k of quizKeys) {
        if (typeof p[k] === "string") patch.profile!.quiz[k] = p[k] as string;
      }
    }
    // maxCommute: "20"/"30"/"45" → number
    if (typeof old.maxCommute === "string" && old.maxCommute) {
      const n = parseInt(old.maxCommute, 10);
      if (!isNaN(n)) patch.commuteRadius = n;
    }
    if (typeof old.coachOff === "boolean") patch.coachOff = old.coachOff;
    // old.notes: id -> string — migrate to new notes field
    if (old.notes && typeof old.notes === "object" && !Array.isArray(old.notes)) {
      patch.notes = {};
      for (const [id, body] of Object.entries(old.notes as Record<string, unknown>)) {
        if (typeof body === "string") patch.notes[id] = body;
      }
    }
    // old.snooze: id -> ISO date — migrate to snoozedUntil
    if (old.snooze && typeof old.snooze === "object" && !Array.isArray(old.snooze)) {
      patch.snoozedUntil = {};
      for (const [id, val] of Object.entries(old.snooze as Record<string, unknown>)) {
        if (typeof val === "string") patch.snoozedUntil[id] = val;
      }
    }

    // Merge: current v2 wins when it has a real value; old fills empty defaults.
    const cur = state;
    const oldProfile = normalizeProfile(patch.profile);
    const curProfile = normalizeProfile(cur.profile);
    setState({
      applied: { ...patch.applied, ...cur.applied },
      saved: { ...patch.saved, ...cur.saved },
      hidden: { ...patch.hidden, ...cur.hidden },
      snoozedUntil: { ...(patch.snoozedUntil ?? {}), ...cur.snoozedUntil },
      notes: { ...(patch.notes ?? {}), ...cur.notes },
      appliedLog: { ...patch.appliedLog, ...cur.appliedLog },
      applicationStatus: { ...patch.applicationStatus, ...cur.applicationStatus },
      followUps: { ...patch.followUps, ...cur.followUps },
      applicationPacks: { ...patch.applicationPacks, ...cur.applicationPacks },
      followAlertDay: cur.followAlertDay || "",
      profile: normalizeProfile({
        ...oldProfile,
        ...curProfile,
        preferredName: curProfile.preferredName || oldProfile.preferredName || "",
        legalName: curProfile.legalName || oldProfile.legalName || "",
        resume: curProfile.resume || oldProfile.resume || "",
        documents: [...curProfile.documents, ...oldProfile.documents],
        activeDocumentId: curProfile.activeDocumentId || oldProfile.activeDocumentId,
        quiz: { ...oldProfile.quiz, ...curProfile.quiz },
      }),
      commuteRadius: cur.commuteRadius ?? patch.commuteRadius,
      coachOff: cur.coachOff || (patch.coachOff ?? false),
    });
    localStorage.setItem(V1_MIGRATED, "1");
  } catch {
    /* ignore migration errors — never block the app */
  }
}

export function jobMap(jobs: { id: string }[]): Map<string, { id: string }> {
  return new Map(jobs.map((j) => [j.id, j]));
}
