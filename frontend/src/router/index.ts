import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router';
import type { RouteLocationNormalized, RouteRecordNormalized } from 'vue-router';
import { buildPermissionTitleSegments, findPermissionKeyByRoutePath } from '@/features/access/permissionRegistry';
import { useFeatureAccessStore } from '@/store/featureAccessStore';
import { useUserStore } from '@/store/userStore';
import MainLayout from '@/layout/MainLayout.vue';
import { privateRoutes } from '@/private';
import Home from '@/views/Home.vue';

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
    path: '/attendance-feedback',
    name: 'AttendanceWjxFeedbackPublic',
    component: () => import('@/views/attendance/AttendanceWjxFeedbackPublic.vue'),
    meta: { requiresAuth: false, skipFeatureAccess: true, permissionKey: 'attendance.wjx-feedback' },
  },
  {
    path: '/f/attendance-feedback',
    redirect: '/attendance-feedback',
    meta: { requiresAuth: false, skipFeatureAccess: true },
  },
  {
    path: '/',
    component: MainLayout,
    meta: { requiresAuth: false }, // Main layout accessible to everyone
    children: [
      {
        path: '',
        name: 'Home',
        component: Home,
        meta: { permissionKey: 'home' },
      },
      {
        path: 'fanxiu/calculator',
        name: 'BeastSoulCalculator',
        component: () => import('@/views/fanxiu/Calculator.vue'),
        meta: { requiresAuth: false }, // Public access
      },
      {
        path: 'fanxiu/draw-calc',
        name: 'DrawCalculator',
        component: () => import('@/views/fanxiu/DrawCalculator.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'fanxiu/discount',
        name: 'FanxiuDiscountGuide',
        component: () => import('@/views/fanxiu/DiscountGuide.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'fanxiu/task-status',
        name: 'FanxiuTaskStatus',
        component: () => import('@/views/fanxiu/TaskStatus.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'fanxiu/recharge',
        name: 'FanxiuRecharge',
        component: () => import('@/views/fanxiu/Recharge.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'fanxiu/xianzhou-race',
        name: 'XianzhouRace',
        component: () => import('@/views/fanxiu/XianzhouRace.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'fanxiu/cuijian-trial',
        name: 'CuijianTrial',
        component: () => import('@/views/fanxiu/CuijianTrial.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'dsp/calculator',
        name: 'DspCalculator',
        component: () => import('@/views/dsp/DspStatic.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'magic-craft/xor-matrix',
        name: 'XorMatrix',
        component: () => import('@/views/magic-craft/XorMatrix.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'notes/center',
        name: 'NotesCenter',
        component: () => import('@/views/notes/NotesCenter.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'notes/star-map',
        redirect: '/notes/center?tab=calendar'
      },
      {
        path: 'notes/calendar',
        redirect: '/notes/center?tab=calendar'
      },
      {
        path: 'notes/infinite-canvas',
        name: 'InfiniteCanvas',
        component: () => import('@/views/tools/InfiniteCanvas.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'cluster',
        name: 'ClusterManager',
        redirect: to => ({ path: '/cluster/tasks', query: to.query }),
        meta: { requiresAuth: true }, // Cluster management requires login
      },
      {
        path: 'cluster/tasks',
        name: 'DeviceTasks',
        component: () => import('@/views/TaskManager.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'cluster/media',
        alias: 'cluster/images',
        name: 'DeviceMediaBrowser',
        redirect: to => ({ path: '/cluster/files', query: to.query }),
        meta: { requiresAuth: true },
      },
      {
        path: 'cluster/files',
        name: 'DeviceFileBrowser',
        component: () => import('@/views/cluster/DeviceFileBrowser.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'cluster/labelme',
        name: 'DeviceLabelmeBrowser',
        component: () => import('@/views/cluster/DeviceLabelmeBrowser.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'tools/password-generator',
        name: 'PasswordGenerator',
        component: () => import('@/views/tools/PasswordGenerator.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'tools/color-tools',
        name: 'ColorTools',
        component: () => import('@/views/tools/ColorTools.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'tools/image-browser',
        name: 'ImageBrowser',
        component: () => import('@/views/tools/ImageBrowser.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'tools/labelme-lite',
        name: 'LabelmeLite',
        redirect: '/cluster/labelme',
        meta: { requiresAuth: true },
      },
      {
        path: 'tools/ai-config',
        name: 'AiConfig',
        component: () => import('@/views/tools/AiConfig.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'tools/ai-chat',
        name: 'AiChat',
        component: () => import('@/views/tools/AiChat.vue'),
        meta: { requiresAuth: false },
      },
      {
        path: 'tools/ai-reduction',
        name: 'AiReduction',
        component: () => import('@/views/tools/AiReduction.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'tools/ai-git-commit',
        name: 'AiGitCommit',
        component: () => import('@/views/tools/AiGitCommit.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'attendance/configs',
        name: 'AttendanceConfigs',
        component: () => import('@/views/attendance/AttendanceConfigs.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'attendance/questionnaire/design',
        alias: ['attendance/wjx/templates', 'attendance/wjx-templates'],
        redirect: '/attendance/questionnaire/feedback',
        meta: { requiresAuth: false, skipFeatureAccess: true },
      },
      {
        path: 'attendance/questionnaire/feedback',
        alias: 'attendance/wjx/feedback',
        name: 'AttendanceWjxFeedback',
        component: () => import('@/views/attendance/AttendanceWjxFeedback.vue'),
        meta: { requiresAuth: false, skipFeatureAccess: true, permissionKey: 'attendance.wjx-feedback' },
      },
      {
        path: 'attendance/questionnaire/data',
        alias: 'attendance/wjx/data',
        name: 'AttendanceWjxData',
        component: () => import('@/views/attendance/AttendanceWjxData.vue'),
        meta: { requiresAuth: true, permissionKey: 'attendance.wjx-data' },
      },
      {
        path: 'attendance/orders',
        name: 'AttendanceOrders',
        component: () => import('@/views/attendance/AttendanceOrders.vue'),
        meta: { requiresAuth: true },
      },
      {
        path: 'cluster/logs/:id',
        name: 'TaskLogs',
        component: () => import('@/views/TaskLogs.vue'),
        meta: { requiresAuth: true, permissionKey: 'cluster.tasks' }, // Logs require login
      },
      {
        path: 'admin/accounts',
        name: 'AccountManager',
        component: () => import('@/views/admin/AccountManager.vue'),
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      {
        path: 'admin/images',
        name: 'StorageManager',
        component: () => import('@/views/admin/StorageManager.vue'),
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      ...privateRoutes,
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

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
  if (to.name === 'AttendanceWjxFeedback') {
    return ['问卷', '配置']
  }
  if (to.name === 'AttendanceWjxFeedbackPublic') {
    return ['问卷', '采集页面']
  }
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
  if (route.meta.skipFeatureAccess) {
    return false
  }
  if (!route.name && route.path === '/' && route.children.length > 0) {
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

router.beforeEach(async (to) => {
  const userStore = useUserStore();
  const featureAccessStore = useFeatureAccessStore();

  if (userStore.isAuthenticated && (to.name === 'Login' || to.name === 'Register')) {
    return { name: 'Home' }
  }

  if (to.matched.some((record) => record.meta.requiresAuth) && !userStore.isAuthenticated) {
    return { name: 'Login' }
  }

  if (to.matched.some((record) => record.meta.requiresAdmin)) {
    if (!userStore.user) {
      await userStore.fetchUserProfile();
    }

    if (!userStore.isAuthenticated) {
      return { name: 'Login' }
    }

    if (!userStore.user) {
      return true
    }

    if (!userStore.isAdmin) {
      return { name: 'Forbidden' }
    }
  }

  if (to.matched.some((record) => record.meta.skipFeatureAccess)) {
    return true
  }

  const permissionKey = getMatchedPermissionKey(to);
  if (!permissionKey) {
    return { name: 'Forbidden' }
  }

  try {
    if (featureAccessStore.loading || !featureAccessStore.isReady) {
      await featureAccessStore.ensureLoaded();
    }
  } catch (error) {
    console.warn('Failed to ensure feature access context:', error);
    if (!featureAccessStore.isReady) {
      return true
    }
  }

  if (featureAccessStore.isReady && !featureAccessStore.isAllowed(permissionKey)) {
    return { name: 'Forbidden' }
  }

  return true
});

router.afterEach((to) => {
  document.title = buildDocumentTitle(to)
})

export default router;
