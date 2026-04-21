import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiChat',
  canonicalPath: '/tools/ai-chat',
  component: () => import('./page.vue'),
}

export default page
