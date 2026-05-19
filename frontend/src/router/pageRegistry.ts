import type {
  RouteLocationNormalizedLoaded,
  RouteRecordRaw,
} from 'vue-router'

import { findPermissionKeyByRoutePath } from '@/features/access/permissionRegistry'
import { pluginPageRegistry } from '@/plugins'
import type { AppPageDefinition } from '@/router/pageRegistryTypes'
import { standardPageRegistry } from '@/standard'
import { getStandaloneRouteName, toStandalonePath } from '@/router/standalone'

export interface LegacyRouteRedirectDefinition {
  scope: 'main' | 'root'
  path: string
  alias?: string | string[]
  redirect: RouteRecordRaw['redirect']
  requiresAuth?: boolean
  requiresAdmin?: boolean
  skipFeatureAccess?: boolean
}

export const pageRegistry: AppPageDefinition[] = [
  ...standardPageRegistry,
  ...pluginPageRegistry,
]

export const legacyRouteRedirects: LegacyRouteRedirectDefinition[] = [
  {
    scope: 'main',
    path: '/notes/star-map',
    redirect: '/notes/center?tab=calendar',
  },
  {
    scope: 'main',
    path: '/notes/calendar',
    redirect: '/notes/center?tab=calendar',
  },
  {
    scope: 'main',
    path: '/cluster',
    redirect: to => ({ path: '/cluster/runtime', query: to.query }),
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/cluster/tasks',
    redirect: to => ({ path: '/cluster/runtime', query: to.query }),
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/admin/background-tasks',
    redirect: to => ({ path: '/cluster/runtime', query: to.query }),
    requiresAuth: true,
    requiresAdmin: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/cluster/media',
    alias: 'cluster/images',
    redirect: to => ({ path: '/cluster/files', query: to.query }),
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/cluster/storage',
    redirect: to => ({ path: '/cluster/treesize', query: to.query }),
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/tools/labelme-lite',
    redirect: '/cluster/labelme',
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/fanxiu/xianzhou-race',
    redirect: '/fanxiu/activity-list/xianzhou-marathon',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/attendance/wjx/data',
    redirect: '/attendance/questionnaire/data',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/attendance/courses',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/attendance/courses/20260412-chanzong-12qi-1jie',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/attendance/courses/20260412-chanzong-12qi-1jie/registration',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/attendance/courses/20260412-chanzong-12qi-1jie/attendance',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'root',
    path: '/attendance-feedback',
    redirect: '/standalone/attendance/questionnaire/collect',
    skipFeatureAccess: true,
  },
]

export const pageRegistryByName = new Map(
  pageRegistry.map((page) => [page.routeName, page] as const),
)

export function getPageCanonicalPath(routeName: string): string | null {
  return pageRegistryByName.get(routeName)?.canonicalPath ?? null
}

export function requirePageCanonicalPath(routeName: string): string {
  const path = getPageCanonicalPath(routeName)
  if (!path) {
    throw new Error(`未找到页面路径定义：${routeName}`)
  }
  return path
}

export function getPageMenuPath(routeName: string): string | null {
  const page = pageRegistryByName.get(routeName)
  if (!page) {
    return null
  }
  return page.menuPath ?? page.canonicalPath
}

export function requirePageMenuPath(routeName: string): string {
  const path = getPageMenuPath(routeName)
  if (!path) {
    throw new Error(`未找到页面菜单路径定义：${routeName}`)
  }
  return path
}

function canonicalPathToChildPath(canonicalPath: string): string {
  return canonicalPath === '/' ? '' : canonicalPath.replace(/^\//, '')
}

function joinCanonicalPath(parentPath: string, childPath: string): string {
  if (!childPath) {
    return parentPath || '/'
  }
  if (childPath.startsWith('/')) {
    return childPath
  }
  const base = parentPath === '/' ? '' : parentPath
  return `${base}/${childPath}`.replace(/\/+/g, '/')
}

function projectStandaloneRedirect(redirect: RouteRecordRaw['redirect']): RouteRecordRaw['redirect'] {
  if (!redirect) {
    return redirect
  }
  if (typeof redirect === 'string') {
    return toStandalonePath(redirect)
  }
  if (typeof redirect === 'function') {
    return (to) => {
      const result = redirect(to)
      if (typeof result === 'string') {
        return toStandalonePath(result)
      }
      if (result && typeof result === 'object' && 'path' in result && typeof result.path === 'string') {
        return {
          ...result,
          path: toStandalonePath(result.path),
        }
      }
      return result
    }
  }
  if (typeof redirect === 'object' && 'path' in redirect && typeof redirect.path === 'string') {
    return {
      ...redirect,
      path: toStandalonePath(redirect.path),
    }
  }
  return redirect
}

function projectStandaloneAlias(alias: RouteRecordRaw['alias']): RouteRecordRaw['alias'] {
  if (!alias) {
    return alias
  }
  const transform = (value: string) => (value.startsWith('/') ? toStandalonePath(value) : value)
  if (Array.isArray(alias)) {
    return alias.map(transform)
  }
  return transform(alias)
}

export function buildMainPageRoutes(
  pages: AppPageDefinition[] = pageRegistry,
): RouteRecordRaw[] {
  return pages.map((page) => ({
    path: canonicalPathToChildPath(page.canonicalPath),
    name: page.routeName,
    component: page.component,
    meta: {
      ...(page.permissionKey ? { permissionKey: page.permissionKey } : {}),
      requiresAuth: page.requiresAuth ?? false,
      ...(page.requiresAdmin ? { requiresAdmin: true } : {}),
      ...(page.menuPath === null ? {} : { menuPath: page.menuPath ?? page.canonicalPath }),
      ...(page.standaloneEnabled === false ? { standaloneDisabled: true } : {}),
    },
  }))
}

export function buildLegacyRedirectRoutes(
  scope: LegacyRouteRedirectDefinition['scope'],
  redirects: LegacyRouteRedirectDefinition[] = legacyRouteRedirects,
): RouteRecordRaw[] {
  return redirects
    .filter((item) => item.scope === scope)
    .map((item) => ({
      path: scope === 'main' ? canonicalPathToChildPath(item.path) : item.path,
      ...(item.alias ? { alias: item.alias } : {}),
      redirect: item.redirect,
      meta: {
        requiresAuth: item.requiresAuth ?? false,
        ...(item.requiresAdmin ? { requiresAdmin: true } : {}),
        ...(item.skipFeatureAccess ? { skipFeatureAccess: true } : {}),
        ...(scope === 'main' ? { standaloneDisabled: true } : {}),
      },
    }))
}

export function normalizeChildRoutes(
  routes: RouteRecordRaw[],
  parentCanonicalPath = '/',
): RouteRecordRaw[] {
  return routes.map((route) => {
    const canonicalPath = joinCanonicalPath(parentCanonicalPath, route.path)
    const children = route.children ? normalizeChildRoutes(route.children, canonicalPath) : undefined
    const meta = {
      ...(route.meta ?? {}),
      canonicalPath,
      shell: 'main',
    } as Record<string, unknown>
    const inferredPermissionKey = typeof meta.permissionKey === 'string'
      ? meta.permissionKey
      : findPermissionKeyByRoutePath(canonicalPath)
    if (typeof inferredPermissionKey === 'string' && inferredPermissionKey) {
      meta.permissionKey = inferredPermissionKey
    }
    return {
      ...route,
      meta,
      children,
    }
  })
}

export function projectStandaloneRoutes(routes: RouteRecordRaw[]): RouteRecordRaw[] {
  return routes
    .filter((route) => !route.meta?.standaloneDisabled)
    .map((route) => {
      const children = route.children ? projectStandaloneRoutes(route.children) : undefined
      const alias = projectStandaloneAlias(route.alias)
      const redirect = projectStandaloneRedirect(route.redirect)
      return {
        ...route,
        name: getStandaloneRouteName(route.name) ?? undefined,
        meta: {
          ...(route.meta ?? {}),
          shell: 'standalone',
        },
        ...(alias !== undefined ? { alias } : {}),
        ...(redirect !== undefined ? { redirect } : {}),
        children,
      }
    })
}

export function getMatchedMenuPath(
  route: Pick<RouteLocationNormalizedLoaded, 'matched' | 'path'>,
): string | null {
  const matchedMenuPath = [...route.matched]
    .reverse()
    .map((record) => {
      const menuPath = record.meta.menuPath
      return typeof menuPath === 'string' && menuPath ? menuPath : null
    })
    .find((menuPath) => menuPath !== null)

  if (matchedMenuPath) {
    return matchedMenuPath
  }

  const page = pageRegistry.find((item) => item.canonicalPath === route.path)
  if (!page) {
    return null
  }
  return page.menuPath ?? page.canonicalPath
}
