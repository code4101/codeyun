import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesWechat',
  canonicalPath: '/notes/wechat-data',
  component: () => import('./page.vue'),
  permissionKey: 'notes.wechat',
  requiresAuth: true,
}

export default page
