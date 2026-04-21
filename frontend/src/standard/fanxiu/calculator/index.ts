import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'BeastSoulCalculator',
  canonicalPath: '/fanxiu/calculator',
  component: () => import('./page.vue'),
}

export default page
