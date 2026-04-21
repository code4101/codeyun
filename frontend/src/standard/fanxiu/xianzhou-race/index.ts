import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'XianzhouRace',
  canonicalPath: '/fanxiu/xianzhou-race',
  component: () => import('./page.vue'),
}

export default page
