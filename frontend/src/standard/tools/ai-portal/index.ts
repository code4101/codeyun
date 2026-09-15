import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiPortal',
  canonicalPath: '/tools/ai-portal',
  component: () => import('./page.vue'),
}

export default page
