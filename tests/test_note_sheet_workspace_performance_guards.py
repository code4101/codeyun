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


def test_note_sheet_workspace_reuses_normalized_cell_meta_during_document_load():
    source = _workspace_source()
    start = source.index("function loadSheetDocument(document: SheetDocument, sourceDocument?: unknown) {")
    end = source.index("\nfunction clearSaveTimer()", start)
    body = source[start:end]

    assert "const cachedCellMeta = normalizedDocumentCellMetaCache.get(document)" in body
    assert "cellMeta.value = cachedCellMeta" in body
    assert "?? mergeSourceDocumentInlineLinksIntoCellMeta(" in body
    assert "trace?.mark('cell-meta')" in body
    assert "cellMetaCacheHit: !!cachedCellMeta" in body


def test_note_sheet_workspace_traces_sheet_document_normalization_phases():
    source = _workspace_source()
    start = source.index("function normalizeSheetDocument(")
    end = source.index("\nfunction mergeRemoteHeaderPrefixIntoLocalDraft", start)
    body = source[start:end]

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
        "cell-meta-registration-add-action",
        "cell-meta-registration-composite-action",
        "cell-meta",
        "entities",
        "formula-widths",
    ]:
        assert f"normalizeTrace?.mark('{phase}')" in body


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
