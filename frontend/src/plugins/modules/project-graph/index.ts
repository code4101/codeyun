import type { PluginFrontendModule } from '@/plugins'

export default {
  pages: [{
    routeName: 'ProjectGraphPlayground',
    pageLayout: 'workspace',
    canonicalPath: '/notes/project-graph',
    component: () => import('./page.vue'),
    permissionKey: 'plugins.project-graph',
    requiresAuth: true,
    supportsContentOnly: true,
  }],
} satisfies PluginFrontendModule
