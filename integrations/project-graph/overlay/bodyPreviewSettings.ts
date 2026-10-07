/** Application preference shared by graph frames on this browser origin.
 * It never enters PRG serialization. Missing/invalid preferences default off,
 * including existing installations with upstream alwaysShowDetails enabled.
 */
export const bodyPreviewStorageKey = 'codeyun.pg.settings.body-preview.enabled';
const listeners = new Set<() => void>();
let enabled = false;
try { enabled = localStorage.getItem(bodyPreviewStorageKey) === 'true'; } catch { /* Session preference. */ }

export function bodyPreviewEnabled() { return enabled; }
export function subscribeBodyPreview(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function setBodyPreviewEnabled(value: boolean) {
  enabled = value;
  try { localStorage.setItem(bodyPreviewStorageKey, String(value)); } catch { /* Keep the session choice. */ }
  for (const listener of listeners) listener();
}
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.storageArea !== localStorage || (event.key !== bodyPreviewStorageKey && event.key !== null)) return;
  enabled = event.key !== null && event.newValue === 'true';
  for (const listener of listeners) listener();
});
