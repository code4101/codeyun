import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import infiniteCanvasPage from './infinite-canvas'
import eastmoneyPage from './eastmoney'
import freebillPage from './freebill'
import notesCenterPage from './center'
import notesSheetEditorPage from './sheet-editor'
import notesSheetsManagerPage from './sheets-manager'
import notesWorkbookViewPage from './workbook-view'
import notesWechatPage from './wechat'

const pages: AppPageDefinition[] = [
  notesCenterPage,
  eastmoneyPage,
  freebillPage,
  infiniteCanvasPage,
  notesSheetsManagerPage,
  notesWechatPage,
  notesWorkbookViewPage,
  notesSheetEditorPage,
]

export default pages
