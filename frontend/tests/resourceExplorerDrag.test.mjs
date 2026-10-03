import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import test from 'node:test'
import { build } from 'esbuild'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'

const frontend = fileURLToPath(new URL('../', import.meta.url))
const compiled = await build({
  stdin: { contents: "export { default } from './src/components/resource-explorer/ResourceExplorer.vue'", resolveDir: frontend },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'vue-test', setup(builder) {
    builder.onResolve({ filter: /^vue$/ }, () => ({ path: pathToFileURL(path.join(frontend, 'node_modules/vue/index.mjs')).href, external: true }))
    builder.onLoad({ filter: /\.vue$/ }, async ({ path: filename }) => {
      const { descriptor, errors } = parse(await readFile(filename, 'utf8'), { filename })
      assert.deepEqual(errors, [])
      return { contents: compileScript(descriptor, { id: filename, inlineTemplate: true }).content, loader: 'ts' }
    })
  } }],
})
const { default: Explorer } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)

// Exercise native drag handlers on the actual Vue component, including compressed paths.
test('file drops target the deepest compressed folder or root; invalid and external drags do not move', async () => {
  const node = type => ({ type, props: {}, children: [], parent: null })
  const root = node('root')
  const all = n => [n, ...n.children.flatMap(all)]
  const renderer = createRenderer({
    createElement: node, createText: () => node('#text'), createComment: () => node('#comment'),
    setText() {}, setElementText(n, text) { n.text = text; n.children = [] },
    parentNode: n => n.parent, nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1] ?? null,
    patchProp: (n, key, old, value) => { n.props[key] = value },
    insert(n, parent, anchor = null) {
      if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1)
      n.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      parent.children.splice(index < 0 ? parent.children.length : index, 0, n)
    },
    remove(n) { n.parent?.children.splice(n.parent.children.indexOf(n), 1) },
  })
  const file = { id: 'file', name: '图.prg', kind: 'file' }
  const locked = { id: 'locked', name: '共享.prg', kind: 'file' }
  const inner = { id: 'inner', name: '子目录', kind: 'directory', children: [] }
  const outer = { id: 'outer', name: '目录', kind: 'directory', children: [inner] }
  const second = { id: 'second', name: '第二张图.prg', kind: 'file' }
  const moves = []
  const commands = []
  const app = renderer.createApp({ render: () => h(Explorer, {
    nodes: [file, locked, outer, second], multiSelect: true, canDrag: n => ['file', 'second', 'inner'].includes(n.id),
    canDrop: (source, target) => target === null || target.id === 'inner',
    onMove: (sources, target) => moves.push([sources.map(source => source.id).join(','), target?.id ?? 'root']),
    onCommand: (command, selection, target) => commands.push([command, selection.map(n => n.id), target?.id ?? 'root']),
  }) })
  app.mount(root)
  const rows = () => all(root).filter(n => n.props.role === 'treeitem')
  const event = () => ({ prevented: false, preventDefault() { this.prevented = true }, stopPropagation() {}, dataTransfer: { setData() {} } })
  // An external payload never provides a trusted in-tree source.
  rows()[2].props.onDrop(event())
  assert.equal(moves.length, 0)
  const denied = event()
  rows()[1].props.onDragstart(denied)
  assert.equal(denied.prevented, true)
  rows()[0].props.onDragstart(event())
  await nextTick()
  const invalid = event()
  rows()[1].props.onDragover(invalid)
  assert.equal(invalid.prevented, true, 'file rows accept a drop into their parent, here the root')
  const over = event()
  rows()[2].props.onDragover(over)
  assert.equal(over.prevented, true)
  assert.equal(over.dataTransfer.dropEffect, 'move')
  await nextTick()
  assert.match(rows()[2].props.class, /drop-target/)
  rows()[2].props.onDrop(event())
  await nextTick()
  assert.deepEqual(moves, [['file', 'inner']])
  rows()[0].props.onDragstart(event())
  await nextTick()
  const rootDrop = all(root).find(n => n.props.class?.includes('root-drop'))
  rootDrop.props.onDragover(event())
  rootDrop.props.onDrop(event())
  await nextTick()
  assert.deepEqual(moves, [['file', 'inner'], ['file', 'root']])
  rows()[0].props.onDragstart(event())
  rows()[0].props.onDragend(event())
  rows()[2].props.onDrop(event())
  assert.equal(moves.length, 2, 'cancelled drags cannot move a resource')
  rows()[0].props.onClick(event())
  rows()[3].props.onClick({ ...event(), ctrlKey: true })
  rows()[0].props.onDragstart(event())
  rows()[2].props.onDrop(event())
  assert.deepEqual(moves[2], ['file,second', 'inner'], 'dragging a selected file carries the whole selection')
  const row = rows()[3]
  row.props.onKeydown({ ...event(), key: 'x', ctrlKey: true, target: row, currentTarget: row })
  assert.deepEqual(commands.at(-1), ['cut', ['file', 'second'], 'root'])
  row.props.onKeydown({ ...event(), key: 'v', ctrlKey: true, target: row, currentTarget: row })
  assert.deepEqual(commands.at(-1), ['paste', ['file', 'second'], 'root'])
  rows()[2].props.onClick(event())
  rows()[2].props.onDragstart(event())
  const tree = all(root).find(n => n.props.role === 'tree')
  tree.props.onDrop(event())
  assert.deepEqual(moves.at(-1), ['inner', 'root'], 'directories use the same drag interaction')
  rows()[0].props.onClick(event())
  const first = rows()[0]
  first.props.onKeydown({ ...event(), key: 'F2', target: first, currentTarget: first })
  assert.deepEqual(commands.at(-1), ['rename', ['file'], 'root'])
  first.props.onKeydown({ ...event(), key: 'Delete', target: first, currentTarget: first })
  assert.deepEqual(commands.at(-1), ['delete', ['file'], 'root'])
  app.unmount()
})
