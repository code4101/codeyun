import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

// Run after `npm run build`: verify the emitted graph, including dependencies
// pulled into shared chunks by build plugins, rather than source import syntax.
const dist = new URL('../dist/', import.meta.url)
const manifest = JSON.parse(await readFile(new URL('.vite/manifest.json', dist), 'utf8'))

function staticFiles(entries) {
  const visited = new Set()
  const files = new Set()
  function visit(key) {
    if (visited.has(key)) return
    visited.add(key)
    const chunk = manifest[key]
    assert.ok(chunk, `Missing manifest entry: ${key}`)
    files.add(chunk.file)
    for (const css of chunk.css ?? []) files.add(css)
    for (const dependency of chunk.imports ?? []) visit(dependency)
  }
  entries.forEach(visit)
  return files
}

for (const page of ['src/standard/home/page.vue', 'src/views/Login.vue']) {
  test(`${page} keeps feature bundles outside its startup graph`, async () => {
    const files = staticFiles(['index.html', page])
    for (const feature of [
      'src/plugins/modules/project-graph/page.vue',
      'src/standard/pdf/workspace/page.vue',
      'src/standard/notes/components/NoteSheetWorkspace.vue',
    ]) {
      assert.ok(manifest[feature], `Missing lazy feature: ${feature}`)
      assert.ok(!files.has(manifest[feature].file), `Startup imports ${feature}`)
    }
    for (const file of files) {
      assert.doesNotMatch(file, /file-viewer|RTFJS|pdfjs|handsontable|editor-core|echarts|maplibre|elk-vendor/)
    }
    const bytes = (await Promise.all([...files].map(file => stat(fileURLToPath(new URL(file, dist))))))
      .reduce((sum, file) => sum + file.size, 0)
    assert.ok(bytes < 1_000_000, `Startup assets exceed 1 MB: ${bytes} bytes`)
  })
}
