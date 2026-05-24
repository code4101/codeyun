import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuProtocolSemantics',
  canonicalPath: '/fanxiu/protocol-semantics',
  component: () => import('./page.vue'),
}

export default page
