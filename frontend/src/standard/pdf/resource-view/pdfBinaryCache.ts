const DB_NAME = 'codeyun-pdf-reader';
const DB_VERSION = 1;
const STORE_NAME = 'files';
const OPEN_TIMEOUT_MS = 1500;
const MAX_TOTAL_BYTES = 200 * 1024 * 1024;
const MAX_FILE_BYTES = 100 * 1024 * 1024;
const MAX_FILES = 3;
const TTL_MS = 14 * 24 * 60 * 60 * 1000;

interface PdfRecord {
  key: string;
  data: Uint8Array;
  size: number;
  updatedAt: number;
  lastAccess: number;
}

interface PruneEntry {
  key: string;
  size: number;
  updatedAt: number;
  lastAccess: number;
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

function getFactory(): IDBFactory | null {
  try {
    if (typeof indexedDB === 'undefined') {
      return null;
    }
    return indexedDB;
  } catch {
    return null;
  }
}

function resetDb(): void {
  dbPromise = null;
}

function getDb(): Promise<IDBDatabase | null> {
  if (!dbPromise) {
    dbPromise = openDatabase();
  }
  return dbPromise;
}

function openDatabase(): Promise<IDBDatabase | null> {
  const factory = getFactory();
  if (!factory) {
    return Promise.resolve(null);
  }

  return new Promise<IDBDatabase | null>((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const settle = (db: IDBDatabase | null): void => {
      if (settled) {
        closeQuietly(db);
        return;
      }
      settled = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      resolve(db);
    };

    timer = setTimeout(() => {
      timer = null;
      settle(null);
    }, OPEN_TIMEOUT_MS);

    let request: IDBOpenDBRequest;
    try {
      request = factory.open(DB_NAME, DB_VERSION);
    } catch {
      settle(null);
      return;
    }

    request.onupgradeneeded = () => {
      try {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'key' });
        }
      } catch {}
    };

    request.onerror = () => {
      settle(null);
    };

    request.onblocked = () => {
      settle(null);
    };

    request.onsuccess = () => {
      const db = request.result;
      if (settled) {
        closeQuietly(db);
        return;
      }
      db.onversionchange = () => {
        closeQuietly(db);
        resetDb();
      };
      db.onclose = () => {
        resetDb();
      };
      settle(db);
    };
  });
}

function closeQuietly(db: IDBDatabase | null): void {
  if (!db) {
    return;
  }
  try {
    db.close();
  } catch {}
}

function awaitTransaction(tx: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve) => {
    let done = false;
    const finish = (): void => {
      if (done) {
        return;
      }
      done = true;
      resolve();
    };
    tx.oncomplete = finish;
    tx.onabort = finish;
    tx.onerror = finish;
  });
}

function toUint8(value: unknown): Uint8Array | null {
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  return null;
}

function isUsableRecord(value: unknown): value is PdfRecord {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const record = value as Partial<PdfRecord>;
  if (typeof record.key !== 'string') {
    return false;
  }
  if (typeof record.size !== 'number' || !Number.isFinite(record.size) || record.size < 0) {
    return false;
  }
  if (typeof record.updatedAt !== 'number' || !Number.isFinite(record.updatedAt)) {
    return false;
  }
  if (typeof record.lastAccess !== 'number' || !Number.isFinite(record.lastAccess)) {
    return false;
  }
  return toUint8(record.data) !== null;
}

function safeDelete(store: IDBObjectStore, key: IDBValidKey): void {
  try {
    store.delete(key);
  } catch {}
}

export async function readPdfBinary(key: string): Promise<Uint8Array | null> {
  if (typeof key !== 'string' || key.length === 0) {
    return null;
  }

  const db = await getDb();
  if (!db) {
    return null;
  }

  let tx: IDBTransaction;
  try {
    tx = db.transaction(STORE_NAME, 'readwrite');
  } catch {
    resetDb();
    return null;
  }

  let result: Uint8Array | null = null;
  try {
    const store = tx.objectStore(STORE_NAME);
    const request = store.get(key);
    request.onsuccess = () => {
      try {
        const record: unknown = request.result;
        if (!isUsableRecord(record)) {
          if (record) {
            safeDelete(store, key);
          }
          return;
        }
        const now = Date.now();
        if (now - record.updatedAt > TTL_MS) {
          safeDelete(store, key);
          return;
        }
        const bytes = toUint8(record.data);
        if (!bytes) {
          safeDelete(store, key);
          return;
        }
        result = bytes;
        record.lastAccess = now;
        try {
          store.put(record);
        } catch {}
      } catch {
        result = null;
      }
    };
    request.onerror = () => {};
  } catch {
    return null;
  }

  await awaitTransaction(tx);
  return result;
}

export async function writePdfBinary(key: string, data: Uint8Array): Promise<void> {
  if (typeof key !== 'string' || key.length === 0) {
    return;
  }
  if (!(data instanceof Uint8Array)) {
    return;
  }

  let sourceSize: number;
  try {
    sourceSize = data.byteLength;
  } catch {
    return;
  }
  if (sourceSize <= 0 || sourceSize > MAX_FILE_BYTES) {
    return;
  }

  let copy: Uint8Array;
  try {
    copy = new Uint8Array(data);
  } catch {
    return;
  }
  if (copy.byteLength !== sourceSize) {
    return;
  }

  const db = await getDb();
  if (!db) {
    return;
  }

  let tx: IDBTransaction;
  try {
    tx = db.transaction(STORE_NAME, 'readwrite');
  } catch {
    resetDb();
    return;
  }

  const now = Date.now();
  const record: PdfRecord = {
    key,
    data: copy,
    size: copy.byteLength,
    updatedAt: now,
    lastAccess: now,
  };

  try {
    const store = tx.objectStore(STORE_NAME);
    const entries: PruneEntry[] = [];
    const cursorRequest = store.openCursor();
    cursorRequest.onsuccess = () => {
      const cursor = cursorRequest.result;
      if (!cursor) {
        pruneAndPut(store, entries, record);
        return;
      }
      const value = cursor.value as Partial<PdfRecord> | null;
      if (value && typeof value.key === 'string') {
        entries.push({
          key: value.key,
          size: typeof value.size === 'number' && Number.isFinite(value.size) ? value.size : 0,
          updatedAt: typeof value.updatedAt === 'number' && Number.isFinite(value.updatedAt) ? value.updatedAt : 0,
          lastAccess: typeof value.lastAccess === 'number' && Number.isFinite(value.lastAccess) ? value.lastAccess : 0,
        });
      }
      cursor.continue();
    };
    cursorRequest.onerror = () => {};
  } catch {
    return;
  }

  await awaitTransaction(tx);
}

function pruneAndPut(store: IDBObjectStore, entries: PruneEntry[], record: PdfRecord): void {
  const now = record.updatedAt;
  let total = record.size;
  const survivors: PruneEntry[] = [];

  for (const entry of entries) {
    if (entry.key === record.key) {
      continue;
    }
    if (now - entry.updatedAt > TTL_MS) {
      safeDelete(store, entry.key);
      continue;
    }
    total += entry.size;
    survivors.push(entry);
  }

  survivors.sort((a, b) => a.lastAccess - b.lastAccess);

  while (survivors.length > MAX_FILES - 1) {
    const victim = survivors.shift();
    if (!victim) {
      break;
    }
    safeDelete(store, victim.key);
    total -= victim.size;
  }

  while (total > MAX_TOTAL_BYTES && survivors.length > 0) {
    const victim = survivors.shift();
    if (!victim) {
      break;
    }
    safeDelete(store, victim.key);
    total -= victim.size;
  }

  try {
    store.put(record);
  } catch {}
}

export async function deletePdfBinary(key: string): Promise<void> {
  if (typeof key !== 'string' || key.length === 0) {
    return;
  }

  const db = await getDb();
  if (!db) {
    return;
  }

  let tx: IDBTransaction;
  try {
    tx = db.transaction(STORE_NAME, 'readwrite');
  } catch {
    resetDb();
    return;
  }

  try {
    tx.objectStore(STORE_NAME).delete(key);
  } catch {
    return;
  }

  await awaitTransaction(tx);
}
