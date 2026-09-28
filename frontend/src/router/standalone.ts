import type { RouteLocationNormalizedLoaded, RouteLocationRaw } from 'vue-router'

/** 兼容既有调用方名称；单独打开使用同一正式路由的 ui=1，不再生成第二套路由。 */
export function buildStandaloneRouteLocation(
  route: Pick<RouteLocationNormalizedLoaded, 'name' | 'params' | 'query' | 'hash' | 'matched'>,
): RouteLocationRaw | null {
  if (typeof route.name !== 'string' || !route.name) return null
  return { name: route.name, params: route.params, query: { ...route.query, ui: '1' }, hash: route.hash }
}
