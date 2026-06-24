import { computed, onBeforeUnmount, ref } from 'vue';

export type AutoSaveStatus = 'saved' | 'saving' | 'unsaved';

interface AutoSaveDraftPayload<T> {
  updatedAt: number;
  snapshot: T;
  baselineSnapshot: T | null;
}

export interface AutoSaveDraftCandidate<T> {
  updatedAt: number;
  snapshot: T;
  hasConflict: boolean;
}

interface UseAutoSaveOptions<T> {
  debounceMs?: number;
  draftTtlMs?: number;
  clone?: (value: T) => T;
  equals: (left: T, right: T) => boolean;
  save: (snapshot: T) => Promise<T | void | null>;
  storageKey?: () => string | null;
  saveOnPageHide?: (snapshot: T, baselineSnapshot: T | null) => void;
  onError?: (error: unknown) => void;
}

const canUseLocalStorage = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';

const defaultClone = <T>(value: T): T => JSON.parse(JSON.stringify(value));

export const useAutoSave = <T>(options: UseAutoSaveOptions<T>) => {
  const clone = options.clone ?? defaultClone<T>;
  const debounceMs = options.debounceMs ?? 2000;
  const draftTtlMs = options.draftTtlMs ?? 1000 * 60 * 60 * 24 * 7;

  const saveStatus = ref<AutoSaveStatus>('saved');
  const lastDraftAt = ref<number | null>(null);

  let baselineSnapshot: T | null = null;
  let latestSnapshot: T | null = null;
  let changeVersion = 0;
  let savedVersion = 0;
  let saveTimer: ReturnType<typeof setTimeout> | null = null;
  let activeFlush: Promise<boolean> | null = null;

  const clearSaveTimer = () => {
    if (!saveTimer) return;
    clearTimeout(saveTimer);
    saveTimer = null;
  };

  const readDraft = (): AutoSaveDraftPayload<T> | null => {
    const key = options.storageKey?.();
    if (!key || !canUseLocalStorage()) return null;

    try {
      const raw = window.localStorage.getItem(key);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as AutoSaveDraftPayload<T>;
      if (!parsed?.snapshot) return null;
      lastDraftAt.value = typeof parsed.updatedAt === 'number' ? parsed.updatedAt : null;
      return {
        updatedAt: lastDraftAt.value ?? Date.now(),
        snapshot: clone(parsed.snapshot),
        baselineSnapshot: parsed.baselineSnapshot ? clone(parsed.baselineSnapshot) : null
      };
    } catch {
      return null;
    }
  };

  const persistDraft = (snapshot: T | null) => {
    const key = options.storageKey?.();
    if (!key || !canUseLocalStorage()) return;

    if (!snapshot) {
      window.localStorage.removeItem(key);
      lastDraftAt.value = null;
      return;
    }

    try {
      const payload: AutoSaveDraftPayload<T> = {
        updatedAt: Date.now(),
        snapshot: clone(snapshot),
        baselineSnapshot: baselineSnapshot ? clone(baselineSnapshot) : null
      };
      window.localStorage.setItem(key, JSON.stringify(payload));
      lastDraftAt.value = payload.updatedAt;
    } catch {
      // Ignore storage quota errors; autosave still works in memory.
    }
  };

  const clearDraft = () => persistDraft(null);

  const hasUnsavedChanges = computed(() => {
    if (!latestSnapshot || !baselineSnapshot) return false;
    return !options.equals(latestSnapshot, baselineSnapshot);
  });

  const getLatestSnapshot = () => latestSnapshot ? clone(latestSnapshot) : null;
  const getBaselineSnapshot = () => baselineSnapshot ? clone(baselineSnapshot) : null;

  const syncCleanState = (snapshot: T) => {
    baselineSnapshot = clone(snapshot);
    latestSnapshot = clone(snapshot);
    saveStatus.value = 'saved';
    clearDraft();
  };

  const loadSnapshot = (
    snapshot: T | null,
    optionsOverride: { draftStrategy?: 'prompt' | 'auto' | 'discard' } = {}
  ) => {
    clearSaveTimer();
    saveStatus.value = 'saved';
    changeVersion = 0;
    savedVersion = 0;

    if (!snapshot) {
      baselineSnapshot = null;
      latestSnapshot = null;
      clearDraft();
      return {
        snapshot: null as T | null,
        restored: false,
        draftUpdatedAt: null as number | null,
        pendingDraft: null as AutoSaveDraftCandidate<T> | null,
        expiredDraft: false
      };
    }

    baselineSnapshot = clone(snapshot);
    latestSnapshot = clone(snapshot);

    const draftPayload = readDraft();
    if (draftPayload) {
      const isExpired = Date.now() - draftPayload.updatedAt > draftTtlMs;
      if (isExpired) {
        clearDraft();
        return {
          snapshot: clone(latestSnapshot),
          restored: false,
          draftUpdatedAt: draftPayload.updatedAt,
          pendingDraft: null as AutoSaveDraftCandidate<T> | null,
          expiredDraft: true
        };
      }

      if (!options.equals(draftPayload.snapshot, baselineSnapshot)) {
        const pendingDraft: AutoSaveDraftCandidate<T> = {
          updatedAt: draftPayload.updatedAt,
          snapshot: clone(draftPayload.snapshot),
          hasConflict: draftPayload.baselineSnapshot
            ? !options.equals(draftPayload.baselineSnapshot, baselineSnapshot)
            : true
        };

        if (optionsOverride.draftStrategy === 'auto') {
          latestSnapshot = clone(pendingDraft.snapshot);
          changeVersion = 1;
          saveStatus.value = 'unsaved';
          persistDraft(latestSnapshot);
          return {
            snapshot: clone(latestSnapshot),
            restored: true,
            draftUpdatedAt: pendingDraft.updatedAt,
            pendingDraft: null as AutoSaveDraftCandidate<T> | null,
            expiredDraft: false
          };
        }

        if (optionsOverride.draftStrategy === 'discard') {
          clearDraft();
          return {
            snapshot: clone(latestSnapshot),
            restored: false,
            draftUpdatedAt: pendingDraft.updatedAt,
            pendingDraft: null as AutoSaveDraftCandidate<T> | null,
            expiredDraft: false
          };
        }

        return {
          snapshot: clone(latestSnapshot),
          restored: false,
          draftUpdatedAt: pendingDraft.updatedAt,
          pendingDraft,
          expiredDraft: false
        };
      }
    }

    clearDraft();
    return {
      snapshot: clone(latestSnapshot),
      restored: false,
      draftUpdatedAt: null as number | null,
      pendingDraft: null as AutoSaveDraftCandidate<T> | null,
      expiredDraft: false
    };
  };

  const restoreDraft = (snapshot: T) => {
    latestSnapshot = clone(snapshot);
    changeVersion = Math.max(changeVersion + 1, 1);
    saveStatus.value = 'unsaved';
    persistDraft(latestSnapshot);
  };

  const markDirty = (snapshot: T | null, markOptions: { immediate?: boolean; delayMs?: number } = {}) => {
    clearSaveTimer();

    if (!snapshot) {
      latestSnapshot = null;
      saveStatus.value = 'saved';
      clearDraft();
      return;
    }

    latestSnapshot = clone(snapshot);

    if (baselineSnapshot && options.equals(latestSnapshot, baselineSnapshot)) {
      saveStatus.value = 'saved';
      clearDraft();
      return;
    }

    changeVersion += 1;
    saveStatus.value = 'unsaved';
    persistDraft(latestSnapshot);

    const delayMs = markOptions.immediate ? 0 : Math.max(0, markOptions.delayMs ?? debounceMs);
    saveTimer = setTimeout(() => {
      void flush();
    }, delayMs);
  };

  const flush = async () => {
    clearSaveTimer();

    if (!latestSnapshot) return true;
    if (baselineSnapshot && options.equals(latestSnapshot, baselineSnapshot)) {
      saveStatus.value = 'saved';
      clearDraft();
      return true;
    }

    if (activeFlush) {
      await activeFlush;
      return !latestSnapshot || !baselineSnapshot ? false : options.equals(latestSnapshot, baselineSnapshot);
    }

    activeFlush = (async () => {
      while (latestSnapshot && (!baselineSnapshot || !options.equals(latestSnapshot, baselineSnapshot))) {
        const snapshotToSave = clone(latestSnapshot);
        const versionToSave = changeVersion;
        saveStatus.value = 'saving';

        try {
          const savedSnapshot = await options.save(snapshotToSave);
          const canonicalSnapshot = clone((savedSnapshot ?? snapshotToSave) as T);
          baselineSnapshot = canonicalSnapshot;
          savedVersion = Math.max(savedVersion, versionToSave);

          if (versionToSave === changeVersion) {
            syncCleanState(canonicalSnapshot);
            break;
          }

          saveStatus.value = 'unsaved';
          persistDraft(latestSnapshot);
        } catch (error) {
          saveStatus.value = 'unsaved';
          persistDraft(latestSnapshot);
          options.onError?.(error);
          return false;
        }
      }

      return !!latestSnapshot && !!baselineSnapshot && options.equals(latestSnapshot, baselineSnapshot);
    })();

    try {
      return await activeFlush;
    } finally {
      activeFlush = null;
    }
  };

  const persistCurrentDraft = () => {
    if (!latestSnapshot) return;
    if (baselineSnapshot && options.equals(latestSnapshot, baselineSnapshot)) {
      clearDraft();
      return;
    }
    persistDraft(latestSnapshot);
  };

  const handlePageHide = () => {
    persistCurrentDraft();
    if (saveStatus.value === 'unsaved') {
      if (latestSnapshot) {
        options.saveOnPageHide?.(clone(latestSnapshot), baselineSnapshot ? clone(baselineSnapshot) : null);
      }
      void flush();
    }
  };

  const handleVisibilityChange = () => {
    if (document.visibilityState === 'hidden') {
      handlePageHide();
    }
  };

  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', handlePageHide);
    document.addEventListener('visibilitychange', handleVisibilityChange);
  }

  onBeforeUnmount(() => {
    clearSaveTimer();
    persistCurrentDraft();
    if (saveStatus.value === 'unsaved') {
      void flush();
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('pagehide', handlePageHide);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    }
  });

  return {
    saveStatus,
    lastDraftAt,
    hasUnsavedChanges,
    loadSnapshot,
    restoreDraft,
    markDirty,
    flush,
    clearDraft,
    getLatestSnapshot,
    getBaselineSnapshot
  };
};
