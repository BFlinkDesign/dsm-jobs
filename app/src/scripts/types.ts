export type Job = {
  id: string;
  title: string;
  company: string;
  location: string;
  pay: string;
  payNum: number;
  remote: boolean;
  trusted: boolean;
  trustLabel: string;
  good: boolean;
  tagLabel: string;
  posted: string;
  url: string;
  category: string;
  commute: string;
  commuteMin: number | null;
  about: string;
  descFull: string;
  trains: boolean;
  contactPhone: string;
  contactEmail: string;
  contactName: string;
};

export type Meta = {
  contact: string;
  phone: string;
  generated: string;
  hidden: number;
  total: number;
  safe: number;
};

export type PortalCfg = { url?: string; key?: string };

export type FollowUp = {
  name: string;
  phone: string;
  email: string;
  on: string;
  done: boolean;
};

export type FilterPrefs = {
  searchQ: string;
  filterRemote: "all" | "local" | "remote";
  filterPay: boolean;
  filterSaved: boolean;
  filterApplied: boolean;
  filterTrain: boolean;
  filterTrusted: boolean;
  filterCategory: string;
  showHidden: boolean;
};

export function defaultFilters(): FilterPrefs {
  return {
    searchQ: "",
    filterRemote: "all",
    filterPay: false,
    filterSaved: false,
    filterApplied: false,
    filterTrain: false,
    filterTrusted: false,
    filterCategory: "",
    showHidden: false,
  };
}

export type AppliedEntry = {
  t: string;   // job title (captured at apply time so log survives job leaving feed)
  c: string;   // company
  d: string;   // ISO date applied
  u: string;   // apply URL
  ts?: string; // ISO timestamp (for finer sort/display)
};

export type AtsAlignment = {
  strong_matches: string[];
  suggested_keywords: string[];
  note: string;
};

export type ApplicationPack = {
  id: string;
  jobId: string;
  jobTitle: string;
  company: string;
  createdAt: string;
  resume: string;
  coverNote: string;
  followUp: string;
  changes: string[];
  ats: AtsAlignment;
};

export type AppState = {
  applied: Record<string, boolean>;
  saved: Record<string, boolean>;
  hidden: Record<string, boolean>;
  snoozedUntil: Record<string, string>;  // id -> ISO date; job hidden until >= this date
  notes: Record<string, string>;          // per-job text notes (synced to job_notes table)
  followUps: Record<string, FollowUp>;
  appliedLog: Record<string, AppliedEntry>;
  applicationPacks: Record<string, ApplicationPack>;
  followAlertDay: string;                 // last day follow-up browser notifications fired
  seen: string[];                         // job ids from previous visit (for "New" badges)
  filters: FilterPrefs;
  profile: {
    preferredName: string;
    legalName: string;
    resume: string;
    quiz: Record<string, string>;
  };
  commuteRadius: number | null;
  coachOff: boolean;
};

export type ViewName = "jobs" | "today" | "apps" | "corner" | "help";
