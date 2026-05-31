import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import infiniteCanvasPage from './infinite-canvas'
import notesChatDataPage from './chat-data'
import commonSitesPage from './common-sites'
import eastmoneyPage from './eastmoney'
import freebillPage from './freebill'
import notesCenterPage from './center'
import notesSheetEditorPage from './sheet-editor'
import notesSheetsManagerPage from './sheets-manager'
import notesTrashPage from './trash'
import notesWorkbookViewPage from './workbook-view'
import notesWechatPage from './wechat'
import notesQqPage from './qq'
import notesWechatStoragePage from './wechat/storage'

const pages: AppPageDefinition[] = [
  notesCenterPage,
  notesChatDataPage,
  commonSitesPage,
  eastmoneyPage,
  freebillPage,
  infiniteCanvasPage,
  notesSheetsManagerPage,
  notesTrashPage,
  notesWechatPage,
  notesQqPage,
  notesWechatStoragePage,
  notesWorkbookViewPage,
  notesSheetEditorPage,
]

export default pages
