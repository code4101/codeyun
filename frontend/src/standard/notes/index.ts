import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import infiniteCanvasPage from './infinite-canvas'
import notesChatDataPage from './chat-data'
import commonSitesPage from './common-sites'
import eastmoneyPage from './eastmoney'
import eastmoneyRobotHistoryPage from './eastmoney/robot-history'
import eastmoneySyncPage from './eastmoney/sync'
import eastmoneyTradePage from './eastmoney/trade'
import freebillPage from './freebill'
import githubProjectsPage from './github-projects'
import notesCenterPage from './center'
import notesTaskSystemPage from './task-system'
import notesSheetEditorPage from './sheet-editor'
import notesSheetsManagerPage from './sheets-manager'
import notesTrashPage from './trash'
import notesWorkbookViewPage from './workbook-view'
import notesWechatPage from './wechat'
import notesQqPage from './qq'
import notesWechatStoragePage from './wechat/storage'
import notesMobileSmsPage from './mobile-sms'

const pages: AppPageDefinition[] = [
  notesCenterPage,
  notesTaskSystemPage,
  notesChatDataPage,
  githubProjectsPage,
  commonSitesPage,
  eastmoneyPage,
  eastmoneyTradePage,
  eastmoneyRobotHistoryPage,
  eastmoneySyncPage,
  freebillPage,
  infiniteCanvasPage,
  notesSheetsManagerPage,
  notesTrashPage,
  notesWechatPage,
  notesQqPage,
  notesWechatStoragePage,
  notesMobileSmsPage,
  notesWorkbookViewPage,
  notesSheetEditorPage,
]

export default pages
