import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFallbackSceneGraphNodes, layoutSceneGraph } from '../src/standard/fanxiu/data-annotation/sceneGraphLayout.ts';

test('real layout engine positions a directed pair and routes its edge', async () => {
  const result = await layoutSceneGraph([{ id: 'source' }, { id: 'target' }], [
    { id: 'transition', source: 'source', target: 'target' },
  ]);
  const source = result.positionById.get('source')!;
  const target = result.positionById.get('target')!;
  assert.ok(Number.isFinite(source.x) && Number.isFinite(source.y));
  assert.ok(target.x > source.x);
  const sections = result.sectionsByEdgeId.get('transition')!;
  assert.ok(sections.length > 0);
  assert.ok(sections[0].endPoint.x > sections[0].startPoint.x);
});

test('fallback preserves graph metadata and tolerates nodes without display data', () => {
  const nodes = [
    { id: 'parent', position: { x: 0, y: 0 }, data: { depth: 1, label: 'parent' } },
    { id: 'child', position: { x: 0, y: 0 } },
  ];
  const result = buildFallbackSceneGraphNodes(nodes);
  assert.deepEqual(nodes.map((node) => node.position), [{ x: 0, y: 0 }, { x: 0, y: 0 }]);
  assert.equal(result[0].data, nodes[0].data);
  assert.ok(result[0].position.x < result[1].position.x);
});
