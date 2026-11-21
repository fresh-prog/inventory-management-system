// Minimal IndexedDB key-value store for device-local drafts (survive reloads, crashes and
// lost connections). Drafts are NOT posted transactions: they are clearly labelled as such.
const DB = "kitchenledger";
const STORE = "drafts";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const r = fn(t.objectStore(STORE));
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

export const drafts = {
  get: <T>(key: string) => tx<T | undefined>("readonly", (s) => s.get(key)).catch(() => undefined),
  set: (key: string, value: unknown) => tx("readwrite", (s) => s.put(value, key)).catch(() => undefined),
  del: (key: string) => tx("readwrite", (s) => s.delete(key)).catch(() => undefined),
  keys: () => tx<IDBValidKey[]>("readonly", (s) => s.getAllKeys()).catch(() => [] as IDBValidKey[]),
};
