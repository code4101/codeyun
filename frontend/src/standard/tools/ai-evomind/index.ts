import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiEvoMind',
  canonicalPath: '/tools/ai-evomind',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page
