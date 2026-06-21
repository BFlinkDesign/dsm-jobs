// Verification harness for the AI spend-cap helper (spend_cap.ts).
//
// The repo's CI is Python-only and never type-checks or runs this Deno/TS code,
// so this suite (run by .github/workflows/edge-checks.yml) is the proof loop for
// the spend-cap's load-bearing behavior. No network, no real Supabase: a fake
// client is injected via the `db` argument these functions already accept, and
// `fetch` is stubbed for the alert-email path.
//
// What IS covered here (the half the TS owns):
//   1. Fail-closed pre-gate: any RPC error / null / shapeless row => allowed:false.
//   2. Cost accounting: costForUsage + accumulateCost against the RATES table,
//      for BOTH models (claude-sonnet-4-6 and claude-opus-4-8). Exact numbers.
//   3. Alert dispatch: recordSpendAndAlert sends an email iff the RPC returns the
//      fire flag, skips the RPC entirely when cost <= 0, and never throws when the
//      RPC fails (the spend already happened — it cannot fail closed).
//
// What is NOT covered here (and the seam that would close it):
//   - The fire-EXACTLY-ONCE-per-month idempotence itself lives in the SQL function
//     record_ai_spend (the atomic `(not was_20) and (new_total >= 20)` flag flip in
//     20260619112335_ai_spend_ledger.sql). That is a Postgres property, not a Deno
//     one; re-implementing it in a mock here would only test the mock. Closing it
//     needs a DB-level test (pgTAP / an integration test against a real Postgres),
//     out of scope for a Deno unit suite.

import { assertAlmostEquals, assertEquals } from "jsr:@std/assert@1";
import { type SupabaseClient } from "npm:@supabase/supabase-js@2";
import {
  accumulateCost,
  checkSpendAllowed,
  costForUsage,
  recordSpendAndAlert,
  SPEND_CAP_USD,
} from "./spend_cap.ts";

// ── Test doubles ───────────────────────────────────────────────────────────

type RpcResult = { data: unknown; error: { message: string } | null };

// A fake supabase-js client whose only used surface is `.rpc(name, args)`.
// `script` returns the {data,error} for each successive call; `calls` records
// what record_ai_spend was invoked with so we can assert it was (or wasn't) hit.
function fakeClient(script: (callIndex: number) => RpcResult) {
  const calls: Array<{ name: string; args: unknown }> = [];
  const client = {
    rpc(name: string, args: unknown): Promise<RpcResult> {
      const result = script(calls.length);
      calls.push({ name, args });
      return Promise.resolve(result);
    },
  };
  // The real type has a huge surface we don't touch; cast through unknown so
  // `deno check` is satisfied without re-implementing SupabaseClient.
  return { db: client as unknown as SupabaseClient, calls };
}

const ok = (row: unknown): RpcResult => ({ data: [row], error: null });

// ───────────────────────────────────────────────────────────────────────────
// TEST 1 — FAIL-CLOSED pre-gate (the security property: deny on uncertainty).
// ───────────────────────────────────────────────────────────────────────────

Deno.test("checkSpendAllowed: RPC error => refuse (allowed=false)", async () => {
  const { db } = fakeClient(() => ({ data: null, error: { message: "boom" } }));
  const res = await checkSpendAllowed(db);
  assertEquals(res.allowed, false);
});

Deno.test("checkSpendAllowed: null data => refuse", async () => {
  const { db } = fakeClient(() => ({ data: null, error: null }));
  const res = await checkSpendAllowed(db);
  assertEquals(res.allowed, false);
});

Deno.test("checkSpendAllowed: empty array (no row) => refuse", async () => {
  const { db } = fakeClient(() => ({ data: [], error: null }));
  const res = await checkSpendAllowed(db);
  assertEquals(res.allowed, false);
});

Deno.test("checkSpendAllowed: row missing numeric mtd => refuse", async () => {
  const { db } = fakeClient(() => ok({ mtd: "not-a-number", fire_warn: false, fire_stop: false }));
  const res = await checkSpendAllowed(db);
  assertEquals(res.allowed, false);
});

Deno.test("checkSpendAllowed: under cap => allow", async () => {
  const { db, calls } = fakeClient(() => ok({ mtd: 10.5, fire_warn: false, fire_stop: false }));
  const res = await checkSpendAllowed(db);
  assertEquals(res.allowed, true);
  assertEquals(res.mtd, 10.5);
  // The pre-gate must read MTD with a ZERO-cost ledger call (never spends).
  assertEquals(calls[0].name, "record_ai_spend");
  assertEquals(calls[0].args, { cost_usd: 0 });
});

Deno.test("checkSpendAllowed: boundary mtd=24.99 allows, mtd=25.00 refuses", async () => {
  const under = fakeClient(() => ok({ mtd: 24.99, fire_warn: false, fire_stop: false }));
  assertEquals((await checkSpendAllowed(under.db)).allowed, true);

  const at = fakeClient(() => ok({ mtd: SPEND_CAP_USD, fire_warn: false, fire_stop: false }));
  // allowed === mtd < SPEND_CAP_USD, so exactly at the cap must refuse.
  assertEquals((await checkSpendAllowed(at.db)).allowed, false);
});

// ───────────────────────────────────────────────────────────────────────────
// TEST 2 — COST ACCOUNTING against the RATES table, BOTH models. Exact USD.
//   sonnet-4-6 : $3.00 in / $15.00 out per 1M tokens
//   opus-4-8   : $5.00 in / $25.00 out per 1M tokens
// Cases chosen so the float math is exact (assertAlmostEquals guards anyway).
// ───────────────────────────────────────────────────────────────────────────

Deno.test("costForUsage: claude-sonnet-4-6 priced from RATES", () => {
  // 1M in + 1M out = (1e6*3 + 1e6*15) / 1e6 = 18.00
  assertAlmostEquals(
    costForUsage("claude-sonnet-4-6", { input_tokens: 1_000_000, output_tokens: 1_000_000 }),
    18.0,
    1e-9,
  );
  // 500k in + 100k out = (500000*3 + 100000*15) / 1e6 = 3.00
  assertAlmostEquals(
    costForUsage("claude-sonnet-4-6", { input_tokens: 500_000, output_tokens: 100_000 }),
    3.0,
    1e-9,
  );
});

Deno.test("costForUsage: claude-opus-4-8 priced from RATES", () => {
  // 1M in + 1M out = (1e6*5 + 1e6*25) / 1e6 = 30.00
  assertAlmostEquals(
    costForUsage("claude-opus-4-8", { input_tokens: 1_000_000, output_tokens: 1_000_000 }),
    30.0,
    1e-9,
  );
  // 200k in + 50k out = (200000*5 + 50000*25) / 1e6 = 2.25
  assertAlmostEquals(
    costForUsage("claude-opus-4-8", { input_tokens: 200_000, output_tokens: 50_000 }),
    2.25,
    1e-9,
  );
});

Deno.test("costForUsage: unknown model and missing usage => 0 (never throws)", () => {
  assertEquals(costForUsage("gpt-imaginary", { input_tokens: 1_000_000, output_tokens: 1_000_000 }), 0);
  assertEquals(costForUsage("claude-opus-4-8", null), 0);
  assertEquals(costForUsage("claude-opus-4-8", undefined), 0);
  // Partial usage: only input tokens present (output defaults to 0).
  // 1M in opus = 1e6*5 / 1e6 = 5.00
  assertAlmostEquals(costForUsage("claude-opus-4-8", { input_tokens: 1_000_000 }), 5.0, 1e-9);
});

Deno.test("accumulateCost: sums across both models (resume-tailor's mixed loop)", () => {
  // opus(1M/1M)=30.00 + sonnet(1M/1M)=18.00 = 48.00
  const total = accumulateCost([
    { model: "claude-opus-4-8", usage: { input_tokens: 1_000_000, output_tokens: 1_000_000 } },
    { model: "claude-sonnet-4-6", usage: { input_tokens: 1_000_000, output_tokens: 1_000_000 } },
  ]);
  assertAlmostEquals(total, 48.0, 1e-9);
  assertEquals(accumulateCost([]), 0);
});

// ───────────────────────────────────────────────────────────────────────────
// TEST 3 — recordSpendAndAlert: alert dispatch + idempotence-of-dispatch.
//
// The fire-once-PER-MONTH guarantee is the SQL function's job (see header note);
// here we verify the TS half: an email is sent iff the RPC reports the fire flag,
// the RPC is skipped for non-positive cost, and a record/alert failure is
// swallowed (cannot fail closed — the money was already spent).
//
// sendAlert() reads RESEND_API_KEY/ALERT_EMAIL_FROM/ALERT_EMAIL_TO from the env
// and POSTs to api.resend.com. We set those three env vars and stub `fetch`, so
// no real email leaves. The workflow runs this with --allow-env for exactly
// those three names (reading env without the grant throws NotCapable, which the
// catch would swallow — silently killing the assertion).
// ───────────────────────────────────────────────────────────────────────────

// Run the body with RESEND_* env set and fetch stubbed; always restore both.
async function withStubbedEmail(
  body: (sentTo: string[]) => Promise<void>,
): Promise<void> {
  const sentTo: string[] = [];
  const realFetch = globalThis.fetch;
  const prevKey = Deno.env.get("RESEND_API_KEY");
  const prevFrom = Deno.env.get("ALERT_EMAIL_FROM");
  const prevTo = Deno.env.get("ALERT_EMAIL_TO");

  Deno.env.set("RESEND_API_KEY", "re_test_key_not_real");
  Deno.env.set("ALERT_EMAIL_FROM", "alerts@example.test");
  Deno.env.set("ALERT_EMAIL_TO", "brady@example.test");
  // deno-lint-ignore require-await
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input.toString();
    sentTo.push(url);
    return new Response("{}", { status: 200 });
  }) as typeof fetch;

  try {
    await body(sentTo);
  } finally {
    globalThis.fetch = realFetch;
    const restore = (k: string, v: string | undefined) =>
      v === undefined ? Deno.env.delete(k) : Deno.env.set(k, v);
    restore("RESEND_API_KEY", prevKey);
    restore("ALERT_EMAIL_FROM", prevFrom);
    restore("ALERT_EMAIL_TO", prevTo);
  }
}

Deno.test("recordSpendAndAlert: cost <= 0 never calls the ledger", async () => {
  for (const cost of [0, -1, NaN]) {
    const { db, calls } = fakeClient(() => ok({ mtd: 0, fire_warn: false, fire_stop: false }));
    await recordSpendAndAlert(db, cost);
    assertEquals(calls.length, 0, `cost ${cost} must not touch the ledger`);
  }
});

Deno.test("recordSpendAndAlert: fires the $20 warning email once when fire_warn is set", async () => {
  await withStubbedEmail(async (sentTo) => {
    const { db, calls } = fakeClient(() => ok({ mtd: 20.5, fire_warn: true, fire_stop: false }));
    await recordSpendAndAlert(db, 5.0);
    assertEquals(calls.length, 1); // recorded the real cost
    assertEquals(calls[0].args, { cost_usd: 5.0 });
    assertEquals(sentTo.length, 1); // exactly one email for the one fired flag
    assertEquals(sentTo[0], "https://api.resend.com/emails");
  });
});

Deno.test("recordSpendAndAlert: fires the $25 stop email once when fire_stop is set", async () => {
  await withStubbedEmail(async (sentTo) => {
    const { db } = fakeClient(() => ok({ mtd: 25.5, fire_warn: false, fire_stop: true }));
    await recordSpendAndAlert(db, 5.0);
    assertEquals(sentTo.length, 1);
    assertEquals(sentTo[0], "https://api.resend.com/emails");
  });
});

Deno.test("recordSpendAndAlert: no flags set => no email", async () => {
  await withStubbedEmail(async (sentTo) => {
    const { db } = fakeClient(() => ok({ mtd: 5.0, fire_warn: false, fire_stop: false }));
    await recordSpendAndAlert(db, 5.0);
    assertEquals(sentTo.length, 0);
  });
});

Deno.test("recordSpendAndAlert: subsequent calls past a crossed threshold send no further email", async () => {
  // Mirrors the SQL dedup at the TS boundary: once the flag has flipped, later
  // calls come back with fire_warn=false, so no second warning email is sent.
  await withStubbedEmail(async (sentTo) => {
    // Call A: just crossed $20 -> flag set -> one email.
    const a = fakeClient(() => ok({ mtd: 20.5, fire_warn: true, fire_stop: false }));
    await recordSpendAndAlert(a.db, 1.0);
    // Call B: already over $20, flag already flipped server-side -> no flag -> no email.
    const b = fakeClient(() => ok({ mtd: 21.0, fire_warn: false, fire_stop: false }));
    await recordSpendAndAlert(b.db, 0.5);
    assertEquals(sentTo.length, 1, "warning email must fire only on the crossing call");
  });
});

Deno.test("recordSpendAndAlert: RPC failure is swallowed, never throws (spend already incurred)", async () => {
  const { db } = fakeClient(() => ({ data: null, error: { message: "ledger down" } }));
  // Must resolve, not reject: a post-call ledger failure cannot fail the request.
  await recordSpendAndAlert(db, 5.0);
});
