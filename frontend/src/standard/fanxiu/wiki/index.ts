import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuWiki',
  canonicalPath: '/fanxiu/wiki',
  component: () => import('./page.vue'),
}

export default page
