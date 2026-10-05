import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test, { type TestContext } from 'node:test';
import { installTouchSelectionActions } from '../../integrations/project-graph/overlay/touchSelectionActions.ts';
const { JSDOM } = createRequire(import.meta.url)('jsdom');

function fixture(t: TestContext) {
  const dom = new JSDOM('<div role="toolbar"></div>');
  const toolbar = dom.window.document.querySelector('div')!;
  let ids: string[] = [], readOnly = false, enabled = true, pending = false;
  const removed: string[][] = [], errors: unknown[] = [];
  let complete: () => void = () => {}, undos = 0;
  const actions = installTouchSelectionActions(toolbar, {
    selectedIds: () => [...ids], readOnly: () => readOnly, enabled: () => enabled && !pending,
    remove: async selected => { removed.push(selected); await new Promise<void>(resolve => { complete = resolve; }); },
    undo: async () => { undos++; }, busy: value => { pending = value; }, error: error => errors.push(error),
  });
  t.after(() => { actions.dispose(); dom.window.close(); });
  const remove = toolbar.querySelector('[data-touch-action="delete"]') as HTMLButtonElement;
  const undo = toolbar.querySelector('[data-touch-action="undo"]') as HTMLButtonElement;
  return { toolbar, actions, remove, undo, removed, errors,
    select(value: string[]) { ids = value; actions.refresh(); },
    readonly(value: boolean) { readOnly = value; actions.refresh(); },
    enabled(value: boolean) { enabled = value; actions.refresh(); },
    complete: () => complete(), undos: () => undos };
}

test('touch delete communicates selection size and cannot run on an empty selection', t => {
  const f = fixture(t);
  assert.equal(f.remove.disabled, true); f.remove.click(); assert.deepEqual(f.removed, []);
  f.select(['node', 'edge']); assert.equal(f.remove.textContent, '删除 (2)');
  assert.equal(f.remove.disabled, false);
  f.select([]); assert.equal(f.remove.disabled, true);
});

test('lease wait captures selection and blocks repeated delete and undo taps', async t => {
  const f = fixture(t);
  f.select(['node']); f.remove.click();
  assert.equal(f.remove.disabled, true); assert.equal(f.undo.disabled, true);
  f.select(['parent']); f.remove.click(); f.undo.click();
  assert.deepEqual(f.removed, [['node']]); assert.equal(f.undos(), 0);
  f.select([]); f.complete(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.remove.disabled, true); assert.equal(f.undo.disabled, false);
  f.undo.click(); await new Promise(resolve => setImmediate(resolve)); assert.equal(f.undos(), 1);
  assert.deepEqual(f.errors, []);
});

test('viewers have no mutation controls and collaboration unavailable disables actions', t => {
  const f = fixture(t); f.select(['node']); f.readonly(true);
  assert.equal((f.toolbar.querySelector('.codeyun-touch-selection') as HTMLElement).hidden, true);
  f.remove.click(); f.undo.click(); assert.deepEqual(f.removed, []); assert.equal(f.undos(), 0);
  f.readonly(false); f.enabled(false); assert.equal(f.remove.disabled, true); assert.equal(f.undo.disabled, true);
  f.enabled(true); assert.equal(f.remove.disabled, false);
});
