import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import infiniteCanvasPage from './infinite-canvas'
import notesCenterPage from './center'

const pages: AppPageDefinition[] = [
  notesCenterPage,
  infiniteCanvasPage,
]

export default pages
