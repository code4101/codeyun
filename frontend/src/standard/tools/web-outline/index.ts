import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'WebOutline',
  canonicalPath: '/tools/web-outline',
  component: () => import('./page.vue'),
}

export default page

