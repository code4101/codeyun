import { AutoColumnSize } from 'handsontable/plugins/autoColumnSize'
import { AutoRowSize } from 'handsontable/plugins/autoRowSize'
import { Autofill } from 'handsontable/plugins/autofill'
import { ContextMenu } from 'handsontable/plugins/contextMenu'
import { CopyPaste } from 'handsontable/plugins/copyPaste'
import { DragToScroll } from 'handsontable/plugins/dragToScroll'
import { HiddenColumns } from 'handsontable/plugins/hiddenColumns'
import { HiddenRows } from 'handsontable/plugins/hiddenRows'
import { ManualColumnMove } from 'handsontable/plugins/manualColumnMove'
import { ManualColumnResize } from 'handsontable/plugins/manualColumnResize'
import { ManualRowMove } from 'handsontable/plugins/manualRowMove'
import { ManualRowResize } from 'handsontable/plugins/manualRowResize'
import { MergeCells } from 'handsontable/plugins/mergeCells'
import { MultipleSelectionHandles } from 'handsontable/plugins/multipleSelectionHandles'
import { StretchColumns } from 'handsontable/plugins/stretchColumns'
import { TouchScroll } from 'handsontable/plugins/touchScroll'
import { UndoRedo } from 'handsontable/plugins/undoRedo'
import { registerPlugin } from 'handsontable/plugins/registry'
import { registerLanguageDictionary } from 'handsontable/i18n/registry'
import zhCN from 'handsontable/i18n/languages/zh-CN'

let registered = false

export function registerCodeyunHandsontableModules() {
  if (registered) {
    return
  }

  registered = true

  registerPlugin(AutoColumnSize)
  registerPlugin(AutoRowSize)
  registerPlugin(Autofill)
  registerPlugin(ContextMenu)
  registerPlugin(CopyPaste)
  registerPlugin(DragToScroll)
  registerPlugin(HiddenColumns)
  registerPlugin(HiddenRows)
  registerPlugin(ManualColumnMove)
  registerPlugin(ManualColumnResize)
  registerPlugin(ManualRowMove)
  registerPlugin(ManualRowResize)
  registerPlugin(MergeCells)
  registerPlugin(MultipleSelectionHandles)
  registerPlugin(StretchColumns)
  registerPlugin(TouchScroll)
  registerPlugin(UndoRedo)
  registerLanguageDictionary(zhCN)
}
