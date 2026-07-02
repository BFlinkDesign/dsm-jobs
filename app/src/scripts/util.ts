export function esc(s: string): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/`/g, "&#96;");
}

export function safeUrl(u: string): string {
  try {
    const p = new URL(u);
    return p.protocol === "http:" || p.protocol === "https:" ? u : "";
  } catch {
    return "";
  }
}

export function debounce<T extends (...a: never[]) => void>(fn: T, ms: number): T {
  let t: ReturnType<typeof setTimeout> | undefined;
  return ((...args: never[]) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  }) as T;
}

export function addDaysISO(iso: string, days: number): string {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function weekStart(): string {
  const d = new Date();
  d.setDate(d.getDate() - d.getDay()); // back to Sunday (Iowa week runs Sun–Sat)
  return d.toISOString().slice(0, 10);
}

export function fmtStamp(ts: string | undefined, date: string): string {
  if (ts) {
    const dt = new Date(ts);
    if (!isNaN(dt.getTime())) {
      return dt.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }
  }
  return date || "";
}

export function daysSince(iso: string): number | null {
  if (!iso) return null;
  const t = Date.parse(String(iso).slice(0, 10) + "T12:00:00");
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86400000);
}

export function ago(iso: string): string {
  const d = daysSince(iso);
  if (d == null) return "";
  if (d < 1) return "today";
  if (d === 1) return "yesterday";
  if (d < 7) return `${d} days ago`;
  const w = Math.floor(d / 7);
  return `${w} week${w === 1 ? "" : "s"} ago`;
}

// Mirrors strip_remote_decoration() in find_admin_jobs.py. The scanner already
// strips a redundant "Remote" from a remote job's title before it ever reaches
// jobs.json, but this is the DEFENSIVE second layer: cached/offline jobs.json
// from before that fix, or any future feed source, can still carry a title
// like "Data Entry Clerk - Remote" alongside the card's own "Remote" location
// tag — which would read "Data Entry Clerk - Remote · Remote". Only strips a
// decoration anchored at the very start or end of the title; a mid-title word
// ("Senior Remote-Friendly Assistant") is left alone.
const REMOTE_DECOR_PHRASE = "(?:100%\\s*remote\\b|fully\\s*remote\\b|remote\\b|work[\\s-]?from[\\s-]?home\\b|wfh\\b)";
const REMOTE_DECOR_TRAILING = new RegExp(`\\s*[-–—:,]?\\s*[(\\[]?\\s*${REMOTE_DECOR_PHRASE}\\s*[)\\]]?\\s*$`, "i");
const REMOTE_DECOR_LEADING = new RegExp(`^\\s*[(\\[]?\\s*${REMOTE_DECOR_PHRASE}\\s*[)\\]]?\\s*[-–—:,]?\\s*`, "i");

export function stripRedundantRemoteLabel(title: string): string {
  let t = (title || "").trim();
  if (!t) return t;
  const original = t;
  let prev: string | null = null;
  while (prev !== t) {
    prev = t;
    t = t.replace(REMOTE_DECOR_TRAILING, "").replace(REMOTE_DECOR_LEADING, "");
    t = t.replace(/^[\s\t\-–—:,]+|[\s\t\-–—:,]+$/g, "");
  }
  return t || original;
}

export function relativePosted(posted: string): string {
  if (!posted) return "";
  const t = Date.parse(posted);
  if (Number.isNaN(t)) return posted;
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days < 1) return "posted today";
  if (days === 1) return "posted yesterday";
  if (days < 14) return `posted ${days} days ago`;
  const w = Math.floor(days / 7);
  return `posted ${w} week${w === 1 ? "" : "s"} ago`;
}

const SKULL = `<svg class="skull-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 2c-3.5 0-6 2.8-6 6.5 0 2 .8 3.5 2 4.7V16h8v-2.8c1.2-1.2 2-2.7 2-4.7C18 4.8 15.5 2 12 2z"/><circle cx="9" cy="9" r="1.2" fill="currentColor"/><circle cx="15" cy="9" r="1.2" fill="currentColor"/><path d="M9 18v3M12 18v3M15 18v3"/></svg>`;

const BAT = `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C7 6 3 7 1 6c1 4 5 10 11 11 6-1 10-7 11-11-2 1-6 0-11-4z"/></svg>`;

export { SKULL, BAT };
