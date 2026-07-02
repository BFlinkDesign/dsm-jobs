// Web Push subscription helper — a soft, best-effort enhancement layered on
// TOP of the existing in-app Notification follow-up reminders. It never
// replaces that fallback: if push isn't supported, the VAPID key isn't
// configured, permission is denied, or any step fails, this degrades to a
// clean no-op and the in-app Notification path (app.ts maybeNotifyFollowUps)
// keeps working exactly as it does today.
//
// Deliberately separate from the existing Notification permission prompt
// (#notifybtn in app.ts) — a user may grant one without the other, and iOS
// Safari's Push API has real platform constraints (PWA must be added to home
// screen, iOS 16.4+) that the plain Notification API doesn't share.

import type { SupabaseClient } from "@supabase/supabase-js";

// Uint8Array<ArrayBuffer> (not the default ArrayBufferLike) so this is a valid
// BufferSource for PushManager.subscribe's applicationServerKey under strict lib.
function base64UrlToUint8Array(base64Url: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** True only when every precondition for Web Push subscribing is met. */
export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    typeof Notification !== "undefined"
  );
}

/** Current permission for the Notification/Push permission (shared browser prompt). */
export function pushPermission(): NotificationPermission | "unsupported" {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}

/** Is there already an active push subscription for this browser? */
export async function hasActivePushSubscription(): Promise<boolean> {
  if (!pushSupported()) return false;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    return !!sub;
  } catch {
    return false;
  }
}

/**
 * Request push permission (if needed), subscribe via PushManager, and store
 * the subscription server-side so send-followup-push can reach this device.
 * Returns true only on full success; every failure path is a silent, logged
 * no-op — never throws, never blocks the caller's UI flow.
 */
export async function subscribeToPush(sb: SupabaseClient, vapidPublicKey: string): Promise<boolean> {
  if (!pushSupported() || !vapidPublicKey) return false;
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return false;

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToUint8Array(vapidPublicKey),
      });
    }

    const json = sub.toJSON();
    const p256dh = json.keys?.p256dh;
    const authKey = json.keys?.auth;
    if (!p256dh || !authKey) return false;

    const { error } = await sb.from("push_subscriptions").upsert({
      endpoint: sub.endpoint,
      p256dh,
      auth_key: authKey,
      user_agent: navigator.userAgent.slice(0, 300),
      last_seen: new Date().toISOString(),
    });
    return !error;
  } catch {
    return false;
  }
}

/** Best-effort unsubscribe (used if she signs out or explicitly turns push off). */
export async function unsubscribeFromPush(sb: SupabaseClient | null): Promise<void> {
  if (!pushSupported()) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return;
    const endpoint = sub.endpoint;
    await sub.unsubscribe();
    if (sb) {
      await sb.from("push_subscriptions").delete().eq("endpoint", endpoint);
    }
  } catch {
    /* best-effort only */
  }
}
