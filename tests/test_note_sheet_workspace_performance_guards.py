from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_COMPONENT = REPO_ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue"


def _workspace_source() -> str:
    return WORKSPACE_COMPONENT.read_text(encoding="utf-8")


def test_note_sheet_workspace_data_refresh_uses_load_data_instead_of_update_settings():
    source = _workspace_source()

    assert "function loadCurrentHotGridRows(" in source
    assert "hot.loadData(sheetHotGridRows.value)" in source
    assert "updateSettings({ data: sheetHotGridRows.value" not in source
    assert "updateSettings({\n      data: sheetHotGridRows.value" not in source


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
    assert ':viewport-column-rendering-offset="sheetHotViewportColumnRenderingOffset"' in source
    assert ':render-all-columns="rowMarkerColumnCount > 0"' not in source


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

    assert "const cachedFormulaDisplayState = normalizedDocumentFormulaDisplayCache.get(document)" in body
    assert "const formulaDisplayForWidths = cachedFormulaDisplayState" in body
    assert "?? buildFormulaDisplayStateForRows(" in body
    assert "formulaDisplayState.value = formulaDisplayForWidths" in body
    assert "refreshFormulaDisplayState()" not in body
    assert "formulaDisplayCacheHit: !!cachedFormulaDisplayState" in body


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
