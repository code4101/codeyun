import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'XorMatrix',
  canonicalPath: '/magic-craft/xor-matrix',
  component: () => import('./page.vue'),
}

export default page
