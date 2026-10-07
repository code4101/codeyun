import assert from 'node:assert/strict'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import axios from 'axios'
import { build } from 'esbuild'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('ebook content, reading state and resources survive a response after 10 seconds', { timeout: 20000 }, async () => {
  const server = http.createServer((request, response) => {
    const timer = setTimeout(() => {
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ id: 'slow-book', book_id: 'slow-book' }))
    }, 10200)
    response.on('close', () => clearTimeout(timer))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const baseURL = `http://127.0.0.1:${server.address().port}`
  try {
    const compiled = await build({
      entryPoints: [path.join(frontend, 'src/api/linuxDoBooks.ts')],
      bundle: true, write: false, format: 'esm', platform: 'node',
      plugins: [{ name: 'local-api', setup(builder) {
        builder.onResolve({ filter: /^\.\/index$/ }, () => ({ path: 'api', namespace: 'mock' }))
        builder.onResolve({ filter: /^axios$/ }, () => ({ path: pathToFileURL(path.join(frontend, 'node_modules/axios/index.js')).href, external: true }))
        builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({
          contents: `import axios from 'axios'; export default axios.create({baseURL:${JSON.stringify(baseURL)},timeout:10000,proxy:false});`,
          resolveDir: frontend,
        }))
      } }],
    })
    const api = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
    const [book, state, resource] = await Promise.all([
      api.fetchLinuxDoBook('slow-book'),
      api.fetchLinuxDoBookReadingState('slow-book'),
      api.fetchElectronicBookResource('/api/linux-do-books/slow-book/resources/cover.png'),
      assert.rejects(axios.get(`${baseURL}/default`, { timeout: 10000, proxy: false }), error => error.code === 'ECONNABORTED'),
    ])
    assert.equal(book.id, 'slow-book')
    assert.equal(state.book_id, 'slow-book')
    assert.ok(resource)
  } finally {
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
})
