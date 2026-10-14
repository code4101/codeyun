import type { PluginFrontendModule } from '@/plugins'

export default {
  pages: [{
    routeName: 'ClusterLingnet',
    canonicalPath: '/cluster/lingnet',
    component: () => import('./page.vue'),
    permissionKey: 'cluster.lingnet',
    requiresAuth: true,
    requiresAdmin: true,
  }],
} satisfies PluginFrontendModule
