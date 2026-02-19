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
        path: 'cluster',
        name: 'ClusterManager',
        component: () => import('@/views/TaskManager.vue'),
        meta: { requiresAuth: true }, // Cluster management requires login
      },
      {
        path: 'cluster/logs/:id',
        name: 'TaskLogs',
        component: () => import('@/views/TaskLogs.vue'),
        meta: { requiresAuth: true }, // Logs require login
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, from, next) => {
  const userStore = useUserStore();
  
  // Check if route requires auth
  if (to.matched.some(record => record.meta.requiresAuth)) {
    if (!userStore.isAuthenticated) {
      next({ name: 'Login' });
    } else {
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
