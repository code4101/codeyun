import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import MainLayout from '@/layout/MainLayout.vue';
import { privateRoutes } from '@/private';
import Home from '@/views/Home.vue';

const routes: Array<RouteRecordRaw> = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/Register.vue'),
    meta: { requiresAuth: false },
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
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      {
        path: 'attendance/wjx-templates',
        name: 'AttendanceWjxTemplates',
        component: () => import('@/views/attendance/AttendanceWjxTemplates.vue'),
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      {
        path: 'cluster/logs/:id',
        name: 'TaskLogs',
        component: () => import('@/views/TaskLogs.vue'),
        meta: { requiresAuth: true }, // Logs require login
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

router.beforeEach(async (to, _from, next) => {
  const userStore = useUserStore();
  
  // Check if route requires auth
  if (to.matched.some(record => record.meta.requiresAuth)) {
    if (!userStore.isAuthenticated) {
      next({ name: 'Login' });
    } else {
      // Check for Admin requirement
      if (to.matched.some(record => record.meta.requiresAdmin)) {
        // Ensure user profile is loaded to check isAdmin
        if (!userStore.user) {
           await userStore.fetchUserProfile();
        }
        
        if (!userStore.isAdmin) {
          // Not authorized
          next({ name: 'Home' });
          return;
        }
      }
      next();
    }
  } else {
    // If route doesn't require auth (login/register)
    if (userStore.isAuthenticated && (to.name === 'Login' || to.name === 'Register')) {
      next({ name: 'Home' });
    } else {
      next();
    }
  }
});

export default router;
