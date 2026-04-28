import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import infiniteCanvasPage from './infinite-canvas'
import notesCenterPage from './center'
import notesSheetEditorPage from './sheet-editor'
import notesSheetsManagerPage from './sheets-manager'
import notesWorkbookViewPage from './workbook-view'

const pages: AppPageDefinition[] = [
  notesCenterPage,
  infiniteCanvasPage,
  notesSheetsManagerPage,
  notesWorkbookViewPage,
  notesSheetEditorPage,
]

export default pages
