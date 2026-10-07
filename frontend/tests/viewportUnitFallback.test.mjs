import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import postcss from 'postcss'
import { parse } from '@vue/compiler-sfc'
import { build } from 'esbuild'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const compiled = await build({ entryPoints: [path.join(frontend, 'tooling/viewportUnitFallback.ts')], bundle: true, write: false, format: 'esm', platform: 'node' })
export const { viewportUnitFallback, staticViewportValue } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
export async function viewportDeclarations() {
  const result = []
  for (const name of await readdir(path.join(frontend, 'src'), { recursive: true })) {
    if (!/\.(vue|css)$/.test(name)) continue
    const source = await readFile(path.join(frontend, 'src', name), 'utf8')
    const blocks = name.endsWith('.vue') ? parse(source).descriptor.styles.map(style => style.content) : [source]
    for (const block of blocks) postcss.parse(block).walkDecls(declaration => {
      if (staticViewportValue(declaration.value) !== declaration.value) result.push({ file: name, property: declaration.prop, value: declaration.value })
    })
  }
  return result
}

test('viewport fallback preserves functions, cascade, important and quoted literals', async () => {
  const source = '.x{height:100dvh!important;max-height:clamp(20px,calc(70svh - 32px),720px);width:50lvw;content:"100dvh";background:url(/100dvh.png);--size:100dvh}'
  const result = await postcss([viewportUnitFallback()]).process(source, { from: undefined })
  assert.ok(result.css.includes('height:100vh!important;height:100dvh!important'))
  assert.ok(result.css.includes('max-height:clamp(20px,calc(70vh - 32px),720px);max-height:clamp(20px,calc(70svh - 32px),720px)'))
  assert.ok(result.css.includes('width:50vw;width:50lvw'))
  assert.ok(result.css.includes('content:"100dvh";background:url(/100dvh.png);--size:100dvh'))
  const repeated = await postcss([viewportUnitFallback()]).process(result.css, { from: undefined })
  assert.equal(repeated.css, result.css)
})

test('every current feature using modern viewport units receives a static fallback', async () => {
  const declarations = await viewportDeclarations()
  assert.ok(declarations.length >= 20)
  for (const declaration of declarations) {
    assert.ok(!declaration.property.startsWith('--'), `${declaration.file}: custom properties need @supports`)
    const input = `.probe{${declaration.property}:${declaration.value}}`
    const result = await postcss([viewportUnitFallback()]).process(input, { from: undefined })
    const values = []
    result.root.walkDecls(node => values.push(node.value))
    assert.deepEqual(values, [staticViewportValue(declaration.value), declaration.value], declaration.file)
  }
})

test('production CSS has no ordinary dynamic viewport declarations without fallback', {
  skip: process.env.CODEYUN_VERIFY_BUILD !== '1' && process.env.npm_lifecycle_event !== 'test:viewport-build',
}, async () => {
  const files = (await readdir(path.join(frontend, 'dist/assets'))).filter(name => name.endsWith('.css'))
  assert.ok(files.length)
  for (const file of files) {
    const root = postcss.parse(await readFile(path.join(frontend, 'dist/assets', file), 'utf8'))
    root.walkDecls(declaration => {
      const fallback = staticViewportValue(declaration.value)
      if (fallback === declaration.value || declaration.prop.startsWith('--')) return
      let previous = declaration.prev()
      while (previous?.type === 'comment') previous = previous.prev()
      assert.ok(previous?.type === 'decl' && previous.prop === declaration.prop && previous.value === fallback, `${file}: ${declaration.toString()}`)
    })
  }
})
