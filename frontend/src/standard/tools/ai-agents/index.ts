import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiAgents',
  canonicalPath: '/tools/ai-agents',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page
