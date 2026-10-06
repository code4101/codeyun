import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
const compiled = await build({ entryPoints: ['src/components/rich-text/textQuoteAnchor.ts'], absWorkingDir: new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'), bundle: true, write: false, format: 'esm' })
const { matchingQuoteOffset: locate } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
test('duplicate quotes require unique context', () => {
  assert.equal(locate('left Q right Q', 'Q', '', ''), -1)
  assert.equal(locate('left Q right Q', 'Q', 'left ', ' right'), 5)
  assert.equal(locate('left Q right Q', 'Q', 'missing', ''), -1)
  assert.equal(locate('aaaa', 'aa', '', ''), -1)
})
test('unique quotes survive surrounding edits; absent/empty quotes stay unanchored', () => {
  assert.equal(locate('updated Q end', 'Q', 'old ', ' context'), 8)
  assert.equal(locate('new text', 'removed', '', ''), -1)
  assert.equal(locate('new text', '', '', ''), -1)
})
