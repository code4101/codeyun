import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ResetCodex',
  canonicalPath: '/tools/reset-codex',
  component: () => import('./page.vue'),
}

export default page
