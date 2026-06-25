// Shared AI spend-cap helper for BOTH paid edge functions (companion +
// resume-tailor). A HARD month-to-date cap on the deployed app's Anthropic
// spend, enforced before every AI call and tracked in a SECURITY DEFINER
// Postgres ledger (see supabase/migrations/*_ai_spend_ledger.sql).
//
// Design:
// - BEFORE each AI call, the function reads MTD (record_ai_spend(0)) and refuses
//   if it's already at/over the cap. FAILS CLOSED: any ledger error => refuse,
//   mirroring the per-user rate-cap fail-closed pattern in companion/index.ts.
// - AFTER each successful AI call, the function records the real provider cost.
//   A post-call record failure CANNOT fail closed (the money was already spent)
//   — it's logged, and the user still gets the reply they paid for.
// - Brady is emailed once when MTD first crosses $20 (warning) and once at the
//   $25 stop, via Resend. The ledger's alerted_* flags make this fire exactly
//   once per month; email is a clean no-op (+log) when RESEND_API_KEY is unset,
//   mirroring the existing SENTRY_DSN gating.

import { type SupabaseClient } from "npm:@supabase/supabase-js@2";

// ── Pricing table — per-MILLION-token rates (USD) ──────────────────────────
// VERIFY against current Anthropic pricing before deploy:
//   https://platform.claude.com/docs/en/about-claude/models/overview
// Source as of 2026-06-19 (claude-api skill, cached 2026-06-04):
//   claude-opus-4-8   : $5.00 in  / $25.00 out
//   claude-sonnet-4-6 : $3.00 in  / $15.00 out
// companion uses claude-sonnet-4-6; resume-tailor uses claude-opus-4-8 (writer)
// + claude-sonnet-4-6 (critic). Keep these IN SYNC with the model ids in each
// function — a wrong rate is a silent cap bug.
const RATES: Record<string, { in: number; out: number }> = {
  "claude-opus-4-8": { in: 5.00, out: 25.00 },
  "claude-sonnet-4-6": { in: 3.00, out: 15.00 },
};

// The $20 warning and $25 hard-stop thresholds are owned by the SQL function
// (record_ai_spend) so the once-only email dedup is atomic. SPEND_CAP_USD here
// is only for the pre-call gate's MTD comparison; keep it equal to the SQL stop.
export const SPEND_CAP_USD = 25.00;

// Per-call usage as Anthropic returns it. With prompt caching ON (both paid
// functions set cache_control on the system prompt) `input_tokens` is only the
// UNCACHED remainder — the cached tokens are billed under the cache_* classes
// and must be priced too, or the ledger undercounts and the $25 cap trips late.
//   cache write (5m ephemeral): 1.25x base input rate
//   cache read                : 0.10x base input rate
export type Usage = {
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
} | null | undefined;

const CACHE_WRITE_MULT = 1.25;
const CACHE_READ_MULT = 0.10;

// Provider cost in USD for one model call. Unknown model => 0 but LOGGED (a
// silent $0 would let a model-id typo bypass the cap); never throws, so a
// pricing miss can't crash a request.
export function costForUsage(model: string, usage: Usage): number {
  const rate = RATES[model];
  if (!rate) {
    console.error(`spend-cap: no pricing for model '${model}' — cost counted as $0 (cap may not trip)`);
    return 0;
  }
  if (!usage) return 0;
  const inTok = usage.input_tokens ?? 0;
  const outTok = usage.output_tokens ?? 0;
  const cacheWrite = usage.cache_creation_input_tokens ?? 0;
  const cacheRead = usage.cache_read_input_tokens ?? 0;
  return (
    inTok * rate.in +
    cacheWrite * rate.in * CACHE_WRITE_MULT +
    cacheRead * rate.in * CACHE_READ_MULT +
    outTok * rate.out
  ) / 1_000_000;
}

// Accumulate cost across multiple model calls (resume-tailor's write/critique/
// revise loop spends across up to ~6 calls and two models). Sum, then record once.
export function accumulateCost(
  calls: Array<{ model: string; usage: Usage }>,
): number {
  let total = 0;
  for (const c of calls) total += costForUsage(c.model, c.usage);
  return total;
}

type SpendRow = { mtd: number; fire_warn: boolean; fire_stop: boolean };

// Call record_ai_spend(costUsd) and normalize the single-row TABLE result.
// Throws on any RPC/shape error so callers can FAIL CLOSED on the pre-gate.
async function callRecord(db: SupabaseClient, costUsd: number): Promise<SpendRow> {
  const { data, error } = await db.rpc("record_ai_spend", { cost_usd: costUsd });
  if (error) throw new Error(`record_ai_spend failed: ${error.message}`);
  const row = Array.isArray(data) ? data[0] : data;
  if (!row || typeof row.mtd !== "number") {
    throw new Error("record_ai_spend returned no usable row");
  }
  return { mtd: Number(row.mtd), fire_warn: !!row.fire_warn, fire_stop: !!row.fire_stop };
}

// ── Pre-call gate ──────────────────────────────────────────────────────────
// Read MTD with a zero-cost ledger call. Returns { allowed } — allowed=false
// means refuse the AI call. FAILS CLOSED: any error => allowed:false (refuse,
// don't spend), exactly like the existing rate-cap (`capErr || recent === null`).
//
// `db` is the caller's existing user-JWT client. record_ai_spend is SECURITY
// DEFINER with EXECUTE granted to `authenticated`, so the user-JWT client can
// invoke it and the function reaches the RLS-locked ledger as its owner — no
// service-role key needed.
export async function checkSpendAllowed(
  db: SupabaseClient,
): Promise<{ allowed: boolean; mtd: number }> {
  try {
    const row = await callRecord(db, 0);
    return { allowed: row.mtd < SPEND_CAP_USD, mtd: row.mtd };
  } catch (err) {
    console.error("spend-cap pre-gate error (failing closed)", err);
    return { allowed: false, mtd: NaN };
  }
}

// ── Post-call record ───────────────────────────────────────────────────────
// Record the real provider cost AFTER a successful AI call, then fire the
// warning/stop email if this call crossed a threshold. Never throws and never
// fails the request: the spend already happened, so a ledger or email failure
// is logged (Sentry-style) but the user still gets their reply.
export async function recordSpendAndAlert(db: SupabaseClient, costUsd: number): Promise<void> {
  if (!(costUsd > 0)) return;  // nothing to record (0, NaN, or negative)
  try {
    const row = await callRecord(db, costUsd);
    if (row.fire_warn) {
      await sendAlert(
        "Eagle jobs AI spend — $20 warning",
        `Month-to-date Anthropic spend has crossed $20.00 (now $${row.mtd.toFixed(2)}). ` +
          `The hard stop is $${SPEND_CAP_USD.toFixed(2)}.`,
      );
    }
    if (row.fire_stop) {
      await sendAlert(
        "Eagle jobs AI spend — $25 STOP reached",
        `Month-to-date Anthropic spend has reached the $${SPEND_CAP_USD.toFixed(2)} cap ` +
          `(now $${row.mtd.toFixed(2)}). AI features are paused until next month.`,
      );
    }
  } catch (err) {
    // Cannot fail closed here — the API call already cost money. Log and move on.
    console.error("spend-cap record/alert error (spend already incurred)", err);
  }
}

// ── Email via Resend REST API ──────────────────────────────────────────────
// Gated on RESEND_API_KEY exactly like the SENTRY_DSN check: unset => clean
// no-op + a log line, never a throw. All of key/from/to come from Deno.env and
// are NEVER committed. Awaited before the caller returns (the Deno isolate can
// freeze right after the response and drop an un-awaited fetch — same reason
// companion flushes Sentry before returning).
async function sendAlert(subject: string, text: string): Promise<void> {
  const key = Deno.env.get("RESEND_API_KEY");
  const from = Deno.env.get("ALERT_EMAIL_FROM");
  const to = Deno.env.get("ALERT_EMAIL_TO");
  if (!key || !from || !to) {
    console.log(`spend-cap alert skipped (RESEND not configured): ${subject}`);
    return;
  }
  try {
    const resp = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ from, to, subject, text }),
    });
    if (!resp.ok) {
      console.error("spend-cap alert email failed", resp.status, (await resp.text()).slice(0, 200));
    }
  } catch (err) {
    console.error("spend-cap alert email error", err);
  }
}
