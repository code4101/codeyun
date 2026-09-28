import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordNormalized,
  type RouteRecordRaw,
} from 'vue-router'

import { buildPermissionTitleSegments, findPermissionKeyByRoutePath } from '@/features/access/permissionRegistry'
import MainLayout from '@/layout/MainLayout.vue'
import StandaloneLayout from '@/layout/StandaloneLayout.vue'
import {
  buildLegacyRedirectRoutes,
  buildMainPageRoutes,
  normalizeChildRoutes,
  projectStandaloneRoutes,
} from '@/router/pageRegistry'
import { STANDALONE_PREFIX } from '@/router/standalone'
import { useFeatureAccessStore } from '@/store/featureAccessStore'
import { useUserStore } from '@/store/userStore'
import { markBootPerf, markBootPerfAsync } from '@/utils/bootPerf'
import { installRouteLoadRecovery } from '@/router/routeLoadRecovery'

markBootPerf('router.module')

const normalizedMainChildRoutes = normalizeChildRoutes([
  ...buildMainPageRoutes(),
  ...buildLegacyRedirectRoutes('main'),
])

const standaloneChildRoutes = projectStandaloneRoutes(normalizedMainChildRoutes)

const routes: Array<RouteRecordRaw> = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false, skipFeatureAccess: true },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/Register.vue'),
    meta: { requiresAuth: false, skipFeatureAccess: true },
  },
  {
    path: '/403',
    name: 'Forbidden',
    component: () => import('@/views/Forbidden.vue'),
    meta: { requiresAuth: false, skipFeatureAccess: true },
  },
  {
    path: '/kq5034/feedback',
    component: () => import('@/attendance-feedback/PublicAttendanceFeedback.vue'),
    meta: { requiresAuth: false, skipFeatureAccess: true },
  },
  ...['', '/standalone'].map((prefix): RouteRecordRaw => ({
    path: `${prefix}/attendance/:pathMatch(.*)*`,
    redirect: to => ({ path: to.path.replace(`${prefix}/attendance`, `${prefix}/kq5034`), query: to.query, hash: to.hash }),
    meta: { requiresAuth: false, skipFeatureAccess: true },
  })),
  ...buildLegacyRedirectRoutes('root'),
  {
    path: '/workbook/:workbookId',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [
      {
        path: '',
        name: 'PublicWorkbookResource',
        component: () => markBootPerfAsync('route.public-workbook-resource.import', () => import('@/standard/notes/resource-view/page.vue')),
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
    ],
  },
  {
    path: '/kq5034/workbook/:workbookId',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [
      {
        path: '',
        name: 'IndependentAttendanceWorkbookResource',
        component: () => markBootPerfAsync('route.attendance-workbook-resource.import', () => import('@/standard/notes/resource-view/page.vue')),
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
    ],
  },
  {
    path: '/sheet/:sheetId',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [
      {
        path: '',
        name: 'PublicSheetResource',
        component: () => markBootPerfAsync('route.public-sheet-resource.import', () => import('@/standard/notes/resource-view/page.vue')),
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
    ],
  },
  {
    path: '/kq5034/sheet/:sheetId',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [
      {
        path: '',
        name: 'IndependentAttendanceSheetResource',
        component: () => markBootPerfAsync('route.attendance-sheet-resource.import', () => import('@/standard/notes/resource-view/page.vue')),
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
    ],
  },
  {
    path: '/doc/:noteId',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [
      {
        path: '',
        name: 'NoteDocResource',
        component: () => import('@/standard/notes/doc-view/page.vue'),
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
    ],
  },
  {
    path: '/reader',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [{ path: '', name: 'ReaderWorkspace', component: () => import('@/standard/pdf/workspace/page.vue'), meta: { requiresAuth: false, skipFeatureAccess: true } }],
  },
  {
    path: '/fanxiu-resource/:resourceType/:resourceId',
    component: StandaloneLayout,
    meta: { requiresAuth: false, skipFeatureAccess: true },
    children: [
      {
        path: '',
        name: 'FanxiuResource',
        component: () => import('@/standard/fanxiu/resource-view/page.vue'),
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
    ],
  },
  {
    path: STANDALONE_PREFIX,
    component: StandaloneLayout,
    meta: { requiresAuth: false },
    children: standaloneChildRoutes,
  },
  {
    path: '/',
    component: MainLayout,
    meta: { requiresAuth: false },
    children: normalizedMainChildRoutes,
  },
]

markBootPerf('router.before-create')
const router = createRouter({
  history: createWebHistory(),
  routes,
})
installRouteLoadRecovery(router)
markBootPerf('router.after-create')

function getMatchedPermissionKey(to: RouteLocationNormalized): string | null {
  const matchedPermissionKey = [...to.matched]
    .reverse()
    .map((record) => {
      const permissionKey = record.meta.permissionKey
      return typeof permissionKey === 'string' && permissionKey ? permissionKey : null
    })
    .find((permissionKey) => permissionKey !== null)
  if (matchedPermissionKey) {
    return matchedPermissionKey
  }
  return findPermissionKeyByRoutePath(to.path)
}

function getDocumentTitleSegments(to: RouteLocationNormalized): string[] {
  const permissionKey = getMatchedPermissionKey(to)
  if (!permissionKey) {
    return []
  }
  return buildPermissionTitleSegments(permissionKey, { omitRoot: true })
}

function buildDocumentTitle(to: RouteLocationNormalized): string {
  const segments = getDocumentTitleSegments(to)
  if (segments.length === 0) {
    return 'CodeYun'
  }
  return `${segments.join('/')} - CodeYun`
}

function shouldCheckFeatureAccess(route: RouteRecordNormalized): boolean {
  // 兼容地址不渲染页面；Vue Router 在目标路由上执行守卫，由目标承担权限校验。
  if (route.redirect) {
    return false
  }
  if (route.meta.skipFeatureAccess) {
    return false
  }
  if (!route.name && route.children.length > 0) {
    return false
  }
  return true
}

function assertFeatureAccessRouteCoverage() {
  const uncoveredRoutes = router.getRoutes()
    .filter((route) => shouldCheckFeatureAccess(route))
    .filter((route) => !getMatchedPermissionKey({
      ...route,
      matched: [route],
      meta: route.meta,
      path: route.path,
    } as RouteLocationNormalized))
    .map((route) => route.path)

  if (uncoveredRoutes.length > 0) {
    throw new Error(`以下路由缺少权限映射：${uncoveredRoutes.join(', ')}`)
  }
}

assertFeatureAccessRouteCoverage()
markBootPerf('router.access-coverage-ready')

router.beforeEach(async (to) => {
  // 状态只属于本次导航，不修改共享的路由记录；保留目标 URL 展示拒绝访问页。
  to.meta.accessDenied = false
  markBootPerf('router.beforeEach.start', { path: to.fullPath, name: String(to.name ?? '') })
  const userStore = useUserStore()
  const featureAccessStore = useFeatureAccessStore()

  if (userStore.isAuthenticated && (to.name === 'Login' || to.name === 'Register')) {
    return { name: 'Home' }
  }

  if (to.matched.some((record) => record.meta.requiresAuth) && !userStore.isAuthenticated) {
    return { name: 'Login' }
  }

  if (to.matched.some((record) => record.meta.requiresAdmin)) {
    if (!userStore.user) {
      await userStore.fetchUserProfile()
    }

    if (!userStore.isAuthenticated) {
      return { name: 'Login' }
    }

    if (!userStore.user) {
      return true
    }

    if (!userStore.isAdmin) {
      to.meta.accessDenied = true
      return true
    }
  }

  if (to.matched.some((record) => record.meta.skipFeatureAccess)) {
    markBootPerf('router.beforeEach.skip-feature-access', { path: to.fullPath })
    return true
  }

  const permissionKey = getMatchedPermissionKey(to)
  if (!permissionKey) {
    to.meta.accessDenied = true
    return true
  }

  try {
    if (featureAccessStore.loading || !featureAccessStore.isReady) {
      await featureAccessStore.ensureLoaded()
    }
  } catch (error) {
    console.warn('Failed to ensure feature access context:', error)
    if (!featureAccessStore.isReady) {
      return true
    }
  }

  if (featureAccessStore.isReady && !featureAccessStore.isAllowed(permissionKey)) {
    to.meta.accessDenied = true
    return true
  }

  markBootPerf('router.beforeEach.end', { path: to.fullPath })
  return true
})

router.afterEach((to) => {
  document.title = buildDocumentTitle(to)
  markBootPerf('router.afterEach', { path: to.fullPath, name: String(to.name ?? '') })
})

export default router
