import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ZaohuaPasturePlanner',
  canonicalPath: '/zaohua/pasture',
  component: () => import('./page.vue'),
}

export default page
