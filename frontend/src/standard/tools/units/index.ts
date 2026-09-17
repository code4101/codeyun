import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'Units',
  canonicalPath: '/tools/units',
  component: () => import('./page.vue'),
}

export default page
