import type { AppState } from "./types";

const LS = "dsm-jobs-state-v2";

export function defaultState(): AppState {
  return {
    applied: {},
    saved: {},
    hidden: {},
    followUps: {},
    appliedLog: {},
    profile: { preferredName: "", legalName: "", resume: "", quiz: {} },
    commuteRadius: null,
    coachOff: false,
  };
}

let state: AppState = defaultState();

export function getState(): AppState {
  return state;
}

export function setState(patch: Partial<AppState>): void {
  state = { ...state, ...patch };
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
    state = { ...defaultState(), ...parsed, profile: { ...defaultState().profile, ...parsed.profile } };
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

export function jobMap(jobs: { id: string }[]): Map<string, { id: string }> {
  return new Map(jobs.map((j) => [j.id, j]));
}
