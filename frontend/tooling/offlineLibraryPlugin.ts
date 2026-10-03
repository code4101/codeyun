import { build, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'
import os from 'node:os'
import { createHash } from 'node:crypto'
import { readFile, readdir } from 'node:fs/promises'

const base = '/offline-library/'
/** Build an independent reader shell in both deployment modes. Development
 * serves a production bundle from the OS temp directory: caching Vite's live
 * module graph would leave unvisited imports/HMR dependent on the network. */
export function offlineLibraryPlugin(frontendRoot: string): Plugin {
  const directoryId = createHash('sha256').update(frontendRoot).digest('hex').slice(0, 12)
  const devOutput = path.join(os.tmpdir(), 'codeyun', 'offline-library', `${directoryId}-${process.pid}`)
  let compiled: Promise<void> | undefined
  let production = false
  let outputRoot = path.join(frontendRoot, 'dist')
  const mime: Record<string, string> = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm' }
  async function buildReader(outDir: string) {
    const previousNodeEnv = process.env.NODE_ENV
    try { await build({
      configFile: false, root: path.join(frontendRoot, 'offline-library'), base, publicDir: false, logLevel: 'warn',
      plugins: [vue(), runtimeAssets(frontendRoot)],
      build: { outDir, emptyOutDir: true, assetsInlineLimit: 0, chunkSizeWarningLimit: 1500 },
      resolve: { alias: { '@': path.join(frontendRoot, 'src') } },
    }) } finally {
      // Vite's embedded production build must not change the surrounding
      // development server's environment or its next config reload.
      if (previousNodeEnv === undefined) delete process.env.NODE_ENV
      else process.env.NODE_ENV = previousNodeEnv
    }
  }
  return {
    name: 'codeyun-offline-library',
    configResolved(config) { production = config.command === 'build'; outputRoot = path.resolve(frontendRoot, config.build.outDir) },
    configureServer(server) {
      server.watcher.on('change', filename => {
        const normalized = filename.replaceAll('\\', '/')
        if (normalized.includes('/offline-library/')) compiled = undefined
      })
      server.middlewares.use(async (request, response, next) => {
        const url = new URL(request.url ?? '/', 'http://localhost')
        if (url.pathname === '/offline-library') { response.writeHead(302, { Location: base }); response.end(); return }
        if (!url.pathname.startsWith(base)) { next(); return }
        try {
          compiled ??= buildReader(devOutput).catch(cause => { compiled = undefined; throw cause })
          await compiled
          const relative = decodeURIComponent(url.pathname.slice(base.length)) || 'index.html'
          const filename = path.resolve(devOutput, relative)
          if (!filename.startsWith(devOutput + path.sep)) { response.writeHead(403); response.end(); return }
          const content = await readFile(filename)
          response.writeHead(200, { 'Content-Type': mime[path.extname(filename)] ?? 'application/octet-stream', 'Cache-Control': 'no-cache' })
          response.end(request.method === 'HEAD' ? undefined : content)
        } catch (cause) { server.config.logger.error(`Offline library: ${String(cause)}`); response.writeHead(503); response.end('Offline reader unavailable') }
      })
    },
    async closeBundle() { if (production) await buildReader(path.join(outputRoot, 'offline-library')) },
  }
}

function runtimeAssets(frontendRoot: string): Plugin {
  return {
    name: 'codeyun-offline-runtime-assets', enforce: 'post',
    async generateBundle(_options, bundle) {
      const paths = Object.keys(bundle).filter(name => !name.endsWith('.map'))
      const hash = createHash('sha256')
      for (const name of [...paths].sort()) {
        const output = bundle[name]!
        hash.update(name).update(output.type === 'chunk' ? output.code : output.source)
      }
      async function copy(directory: string, prefix: string) {
        for (const item of await readdir(directory, { withFileTypes: true })) {
          const filename = `${prefix}/${item.name}`
          if (item.isDirectory()) { await copy(path.join(directory, item.name), filename); continue }
          // License/readme files are not runtime dependencies.
          if (/^(LICENSE|README)/i.test(item.name)) continue
          const source = await readFile(path.join(directory, item.name))
          thisPlugin.emitFile({ type: 'asset', fileName: filename, source })
          paths.push(filename); hash.update(filename).update(source)
        }
      }
      const thisPlugin = this
      for (const directory of ['cmaps', 'standard_fonts', 'wasm']) {
        await copy(path.join(frontendRoot, 'node_modules', 'pdfjs-dist', directory), `pdfjs/${directory}`)
      }
      const version = hash.digest('hex').slice(0, 20)
      const urls = paths.map(name => base + name)
      this.emitFile({ type: 'asset', fileName: 'runtime-manifest.json', source: JSON.stringify({ version, assets: urls }) })
      this.emitFile({ type: 'asset', fileName: 'sw.js', source: serviceWorkerSource(version, urls) })
    },
  }
}
function serviceWorkerSource(version: string, assets: string[]) {
  return `/* Only the reader shell is cached here. Private book packages live in account-scoped IndexedDB. */
const CACHE = 'codeyun-offline-runtime-${version}';
const ASSETS = ${JSON.stringify(assets)};
const PATHS = new Set(ASSETS);
self.addEventListener('install', event => event.waitUntil((async () => {
  try {
    const cache = await caches.open(CACHE);
    // Bound simultaneous requests, including PDF fonts/CMaps/WASM, and reject
    // partial installations. Failed updates leave the old runtime available.
    let next = 0;
    await Promise.all(Array.from({length: 6}, async () => {
      while (next < ASSETS.length) {
        const url = ASSETS[next++];
        const response = await fetch(url, {cache: 'reload'});
        if (!response.ok) throw new Error('Offline asset failed: ' + url);
        await cache.put(url, response);
      }
    }));
    await self.skipWaiting();
  } catch (error) { await caches.delete(CACHE); throw error; }
})()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  const names = (await caches.keys()).filter(name => name.startsWith('codeyun-offline-runtime-'));
  // Retain the previous runtime for tabs already using its hashed chunks.
  for (const name of names.slice(0, Math.max(0, names.length - 2))) if (name !== CACHE) await caches.delete(name);
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (event.request.mode === 'navigate' && url.pathname.startsWith('${base}')) {
    event.respondWith(caches.open(CACHE).then(cache => cache.match('${base}index.html')).then(response => response || fetch(event.request)));
  } else if (PATHS.has(url.pathname)) {
    event.respondWith(caches.match(url.pathname).then(response => response || fetch(event.request)));
  }
});`
}
