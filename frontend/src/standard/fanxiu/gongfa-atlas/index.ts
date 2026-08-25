import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuGongfaAtlas',
  canonicalPath: '/fanxiu/inventory/gongfa-atlas',
  component: () => import('./page.vue'),
}

export default page
