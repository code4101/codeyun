import assert from 'node:assert/strict'
import test from 'node:test'
import { createDockLayout, dockSides, isDockToolVisible, locateTool, moveDockTool, openDockTool, selectDockTool, restoreDockLayout, type DockTool } from '../src/components/docking/dockLayout.ts'
const tools: DockTool[] = [
  { id: 'toc', title: '目录', icon: 'document', position: 'left', open: true },
  { id: 'search', title: '搜索', icon: 'search', position: 'left' },
  { id: 'info', title: '信息', icon: 'info', position: 'left' },
  { id: 'outline', title: '大纲', icon: 'outline', position: 'right', open: true },
]
test('plain click selects exclusively, clicking the sole selection closes it', () => {
  const layout = createDockLayout(tools)
  selectDockTool(layout, 'search')
  assert.deepEqual(layout.regions.left.active, ['search'])
  selectDockTool(layout, 'search')
  assert.deepEqual(layout.regions.left.active, [])
  assert.ok(isDockToolVisible(layout, 'outline'))
})
test('Ctrl click toggles independent membership; plain click on a member keeps only it', () => {
  const layout = createDockLayout(tools)
  selectDockTool(layout, 'search', true)
  selectDockTool(layout, 'info', true)
  assert.deepEqual(layout.regions.left.active, ['toc', 'search', 'info'])
  selectDockTool(layout, 'toc', true)
  assert.deepEqual(layout.regions.left.active, ['search', 'info'])
  assert.ok(isDockToolVisible(layout, 'search'))
  selectDockTool(layout, 'search')
  assert.deepEqual(layout.regions.left.active, ['search'])
  assert.ok(isDockToolVisible(layout, 'outline'))
})
test('region hiding preserves selections, programmatic open reveals without closing peers', () => {
  const layout = createDockLayout(tools)
  openDockTool(layout, 'search')
  layout.regions.left.visible = false
  assert.equal(isDockToolVisible(layout, 'search'), false)
  openDockTool(layout, 'search')
  assert.deepEqual(layout.regions.left.active, ['toc', 'search'])
  assert.ok(isDockToolVisible(layout, 'search'))
})
test('move and reorder retain uniqueness and do not close destination peers', () => {
  const layout = createDockLayout(tools)
  moveDockTool(layout, 'search', 'left', 'toc')
  assert.deepEqual(layout.regions.left.tools, ['search', 'toc', 'info'])
  moveDockTool(layout, 'search', 'right')
  assert.deepEqual(layout.regions.right.active, ['outline', 'search'])
  for (const tool of tools) for (const side of dockSides) {
    moveDockTool(layout, tool.id, side)
    assert.equal(locateTool(layout, tool.id), side)
    assert.ok(isDockToolVisible(layout, tool.id))
    assert.deepEqual(Object.values(layout.regions).flatMap(r => r.tools).sort(), tools.map(t => t.id).sort())
    for (const region of Object.values(layout.regions)) assert.ok(region.active.every(id => region.tools.includes(id)))
  }
})
test('new layout roundtrips expanded set, weights and hidden regions', () => {
  const layout = createDockLayout(tools)
  openDockTool(layout, 'search')
  layout.regions.left.visible = false
  layout.regions.left.weights = { toc: .7, search: 1.3, info: 1 }
  const restored = restoreDockLayout(tools, JSON.parse(JSON.stringify(layout)))
  assert.deepEqual(restored.regions.left, layout.regions.left)
})
test('legacy split positions migrate to ordered multi-open regions', () => {
  const layout = restoreDockLayout(tools, { version: 1, groups: {
    'left-top': { tools: ['toc', 'info'], active: 'toc' },
    'left-bottom': { tools: ['search'], active: 'search' },
    'right-top': { tools: ['outline'], active: null },
  }, regions: { left: { visible: false, size: 310, ratio: .7 } } })
  assert.deepEqual(layout.regions.left.tools, ['toc', 'info', 'search'])
  assert.deepEqual(layout.regions.left.active, ['toc', 'search'])
  assert.equal(layout.regions.left.visible, false)
  assert.equal(layout.regions.left.weights.toc, 1.4)
  assert.deepEqual(layout.regions.right.active, [])
})
test('corrupt storage filters duplicates and stale IDs and adds newly registered tools', () => {
  const layout = restoreDockLayout(tools, { version: 2, regions: {
    left: { tools: ['search', 'search', 'removed'], active: ['search', 'removed'], size: -4, weights: { search: Infinity } },
    right: { tools: ['search', 'outline'], active: [], size: 9000 },
  } })
  assert.deepEqual(Object.values(layout.regions).flatMap(r => r.tools).sort(), tools.map(t => t.id).sort())
  assert.equal(layout.regions.left.size, 160)
  assert.equal(layout.regions.right.size, 900)
  assert.equal(layout.regions.left.weights.search, 1)
  assert.deepEqual(layout.regions.right.active, [])
})
