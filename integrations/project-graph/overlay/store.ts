/** Browser replacement for Tauri's settings store; document storage belongs to the host. */
export class LazyStore {
  constructor(private name: string) {}
  async init() {}
  async get<T>(key: string): Promise<T | undefined> {
    const value = localStorage.getItem(`codeyun.pg.settings.${this.name}.${key}`);
    return value === null ? undefined : JSON.parse(value);
  }
  async set(key: string, value: unknown) { localStorage.setItem(`codeyun.pg.settings.${this.name}.${key}`, JSON.stringify(value)); }
  async delete(key: string) { localStorage.removeItem(`codeyun.pg.settings.${this.name}.${key}`); return true; }
  async entries() {
    const prefix = `codeyun.pg.settings.${this.name}.`;
    return Object.keys(localStorage).filter(key => key.startsWith(prefix)).map(key => [key.slice(prefix.length), JSON.parse(localStorage.getItem(key)!) ]);
  }
  async save() {}
  async close() {}
  async onChange() { return () => {}; }
  async onKeyChange() { return () => {}; }
}
export { LazyStore as Store };
export async function load(name: string) { return new LazyStore(name); }
