import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'

const result = await build({ entryPoints: [fileURLToPath(new URL('../src/router/uiPresentation.ts', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external' })
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { resolveUiPresentation, legacyStandaloneLocation, withUiLevel } = module.exports

test('UI defaults, explicit overrides and unsupported content mode have one resolution policy', () => {
  for (const defaultUi of [0, 1, 2]) for (const supportsContentOnly of [false, true]) {
    for (const value of [undefined, '0', '1', '2', '', '3', '-1', null, ['0', '2']]) {
      const requested = ['0', '1', '2'].includes(value) ? Number(value) : defaultUi
      const effective = requested === 0 && !supportsContentOnly ? 1 : requested
      assert.deepEqual(resolveUiPresentation({ ui: value }, { defaultUi, supportsContentOnly }), {
        requestedUi: requested, ui: effective, showAppNavigation: effective === 2, showWorkbench: effective !== 0,
      })
    }
  }
  assert.equal(resolveUiPresentation({}).ui, 2)
})

test('legacy links retain resource queries and fragments, honor explicit UI and replace history', () => {
  assert.deepEqual(legacyStandaloneLocation({ path: '/standalone/notes/library', query: { id: '12', ui: '0', filter: ['a', 'b'] }, hash: '#page7' }), {
    path: '/notes/library', query: { id: '12', ui: '0', filter: ['a', 'b'] }, hash: '#page7', replace: true,
  })
  assert.equal(legacyStandaloneLocation({ path: '/standalone', query: {}, hash: '' }).path, '/')
  assert.equal(legacyStandaloneLocation({ path: '/standalone/tools/globe', query: { ui: 'bad' }, hash: '' }).query.ui, '1')
  assert.equal(withUiLevel('/standalone/notes/library?id=12&ui=2#page7', 1), '/notes/library?id=12&ui=1#page7')
  assert.equal(withUiLevel('/standalone-example?a=1', 0), '/standalone-example?a=1&ui=0')
  assert.equal(withUiLevel('https://code4101.com/reader?id=3#p2', 0), 'https://code4101.com/reader?id=3&ui=0#p2')
})
