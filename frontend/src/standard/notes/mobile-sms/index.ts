import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesMobileSms',
  canonicalPath: '/notes/mobile-sms',
  component: () => import('./page.vue'),
  permissionKey: 'notes.mobile-sms',
  requiresAuth: true,
}

export default page
