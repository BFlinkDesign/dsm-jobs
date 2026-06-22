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
  sb = createClient(cfg.url, cfg.key);
  return sb;
}

export async function currentUser(client: SupabaseClient): Promise<User | null> {
  const { data } = await client.auth.getSession();
  return data.session?.user ?? null;
}

export async function signUp(client: SupabaseClient, email: string, password: string): Promise<string | null> {
  const { error } = await client.auth.signUp({ email, password });
  return error?.message ?? null;
}

export async function signIn(client: SupabaseClient, email: string, password: string): Promise<string | null> {
  const { error } = await client.auth.signInWithPassword({ email, password });
  return error?.message ?? null;
}

export async function signOut(client: SupabaseClient): Promise<void> {
  await client.auth.signOut();
}
