import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesChatData',
  canonicalPath: '/notes/chat-data',
  component: () => import('./page.vue'),
  permissionKey: 'notes.chat-data',
}

export default page
