/** Presentation boundary: the provider still resolves configured shortcuts and pages. */
type Hint = { displayKey: string; title: string };
let enabled = false;
let keys: string[] = [];
let items: Hint[] = [];
let page = '';
let previous = '';
let publish: (value: { keys: string[]; items: Hint[]; page: string }) => void = () => {};
function emit() {
  const value = { keys, items, page };
  const next = JSON.stringify(value);
  if (next !== previous) { previous = next; publish(value); }
}
export function configureSharedKeyboardHints(active: boolean, callback?: typeof publish) {
  enabled = active;
  if (callback) publish = callback;
  keys = []; items = []; page = ''; previous = ''; emit();
}
export function relayShortcutHints(value: Hint[], pagination = '') {
  if (!enabled) return false;
  items = value.map(({ displayKey, title }) => ({ displayKey, title })); page = pagination; emit();
  return true;
}
export function relayPressedKeys(value: string[]) {
  if (!enabled) return false;
  keys = value.map(key => key === ' ' ? '␣' : key); emit();
  return true;
}
