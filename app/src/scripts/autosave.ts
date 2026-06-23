import { defaultState, getState, setState } from "./store";
import type { AppliedEntry, FilterPrefs, PortalCfg } from "./types";
import { defaultFilters } from "./types";
import { debounce, todayISO } from "./util";

type Sb = ReturnType<typeof import("@supabase/supabase-js")["createClient"]>;

let sb: Sb | null = null;
let userId: string | null = null;
let onToast: ((msg: string) => void) | null = null;

// Tracks the Supabase row UUID for each job's note (job_id -> row.id).
const noteRowIds: Record<string, string> = {};
const noteDebounceTimers: Record<string, ReturnType<typeof setTimeout>> = {};

const CHAT_LS_KEY = "dsm-jobs-chat";

export function initAutosave(client: Sb, uid: string, toast: (msg: string) => void): void {
  sb = client;
  userId = uid;
  onToast = toast;
}

export function clearAutosave(): void {
  sb = null;
  userId = null;
}

/** Push a single job note to Supabase (debounced per job). */
export function debouncePushNote(jobId: string): void {
  clearTimeout(noteDebounceTimers[jobId]);
  noteDebounceTimers[jobId] = setTimeout(() => { void pushNoteNow(jobId); }, 900);
}

async function pushNoteNow(jobId: string): Promise<void> {
  if (!sb || !userId) return;
  const body = getState().notes[jobId] ?? "";
  try {
    if (!body) {
      if (noteRowIds[jobId]) {
        const { error } = await sb.from("job_notes").delete().eq("id", noteRowIds[jobId]);
        if (error) throw error;
        delete noteRowIds[jobId];
      }
      return;
    }
    if (noteRowIds[jobId]) {
      const { error } = await sb.from("job_notes").update({ body }).eq("id", noteRowIds[jobId]);
      if (error) throw error;
    } else {
      const { data, error } = await sb
        .from("job_notes")
        .insert({ job_id: jobId, body })
        .select("id")
        .single();
      if (error) throw error;
      if (data?.id) noteRowIds[jobId] = String(data.id);
    }
  } catch {
    onToast?.("Couldn't sync that note — still saved on this phone");
  }
}

/** Pull all job notes for this user into state.notes + populate noteRowIds. */
export async function pullNotes(): Promise<void> {
  if (!sb || !userId) return;
  try {
    const { data } = await sb
      .from("job_notes")
      .select("id, job_id, body, created_at")
      .order("created_at", { ascending: false });
    if (!data) return;
    const seen: Record<string, boolean> = {};
    const patch: Record<string, string> = {};
    for (const row of data) {
      const jid = String(row.job_id);
      if (seen[jid]) continue; // newest-first: keep only latest per job
      seen[jid] = true;
      noteRowIds[jid] = String(row.id);
      patch[jid] = String(row.body);
    }
    const cur = getState();
    setState({ notes: { ...patch, ...cur.notes } });
  } catch {
    onToast?.("Couldn't load notes from account — using this phone's copy");
  }
}

/** Insert a single chat message into Supabase chat_messages. Fail-silent. */
export async function pushChatMessage(role: "user" | "assistant", body: string): Promise<void> {
  if (!sb || !userId) return;
  try {
    const { error } = await sb.from("chat_messages").insert({ role, body });
    if (error) throw error;
  } catch {
    onToast?.("Couldn't sync chat — still saved on this phone");
  }
}

/** Persist last 14 chat messages to localStorage (fallback when offline). */
export function saveChatToLocal(msgs: Array<{ role: string; body: string }>): void {
  try {
    localStorage.setItem(CHAT_LS_KEY, JSON.stringify(msgs.slice(-14)));
  } catch { /* quota */ }
}

/** Load up to 14 chat messages: Supabase if available, else localStorage. */
export async function loadChatHistory(): Promise<Array<{ role: string; body: string }>> {
  if (sb && userId) {
    try {
      const { data } = await sb
        .from("chat_messages")
        .select("role, body")
        .order("created_at", { ascending: false })
        .limit(14);
      if (data && data.length > 0) {
        const msgs = (data as Array<{ role: string; body: string }>).reverse();
        saveChatToLocal(msgs);
        return msgs;
      }
    } catch { /* fall through to localStorage */ }
  }
  try {
    const raw = localStorage.getItem(CHAT_LS_KEY);
    if (raw) return JSON.parse(raw) as Array<{ role: string; body: string }>;
  } catch { /* ignore */ }
  return [];
}

const pushProfile = debounce(async () => {
  if (!sb || !userId) return;
  const s = getState();
  const profile = {
    ...s.profile,
    applied: s.applied,
    saved: s.saved,
    hidden: s.hidden,
    snoozedUntil: s.snoozedUntil,
    followUps: s.followUps,
    appliedLog: s.appliedLog,
    followAlertDay: s.followAlertDay,
    commuteRadius: s.commuteRadius,
    coachOff: s.coachOff,
    seen: s.seen,
    filters: s.filters,
  };
  const { error } = await sb.from("user_profile").upsert({
    user_id: userId,
    profile,
    updated_at: new Date().toISOString(),
  });
  if (error) {
    onToast?.("Couldn't save to account — still saved on this phone");
  } else {
    onToast?.("Saved");
  }
}, 500);

export function autosave(): void {
  pushProfile();
}

export async function pullProfile(): Promise<void> {
  if (!sb || !userId) return;
  const { data } = await sb.from("user_profile").select("profile").eq("user_id", userId).maybeSingle();
  if (!data?.profile || typeof data.profile !== "object") return;
  const p = data.profile as Record<string, unknown>;
  const cur = getState();
  const d = defaultState();
  const quiz = { ...cur.profile.quiz };
  const quizKeys = ["kind", "where", "time", "pay", "confidence"] as const;
  for (const k of quizKeys) {
    const nested = (p.quiz as Record<string, string> | undefined)?.[k];
    const flat = p[k];
    if (typeof nested === "string") quiz[k] = nested;
    else if (typeof flat === "string") quiz[k] = flat;
  }
  setState({
    profile: {
      ...d.profile,
      ...(p as typeof cur.profile),
      quiz,
    },
    applied: { ...cur.applied, ...((p.applied as Record<string, boolean>) || {}) },
    saved: { ...cur.saved, ...((p.saved as Record<string, boolean>) || {}) },
    hidden: { ...cur.hidden, ...((p.hidden as Record<string, boolean>) || {}) },
    snoozedUntil: { ...((p.snoozedUntil as Record<string, string>) || {}), ...cur.snoozedUntil },
    followUps: { ...cur.followUps, ...((p.followUps as typeof cur.followUps) || {}) },
    appliedLog: { ...cur.appliedLog, ...((p.appliedLog as Record<string, AppliedEntry>) || {}) },
    followAlertDay: (p.followAlertDay as string) || cur.followAlertDay,
    commuteRadius: (p.commuteRadius as number | null) ?? cur.commuteRadius,
    coachOff: (p.coachOff as boolean) ?? cur.coachOff,
    seen: Array.isArray(p.seen) ? (p.seen as string[]) : cur.seen,
    filters: { ...defaultFilters(), ...cur.filters, ...((p.filters as FilterPrefs) || {}) },
  });
}

/**
 * One-time pull from legacy per-job tables (user_job_status, job_notes) into the
 * new user_profile blob shape. Runs once per user, guarded by a localStorage flag.
 * chat_messages sync separately via loadChatHistory / pushChatMessage.
 */
export async function pullLegacyTables(): Promise<void> {
  if (!sb || !userId) return;
  const GUARD = `dsm-jobs-legacy-migrated-${userId}`;
  if (localStorage.getItem(GUARD)) return;
  try {
    const { data: statusRows, error: statusErr } = await sb
      .from("user_job_status")
      .select("job_id, applied, applied_on, saved, hidden");
    if (statusErr) return;

    if (statusRows && statusRows.length > 0) {
      const cur = getState();
      const pApplied: Record<string, boolean> = {};
      const pSaved: Record<string, boolean> = {};
      const pHidden: Record<string, boolean> = {};
      const pLog: Record<string, AppliedEntry> = {};

      for (const row of statusRows) {
        const id = String(row.job_id);
        if (row.applied && !cur.applied[id]) {
          pApplied[id] = true;
          if (!cur.appliedLog[id]) {
            pLog[id] = {
              t: "", c: "",
              d: row.applied_on ? String(row.applied_on) : todayISO(),
              u: "",
            };
          }
        }
        if (row.saved && !cur.saved[id]) pSaved[id] = true;
        if (row.hidden && !cur.hidden[id]) pHidden[id] = true;
      }

      setState({
        applied: { ...pApplied, ...cur.applied },
        saved: { ...pSaved, ...cur.saved },
        hidden: { ...pHidden, ...cur.hidden },
        appliedLog: { ...pLog, ...cur.appliedLog },
      });
    }
    await pullNotes();
    localStorage.setItem(GUARD, "1");
  } catch {
    /* legacy tables may be absent or inaccessible — retry next sign-in */
  }
}

export async function loadPortal(): Promise<PortalCfg> {
  try {
    const r = await fetch(`${import.meta.env.BASE_URL}portal.json`);
    if (!r.ok) return {};
    return (await r.json()) as PortalCfg;
  } catch {
    return {};
  }
}
