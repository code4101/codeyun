import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'PdfDocumentLibrary',
  canonicalPath: '/notes/library',
  component: () => import('./page.vue'),
  permissionKey: 'notes.pdfs',
  requiresAuth: true,
}

export default page
