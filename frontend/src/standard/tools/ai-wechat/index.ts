import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiWechat',
  canonicalPath: '/tools/ai-wechat',
  component: () => import('./page.vue'),
}

export default page
