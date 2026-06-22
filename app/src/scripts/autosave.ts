import { defaultState, getState, setState } from "./store";
import type { PortalCfg } from "./types";
import { debounce } from "./util";

type Sb = ReturnType<typeof import("@supabase/supabase-js")["createClient"]>;

let sb: Sb | null = null;
let userId: string | null = null;
let onToast: ((msg: string) => void) | null = null;

export function initAutosave(client: Sb, uid: string, toast: (msg: string) => void): void {
  sb = client;
  userId = uid;
  onToast = toast;
}

export function clearAutosave(): void {
  sb = null;
  userId = null;
}

const pushProfile = debounce(async () => {
  if (!sb || !userId) return;
  const s = getState();
  const profile = {
    ...s.profile,
    applied: s.applied,
    saved: s.saved,
    hidden: s.hidden,
    followUps: s.followUps,
    appliedLog: s.appliedLog,
    commuteRadius: s.commuteRadius,
    coachOff: s.coachOff,
  };
  const { error } = await sb.from("user_profile").upsert({
    user_id: userId,
    profile,
    updated_at: new Date().toISOString(),
  });
  if (!error) onToast?.("Saved");
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
  setState({
    profile: { ...d.profile, ...(p as typeof cur.profile) },
    applied: { ...cur.applied, ...((p.applied as Record<string, boolean>) || {}) },
    saved: { ...cur.saved, ...((p.saved as Record<string, boolean>) || {}) },
    hidden: { ...cur.hidden, ...((p.hidden as Record<string, boolean>) || {}) },
    followUps: { ...cur.followUps, ...((p.followUps as typeof cur.followUps) || {}) },
    appliedLog: { ...cur.appliedLog, ...((p.appliedLog as Record<string, string>) || {}) },
    commuteRadius: (p.commuteRadius as number | null) ?? cur.commuteRadius,
    coachOff: (p.coachOff as boolean) ?? cur.coachOff,
  });
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
