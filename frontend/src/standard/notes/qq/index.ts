import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesQq',
  canonicalPath: '/notes/qq-data',
  component: () => import('../wechat/page.vue'),
  permissionKey: 'notes.qq',
  requiresAuth: true,
}

export default page
