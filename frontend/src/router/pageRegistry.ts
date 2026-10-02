import type {
  RouteLocationNormalizedLoaded,
  RouteRecordRaw,
} from 'vue-router'
import { parseQuery } from 'vue-router'

import { findPermissionKeyByRoutePath } from '@/features/access/permissionRegistry'
import { pluginPageRegistry } from '@/plugins'
import type { AppPageDefinition } from '@/router/pageRegistryTypes'
import { standardPageRegistry } from '@/standard'

export interface LegacyRouteRedirectDefinition {
  scope: 'main' | 'root'
  path: string
  alias?: string | string[]
  redirect: NonNullable<RouteRecordRaw['redirect']>
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
    path: '/tools/window-projection',
    redirect: to => ({ path: '/cluster/window-control', query: to.query, hash: to.hash }),
  },
  {
    scope: 'main',
    path: '/cluster/codex/projection',
    redirect: to => ({ path: '/cluster/window-control', query: to.query, hash: to.hash }),
  },
  {
    scope: 'main',
    path: '/tools/world-clock',
    redirect: to => ({ path: '/tools/globe', query: to.query, hash: to.hash }),
  },
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
    path: '/tools/length-scale',
    redirect: '/tools/units',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/tools/time-scale',
    redirect: '/tools/units',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/tools/mass-scale',
    redirect: '/tools/units',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/tools/area-scale',
    redirect: '/tools/units',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/tools/volume-scale',
    redirect: '/tools/units',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/tools/speed-scale',
    redirect: '/tools/units',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/tools/labelme-lite',
    redirect: '/cluster/labelme',
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/tools/hardware-temperature',
    redirect: '/cluster/hardware-temperature',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/fanxiu/xianzhou-race',
    redirect: '/fanxiu/activity-list/xianzhou-marathon',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/fanxiu/activity-list/top-activity',
    redirect: '/fanxiu/schedule',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/fanxiu/activity-list/resource-ranking',
    redirect: '/fanxiu/schedule',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/fanxiu/activity-list/yunmeng-trial',
    redirect: '/fanxiu/schedule',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/fanxiu/activity-list/xutian-palace',
    redirect: '/fanxiu/schedule',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/kq5034/wjx/data',
    redirect: '/kq5034/questionnaire/data',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/kq5034/courses',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/kq5034/courses/20260412-chanzong-12qi-1jie',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/kq5034/courses/20260412-chanzong-12qi-1jie/registration',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/kq5034/courses/20260412-chanzong-12qi-1jie/attendance',
    redirect: '/notes/sheets',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/notes/wechat',
    redirect: '/notes/wechat-data',
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/notes/pdfs',
    redirect: to => ({ path: '/notes/library', query: to.query }),
    requiresAuth: true,
    skipFeatureAccess: true,
  },
  {
    scope: 'root',
    path: '/attendance-feedback',
    redirect: to => ({ path: '/kq5034/feedback', query: to.query, hash: to.hash }),
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
      ...(page.defaultUi !== undefined ? { defaultUi: page.defaultUi } : {}),
      ...(page.supportsContentOnly ? { supportsContentOnly: true } : {}),
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
      // 兼容跳转保留展示层级、资源参数和片段；目标自带参数（如 tab）覆盖同名旧值。
      redirect: (to, from) => {
        const result = typeof item.redirect === 'function' ? item.redirect(to, from) : item.redirect
        const url = typeof result === 'string' ? new URL(result, 'https://codeyun.invalid') : null
        const target = typeof result === 'string'
          ? { path: url!.pathname, query: parseQuery(url!.search), hash: url!.hash || to.hash }
          : result
        return { ...target, query: { ...to.query, ...target.query }, hash: target.hash ?? to.hash }
      },
      meta: {
        requiresAuth: item.requiresAuth ?? false,
        ...(item.requiresAdmin ? { requiresAdmin: true } : {}),
        ...(item.skipFeatureAccess ? { skipFeatureAccess: true } : {}),
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
    } as RouteRecordRaw
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
