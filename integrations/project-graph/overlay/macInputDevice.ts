/** Application preference shared by graph frames on this browser origin; never enters PRG data.
 * Browsers cannot reliably tell a smooth mouse from a trackpad, so Mac users may choose.
 */
export type MacInputDevice = 'auto' | 'trackpad' | 'mouse';
export const macInputDeviceStorageKey = 'codeyun.pg.settings.mac-input-device';
const listeners = new Set<() => void>();
const parse = (value: string | null): MacInputDevice => value === 'trackpad' || value === 'mouse' ? value : 'auto';
let device: MacInputDevice = 'auto';
try { device = parse(localStorage.getItem(macInputDeviceStorageKey)); } catch { /* Session preference. */ }

export function macInputDevice() { return device; }
export function subscribeMacInputDevice(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function setMacInputDevice(value: MacInputDevice) {
  device = parse(value);
  try { localStorage.setItem(macInputDeviceStorageKey, device); } catch { /* Keep the session choice. */ }
  for (const listener of listeners) listener();
}
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.storageArea !== localStorage || (event.key !== macInputDeviceStorageKey && event.key !== null)) return;
  device = event.key === null ? 'auto' : parse(event.newValue);
  for (const listener of listeners) listener();
});
