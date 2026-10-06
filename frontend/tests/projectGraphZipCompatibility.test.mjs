import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const source = await readFile(new URL('../../integrations/project-graph/overlay/zipCompatibility.ts', import.meta.url), 'utf8')
const { code } = await transform(source, { loader: 'ts', format: 'esm' })
const { supportsNativeZipStreams } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)

test('requires raw DEFLATE support for reading and writing, not just constructor presence', () => {
  class Supported { constructor(format) { assert.equal(format, 'deflate-raw') } }
  class OldChromium { constructor(format) { if (format === 'deflate-raw') throw new TypeError('Unsupported compression format') } }
  assert.equal(supportsNativeZipStreams({ CompressionStream: Supported, DecompressionStream: Supported }), true)
  assert.equal(supportsNativeZipStreams({ CompressionStream: Supported, DecompressionStream: OldChromium }), false)
  assert.equal(supportsNativeZipStreams({ CompressionStream: OldChromium, DecompressionStream: Supported }), false)
  assert.equal(supportsNativeZipStreams({}), false)
})
