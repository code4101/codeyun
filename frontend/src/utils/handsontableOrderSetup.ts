import { AutoColumnSize } from 'handsontable/plugins/autoColumnSize'
import { ContextMenu } from 'handsontable/plugins/contextMenu'
import { CopyPaste } from 'handsontable/plugins/copyPaste'
import { ManualColumnResize } from 'handsontable/plugins/manualColumnResize'
import { ManualRowResize } from 'handsontable/plugins/manualRowResize'
import { StretchColumns } from 'handsontable/plugins/stretchColumns'
import { UndoRedo } from 'handsontable/plugins/undoRedo'
import { registerPlugin } from 'handsontable/plugins/registry'
import { registerLanguageDictionary } from 'handsontable/i18n/registry'
import zhCN from 'handsontable/i18n/languages/zh-CN'

let registered = false

export function registerAttendanceOrderHandsontableModules() {
  if (registered) {
    return
  }

  registered = true

  registerPlugin(AutoColumnSize)
  registerPlugin(ContextMenu)
  registerPlugin(CopyPaste)
  registerPlugin(ManualColumnResize)
  registerPlugin(ManualRowResize)
  registerPlugin(StretchColumns)
  registerPlugin(UndoRedo)
  registerLanguageDictionary(zhCN)
}
