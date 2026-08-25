import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ZaohuaGradeReference',
  canonicalPath: '/zaohua/grades',
  component: () => import('./page.vue'),
}

export default page
