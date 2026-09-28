import type { PluginFrontendModule } from '@/plugins'

export default {
  pages: [{
    routeName: 'ProjectGraphPlayground',
    canonicalPath: '/notes/project-graph',
    component: () => import('./page.vue'),
    permissionKey: 'plugins.project-graph',
    requiresAuth: true,
    standaloneEnabled: true,
  }],
} satisfies PluginFrontendModule
