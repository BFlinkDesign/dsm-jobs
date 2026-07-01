import type { SupabaseClient } from "@supabase/supabase-js";

const DB_NAME = "dsm-jobs-outbox";
const STORE_NAME = "ops";
const DB_VERSION = 1;

export type OutboxKind = "profile" | "note" | "chat" | "chat_clear";

export type OutboxOperation = {
  key: string;
  kind: OutboxKind;
  userId: string;
  payload: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  attempts: number;
};

export type OutboxDraft = {
  key: string;
  kind: OutboxKind;
  userId: string;
  payload: Record<string, unknown>;
};

type OutboxHandler = (op: OutboxOperation, client: SupabaseClient) => Promise<boolean>;

function supportsIndexedDb(): boolean {
  return typeof indexedDB !== "undefined";
}

function openOutbox(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!supportsIndexedDb()) {
      reject(new Error("IndexedDB is not available"));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "key" });
        store.createIndex("userId", "userId", { unique: false });
      }
    };
    req.onerror = () => reject(req.error ?? new Error("Could not open outbox"));
    req.onsuccess = () => resolve(req.result);
  });
}

async function readAllOps(): Promise<OutboxOperation[]> {
  const db = await openOutbox();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.getAll();
    req.onerror = () => reject(req.error ?? new Error("Could not read outbox"));
    req.onsuccess = () => resolve((req.result ?? []) as OutboxOperation[]);
    tx.oncomplete = () => db.close();
    tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error("Could not read outbox"));
    };
  });
}

async function writeOp(op: OutboxOperation): Promise<void> {
  const db = await openOutbox();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put(op);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error("Could not write outbox op"));
    };
  });
}

async function deleteOp(key: string): Promise<void> {
  const db = await openOutbox();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(key);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error("Could not delete outbox op"));
    };
  });
}

async function notifyOutboxChanged(userId?: string): Promise<void> {
  if (typeof window === "undefined") return;
  const count = await pendingOutboxCount(userId);
  window.dispatchEvent(new CustomEvent("dsm-jobs-outbox-change", { detail: { count } }));
}

export async function enqueueOutbox(draft: OutboxDraft): Promise<void> {
  const now = new Date().toISOString();
  const existing = (await readAllOps()).find((op) => op.key === draft.key);
  await writeOp({
    ...draft,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    attempts: existing?.attempts ?? 0,
  });
  await notifyOutboxChanged(draft.userId);
}

export async function pendingOutboxCount(userId?: string): Promise<number> {
  try {
    const ops = await readAllOps();
    return userId ? ops.filter((op) => op.userId === userId).length : ops.length;
  } catch {
    return 0;
  }
}

export async function drainOutbox(
  client: SupabaseClient,
  userId: string,
  handler: OutboxHandler
): Promise<number> {
  const ops = (await readAllOps())
    .filter((op) => op.userId === userId)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  let drained = 0;
  for (const op of ops) {
    const ok = await handler(op, client);
    if (!ok) {
      await writeOp({ ...op, attempts: op.attempts + 1, updatedAt: new Date().toISOString() });
      break;
    }
    await deleteOp(op.key);
    drained += 1;
  }
  if (drained > 0 || ops.length > 0) await notifyOutboxChanged(userId);
  return drained;
}
