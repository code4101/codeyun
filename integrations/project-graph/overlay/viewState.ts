export interface ViewState { scale: number; x: number; y: number }
export function validViewState(value: unknown): value is ViewState {
  if (!value || typeof value !== 'object') return false;
  const state = value as ViewState;
  return Number.isFinite(state.scale) && state.scale >= 1e-10 && state.scale <= 1e10 &&
    Number.isFinite(state.x) && Number.isFinite(state.y);
}
/** Personal view only: never part of PRG content, revisions or undo history. */
export function createViewStateStore(key: string, storage: Storage) {
  let last = '';
  return {
    read(): ViewState | undefined {
      try { const value = JSON.parse(storage.getItem(key) ?? 'null'); if (validViewState(value)) { last = JSON.stringify(value); return value; } } catch { /* Invalid/unavailable browser storage. */ }
    },
    write(value: ViewState) {
      if (!validViewState(value)) return;
      const text = JSON.stringify(value);
      if (text === last) return;
      try { storage.setItem(key, text); last = text; } catch { /* Viewing remains available without storage. */ }
    },
  };
}
