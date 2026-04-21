import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiConfig',
  canonicalPath: '/tools/ai-config',
  component: () => import('./page.vue'),
}

export default page
