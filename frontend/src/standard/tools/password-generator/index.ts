import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'PasswordGenerator',
  canonicalPath: '/tools/password-generator',
  component: () => import('./page.vue'),
}

export default page
