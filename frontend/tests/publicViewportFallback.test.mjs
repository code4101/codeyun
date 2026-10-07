import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import postcss from 'postcss'
import { build } from 'esbuild'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const compiled = await build({ entryPoints: [path.join(frontend, 'tooling/publicViewportFallback.ts')], bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external' })
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { publicViewportStyle } = module.exports

test('public iframe compatibility preserves selectors and media queries while isolating old browsers', () => {
  const source = '.menu{color:red;max-height:50dvh}@media(max-width:700px){.menu{max-height:calc(70dvh - 24px)}}'
  const result = publicViewportStyle([source])
  assert.ok(result.startsWith('@supports not (height:100dvh)'))
  assert.ok(result.includes('.menu{max-height:50vh}'))
  assert.ok(result.includes('@media(max-width:700px){.menu{max-height:calc(70vh - 24px)}}'))
  assert.ok(!result.includes('color:red'))
})

test('all bundled iframe CSS can generate static viewport overrides', async () => {
  const directory = path.join(frontend, 'public/plugins/project-graph/assets')
  const sources = await Promise.all((await readdir(directory)).filter(file => file.endsWith('.css')).map(file => readFile(path.join(directory, file), 'utf8')))
  const result = publicViewportStyle(sources)
  let declarations = 0
  postcss.parse(result).walkDecls(declaration => {
    declarations++
    assert.ok(!/\d+[dsl]v(?:h|w)/.test(declaration.value))
  })
  assert.ok(declarations > 0)
})

test('built iframe entrypoints include compatibility and feedback boot screen has a fallback', {
  skip: process.env.npm_lifecycle_event !== 'test:viewport-build',
}, async () => {
  for (const file of ['embed.html', 'plate.html']) {
    const html = await readFile(path.join(frontend, 'dist/plugins/project-graph', file), 'utf8')
    assert.ok(html.includes('data-codeyun-viewport-fallback'))
    assert.ok(html.includes('@supports not (height:100dvh)'))
  }
  const html = await readFile(path.join(frontend, 'dist/attendance-feedback/index.html'), 'utf8')
  assert.match(html, /min-height:\s*100vh;\s*min-height:\s*100dvh/)
})
