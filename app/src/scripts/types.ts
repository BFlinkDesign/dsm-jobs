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

// vapidPublicKey is the PUBLIC half of the Web Push VAPID key pair — safe to
// ship to the browser by design (it's what PushManager.subscribe needs; the
// private half never leaves Supabase function secrets). Absent -> the "Turn on
// push notifications" control never appears; the in-app Notification fallback
// is unaffected either way.
export type PortalCfg = { url?: string; key?: string; vapidPublicKey?: string };

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
  sortBy: "newest" | "match" | "remote" | "commute" | "pay";
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
    sortBy: "newest",
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

export type ResumeDocument = {
  id: string;
  name: string;
  text: string;
  source: "paste" | "upload";
  createdAt: string;
  updatedAt: string;
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
    documents: ResumeDocument[];
    activeDocumentId: string;
    quiz: Record<string, string>;
  };
  commuteRadius: number | null;
  coachOff: boolean;
};

export type ViewName = "jobs" | "today" | "apps" | "corner" | "help" | "money";

// Resources hub (Money & help tab) — loaded at runtime from resources.json.
export type Resource = {
  name: string;
  what: string;
  who?: string;
  how?: string;
  url?: string;
  phone?: string;
  whatToSay?: string;
  time?: string;
};

export type ResourceSection = {
  id: string;
  title: string;
  subtitle?: string;
  resources: Resource[];
};

export type ResourceHub = {
  intro: string;
  startHere?: { title: string; body: string; phone?: string };
  sections: ResourceSection[];
  skillsIntro?: string;
  skills: Resource[];
  safetyNote?: string;
  updated?: string;
};
