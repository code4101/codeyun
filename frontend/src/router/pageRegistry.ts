import type {
  RouteLocationNormalizedLoaded,
  RouteRecordRaw,
} from 'vue-router'

import { findPermissionKeyByRoutePath } from '@/features/access/permissionRegistry'
import { privatePageRegistry } from '@/private'
import type { AppPageDefinition } from '@/router/pageRegistryTypes'
import Home from '@/views/Home.vue'
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

export const corePageRegistry: AppPageDefinition[] = [
  {
    routeName: 'Home',
    canonicalPath: '/',
    component: Home,
    permissionKey: 'home',
  },
  {
    routeName: 'BeastSoulCalculator',
    canonicalPath: '/fanxiu/calculator',
    component: () => import('@/views/fanxiu/Calculator.vue'),
  },
  {
    routeName: 'DrawCalculator',
    canonicalPath: '/fanxiu/draw-calc',
    component: () => import('@/views/fanxiu/DrawCalculator.vue'),
  },
  {
    routeName: 'FanxiuDiscountGuide',
    canonicalPath: '/fanxiu/discount',
    component: () => import('@/views/fanxiu/DiscountGuide.vue'),
  },
  {
    routeName: 'FanxiuTaskStatus',
    canonicalPath: '/fanxiu/task-status',
    component: () => import('@/views/fanxiu/TaskStatus.vue'),
  },
  {
    routeName: 'FanxiuRecharge',
    canonicalPath: '/fanxiu/recharge',
    component: () => import('@/views/fanxiu/Recharge.vue'),
  },
  {
    routeName: 'XianzhouRace',
    canonicalPath: '/fanxiu/xianzhou-race',
    component: () => import('@/views/fanxiu/XianzhouRace.vue'),
  },
  {
    routeName: 'CuijianTrial',
    canonicalPath: '/fanxiu/cuijian-trial',
    component: () => import('@/views/fanxiu/CuijianTrial.vue'),
  },
  {
    routeName: 'DspCalculator',
    canonicalPath: '/dsp/calculator',
    component: () => import('@/views/dsp/DspStatic.vue'),
  },
  {
    routeName: 'XorMatrix',
    canonicalPath: '/magic-craft/xor-matrix',
    component: () => import('@/views/magic-craft/XorMatrix.vue'),
  },
  {
    routeName: 'NotesCenter',
    canonicalPath: '/notes/center',
    component: () => import('@/views/notes/NotesCenter.vue'),
    menuPath: '/notes/star-map',
  },
  {
    routeName: 'InfiniteCanvas',
    canonicalPath: '/notes/infinite-canvas',
    component: () => import('@/views/tools/InfiniteCanvas.vue'),
  },
  {
    routeName: 'DeviceTasks',
    canonicalPath: '/cluster/tasks',
    component: () => import('@/views/TaskManager.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'DeviceFileBrowser',
    canonicalPath: '/cluster/files',
    component: () => import('@/views/cluster/DeviceFileBrowser.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'DeviceLabelmeBrowser',
    canonicalPath: '/cluster/labelme',
    component: () => import('@/views/cluster/DeviceLabelmeBrowser.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'PasswordGenerator',
    canonicalPath: '/tools/password-generator',
    component: () => import('@/views/tools/PasswordGenerator.vue'),
  },
  {
    routeName: 'ColorTools',
    canonicalPath: '/tools/color-tools',
    component: () => import('@/views/tools/ColorTools.vue'),
  },
  {
    routeName: 'ImageBrowser',
    canonicalPath: '/tools/image-browser',
    component: () => import('@/views/tools/ImageBrowser.vue'),
  },
  {
    routeName: 'AiConfig',
    canonicalPath: '/tools/ai-config',
    component: () => import('@/views/tools/AiConfig.vue'),
  },
  {
    routeName: 'AiChat',
    canonicalPath: '/tools/ai-chat',
    component: () => import('@/views/tools/AiChat.vue'),
  },
  {
    routeName: 'AiReduction',
    canonicalPath: '/tools/ai-reduction',
    component: () => import('@/views/tools/AiReduction.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'AiGitCommit',
    canonicalPath: '/tools/ai-git-commit',
    component: () => import('@/views/tools/AiGitCommit.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'AttendanceConfigs',
    canonicalPath: '/attendance/configs',
    component: () => import('@/views/attendance/AttendanceConfigs.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'AttendanceWjxCatalog',
    canonicalPath: '/attendance/questionnaire/catalog',
    component: () => import('@/views/attendance/AttendanceWjxCatalog.vue'),
    requiresAuth: true,
    permissionKey: 'attendance.wjx-templates',
  },
  {
    routeName: 'AttendanceWjxCollect',
    canonicalPath: '/attendance/questionnaire/collect',
    component: () => import('@/views/attendance/AttendanceWjxCollect.vue'),
    permissionKey: 'attendance.wjx-feedback',
  },
  {
    routeName: 'AttendanceWjxData',
    canonicalPath: '/attendance/questionnaire/data',
    component: () => import('@/views/attendance/AttendanceWjxData.vue'),
    requiresAuth: true,
    permissionKey: 'attendance.wjx-data',
  },
  {
    routeName: 'AttendanceOrders',
    canonicalPath: '/attendance/orders',
    component: () => import('@/views/attendance/AttendanceOrders.vue'),
    requiresAuth: true,
  },
  {
    routeName: 'TaskLogs',
    canonicalPath: '/cluster/logs/:id',
    component: () => import('@/views/TaskLogs.vue'),
    requiresAuth: true,
    permissionKey: 'cluster.tasks',
    menuPath: '/cluster/tasks',
  },
  {
    routeName: 'AccountManager',
    canonicalPath: '/admin/accounts',
    component: () => import('@/views/admin/AccountManager.vue'),
    requiresAuth: true,
    requiresAdmin: true,
  },
  {
    routeName: 'StorageManager',
    canonicalPath: '/admin/images',
    component: () => import('@/views/admin/StorageManager.vue'),
    requiresAuth: true,
    requiresAdmin: true,
  },
]

export const pageRegistry: AppPageDefinition[] = [
  ...corePageRegistry,
  ...privatePageRegistry,
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
    redirect: to => ({ path: '/cluster/tasks', query: to.query }),
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
    path: '/tools/labelme-lite',
    redirect: '/cluster/labelme',
    requiresAuth: true,
  },
  {
    scope: 'main',
    path: '/attendance/questionnaire/design',
    alias: [
      'attendance/questionnaire/feedback',
      'attendance/wjx/templates',
      'attendance/wjx-templates',
      'attendance/wjx/feedback',
    ],
    redirect: '/attendance/questionnaire/catalog',
    skipFeatureAccess: true,
  },
  {
    scope: 'main',
    path: '/attendance/wjx/data',
    redirect: '/attendance/questionnaire/data',
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
