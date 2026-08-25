import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuXianqiaoMechanics',
  canonicalPath: '/fanxiu/xianqiao',
  component: () => import('./page.vue'),
}

export default page
