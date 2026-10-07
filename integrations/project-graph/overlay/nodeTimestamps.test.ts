import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NodeTimestamps, nodeTimes } from './nodeTimestamps.ts';
import { objectsToStage, stageToObjects } from './graphObjects.ts';
const graph = (text = 'hello') => ({ n: { _: 'TextNode', uuid: 'n', text }, '@order': { value: ['n'] } });

test('legacy open and repeated capture preserve unknown times; only first actual edit gets an edit time', () => {
  const clock = new NodeTimestamps(), old = graph(); clock.adopt(old);
  assert.deepEqual(clock.capture(old, 1000), old);
  const edited = clock.capture(graph('edited'), 2000);
  assert.equal(edited.n.createdAt, null); assert.equal(edited.n.updatedAt, 2000);
  assert.deepEqual(clock.capture(edited, 3000), edited);
});
test('new node creation is stable; text, geometry and body changes advance only its edit time', () => {
  const clock = new NodeTimestamps(); clock.adopt({ '@order': { value: [] } });
  const created = clock.capture(graph(), 1000);
  assert.equal(created.n.createdAt, 1000); assert.equal(created.n.updatedAt, 1000);
  const edited = clock.capture({ ...created, n: { ...created.n, text: 'changed', details: [{ text: 'body' }], x: 20 } }, 2000);
  assert.equal(edited.n.createdAt, 1000); assert.equal(edited.n.updatedAt, 2000);
  assert.equal(clock.capture({ ...edited, n: { ...edited.n, x: 25 } }, 1500).n.updatedAt, 2001, 'clock rollback never reverses edit time');
});
test('remote adoption and archive reload never stamp a local edit or change historical creation', () => {
  const server = { ...graph(), n: { ...graph().n, createdAt: 1000, updatedAt: 2000 } };
  const clock = new NodeTimestamps(); clock.adopt(server);
  assert.deepEqual(clock.capture(server, 9000), server);
  const roundtrip = stageToObjects(objectsToStage(server));
  const reopened = new NodeTimestamps(); reopened.adopt(roundtrip);
  assert.deepEqual(reopened.capture(roundtrip, 10000), server);
});
test('undo, redo and delete/restore preserve creation while recording the new edit action', () => {
  const clock = new NodeTimestamps(); const created = clock.capture(graph(), 1000);
  const edited = clock.capture({ ...created, n: { ...created.n, text: 'edited' } }, 2000);
  const undo = clock.project(edited, created, 3000); clock.adopt(undo);
  assert.equal(undo.n.createdAt, 1000); assert.equal(undo.n.updatedAt, 3000);
  const redo = clock.project(undo, edited, 4000); clock.adopt(redo);
  assert.equal(redo.n.updatedAt, 4000);
  const empty = { '@order': { value: [] } }; clock.adopt(empty);
  const restored = clock.project(empty, created, 5000);
  assert.equal(restored.n.createdAt, 1000); assert.equal(restored.n.updatedAt, 5000);
});
test('independent node and structural changes do not update untouched nodes', () => {
  const clock = new NodeTimestamps(); const initial = clock.capture({ ...graph(), m: { uuid: 'm', text: 'other' }, '@order': { value: ['n', 'm'] } }, 1000);
  const next = clock.capture({ ...initial, m: { ...initial.m, text: 'edited' }, '@order': { value: ['m', 'n'] } }, 2000);
  assert.deepEqual(next.n, initial.n); assert.equal(next.m.updatedAt, 2000);
});
test('nested path references do not propagate unrelated node edits into connected nodes', () => {
  const stage = [{ _: 'LineEdge', uuid: 'e', nodes: [{ _: 'TextNode', uuid: 'n', text: 'hello' }] }, { $: '/0/nodes/0' }];
  const clock = new NodeTimestamps(); const initial = clock.capture(stageToObjects(stage), 1000);
  const edited = clock.capture({ ...initial, n: { ...initial.n, text: 'changed' } }, 2000);
  assert.deepEqual(edited.e, initial.e); assert.equal(edited.n.updatedAt, 2000);
});
test('invalid and backwards archive times are rejected', () => {
  for (const createdAt of [-1, 0, true, 1.2, Infinity, 'date']) assert.throws(() => nodeTimes({ createdAt } as any));
  assert.throws(() => nodeTimes({ createdAt: 2000, updatedAt: 1000 }));
});
