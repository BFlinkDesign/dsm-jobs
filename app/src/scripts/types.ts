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

export type AppState = {
  applied: Record<string, boolean>;
  saved: Record<string, boolean>;
  hidden: Record<string, boolean>;
  followUps: Record<string, FollowUp>;
  appliedLog: Record<string, string>;
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
