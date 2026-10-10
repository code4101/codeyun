import { isDesktopMacBrowser } from './browserPlatform.ts';

/** Wheel/trackpad camera multipliers shared by graph frames on this browser origin; never enter PRG data.
 * Scroll scales every wheel/trackpad pan (including deltaMode lines/pages); zoom scales pinch and wheel zoom.
 * 1 keeps upstream behaviour. Desktop Mac defaults differ: its pinch base (1.08^delta) is far above the
 * 1.01-1.05 range the upstream comment calls reasonable, and two-finger pan only moves 0.25 px per delta px.
 */
export type SensitivityKey = 'scroll' | 'zoom';
export const sensitivityRange: Record<SensitivityKey, { min: number; max: number; step: number }> = {
  scroll: { min: 0.25, max: 4, step: 0.05 },
  zoom: { min: 0.2, max: 3, step: 0.05 },
};
export const sensitivityDefaults: Record<SensitivityKey, number> = isDesktopMacBrowser ? { scroll: 2, zoom: 0.6 } : { scroll: 1, zoom: 1 };
export const sensitivityStorageKeys: Record<SensitivityKey, string> = {
  scroll: 'codeyun.pg.settings.scroll-sensitivity',
  zoom: 'codeyun.pg.settings.zoom-sensitivity',
};
const keys = Object.keys(sensitivityStorageKeys) as SensitivityKey[];
const listeners = new Set<() => void>();

export function clampSensitivity(key: SensitivityKey, value: unknown) {
  const { min, max } = sensitivityRange[key];
  const number = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
  return Number.isFinite(number) ? Math.min(max, Math.max(min, Math.round(number * 100) / 100)) : sensitivityDefaults[key];
}
let values: Record<SensitivityKey, number> = { ...sensitivityDefaults };
const read = (key: SensitivityKey, raw: string | null) => raw === null ? sensitivityDefaults[key] : clampSensitivity(key, raw);
try { for (const key of keys) values[key] = read(key, localStorage.getItem(sensitivityStorageKeys[key])); } catch { /* Session preference. */ }
// Snapshot identity changes only on updates so useSyncExternalStore stays stable.
export function sensitivitySnapshot() { return values; }
export const scrollSensitivity = () => values.scroll;
export const zoomSensitivity = () => values.zoom;
export function subscribeSensitivity(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
const notify = () => { values = { ...values }; for (const listener of listeners) listener(); };
/** A null value (or reset) restores the platform default and removes the stored override. */
export function setSensitivity(key: SensitivityKey, value: number | null) {
  const next = value === null ? sensitivityDefaults[key] : clampSensitivity(key, value);
  values[key] = next;
  try {
    if (value === null) localStorage.removeItem(sensitivityStorageKeys[key]);
    else localStorage.setItem(sensitivityStorageKeys[key], String(next));
  } catch { /* Keep the session choice. */ }
  notify();
}
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.storageArea !== localStorage) return;
  if (event.key === null) values = { ...sensitivityDefaults };
  else {
    const key = keys.find(candidate => sensitivityStorageKeys[candidate] === event.key);
    if (!key) return;
    values[key] = read(key, event.newValue);
  }
  notify();
});

/** Pixels for a wheel delta: deltaMode 1 is lines, 2 is pages (https://developer.mozilla.org/en-US/docs/Web/API/WheelEvent/deltaMode). */
export function wheelPixels(delta: number, deltaMode = 0, pageSize = typeof innerHeight === 'number' ? innerHeight : 800) {
  return deltaMode === 1 ? delta * 16 : deltaMode === 2 ? delta * pageSize : delta;
}
