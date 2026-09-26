import type { PluginFrontendModule } from '@/plugins'

export default {
  pages: [{
    routeName: 'ProjectGraphPlayground',
    canonicalPath: '/plugins/project-graph',
    component: () => import('./page.vue'),
    permissionKey: 'plugins.project-graph',
    requiresAuth: false,
    standaloneEnabled: true,
  }],
} satisfies PluginFrontendModule
