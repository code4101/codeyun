import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const pages: AppPageDefinition[] = [
  {
    routeName: 'Home',
    canonicalPath: '/',
    component: () => import('./page.vue'),
    permissionKey: 'home',
  },
]

export default pages
