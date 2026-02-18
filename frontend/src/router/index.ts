import { createRouter, createWebHistory } from 'vue-router';
import MainLayout from '@/layout/MainLayout.vue';
import Home from '@/views/Home.vue';
import FileExplorer from '@/views/FileExplorer.vue';

const routes = [
  {
    path: '/',
    component: MainLayout,
    children: [
      {
        path: '',
        name: 'Home',
        component: Home,
      },
      {
        path: 'tools/file-explorer',
        name: 'FileExplorer',
        component: FileExplorer,
      },
      {
        path: 'tools/command-runner',
        name: 'CommandRunner',
        component: () => import('@/views/CommandRunner.vue'),
      },
      {
        path: 'cluster',
        name: 'ClusterManager',
        component: () => import('@/views/TaskManager.vue'),
      },
      {
        path: 'cluster/logs/:id',
        name: 'TaskLogs',
        component: () => import('@/views/TaskLogs.vue'),
      },
      {
        path: 'about',
        name: 'About',
        component: () => import('@/views/Home.vue'), // Placeholder
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
