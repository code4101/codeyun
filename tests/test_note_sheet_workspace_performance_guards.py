from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_COMPONENT = REPO_ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue"
NOTE_SHEETS_API = REPO_ROOT / "backend/api/note_sheets.py"
NOTE_SHEETS_TS_API = REPO_ROOT / "frontend/src/api/noteSheets.ts"
RESOURCE_VIEW_PAGE = REPO_ROOT / "frontend/src/standard/notes/resource-view/page.vue"


def _workspace_source() -> str:
    return WORKSPACE_COMPONENT.read_text(encoding="utf-8")


def _note_sheets_api_source() -> str:
    return NOTE_SHEETS_API.read_text(encoding="utf-8")


def _note_sheets_ts_api_source() -> str:
    return NOTE_SHEETS_TS_API.read_text(encoding="utf-8")


def _resource_view_source() -> str:
    return RESOURCE_VIEW_PAGE.read_text(encoding="utf-8")


def test_note_sheet_workspace_data_refresh_uses_load_data_instead_of_update_settings():
    source = _workspace_source()

    assert "function loadCurrentHotGridRows(" in source
    assert "const rows = sheetHotGridRows.value" in source
    assert "hot.loadData(rows)" in source
    assert "updateSettings({ data: sheetHotGridRows.value" not in source
    assert "updateSettings({\n      data: sheetHotGridRows.value" not in source


def test_note_sheet_workspace_defers_non_initial_child_components():
    source = _workspace_source()

    assert "import AttendanceFeedbackHistoryList from" not in source
    assert "import NoteSheetAccessDialog from" not in source
    assert "import { StandardColorPickerPopover }" not in source
    assert "from '@/utils/colorToolkit'" not in source
    assert "const AttendanceFeedbackHistoryList = defineAsyncComponent(() => import('@/components/attendance/AttendanceFeedbackHistoryList.vue'))" in source
    assert "const NoteSheetAccessDialog = defineAsyncComponent(() => import('./NoteSheetAccessDialog.vue'))" in source
    assert "const StandardColorPickerPopover = defineAsyncComponent(() => import('@/features/color-tools/components/StandardColorPickerPopover.vue'))" in source
    assert "function mixDuplicateHighlightColors(" in source


def test_note_sheet_workspace_defers_initial_hot_data_until_after_mount():
    source = _workspace_source()
    restore_start = source.index("async function restoreInitialDocument(")
    restore_end = source.index("\nasync function handlePageChange", restore_start)
    restore_body = source[restore_start:restore_end]
    enhance_start = source.index("async function applySheetRenderEnhancement(")
    enhance_end = source.index("\ntype SheetContextMenuItem", enhance_start)
    enhance_body = source[enhance_start:enhance_end]

    assert "const EMPTY_SHEET_HOT_GRID_ROWS: SheetRow[] = []" in source
    assert "const sheetHotInitialGridRows = computed<SheetRow[]>(() => EMPTY_SHEET_HOT_GRID_ROWS)" in source
    assert ':data="sheetHotInitialGridRows"' in source
    assert ':data="sheetHotGridRows"' not in source
    assert "function ensureHotInitialGridRowsLoaded(reason: string)" in source
    assert "async function waitForHotInitialGridRowsLoaded(reason: string)" in source
    assert "hotInitialDataLoadedContentIdentity" in source
    assert "hotInitialDataLoadedInstance" in source
    assert "recordSheetPerfEvent('handsontable.initialLoadData'" in source
    assert "await waitForHotInitialGridRowsLoaded('restore-initial-document')" in restore_body
    assert "trace?.mark(`initial-hot-load-data-${initialHotLoadDataResult}`)" in restore_body
    assert restore_body.index("await nextTick()") < restore_body.index("await waitForHotInitialGridRowsLoaded('restore-initial-document')")
    assert restore_body.index("await waitForHotInitialGridRowsLoaded('restore-initial-document')") < restore_body.index("scheduleSheetRenderEnhancement(")
    assert "ensureHotInitialGridRowsLoaded('render-enhancement')" in enhance_body
    wait_start = source.index("async function waitForHotInitialGridRowsLoaded(reason: string)")
    wait_end = source.index("\nfunction isSheetDocumentHidden", wait_start)
    wait_body = source[wait_start:wait_end]
    assert "requestAnimationFrame" not in wait_body


def test_note_sheet_workspace_load_document_defers_hot_updates_to_vue_wrapper():
    source = _workspace_source()
    match = re.search(
        r"function loadSheetDocument\(document: SheetDocument, sourceDocument\?: unknown\) \{(?P<body>.*?)\n\}",
        source,
        re.S,
    )
    assert match is not None

    body = match.group("body")
    assert "hot-deferred-to-vue" in body
    assert "hotDeferredToVue" in body
    assert "hotApplied: false" in body
    assert "updateHotSettingsWithMergeReset" not in body
    assert ".loadData(" not in body


def test_note_sheet_workspace_keeps_horizontal_column_virtualization_enabled():
    source = _workspace_source()

    assert ':render-all-columns="false"' in source
    assert ':render-all-rows="false"' in source
    assert ':viewport-row-rendering-offset="SHEET_HOT_ROW_RENDERING_OFFSET"' in source
    assert ':auto-column-size="false"' in source
    assert ':viewport-column-rendering-offset="sheetHotViewportColumnRenderingOffset"' in source
    assert ':render-all-columns="rowMarkerColumnCount > 0"' not in source
    assert "const SHEET_HOT_MIN_COLUMN_RENDERING_OFFSET = 3" in source
    assert "const SHEET_HOT_FROZEN_COLUMN_RENDERING_BUFFER = 2" in source
    assert "const SHEET_HOT_ROW_RENDERING_OFFSET = 3" in source
    assert "Math.max(8, fixedHotColumnsStart.value + 6)" not in source


def test_workbook_resource_view_forces_runtime_fill_height_for_virtual_rows():
    source = _workspace_source()
    resource_source = _resource_view_source()
    height_start = source.index("async function updateSheetViewportHeightNow(")
    height_end = source.index("\nfunction handleWindowResize", height_start)
    height_body = source[height_start:height_end]
    grid_height_start = source.index("const sheetGridHeight = computed(() => {")
    grid_height_end = source.index("\n})", grid_height_start) + 3
    grid_height_body = source[grid_height_start:grid_height_end]

    assert "runtimeHeightMode?: SheetHeightMode | null" in source
    assert "runtimeMaxGridHeight?: number | null" in source
    assert "runtimeSheetHeightMode = computed(() => normalizeSheetHeightMode(" in source
    assert "const isContentHeightMode = computed(() => runtimeSheetHeightMode.value === 'content')" in source
    assert "const isContentHeightMode = computed(() => sheetViewSettings.value.height_mode === 'content')" not in source
    assert resource_source.count('runtime-height-mode="fill"') >= 2
    assert "const SHEET_RESOURCE_RUNTIME_MAX_GRID_HEIGHT = 960" in resource_source
    assert resource_source.count(':runtime-max-grid-height="SHEET_RESOURCE_RUNTIME_MAX_GRID_HEIGHT"') >= 2
    assert "Math.min(sheetViewportHeight.value, maxGridHeight)" in grid_height_body
    assert "const SHEET_VIEWPORT_BOTTOM_GAP = 18" in source
    assert "const containerHeight = Math.floor(sheetFrame.clientHeight || frameRect.height)" in height_body
    assert "const windowHeight = Math.floor(window.innerHeight || document.documentElement.clientHeight || 0)" in height_body
    assert "windowHeight - frameRect.top - SHEET_VIEWPORT_BOTTOM_GAP" in height_body
    assert "Math.min(containerHeight, viewportRemainingHeight)" in height_body


def test_note_sheet_workspace_coalesces_viewport_height_updates():
    source = _workspace_source()
    update_start = source.index("function updateSheetViewportHeight(")
    update_end = source.index("\nasync function updateSheetViewportHeightNow", update_start)
    update_body = source[update_start:update_end]
    height_start = source.index("async function updateSheetViewportHeightNow(")
    height_end = source.index("\nfunction handleWindowResize", height_start)
    height_body = source[height_start:height_end]

    assert "let pendingSheetViewportHeightUpdate: Promise<void> | null = null" in source
    assert "let pendingSheetViewportHeightUpdateReasons = new Set<string>()" in source
    assert "if (pendingSheetViewportHeightUpdate)" in update_body
    assert "return pendingSheetViewportHeightUpdate" in update_body
    assert "updateSheetViewportHeightNow(() => (" in update_body
    assert "pendingSheetViewportHeightUpdateReasons = new Set<string>()" in update_body
    assert "reason: getReason()" in height_body


def test_note_sheet_workspace_defers_defined_name_fetch_until_after_sheet_load():
    source = _workspace_source()

    assert "function scheduleDefinedNamesSyncAfterSheetLoad(" in source
    assert "trace?.mark('defined-names-deferred')" in source
    assert "await syncDefinedNamesForRestoreRequest(" not in source


def test_note_sheet_workspace_reuses_formula_display_state_during_document_load():
    source = _workspace_source()
    start = source.index("function loadSheetDocument(document: SheetDocument, sourceDocument?: unknown) {")
    end = source.index("\nfunction clearSaveTimer()", start)
    body = source[start:end]

    assert "function hasFormulaDisplayCells(" in source
    assert "function rowsHaveFormulaExpressions(" in source
    assert "function shouldReuseCachedFormulaDisplayState(" in source
    assert "const cachedFormulaDisplayState = normalizedDocumentFormulaDisplayCache.get(document)" in body
    assert "const formulaDisplayCacheReusable = shouldReuseCachedFormulaDisplayState(" in body
    assert "const formulaDisplayForWidths = formulaDisplayCacheReusable" in body
    assert "buildFormulaDisplayStateForRows(normalizedHeaders, normalizedRows, columnConfigs.value)" in body
    assert "formulaDisplayState.value = formulaDisplayForWidths" in body
    assert "refreshFormulaDisplayState()" not in body
    assert "formulaDisplayCacheHit: formulaDisplayCacheReusable" in body


def test_note_sheet_workspace_does_not_reuse_empty_formula_cache_after_engine_preload():
    source = _workspace_source()
    helper_start = source.index("function shouldReuseCachedFormulaDisplayState(")
    helper_end = source.index("\nfunction buildFormulaDisplayStateForRows", helper_start)
    helper_body = source[helper_start:helper_end]
    load_start = source.index("function loadSheetDocument(document: SheetDocument, sourceDocument?: unknown) {")
    load_end = source.index("\nfunction clearSaveTimer()", load_start)
    load_body = source[load_start:load_end]

    assert "if (!formulaEngineClass.value || hasFormulaDisplayCells(cachedState))" in helper_body
    assert "const formulaHeaderRows = getCurrentFormulaHeaderRows(headers)" in helper_body
    assert "return !rowsHaveFormulaExpressions(formulaHeaderRows) && !rowsHaveFormulaExpressions(sourceRows)" in helper_body
    assert load_body.index("const formulaDisplayCacheReusable = shouldReuseCachedFormulaDisplayState(") < load_body.index("const formulaDisplayForWidths = formulaDisplayCacheReusable")
    assert "formulaDisplayCacheReusable\n    ? cachedFormulaDisplayState" in load_body
    assert ": buildFormulaDisplayStateForRows(normalizedHeaders, normalizedRows, columnConfigs.value)" in load_body


def test_note_sheet_workspace_sheet_perf_is_url_scoped_and_runtime_events_are_lightweight():
    source = _workspace_source()
    read_start = source.index("function readSheetPerfLoggingEnabledPreference()")
    read_end = source.index("\nfunction getDefaultRichTextInlineToolbarColors", read_start)
    read_body = source[read_start:read_end]
    set_start = source.index("function setSheetPerfLoggingEnabled(enabled: boolean)")
    set_end = source.index("\nfunction getSheetPerfBaseContext", set_start)
    set_body = source[set_start:set_end]
    frame_start = source.index("function startSheetPerfFrameMonitor()")
    frame_end = source.index("\nfunction stopSheetPerfFrameMonitor", frame_start)
    frame_body = source[frame_start:frame_end]
    longtask_start = source.index("function startSheetPerfLongTaskObserver()")
    longtask_end = source.index("\nfunction stopSheetPerfLongTaskObserver", longtask_start)
    longtask_body = source[longtask_start:longtask_end]

    assert "SHEET_PERF_FRAME_GAP_LOG_INTERVAL_MS" in source
    assert "SHEET_PERF_RUNTIME_EVENT_LOG_LIMIT" in source
    assert "function shouldRecordSheetPerfRuntimeEvent(" in source
    assert "localStorage" not in read_body
    assert "localStorage" not in set_body
    assert "getSheetPerfHotViewport()" not in frame_body
    assert "getSheetPerfHotViewport()" not in longtask_body
    assert "shouldRecordSheetPerfRuntimeEvent(timestamp)" in frame_body
    assert "shouldRecordSheetPerfRuntimeEvent(entry.startTime)" in longtask_body


def test_note_sheet_workspace_skips_duplicate_formula_refresh_after_document_load():
    source = _workspace_source()
    load_start = source.index("function loadSheetDocument(document: SheetDocument, sourceDocument?: unknown) {")
    load_end = source.index("\nfunction clearSaveTimer()", load_start)
    load_body = source[load_start:load_end]
    watcher_start = source.index("watch(\n  [rows, columnHeaders, headerGroups, columnConfigs, sheetViewSettings],")
    watcher_end = source.index("\nwatch(\n  () => {", watcher_start)
    watcher_body = source[watcher_start:watcher_end]

    assert "let suppressNextFormulaDisplayStructureWatcher = false" in source
    assert "suppressNextFormulaDisplayStructureWatcher = true" in load_body
    assert "formulaDisplayState.value = formulaDisplayForWidths" in load_body
    assert "if (suppressNextFormulaDisplayStructureWatcher)" in watcher_body
    assert "suppressNextFormulaDisplayStructureWatcher = false" in watcher_body
    assert "refreshFormulaDisplayState()" in watcher_body


def test_note_sheet_workspace_records_manual_hot_render_reasons():
    source = _workspace_source()
    patch_start = source.index("function patchSheetPerfHotInstance()")
    patch_end = source.index("\nfunction unpatchSheetPerfHotInstance()", patch_start)
    patch_body = source[patch_start:patch_end]
    helper_start = source.index("function renderHotWithReason(")
    helper_end = source.index("\nfunction startSheetPerfFrameMonitor()", helper_start)
    helper_body = source[helper_start:helper_end]
    source_without_helper = source[:helper_start] + source[helper_end:]

    assert "__codeyunSheetPerfPendingRenderReason?: string" in source
    assert "const reason = hot.__codeyunSheetPerfPendingRenderReason ?? ''" in patch_body
    assert "reason: reason || 'unattributed'" in patch_body
    assert "delete hot.__codeyunSheetPerfPendingRenderReason" in patch_body
    assert "function renderCurrentHotWithReason(reason: string)" in helper_body
    assert "patchedHot.__codeyunSheetPerfPendingRenderReason = reason" in helper_body

    for reason in [
        "workspace-view-restore",
        "viewport-height-changed",
        "row-heights-refresh",
        "grid-structure-refresh",
        "formula-engine-loaded",
        "cell-change",
        "cell-meta-set",
        "access-capabilities",
        "hidden-rows-filter",
    ]:
        assert f"'{reason}'" in source

    assert "getHotInstance()?.render()" not in source_without_helper
    assert "hot?.render()" not in source_without_helper
    assert not re.search(r"(?<!\.)\bhot\.render\(\)", source_without_helper)


def test_note_sheet_workspace_skips_defined_names_render_when_formula_context_unchanged():
    source = _workspace_source()
    sync_start = source.index("function syncDefinedNamesFromResponse(")
    sync_end = source.index("\nfunction normalizeColumnConfigs", sync_start)
    sync_body = source[sync_start:sync_end]
    schedule_start = source.index("function scheduleDefinedNamesSyncAfterSheetLoad(")
    schedule_end = source.index("\nfunction sheetDocumentHasFormulaExpressions", schedule_start)
    schedule_body = source[schedule_start:schedule_end]

    assert "function areDefinedNameItemsEqual(" in source
    assert "function areDefinedNameWorksheetScopesEqual(" in source
    assert "const formulaContextChanged =" in sync_body
    assert "return formulaContextChanged" in sync_body
    assert ".then((formulaContextChanged) => {" in schedule_body
    assert "if (!formulaContextChanged)" in schedule_body
    assert "renderCurrentHotWithReason('deferred-defined-names-sync')" in schedule_body


def test_note_sheet_workspace_uses_embedded_defined_names_context_before_first_mount():
    source = _workspace_source()
    restore_start = source.index("async function restoreInitialDocument(")
    restore_end = source.index("\nasync function handlePageChange", restore_start)
    restore_body = source[restore_start:restore_end]
    api_source = _note_sheets_ts_api_source()

    assert "defined_names_context?: NoteSheetDefinedNamesResponse | null" in api_source
    assert "function getUsableDefinedNamesContext(" in source
    assert "const embeddedDefinedNamesContext = getUsableDefinedNamesContext(" in restore_body
    assert "syncDefinedNamesFromResponse(embeddedDefinedNamesContext)" in restore_body
    assert "trace?.mark('defined-names-embedded')" in restore_body
    assert "scheduleDefinedNamesSyncAfterSheetLoad(" in restore_body
    assert restore_body.index("syncDefinedNamesFromResponse(embeddedDefinedNamesContext)") < restore_body.index("loadSheetDocument(activeDocument, activeSourceDocument)")


def test_workbook_prefetch_includes_workbook_context_for_initial_sheet_detail():
    source = _resource_view_source()
    load_start = source.index("async function loadWorkbookResource()")
    load_end = source.index("\n\nfunction handleSheetTabClick", load_start)
    load_body = source[load_start:load_end]

    assert "fetchNoteSheet(targetSheetId, {" in load_body
    assert "workbookId: targetWorkbookId" in load_body
    assert "includeWorkbookContext: true" in load_body
    assert "includeWorkbookContext: false" not in load_body


def test_note_sheet_detail_serializes_embedded_defined_names_context():
    source = _note_sheets_api_source()
    detail_start = source.index("class NoteSheetDetailResponse(")
    detail_end = source.index("\n\nclass NoteSheetPaginationResponse", detail_start)
    detail_body = source[detail_start:detail_end]
    serialize_start = source.index("def _serialize_sheet_detail(")
    serialize_end = source.index("\n\ndef _build_sheet_defined_names_context", serialize_start)
    serialize_body = source[serialize_start:serialize_end]
    helper_start = source.index("def _build_sheet_defined_names_context(")
    helper_end = source.index("\n\ndef _parse_table_cell_reference", helper_start)
    helper_body = source[helper_start:helper_end]
    get_start = source.index("def get_note_sheet(")
    get_end = source.index("\n\n@router.post(\"/sheets/{sheet_id}/query\")", get_start)
    get_body = source[get_start:get_end]
    query_start = source.index("def query_note_sheet(")
    query_end = source.index("\n\n@router.get(\"/sheets/{sheet_id}/column-options\"", query_start)
    query_body = source[query_start:query_end]

    assert "defined_names_context: Optional[dict[str, Any]] = None" in detail_body
    assert "defined_names_context: dict[str, Any] | None = None" in serialize_body
    assert '"defined_names_context": defined_names_context' in serialize_body
    assert '"workbook": workbook_names' in helper_body
    assert '"worksheets": [{' in helper_body
    assert "_merge_effective_defined_names(workbook_names, worksheet_names)" in helper_body
    assert "workbook if include_workbook_context else None" in get_body
    assert "workbook if payload.include_workbook_context else None" in query_body


def test_note_sheet_workspace_renders_formula_engine_loaded_only_when_display_changes():
    source = _workspace_source()
    ensure_start = source.index("async function ensureFormulaEngineLoaded()")
    ensure_end = source.index("\nfunction createEmptyFormulaDisplayState", ensure_start)
    ensure_body = source[ensure_start:ensure_end]
    refresh_start = source.index("function refreshFormulaDisplayState()")
    refresh_end = source.index("\nfunction getFormulaDisplayTextFromState", refresh_start)
    refresh_body = source[refresh_start:refresh_end]

    assert "function areFormulaDisplayStatesVisuallyEqual(" in source
    assert "const changed = !areFormulaDisplayStatesVisuallyEqual(" in refresh_body
    assert "return changed" in refresh_body
    assert "const formulaDisplayChanged = refreshFormulaDisplayState()" in ensure_body
    assert "if (formulaDisplayChanged)" in ensure_body
    assert "renderCurrentHotWithReason('formula-engine-loaded')" in ensure_body


def test_note_sheet_workspace_preloads_formula_engine_before_initial_document_mount():
    source = _workspace_source()
    load_start = source.index("function loadFormulaEngineClass()")
    load_end = source.index("\nasync function ensureFormulaEngineLoaded()", load_start)
    load_body = source[load_start:load_end]
    preload_start = source.index("async function waitForInitialFormulaEnginePreload(")
    preload_end = source.index("\nfunction createEmptyFormulaDisplayState", preload_start)
    preload_body = source[preload_start:preload_end]
    restore_start = source.index("async function restoreInitialDocument(")
    restore_end = source.index("\nasync function handlePageChange", restore_start)
    restore_body = source[restore_start:restore_end]

    assert "const FORMULA_ENGINE_INITIAL_PRELOAD_BUDGET_MS = 45" in source
    assert "renderCurrentHotWithReason('formula-engine-loaded')" not in load_body
    assert "refreshFormulaDisplayState()" not in load_body
    assert "Promise.race([" in preload_body
    assert "formula-engine-preload-" in restore_body
    assert "waitForInitialFormulaEnginePreload(loadFormulaEngineClass())" in restore_body
    assert restore_body.index("waitForInitialFormulaEnginePreload(loadFormulaEngineClass())") < restore_body.index("loadSheetDocument(activeDocument, activeSourceDocument)")


def test_note_sheet_workspace_records_early_perf_session_asset_identity():
    source = _workspace_source()
    helper_start = source.index("function getSheetPerfRuntimeAssetDetail()")
    helper_end = source.index("\nfunction clearSheetPerfLogFlushTimer", helper_start)
    helper_body = source[helper_start:helper_end]
    install_start = source.index("function installSheetPerfLogger()")
    install_end = source.index("\nfunction uninstallSheetPerfLogger()", install_start)
    install_body = source[install_start:install_end]

    assert "workspaceModuleUrl: import.meta.url" in helper_body
    assert "sheet.perfSession" in install_body
    assert "detail: getSheetPerfRuntimeAssetDetail()" in install_body
    assert "scheduleSheetPerfLogFlush(100)" in install_body


def test_note_sheet_workspace_reuses_normalized_cell_meta_during_document_load():
    source = _workspace_source()
    start = source.index("function loadSheetDocument(document: SheetDocument, sourceDocument?: unknown) {")
    end = source.index("\nfunction clearSaveTimer()", start)
    body = source[start:end]

    assert "const cachedCellMeta = normalizedDocumentCellMetaCache.get(document)" in body
    assert "cellMeta.value = cachedCellMeta" in body
    assert "?? finalizeNormalizedSheetCellMeta(" in body
    assert "mergeSourceDocumentInlineLinksIntoCellMeta(" in body
    assert "trace?.mark('cell-meta')" in body
    assert "cellMetaCacheHit: !!cachedCellMeta" in body


def test_note_sheet_workspace_traces_sheet_document_normalization_phases():
    source = _workspace_source()
    start = source.index("function normalizeSheetDocument(")
    end = source.index("\nfunction mergeRemoteHeaderPrefixIntoLocalDraft", start)
    body = source[start:end]
    helper_start = source.index("function finalizeNormalizedSheetCellMeta(")
    helper_end = source.index("\nfunction cellMetaHasActionType", helper_start)
    helper_body = source[helper_start:helper_end]

    assert "startSheetPerfTrace('sheet.normalizeDocument'" in body
    assert "normalizedDocumentFormulaDisplayCache.set(normalizedDocument, formulaDisplayForWidths)" in body
    assert "normalizedDocumentCellMetaCache.set(normalizedDocument, normalizedCellMeta)" in body
    for phase in [
        "headers-config",
        "grid-settings",
        "source-rows",
        "merge-grid",
        "cell-meta-source",
        "cell-meta-inline-header-links",
        "cell-meta-inline-row-links",
        "cell-meta-inline-grid-links",
        "cell-meta-legacy-actions",
        "cell-meta-registration-pruned-actions",
        "cell-meta-registration-add-action",
        "cell-meta-registration-user-match-action",
        "cell-meta-registration-composite-action",
        "cell-meta",
        "entities",
        "formula-display",
        "formula-widths",
    ]:
        target = helper_body if phase.startswith("cell-meta-registration") or phase == "cell-meta-legacy-actions" else body
        assert f"normalizeTrace?.mark('{phase}')" in target


def test_note_sheet_workspace_skips_formula_display_width_scan_until_formula_engine_is_loaded():
    source = _workspace_source()
    start = source.index("function normalizeSheetDocument(")
    end = source.index("\nfunction mergeRemoteHeaderPrefixIntoLocalDraft", start)
    body = source[start:end]

    assert "const formulaDisplayForWidths = formulaEngineClass.value" in body
    assert "? buildFormulaDisplayStateForRows(" in body
    assert ": createEmptyFormulaDisplayState(dataStartRow)" in body
    assert "normalizedDocumentFormulaDisplayCache.set(normalizedDocument, formulaDisplayForWidths)" in body


def test_note_sheet_workspace_uses_persisted_column_widths_before_auto_measuring():
    source = _workspace_source()
    start = source.index("function normalizeSheetDocument(")
    end = source.index("\nfunction mergeRemoteHeaderPrefixIntoLocalDraft", start)
    body = source[start:end]

    expected = """const normalizedWidths = headers.map((_, index) => {
    const width = Number(sourceWidths[index])
    if (Number.isFinite(width) && width > 0) {
      return normalizeColumnWidthValue(width)
    }
    return getAutoColumnWidth(index, headers, normalizedRows, normalizedColumnConfigs, formulaDisplayForWidths)
  })"""
    assert expected in body
    assert "widthMode === 'fixed' && Number.isFinite(width)" not in body


def test_note_sheet_workspace_renderer_reuses_cell_meta_entry_for_data_cells():
    source = _workspace_source()
    start = source.index("function handleAfterRenderer(")
    end = source.index("\nfunction getExcelColumnLabel", start)
    body = source[start:end]

    assert "const cellMetaEntry = documentRow >= 0 && renderColumn >= 0" in body
    assert "const link = cellMetaEntry?.link ?? null" in body
    assert "const sourceAction = cellMetaEntry?.action ?? null" in body
    assert "const cellStyle = cellMetaEntry?.style ?? null" in body
    assert "normalizeRichTextForText(cellMetaEntry?.rich_text, rawText)" in body
    assert "getCellLinkAt(documentRow, renderColumn)" not in body
    assert "getCellActionAt(documentRow, renderColumn)" not in body
    assert "getCellStyleAt(documentRow, renderColumn)" not in body
    assert "getCellRichTextAt(documentRow, renderColumn, rawText)" not in body


def test_note_sheet_workspace_batches_inline_cell_meta_normalization():
    source = _workspace_source()
    start = source.index("function normalizeSheetDocument(")
    end = source.index("\nfunction mergeRemoteHeaderPrefixIntoLocalDraft", start)
    body = source[start:end]

    assert "{ normalizeResult: false }" in body
    assert "const sourceCellMetaWithInlineGridLinks = hasUnifiedGridRows" in body
    assert "const sourceCellMetaWithInlineLinks = normalizeCellMetaMap(sourceCellMetaWithInlineGridLinks, headers.length)" in body


def test_note_sheet_workspace_uses_precomputed_legacy_action_label_lookup():
    source = _workspace_source()

    assert "const LEGACY_SHEET_CELL_ACTION_LABEL_TYPES = new Map" in source
    start = source.index("function normalizeLegacySheetCellActionType(")
    end = source.index("\nfunction addLegacySheetCellActions", start)
    body = source[start:end]

    assert "LEGACY_SHEET_CELL_ACTION_LABEL_TYPES.get(compactValue)" in body
    assert "legacyLabels" not in body
    assert ".find(" not in body


def test_note_sheet_workspace_keeps_single_registration_order_match_host_when_wechat_column_exists():
    source = _workspace_source()
    start = source.index("function resolveRegistrationOrderMatchHostHeader(")
    end = source.index("\nfunction isRegistrationActionHostColumnInHeaders", start)
    body = source[start:end]

    assert "if (hasNormalizedHeader(headers, '微信支付订单号'))" in body
    assert "return '微信支付订单号'" in body
    assert "return hasNormalizedHeader(headers, '错误手机号') ? '错误手机号' : ''" in body

    host_start = source.index("function isRegistrationActionHostColumnInHeaders(")
    host_end = source.index("\nfunction isSheetCellActionAllowedInRows", host_start)
    host_body = source[host_start:host_end]
    assert "return header === resolveRegistrationOrderMatchHostHeader(headers)" in host_body
    assert "header === '错误手机号' || header === '微信支付订单号'" not in host_body


def test_note_sheet_workspace_uses_legacy_action_text_to_override_misaligned_meta():
    source = _workspace_source()
    start = source.index("function addLegacySheetCellActions(")
    end = source.index("\nfunction pruneInvalidRegistrationHeaderActions", start)
    body = source[start:end]

    assert "if (currentMeta?.action?.type === actionType)" in body
    assert "if (currentMeta?.action) {" not in body


def test_note_sheet_workspace_prunes_invalid_registration_header_actions():
    source = _workspace_source()
    start = source.index("function pruneInvalidRegistrationHeaderActions(")
    end = source.index("\nfunction finalizeNormalizedSheetCellMeta", start)
    body = source[start:end]

    assert "isSheetCellActionAllowedInRows(" in body
    assert "delete nextEntry.action" in body
    assert "return changed ? normalizeCellMetaMap(nextMeta, headers.length) : sourceMeta" in body


def test_note_sheet_workspace_reuses_normalized_action_pipeline_for_cell_meta_fallback():
    source = _workspace_source()
    helper_start = source.index("function finalizeNormalizedSheetCellMeta(")
    helper_end = source.index("\nfunction cellMetaHasActionType", helper_start)
    helper_body = source[helper_start:helper_end]

    assert "addLegacySheetCellActions(" in helper_body
    assert "pruneInvalidRegistrationHeaderActions(" in helper_body
    assert "addDefaultRegistrationUserMatchAction(" in helper_body
    assert "addDefaultRegistrationCompositeUpdateAction(" in helper_body

    load_start = source.index("function loadSheetDocument(")
    load_end = source.index("\nfunction clearSaveTimer()", load_start)
    load_body = source[load_start:load_end]
    assert "const cachedCellMeta = normalizedDocumentCellMetaCache.get(document)" in load_body
    assert "?? finalizeNormalizedSheetCellMeta(" in load_body


def test_note_sheet_workspace_restores_default_registration_user_match_action():
    source = _workspace_source()
    helper_start = source.index("function canRestoreDefaultSheetCellActionInRows(")
    helper_end = source.index("\nfunction resolveRegistrationOrderMatchHostHeader", helper_start)
    helper_body = source[helper_start:helper_end]

    assert "currentMeta.action.type !== targetActionType" in helper_body
    assert "normalizeLegacySheetCellActionType(cellText) === targetActionType" in helper_body

    start = source.index("function addDefaultRegistrationUserMatchAction(")
    end = source.index("\nfunction splitColumnNoteLeadingLink", start)
    body = source[start:end]

    assert "const userIdColumn = headers.findIndex" in body
    assert "actionType === SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH" in body
    assert "actionType === SHEET_CELL_ACTION_REGISTRATION_COMPOSITE_UPDATE" in body
    assert "canRestoreDefaultSheetCellActionInRows(" in body
    assert "type: SHEET_CELL_ACTION_REGISTRATION_USER_MATCH" in body
    assert "label: SHEET_CELL_ACTION_LABELS[SHEET_CELL_ACTION_REGISTRATION_USER_MATCH]" in body


def test_note_sheet_workspace_reconciles_header_entity_actions_with_normalized_cell_meta():
    source = _workspace_source()
    helper_start = source.index("function reconcileHeaderEntityCellMeta(")
    helper_end = source.index("\nfunction replaceEntityCellMeta", helper_start)
    helper_body = source[helper_start:helper_end]

    assert "if (normalizedMeta?.action)" in helper_body
    assert "delete nextMeta.action" in helper_body

    init_start = source.index("function initializeSheetEntitiesFromDocument(")
    init_end = source.index("\nfunction isFormulaExpression", init_start)
    init_body = source[init_start:init_end]

    assert "documentRow < dataStartRow" in init_body
    assert "reconcileHeaderEntityCellMeta(legacyMeta, existingMeta)" in init_body


def test_note_sheet_workspace_does_not_renormalize_cell_meta_after_row_key_transforms():
    source = _workspace_source()

    for function_name in [
        "shiftNormalizedCellMetaRowKeys",
        "insertNormalizedCellMetaRowKeys",
    ]:
        start = source.index(f"function {function_name}(")
        end = source.index("\n}", start)
        body = source[start:end]
        assert "normalizeCellMetaMap(" not in body


def test_note_sheet_workspace_splits_sheet_load_vue_flush_phases():
    source = _workspace_source()
    start = source.index("async function restoreInitialDocument(")
    end = source.index("\nasync function handlePageChange(", start)
    body = source[start:end]

    expected_order = [
        "trace?.mark('load-document')",
        "trace?.mark('content-ready-state')",
        "trace?.mark('cache-remote-detail-deferred')",
        "trace?.mark('restore-local-ui-state')",
        "trace?.mark('reset-runtime-state')",
        "await nextTick()",
        "trace?.mark('next-tick')",
    ]
    last_index = -1
    for marker in expected_order:
        marker_index = body.index(marker)
        assert marker_index > last_index
        last_index = marker_index


def test_note_sheet_workspace_defers_remote_detail_cache_until_after_sheet_load():
    source = _workspace_source()
    start = source.index("async function restoreInitialDocument(")
    end = source.index("\nasync function handlePageChange(", start)
    body = source[start:end]

    assert "scheduleRemoteSheetDetailCacheAfterSheetLoad(remote," in body
    assert "trace?.mark('cache-remote-detail-deferred')" in body
    assert "cacheRemoteSheetDetail(remote," not in body


def test_note_sheet_workspace_skips_duplicate_row_height_refresh_during_sheet_load():
    source = _workspace_source()
    start = source.index("async function restoreInitialDocument(")
    end = source.index("\nasync function handlePageChange(", start)
    body = source[start:end]

    assert "startSheetPerfTrace('sheet.rowHeights'" in source
    assert "refreshComputedRowHeights(" not in body
