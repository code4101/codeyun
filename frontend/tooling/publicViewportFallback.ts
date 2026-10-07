import type { Plugin } from 'vite'
import postcss, { type Container } from 'postcss'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { staticViewportValue } from './viewportUnitFallback'

/** public 中的预编译 iframe 不走 PostCSS，补充仅旧内核启用的尺寸覆盖。
 * 保留原哈希资源的字节内容；HTML 注入不需要监听窗口或新增网络请求。
 */
export function publicViewportStyle(sources: string[]): string {
  const result: string[] = []
  function keepFallbacks(container: Container) {
    for (const node of [...(container.nodes || [])]) {
      if (node.type === 'decl') {
        const fallback = staticViewportValue(node.value)
        if (node.prop.startsWith('--') || fallback === node.value) node.remove()
        else node.value = fallback
      } else if ('nodes' in node && node.nodes) {
        keepFallbacks(node as Container)
        if (!node.nodes.length) node.remove()
      } else node.remove()
    }
  }
  for (const source of sources) {
    const root = postcss.parse(source)
    keepFallbacks(root)
    if (root.nodes.length) result.push(root.toString())
  }
  return `@supports not (height:100dvh){${[...new Set(result)].join('\n')}}`
}

export function publicViewportFallback(): Plugin {
  let publicDir = '', outDir = ''
  let style: Promise<string> | undefined
  const entries = ['plugins/project-graph/embed.html', 'plugins/project-graph/plate.html']
  async function compatibilityStyle() {
    const directory = path.join(publicDir, 'plugins/project-graph/assets')
    const files = (await readdir(directory)).filter(file => file.endsWith('.css')).sort()
    return publicViewportStyle(await Promise.all(files.map(file => readFile(path.join(directory, file), 'utf8'))))
  }
  async function inject(html: string) {
    style ??= compatibilityStyle()
    return html.replace('</head>', `<style data-codeyun-viewport-fallback>${await style}</style></head>`)
  }
  return {
    name: 'codeyun-public-viewport-fallback',
    configResolved(config) { publicDir = config.publicDir; outDir = path.resolve(config.root, config.build.outDir) },
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        const entry = (request.url || '').split('?')[0]?.replace(/^\//, '')
        if (!entry || !entries.includes(entry)) return next()
        try {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.setHeader('Cache-Control', 'no-cache')
          response.end(await inject(await readFile(path.join(publicDir, entry), 'utf8')))
        } catch (error) { next(error as Error) }
      })
    },
    async writeBundle() {
      for (const entry of entries) {
        const file = path.join(outDir, entry)
        await writeFile(file, await inject(await readFile(file, 'utf8')), 'utf8')
      }
    },
  }
}
