/** Shared client contract tests with a non-graph document and controlled transport.
 * The real JWT/SQLite/WebSocket path is tested by backend/test_object_collaboration.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript');

function fixture(initial = {}) {
  const storage = new Map(), sockets = [], events = [];
  let working = structuredClone(initial), server = structuredClone(initial), revision = 0, editable = false, rejectLease = false, hold = false;
  const held = [];
  const storageAPI = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  class Socket {
    static OPEN = 1;
    readyState = 1;
    constructor() { sockets.push(this); queueMicrotask(() => this.onopen?.()); }
    deliver(value) { queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(value) })); }
    send(value) {
      const m = JSON.parse(value);
      if (m.type === 'auth') this.deliver({ type: 'joined', peerId: 'fixture', revision, objects: server, accepted: [] });
      if (m.type === 'acquire') this.deliver(rejectLease ? { type: 'error', id: m.id, code: 423, detail: '他人正在编辑' } :
        { type: 'locked', id: m.id, tokens: Object.fromEntries(m.objects.map(key => [key, 'lease'])) });
      if (m.type === 'commit') {
        for (const c of m.changes) {
          assert.deepEqual(server[c.id] ?? null, c.before);
          if (c.after === null) delete server[c.id]; else server[c.id] = c.after;
        }
        const ack = { type: 'commit', id: m.id, mutationId: m.mutationId, revision: ++revision, changes: m.changes };
        if (hold) held.push(() => this.deliver(ack)); else this.deliver(ack);
      }
      if (m.type === 'renew') this.deliver({type: 'renewed', id: m.id, tokens: m.tokens});
    }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
  }
  const context = { console, structuredClone, crypto, setTimeout, clearTimeout, setInterval, clearInterval,
    sessionStorage: storageAPI, navigator: {onLine: true}, window: new EventTarget(), WebSocket: Socket };
  function load(name, dependencies = {}) {
    const file = path.resolve(__dirname, '../src/collaboration', name);
    const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    }}).outputText;
    const module = { exports: {} };
    vm.runInNewContext(source, { ...context, module, exports: module.exports, require: id => dependencies[id] }, {filename: file});
    return module.exports;
  }
  const state = load('objectState.ts'), {ObjectSession} = load('ObjectSession.ts', {'./objectState.ts': state});
  class ParagraphSession extends ObjectSession {
    // This is the provider's explicit "export draft and resync" action.
    discardAfterExport() { this.resetFromServer(); }
  }
  const session = new ParagraphSession(false, async () => ({url: 'ws://fixture', token: 'fixture'}), 'paragraph-draft',
    (type, value) => events.push({type, value}), {
      capture: async () => structuredClone(working), apply: (_before, after) => { working = structuredClone(after); },
      setEditable: value => { editable = value; }, setSaveState() {},
    });
  return { session, storage, events, sockets, working: () => working, server: () => server, editable: () => editable,
    edit: (key, text) => { working[key] = {text}; session.markDirty(); }, rejectLease: value => { rejectLease = value; },
    remote: (key, text) => {
      const change = {id: key, before: server[key], after: {text}}; server[key] = change.after;
      sockets.at(-1).deliver({type: 'commit', mutationId: 'other-user', revision: ++revision, changes: [change]});
    },
    hold: () => { hold = true; }, release: () => { hold = false; held.splice(0).forEach(send => send()); } };
}
async function until(check) {
  const start = Date.now();
  while (!check()) { if (Date.now() - start > 4000) throw Error('condition timeout'); await new Promise(r => setTimeout(r, 10)); }
}

test('empty paragraph document supports save and own undo without graph metadata', async () => {
  const f = fixture(); f.session.start();
  try {
    await until(f.editable); f.edit('paragraph-1', 'hello'); await f.session.flush();
    assert.deepEqual(f.server(), {'paragraph-1': {text: 'hello'}});
    await f.session.undo(); assert.deepEqual(f.server(), {});
    await f.session.redo(); assert.deepEqual(f.server(), {'paragraph-1': {text: 'hello'}});
    assert.equal(f.storage.size, 0);
  } finally { f.session.dispose(); }
});

test('conflict recovery exports draft then actually resets rather than recreating it on close', async () => {
  const f = fixture({'paragraph-1': {text: 'server'}}); f.session.start();
  try {
    await until(f.editable); f.rejectLease(true); f.edit('paragraph-1', 'draft');
    await assert.rejects(f.session.flush(), /他人正在编辑/);
    assert.ok(f.storage.size); assert.equal(f.editable(), false);
    const exported = structuredClone(f.working()); // Provider successfully preserves its format first.
    f.rejectLease(false); f.session.discardAfterExport();
    await until(() => f.sockets.length > 1 && f.editable());
    assert.equal(exported['paragraph-1'].text, 'draft');
    assert.equal(f.working()['paragraph-1'].text, 'server');
    assert.equal(f.storage.size, 0);
  } finally { f.session.dispose(); }
});

test('navigation flush drains edits made during an in-flight ACK', async () => {
  const f = fixture({'paragraph-1': {text: 'start'}}); f.session.start();
  try {
    await until(f.editable); f.hold(); f.edit('paragraph-1', 'first');
    const flushed = f.session.flush();
    await until(() => f.server()['paragraph-1'].text === 'first');
    f.edit('paragraph-1', 'latest'); f.release(); await flushed;
    assert.equal(f.server()['paragraph-1'].text, 'latest');
    assert.equal(f.session.hasUnsaved, false);
  } finally { f.session.dispose(); }
});

test('undo refuses to overwrite another user update to the same paragraph', async () => {
  const f = fixture({'paragraph-1': {text: 'start'}}); f.session.start();
  try {
    await until(f.editable); f.edit('paragraph-1', 'mine'); await f.session.flush();
    f.remote('paragraph-1', 'theirs'); await until(() => f.working()['paragraph-1'].text === 'theirs');
    await assert.rejects(f.session.undo(), /其他人修改/);
    assert.equal(f.server()['paragraph-1'].text, 'theirs');
    assert.equal(f.editable(), true);
  } finally { f.session.dispose(); }
});
