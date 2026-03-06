import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import MainLayout from '@/layout/MainLayout.vue';
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
        component: () => import('@/views/TaskManager.vue'),
        meta: { requiresAuth: true }, // Cluster management requires login
      },
      {
        path: 'tools/password-generator',
        name: 'PasswordGenerator',
        component: () => import('@/views/tools/PasswordGenerator.vue'),
        meta: { requiresAuth: false },
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
