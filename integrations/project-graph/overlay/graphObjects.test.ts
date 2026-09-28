import { test } from 'node:test';
import assert from 'node:assert/strict';
import { differences, changed, rebaseLocal, stageToObjects, objectsToStage } from './graphObjects.ts';

test('concurrent insertions preserve both objects and converge on server order', () => {
  const base = { a: { uuid: 'a' }, '@order': { value: ['a'] } };
  const local = { ...base, b: { uuid: 'b' }, '@order': { value: ['a', 'b'] } };
  const remote = { ...base, c: { uuid: 'c' }, '@order': { value: ['a', 'c'] } };
  const merged = changed(remote, rebaseLocal(base, differences(base, local), remote));
  assert.deepEqual(merged['@order'].value, ['a', 'c', 'b']);
  assert.ok(merged.b && merged.c);
});
test('content conflict is never hidden by structural rebasing', () => {
  const base = { a: { uuid: 'a', text: 'old' }, '@order': { value: ['a'] } };
  const local = { ...base, a: { uuid: 'a', text: 'local' } };
  const remote = { ...base, a: { uuid: 'a', text: 'remote' } };
  assert.throws(() => rebaseLocal(base, differences(base, local), remote));
});
test('nested first appearance survives stable object conversion', () => {
  const stage = [{ _: 'LineEdge', uuid: 'e', associationList: [{ _: 'TextNode', uuid: 'n' }] }, { $: '/0/associationList/0' }];
  const objects = stageToObjects(stage);
  assert.deepEqual(objects['@order'].value, ['e', 'n']);
  assert.deepEqual(stageToObjects(objectsToStage(objects)), objects);
});
