import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'
const dom = new JSDOM('<body></body>')
globalThis.window = dom.window
globalThis.document = dom.window.document
const compiled = await build({ entryPoints: ['src/standard/pdf/library/readerAnswerMarkdown.ts'], absWorkingDir: new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'), bundle: true, write: false, format: 'esm' })
const { renderReaderAnswer } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)

test('renders Markdown semantics and safe links', () => {
  document.body.innerHTML = renderReaderAnswer('## 事实\n\n**重点**\n\n- 第一条\n- 第二条\n\n|项|值|\n|---|---|\n|a|b|\n\n```js\nconst x = 1\n```\n\n[来源](https://example.org/fact)')
  assert.equal(document.querySelector('h2').textContent, '事实')
  assert.equal(document.querySelector('strong').textContent, '重点')
  assert.equal(document.querySelectorAll('li').length, 2)
  assert.ok(document.querySelector('table'))
  assert.ok(document.querySelector('pre code'))
  assert.equal(document.querySelector('a').target, '_blank')
  assert.equal(document.querySelector('a').rel, 'noopener noreferrer')
})
test('removes script, event handlers, unsafe links and embedded UI', () => {
  document.body.innerHTML = renderReaderAnswer('<script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)" onclick="alert(1)">bad</a><iframe src="https://example.org"></iframe><input autofocus>')
  assert.equal(document.querySelector('script,img,iframe,input,[onclick],[onerror]'), null)
  assert.equal(document.querySelector('a').getAttribute('href'), null)
})
