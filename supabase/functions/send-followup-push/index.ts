// Supabase Edge Function: "send-followup-push" — sends a real OS push
// notification for each due, not-yet-done follow-up reminder.
//
// Why this exists: the in-app `Notification` API (app.ts `maybeNotifyFollowUps`)
// only fires while the tab/PWA is open — on iOS, once she closes the app (the
// normal state for a phone-only user), it never fires again. Web Push delivers
// through the OS even when the app is fully closed, which is the whole point.
//
// This is a SERVER-SIDE cron job, not a page-invoked function: it has to look
// across ALL users' due follow-ups, which requires the service role (RLS
// bypass). It must NEVER be reachable with the anon/publishable key — verify_jwt
// is OFF in supabase/config.toml for this function, and it authenticates the
// caller itself via a shared secret header, exactly so a public repo's schema
// doesn't imply a public trigger. See supabase/functions/voice/index.ts for the
// established "no key set -> clean no-op" secrets pattern this file follows.
//
// Trigger: a scheduled GitHub Actions workflow (.github/workflows/push-followups.yml)
// POSTs here on a cron with the shared secret. Anyone can drive it (pg_cron,
// a different scheduler, curl by hand) as long as they have the secret.
//
// Deploy: supabase functions deploy send-followup-push --no-verify-jwt
//
// Required secrets (Supabase project, via `supabase secrets set`):
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   -- to read across all users (RLS bypass)
//   VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY       -- Web Push key pair (generate once,
//                                                 e.g. `npx web-push generate-vapid-keys`)
//   VAPID_SUBJECT                             -- "mailto:you@example.com" (contact
//                                                 the push service can reach if abusive)
//   PUSH_CRON_SECRET                          -- shared secret the trigger must send
//                                                 as `x-cron-secret` (never the anon key)
//
// If ANY of the VAPID/service secrets are unset, every invocation is a clean,
// loud-logged no-op (HTTP 200 { skipped: true, reason }) — the in-app
// `Notification` fallback keeps working exactly as it does today either way.

import { createClient } from "npm:@supabase/supabase-js@2";
import webpush from "npm:web-push@3";
import * as Sentry from "npm:@sentry/deno@10";

const SENTRY_DSN = Deno.env.get("SENTRY_DSN");
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    tracesSampleRate: 0,
    sendDefaultPii: false,
    beforeSend(event) {
      delete event.user;
      delete event.request;
      delete event.contexts;
      delete event.server_name;
      return event;
    },
  });
}

const env = (k: string): string => Deno.env.get(k) || "";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FollowUp = { name?: string; phone?: string; email?: string; on?: string; done?: boolean };
type ProfileRow = {
  user_id: string;
  profile: { followUps?: Record<string, FollowUp>; appliedLog?: Record<string, { t?: string; c?: string }> } | null;
};
type SubRow = { endpoint: string; p256dh: string; auth_key: string; user_id: string };

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function dueFollowUpIds(profile: ProfileRow["profile"]): string[] {
  const followUps = profile?.followUps || {};
  const today = todayISO();
  return Object.entries(followUps)
    .filter(([, fu]) => !fu.done && fu.on && fu.on <= today)
    .map(([jobId]) => jobId);
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const cronSecret = env("PUSH_CRON_SECRET");
  if (!cronSecret) return json({ skipped: true, reason: "PUSH_CRON_SECRET not configured" });
  if (req.headers.get("x-cron-secret") !== cronSecret) {
    return json({ error: "unauthorized" }, 401);
  }

  const supabaseUrl = env("SUPABASE_URL");
  const serviceKey = env("SUPABASE_SERVICE_ROLE_KEY");
  const vapidPublic = env("VAPID_PUBLIC_KEY");
  const vapidPrivate = env("VAPID_PRIVATE_KEY");
  const vapidSubject = env("VAPID_SUBJECT");
  if (!supabaseUrl || !serviceKey) {
    return json({ skipped: true, reason: "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not configured" });
  }
  if (!vapidPublic || !vapidPrivate || !vapidSubject) {
    return json({ skipped: true, reason: "VAPID keys not configured" });
  }

  webpush.setVapidDetails(vapidSubject, vapidPublic, vapidPrivate);

  const db = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false } });

  try {
    const { data: profiles, error: profErr } = await db
      .from("user_profile")
      .select("user_id, profile");
    if (profErr) throw profErr;

    let notified = 0;
    let staleRemoved = 0;

    for (const row of (profiles ?? []) as ProfileRow[]) {
      const dueIds = dueFollowUpIds(row.profile);
      if (!dueIds.length) continue;

      const { data: subs, error: subErr } = await db
        .from("push_subscriptions")
        .select("endpoint, p256dh, auth_key, user_id")
        .eq("user_id", row.user_id);
      if (subErr || !subs?.length) continue;

      const appliedLog = row.profile?.appliedLog || {};
      const firstId = dueIds[0];
      const entry = appliedLog[firstId];
      const title = entry?.t || "Follow up";
      const company = entry?.c ? ` — ${entry.c}` : "";
      const body = dueIds.length > 1
        ? `${dueIds.length} follow-ups are due. Start with ${title}${company}.`
        : `${title}${company} is ready for a follow-up.`;

      const payload = JSON.stringify({
        title: "Time to follow up",
        body,
        jobId: firstId,
        tag: `followup-${firstId}`,
      });

      for (const sub of subs as SubRow[]) {
        try {
          await webpush.sendNotification(
            {
              endpoint: sub.endpoint,
              keys: { p256dh: sub.p256dh, auth: sub.auth_key },
            },
            payload,
          );
          notified++;
        } catch (err) {
          const statusCode = (err as { statusCode?: number })?.statusCode;
          // 404/410 = the subscription is gone (uninstalled, permission revoked,
          // browser storage cleared) — clean it up so we stop trying forever.
          if (statusCode === 404 || statusCode === 410) {
            await db.from("push_subscriptions").delete().eq("endpoint", sub.endpoint);
            staleRemoved++;
          } else {
            console.error("push send failed", statusCode, err);
            if (SENTRY_DSN) Sentry.captureException(err);
          }
        }
      }
    }

    return json({ notified, staleRemoved });
  } catch (err) {
    console.error("send-followup-push error", err);
    if (SENTRY_DSN) Sentry.captureException(err);
    return json({ error: "send_followup_push_error" }, 500);
  }
});
