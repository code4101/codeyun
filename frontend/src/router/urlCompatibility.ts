import type { RouteLocationNormalized, Router } from 'vue-router'
import { stringifyQuery } from 'vue-router'

export interface UrlCompatibilityRule {
  from: string
  to: string
  kind: string
  source: string
  detail: string
}

/** 从实际注册的路由读取规则，不另存一份迁移清单，也不触发导航或权限请求。 */
export function collectUrlCompatibility(router: Router): UrlCompatibilityRule[] {
  return router.getRoutes().flatMap(record => {
    if (!record.redirect && !record.aliasOf) return []
    if (!record.redirect) return [{
      from: record.path, to: record.aliasOf!.path, kind: '路由别名', source: 'Vue Router',
      detail: '渲染同一页面，地址栏保留旧地址。',
    }]
    const dynamic = typeof record.redirect === 'function'
    try {
      const input = { path: record.path, fullPath: record.path, name: record.name, params: {}, query: {}, hash: '', meta: record.meta, matched: [record] } as RouteLocationNormalized
      const target = typeof record.redirect === 'function' ? record.redirect(input, router.currentRoute.value) : record.redirect
      const query = typeof target === 'object' ? stringifyQuery(target.query ?? {}) : ''
      const to = typeof target === 'string' ? target
        : 'path' in target && target.path ? `${target.path}${query ? `?${query}` : ''}${target.hash ?? ''}`
        : router.resolve(target).fullPath
      return [{
        from: record.path, to, kind: record.path.includes(':') ? '路径模式跳转' : '页面跳转', source: 'Vue Router',
        detail: dynamic ? `动态规则（显示空查询参数下的目标）：\n${record.redirect.toString()}` : '固定跳转；查询参数与锚点按 Vue Router 的重定向规则处理。',
      }]
    } catch (error) {
      return [{ from: record.path, to: '需要具体路径参数', kind: '动态跳转', source: 'Vue Router', detail: `${record.redirect}\n${String(error)}` }]
    }
  }).sort((a, b) => a.from.localeCompare(b.from))
}

/** 独立 HTML 入口先于 Vue Router 生效，直接读取入口源码中的替换跳转。 */
export function collectHtmlCompatibility(source: string, html: string): UrlCompatibilityRule[] {
  return [...html.matchAll(/location\.replace\(["']([^"']+)["']\s*\+\s*location\.search\s*\+\s*location\.hash\)/g)].map(match => ({
    from: source.replace(/\/index\.html$/, '/'), to: match[1]!, kind: 'HTML 入口跳转', source,
    detail: '保留查询参数和锚点，替换当前历史记录。目录地址及 index.html 地址均适用。',
  }))
}
