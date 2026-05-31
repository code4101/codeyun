import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesWechatStorage',
  canonicalPath: '/notes/wechat-data/storage',
  component: () => import('./page.vue'),
  permissionKey: 'notes.wechat.storage',
  requiresAuth: true,
}

export default page
