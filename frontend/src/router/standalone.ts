import type { RouteLocationNormalizedLoaded, RouteLocationRaw } from 'vue-router'

export const STANDALONE_PREFIX = '/standalone'

export function toStandalonePath(path: string): string {
  if (!path.startsWith('/')) {
    return path
  }
  if (path.startsWith(STANDALONE_PREFIX)) {
    return path
  }
  return `${STANDALONE_PREFIX}${path}`
}

export function getStandaloneRouteName(routeName: unknown): string | null {
  if (typeof routeName !== 'string' || !routeName) {
    return null
  }
  if (routeName.endsWith('Standalone')) {
    return routeName
  }
  return `${routeName}Standalone`
}

export function buildStandaloneRouteLocation(
  route: Pick<RouteLocationNormalizedLoaded, 'name' | 'params' | 'query' | 'hash' | 'matched'>,
): RouteLocationRaw | null {
  if (route.matched.some((record) => record.meta.standaloneDisabled)) {
    return null
  }
  const standaloneName = getStandaloneRouteName(route.name)
  if (!standaloneName) {
    return null
  }
  return {
    name: standaloneName,
    params: route.params,
    query: route.query,
    hash: route.hash,
  }
}
