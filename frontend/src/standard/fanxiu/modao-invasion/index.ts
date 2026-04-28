import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuModaoInvasion',
  canonicalPath: '/fanxiu/activity-list/modao-invasion',
  component: () => import('./page.vue'),
}

export default page
