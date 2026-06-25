import { createClient, type SupabaseClient, type User } from "@supabase/supabase-js";
import type { PortalCfg } from "./types";

export type AuthHandle = {
  sb: SupabaseClient;
  user: User;
};

let sb: SupabaseClient | null = null;

export function getClient(): SupabaseClient | null {
  return sb;
}

export async function initAuth(cfg: PortalCfg): Promise<SupabaseClient | null> {
  if (!cfg.url || !cfg.key) return null;
  sb = createClient(cfg.url, cfg.key, {
    auth: { experimental: { passkey: true } },
  });
  return sb;
}

export async function currentUser(client: SupabaseClient): Promise<User | null> {
  const { data } = await client.auth.getSession();
  return data.session?.user ?? null;
}

export async function signUp(client: SupabaseClient, email: string, password: string, redirectTo: string): Promise<string | null> {
  const { error } = await client.auth.signUp({
    email,
    password,
    options: { emailRedirectTo: redirectTo },
  });
  return error?.message ?? null;
}

export async function signIn(client: SupabaseClient, email: string, password: string): Promise<string | null> {
  const { error } = await client.auth.signInWithPassword({ email, password });
  return error?.message ?? null;
}

export async function signOut(client: SupabaseClient): Promise<void> {
  await client.auth.signOut();
}

export async function signInWithGoogle(client: SupabaseClient, redirectTo: string): Promise<string | null> {
  const { error } = await client.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo },
  });
  return error?.message ?? null;
}

export async function signInWithPasskey(client: SupabaseClient): Promise<string | null> {
  const auth = client.auth as SupabaseClient["auth"] & {
    signInWithPasskey?: () => Promise<{ error: { message: string } | null }>;
  };
  if (typeof auth.signInWithPasskey !== "function") {
    return "Passkeys aren't available in this browser.";
  }
  const { error } = await auth.signInWithPasskey();
  return error?.message ?? null;
}

export async function registerPasskey(client: SupabaseClient): Promise<string | null> {
  const auth = client.auth as SupabaseClient["auth"] & {
    registerPasskey?: () => Promise<{ error: { message: string } | null }>;
  };
  if (typeof auth.registerPasskey !== "function") {
    return "Passkeys aren't available in this browser.";
  }
  const { error } = await auth.registerPasskey();
  return error?.message ?? null;
}

export async function signInWithMagicLink(client: SupabaseClient, email: string, redirectTo: string): Promise<string | null> {
  const { error } = await client.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: redirectTo },
  });
  return error?.message ?? null;
}

export async function resetPasswordForEmail(client: SupabaseClient, email: string, redirectTo: string): Promise<string | null> {
  const { error } = await client.auth.resetPasswordForEmail(email, { redirectTo });
  return error?.message ?? null;
}

export async function updatePassword(client: SupabaseClient, password: string): Promise<string | null> {
  const { error } = await client.auth.updateUser({ password });
  return error?.message ?? null;
}

/** Show Google sign-in only when the project actually enables that provider. */
export async function fetchGoogleAuthEnabled(cfg: PortalCfg): Promise<boolean> {
  if (!cfg.url || !cfg.key) return false;
  try {
    const r = await fetch(`${cfg.url}/auth/v1/settings`, { headers: { apikey: cfg.key } });
    const s = (await r.json()) as { external?: { google?: boolean } };
    return !!s?.external?.google;
  } catch {
    return false;
  }
}

export function supportsPasskey(client: SupabaseClient | null): boolean {
  if (!client || typeof window === "undefined") return false;
  if (!window.PublicKeyCredential) return false;
  const auth = client.auth as SupabaseClient["auth"] & { signInWithPasskey?: unknown };
  return typeof auth.signInWithPasskey === "function";
}

export function friendlyAuthError(error: unknown): string {
  const m = String((error as { message?: string })?.message || error || "");
  if (/passkey|webauthn|credential/i.test(m) && /no|not found|none/i.test(m)) {
    return "No passkey found on this device yet. Sign in another way first, then add Face ID below.";
  }
  if (/already registered|user already/i.test(m)) return "You already have an account — try signing in instead.";
  if (/invalid login|invalid credentials|wrong/i.test(m)) return "That email and password don't match. Try again.";
  if (/email not confirmed|confirm/i.test(m)) return "Check your email and tap the confirm link first, then sign in.";
  if (/rate|too many/i.test(m)) return "Too many tries — wait a minute, then try again.";
  if (/fetch|network|load failed/i.test(m)) return "No internet right now — your saves are safe on this phone.";
  return (m || "Something went wrong").slice(0, 110);
}
