from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_COMPONENT = REPO_ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue"
NOTE_SHEETS_API = REPO_ROOT / "backend/api/note_sheets.py"
BACKEND_MODELS = REPO_ROOT / "backend/models.py"
MIGRATION_MANAGER = REPO_ROOT / "backend/migrations/manager.py"
SNAPSHOT_BACKFILL_SCRIPT = REPO_ROOT / "scripts/backfill_note_sheet_page_snapshots.py"
NOTE_SHEETS_TS_API = REPO_ROOT / "frontend/src/api/noteSheets.ts"
RESOURCE_VIEW_PAGE = REPO_ROOT / "frontend/src/standard/notes/resource-view/page.vue"
FRONTEND_MAIN_TS = REPO_ROOT / "frontend/src/main.ts"
FRONTEND_INDEX_HTML = REPO_ROOT / "frontend/index.html"
FRONTEND_API_INDEX_TS = REPO_ROOT / "frontend/src/api/index.ts"


def _workspace_source() -> str:
    return WORKSPACE_COMPONENT.read_text(encoding="utf-8")


def _note_sheets_api_source() -> str:
    return NOTE_SHEETS_API.read_text(encoding="utf-8")


def _backend_models_source() -> str:
    return BACKEND_MODELS.read_text(encoding="utf-8")


def _migration_manager_source() -> str:
    return MIGRATION_MANAGER.read_text(encoding="utf-8")


def _snapshot_backfill_script_source() -> str:
    return SNAPSHOT_BACKFILL_SCRIPT.read_text(encoding="utf-8")


def _note_sheets_ts_api_source() -> str:
    return NOTE_SHEETS_TS_API.read_text(encoding="utf-8")


def _resource_view_source() -> str:
    return RESOURCE_VIEW_PAGE.read_text(encoding="utf-8")


def _frontend_main_source() -> str:
    return FRONTEND_MAIN_TS.read_text(encoding="utf-8")


def _frontend_index_source() -> str:
    return FRONTEND_INDEX_HTML.read_text(encoding="utf-8")


def _frontend_api_index_source() -> str:
    return FRONTEND_API_INDEX_TS.read_text(encoding="utf-8")


def test_note_sheet_workspace_data_refresh_uses_load_data_instead_of_update_settings():
    source = _workspace_source()

    assert "function loadCurrentHotGridRows(" in source
    assert "const rows = sheetHotGridRows.value" in source
    assert "hot.loadData(rows)" in source
    assert "updateSettings({ data: sheetHotGridRows.value" not in source
    assert "updateSettings({\n      data: sheetHotGridRows.value" not in source


def test_hidden_columns_do_not_fall_back_to_handsontable_default_width():
    source = _workspace_source()

    assert "const hotHiddenColumnIndexSet = computed(" in source
    assert "hotHiddenColumnIndexSet.value.has(hotColumn) ? 0.1 : width" in source
    assert "hot.addHook('modifyColWidth', handleModifyColWidth, 3)" in source
    assert ':after-init="handleAfterHotInit"' in source
    assert "if (hiddenColumnIndexSet.value.has(column))" in source
    assert "TD.classList.add('sheet-hidden-column-cell')" in source


def test_note_sheet_workspace_uses_filtered_query_on_first_filtered_page_load():
    source = _workspace_source()
    start = source.index("async function fetchNoteSheetForCurrentView(")
    end = source.index("\nfunction getUsableInitialSheetDetail", start)
    body = source[start:end]

    assert "const activeFilters = buildActiveSheetQueryFilters()" in body
    assert "if (activeFilters.active)" in body
    assert "options?.paginate === true && activeFilters.active" not in body
    assert "return queryNoteSheet(props.sheetId, {" in body
    assert "page: options?.page ?? currentPage.value" in body
    assert "page_size: options?.pageSize ?? pageSize.value" in body
    assert "paginate: options?.paginate" in body
    assert "column_filters: activeFilters.columnFilters" in body
    assert "row_filter_programs: activeFilters.rowFilterPrograms" in body
    assert body.index("if (activeFilters.active)") < body.index("return fetchNoteSheet(props.sheetId, options)")


def test_note_sheet_workspace_marks_loaded_filter_key_before_post_load_filter_watcher():
    source = _workspace_source()
    start = source.index("async function restoreInitialDocument(")
    end = source.index("\nasync function handlePageChange", start)
    body = source[start:end]
    schedule_start = source.index("function scheduleFilteredPaginationReload()")
    schedule_end = source.index("\nfunction runScheduledSheetFilterReload", schedule_start)
    schedule_body = source[schedule_start:schedule_end]
    run_start = source.index("function runScheduledSheetFilterReload()")
    run_end = source.index("\nasync function restoreInitialDocument", run_start)
    run_body = source[run_start:run_end]

    assert "const completedFilters = buildActiveSheetQueryFilters()" in body
    assert "const completedFilterReloadKey = buildSheetFilterReloadKey(completedFilters)" in body
    assert "completedSheetFilterReloadKey = completedFilterReloadKey" in body
    assert "trace?.mark('filter-reload-key-completed')" in body
    assert body.index("completedSheetFilterReloadKey = completedFilterReloadKey") < body.index("await prepareSheetHotViewportBeforeMount('before-hot-mount')")
    assert body.index("completedSheetFilterReloadKey = completedFilterReloadKey") < body.index("scheduleSheetRenderEnhancement(")
    assert "if (!filters.active && pageRowIndexes.value === null)" in schedule_body
    assert schedule_body.index("if (!filters.active && pageRowIndexes.value === null)") < schedule_body.index("const reloadKey = buildSheetFilterReloadKey(filters)")
    assert "|| reloadKey === completedSheetFilterReloadKey" in schedule_body
    assert "isCurrentSheetFilterPaginationReady(filters)" not in schedule_body
    assert "if (reloadKey === completedSheetFilterReloadKey)" in run_body
    assert run_body.index("if (reloadKey === completedSheetFilterReloadKey)") < run_body.index("activeSheetFilterReloadKey = reloadKey")
    assert "if (!filters.active && pageRowIndexes.value === null)" in run_body
    assert run_body.index("if (!filters.active && pageRowIndexes.value === null)") < run_body.index("activeSheetFilterReloadKey = reloadKey")


def test_public_sheet_boot_does_not_eagerly_load_feature_access_context():
    source = _frontend_main_source()

    assert "useFeatureAccessStore" not in source
    assert "featureAccessStore.ensureLoaded()" not in source
    assert "featureAccessStore.refreshContext()" not in source
    assert "router.beforeEach" not in source


def test_boot_perf_snapshot_captures_api_resources_and_long_tasks():
    source = _frontend_index_source()

    assert "longTasks: []" in source
    assert "PerformanceObserver((list) =>" in source
    assert "longTaskObserver.observe({ type: 'longtask', buffered: true })" in source
    assert "entry.duration >= 500 || entry.name.includes('/api/')" in source
    assert "longTasks: (window.__codeyunBootPerf?.longTasks || []).slice(0, 20)" in source


def test_boot_perf_requests_backend_server_timing_headers():
    api_source = _frontend_api_index_source()
    backend_source = _note_sheets_api_source()

    assert "import { isBootPerfEnabled } from '@/utils/bootPerf'" in api_source
    assert "config.headers['X-CodeYun-BootPerf'] = '1'" in api_source
    assert "x_codeyun_boot_perf: str | None = Header(default=None, alias=\"X-CodeYun-BootPerf\")" in backend_source
    assert "response.headers[\"Server-Timing\"] = _format_note_sheet_server_timing(timings)" in backend_source
    assert "_record_note_sheet_timing(timings, checkpoint, \"resolve\")" in backend_source
    assert "_record_note_sheet_timing(timings, checkpoint, \"payload_total\")" in backend_source


def test_public_workbook_routes_preserve_sheet_perf_without_query_spread():
    source = _resource_view_source()
    start = source.index("function getCleanWorkbookRouteQuery(targetSheetId?: number | null)")
    end = source.index("\nfunction setResourceAccessIssue", start)
    body = source[start:end]

    assert "function getCleanWorkbookRouteQuery(targetSheetId?: number | null)" in source
    assert "routeWorkspaceView.value" in source
    assert "query: { ...route.query" not in source
    assert "const nextQuery = { ...route.query }" not in source
    assert "const sheetPerfQuery = Array.isArray(route.query.sheetPerf)" in body
    assert "query.sheetPerf = String(sheetPerfQuery)" in body


def test_public_workbook_initial_load_keeps_workbook_and_sheet_requests_parallel():
    source = _resource_view_source()
    start = source.index("async function loadWorkbookResource()")
    end = source.index("\nfunction selectSheet", start)
    body = source[start:end]

    assert "() => fetchWorkbook(targetWorkbookId)" in body
    assert "resource-view.prefetchSheet" in body
    assert "Promise.all([workbookRequest, sheetRequest])" in body
    assert "active_sheet_detail" not in body


def test_note_sheet_get_path_reuses_normalized_document_for_attendance_sheets():
    source = _note_sheets_api_source()

    assert "def _sheet_identity_list_is_complete(" in source
    assert "_sheet_identity_list_is_complete(source_document.get(\"row_ids\"), count=len(rows))" in source
    assert "_sheet_identity_list_is_complete(source_document.get(\"column_ids\"), count=len(columns))" in source
    assert "return source_document if document_json is not None else _normalize_document_json(source_document)" in source
    assert "assume_normalized: bool = False" in source
    assert "_normalize_attendance_dual_clockin_refund_formulas(\n        document_json,\n        assume_normalized=True,\n    )" in source
    assert "_normalize_attendance_managed_refund_formulas(\n        document_json,\n        assume_normalized=True,\n    )" in source
    assert "if _header_link_count:\n        full_document = _normalize_document_json(full_document)" in source
    assert "page_document[\"row_ids\"] = [" in source


def test_note_sheet_get_path_avoids_large_document_json_during_access_resolve():
    source = _note_sheets_api_source()
    get_start = source.index("def get_note_sheet(")
    get_end = source.index("\n\n@router.post(\"/sheets/{sheet_id}/query\")", get_start)
    get_body = source[get_start:get_end]
    resolver_start = source.index("def _get_note_sheet_or_404(")
    resolver_end = source.index("\n\ndef _get_optional_trusted_device", resolver_start)
    resolver_body = source[resolver_start:resolver_end]

    assert "NOTE_SHEET_DOCUMENT_JSON_CACHE_TTL_SECONDS = 300" in source
    assert "_NOTE_SHEET_DOCUMENT_JSON_CACHE: OrderedDict" in source
    assert "include_document_json: bool = True" in source
    assert "include_document_json=False" in get_body
    assert "document_json = _get_cached_sheet_document_json(session, document)" in get_body
    assert "attributes.set_committed_value(document, \"document_json\", document_json)" in get_body
    assert "load_only(" in source
    assert "SheetDocument.document_json" not in resolver_body


def test_note_sheet_get_path_uses_page_snapshot_before_large_document_json():
    source = _note_sheets_api_source()
    models_source = _backend_models_source()
    migration_source = _migration_manager_source()
    get_start = source.index("def get_note_sheet(")
    get_end = source.index("\n\n@router.post(\"/sheets/{sheet_id}/query\")", get_start)
    get_body = source[get_start:get_end]
    snapshot_start = source.index("def _store_sheet_page_snapshot(")
    snapshot_end = source.index("\n\ndef _is_superuser_or_user_id", snapshot_start)
    snapshot_body = source[snapshot_start:snapshot_end]

    assert "class SheetPageSnapshot(SQLModel, table=True):" in models_source
    assert "UniqueConstraint(\n            \"sheet_id\"" in models_source
    assert "def v79_add_sheet_page_snapshot_table(" in migration_source
    assert "(79, \"Add sheet page snapshot table\", v79_add_sheet_page_snapshot_table)" in migration_source
    assert "snapshot = _get_sheet_page_snapshot(" in get_body
    assert "document_json = _get_cached_sheet_document_json(session, document)" in get_body
    assert get_body.index("snapshot = _get_sheet_page_snapshot(") < get_body.index("document_json = _get_cached_sheet_document_json(session, document)")
    assert "if snapshot is not None:" in get_body
    assert "_serialize_sheet_detail_from_page_snapshot(" in get_body
    assert "_store_sheet_page_snapshot(" in get_body
    assert "build_payload" in get_body
    assert "snapshot_store" in get_body
    assert get_body.index("build_payload") < get_body.index("snapshot_store")
    assert "if pagination is None:\n        return" in snapshot_body
    assert "return _normalize_sheet_text(document.sheet_key) != \"attendance\"" in source


def test_note_sheet_write_paths_prewarm_default_page_snapshot_without_touching_internal_repairs():
    source = _note_sheets_api_source()
    prewarm_start = source.index("def _prewarm_default_sheet_page_snapshot(")
    prewarm_end = source.index("\n\ndef _is_superuser_or_user_id", prewarm_start)
    prewarm_body = source[prewarm_start:prewarm_end]

    assert "page=1" in prewarm_body
    assert "page_size=None" in prewarm_body
    assert "paginate=None" in prewarm_body
    assert "include_workbook_context=False" in prewarm_body
    assert "contextlib.suppress(Exception)" in prewarm_body

    write_paths = [
        ("def patch_note_sheet_table(", "\n\n@router.patch(\"/sheets/{sheet_id}/cells\""),
        ("def patch_note_sheet_cells(", "\n\n@router.post(\"/sheets/{sheet_id}/patch\""),
        ("def patch_note_sheet(", "\n\n@router.websocket(\"/ws/resources/sheet/{sheet_id}\""),
        ("def update_sheet_defined_names_endpoint(", "\n\n@router.put(\"/sheets/{sheet_id}/access\""),
        ("def update_note_sheet(", "\n\n@router.post(\"/sheets/{sheet_id}/import-excel-reset\""),
        ("async def import_note_sheet_excel_reset(", "\n\n@router.post(\n    \"/sheets/{sheet_id}/clockin/link-detection-runs\""),
    ]
    for start_marker, end_marker in write_paths:
        start = source.index(start_marker)
        end = source.index(end_marker, start)
        body = source[start:end]
        assert "_prewarm_default_sheet_page_snapshot(" in body
        assert "document_json=next_document" in body

    for start_marker, end_marker in [
        ("def _normalize_registration_sheet_header_persisted(", "\n\ndef _apply_registration_standard_user_id_column_styles"),
        ("def _sync_attendance_questionnaire_sheet_document(", "\n\ndef _build_paged_document"),
    ]:
        start = source.index(start_marker)
        end = source.index(end_marker, start)
        body = source[start:end]
        assert "_prewarm_default_sheet_page_snapshot(" not in body


def test_note_sheet_snapshot_backfill_scans_only_safe_paginated_sheets():
    source = _note_sheets_api_source()
    script_source = _snapshot_backfill_script_source()
    backfill_start = source.index("def backfill_default_sheet_page_snapshots(")
    backfill_end = source.index("\n\ndef _is_superuser_or_user_id", backfill_start)
    body = source[backfill_start:backfill_end]

    assert "SheetDocument.sheet_key != \"attendance\"" in body
    assert "_active_sheet_condition()" in body
    assert "func.length(SheetDocument.document_json) >= int(min_document_json_bytes)" in body
    assert "paginate_enabled, _page_size = _get_document_pagination_settings(document_json)" in body
    assert "if not paginate_enabled:" in body
    assert "_get_workbooks_for_sheet(session, document)" in body
    assert "include_workbook_context=False" in body
    assert "include_existing: bool = False" in body
    assert "def main() -> None:" in script_source
    assert "migrate_db()" in script_source
    assert "backfill_default_sheet_page_snapshots(" in script_source
    assert "--sheet-id" in script_source


def test_workbook_detail_reuses_loaded_sheet_summaries_and_access():
    source = _note_sheets_api_source()
    start = source.index("def _serialize_workbook_detail(")
    end = source.index("\ndef _serialize_resource_access_grants", start)
    body = source[start:end]

    assert "workbook_access: NoteSheetResourceAccess | None = None" in source
    assert "if workbook_access is not None" in source
    assert "sheet_map: dict[str, SheetDocument] | None = None" in source
    assert "sheet_map=sheet_map" in body
    assert "workbook_access=access" in body


def test_workbook_detail_exposes_server_timing_when_boot_perf_enabled():
    source = _note_sheets_api_source()
    endpoint_start = source.index("def get_workbook(")
    endpoint_end = source.index("\n\n@router.post(\"/workbooks/{workbook_id}/restore\"", endpoint_start)
    endpoint_body = source[endpoint_start:endpoint_end]
    serializer_start = source.index("def _serialize_workbook_detail(")
    serializer_end = source.index("\ndef _serialize_resource_access_grants", serializer_start)
    serializer_body = source[serializer_start:serializer_end]

    assert "response: Response" in endpoint_body
    assert "x_codeyun_boot_perf: str | None = Header(default=None, alias=\"X-CodeYun-BootPerf\")" in endpoint_body
    assert "timings: list[tuple[str, float]] | None = [] if x_codeyun_boot_perf else None" in endpoint_body
    assert "response.headers[\"Server-Timing\"] = _format_note_sheet_server_timing(timings)" in endpoint_body
    assert "timings: list[tuple[str, float]] | None = None" in serializer_body
    assert "_record_note_sheet_timing(timings, checkpoint, \"workbook_links\")" in serializer_body
    assert "_record_note_sheet_timing(timings, checkpoint, \"sheet_summaries\")" in serializer_body
    assert "_record_note_sheet_timing(timings, checkpoint, \"sheet_workbook_refs\")" in serializer_body
    assert "_record_note_sheet_timing(timings, checkpoint, \"sheet_access\")" in serializer_body


def test_note_sheet_workspace_accepts_page_scoped_row_ids():
    source = _workspace_source()

    assert "const sourceRowIds = Array.isArray(document.row_ids) ? document.row_ids : []" in source
    assert "const rowIdsArePaged = sourceRowIds.length === normalizedRows.length" in source
    assert "const sourceRowIdIndex = rowIdsArePaged ? localIndex : getDocumentRowIndex(localIndex)" in source
    assert "normalizeSheetEntityId(sourceRowIds[sourceRowIdIndex])" in source


def test_note_sheet_cell_saves_rebase_system_updates_and_share_one_local_write_lane():
    workspace_source = _workspace_source()
    api_source = _note_sheets_api_source()
    ts_api_source = _note_sheets_ts_api_source()

    assert "expected_value?: unknown" in ts_api_source
    assert "expected_meta?: Record<string, unknown>" in ts_api_source
    assert "expected_value: normalizeCellInputValueForColumn(record.previousValue" in workspace_source
    assert "expected_meta: result.expectedMeta ? { ...result.expectedMeta } : {}" in workspace_source
    assert "expected_value: normalizeCellInputValueForColumn(currentValue, cell.column)" in workspace_source
    assert "expected_value: previousValue" in workspace_source
    assert "row_id: rowId" in workspace_source
    assert "column_id: columnId" in workspace_source
    assert "function ensureDocumentRowEntityId(documentRow: number)" in workspace_source
    assert "nextIds[localDataIndex] = rowId" in workspace_source
    assert "const rowId = ensureDocumentRowEntityId(documentRow)" in workspace_source
    assert "const pendingDocumentSave = saveInFlightPromise?.catch" in workspace_source
    assert "const pendingCellPatches = cellPatchQueue" in workspace_source
    assert "await pendingCellPatches.catch" in workspace_source
    assert "mutation_id: createSaveMutationId()" in workspace_source
    assert "client_instance_id: getSaveClientInstanceId()" in workspace_source
    assert "message.client_instance_id === getSaveClientInstanceId()" in workspace_source
    socket_start = workspace_source.index("function connectSheetResourceSocket(")
    socket_end = workspace_source.index("\nfunction waitForRemoteSaveIdle", socket_start)
    socket_body = workspace_source[socket_start:socket_end]
    assert "sheetRemoteConflictActive = true" not in socket_body
    assert "工作表已被其他人更新" not in workspace_source

    assert "def _rebase_stale_cell_patch_ops(" in api_source
    assert 'operation.op not in {"set-cell-value", "set-cell-meta"}' in api_source
    assert '"expected_value" not in operation.model_fields_set' in api_source
    assert '"expected_meta" not in operation.model_fields_set' in api_source
    assert "if current_value == desired_value:" in api_source
    assert "if current_meta == desired_meta:" in api_source
    assert "operations = _rebase_stale_cell_patch_ops(normalized, operations)" in api_source
    assert ".where(SheetDocument.version == current_version)" in api_source
    assert "if int(result.rowcount or 0) == 1:" in api_source
    assert "for _attempt in range(4):" in api_source


def test_note_sheet_workspace_frame_gap_monitor_ignores_background_throttling():
    source = _workspace_source()
    monitor_start = source.index("function startSheetPerfFrameMonitor()")
    monitor_end = source.index("\nfunction stopSheetPerfFrameMonitor", monitor_start)
    monitor_body = source[monitor_start:monitor_end]

    assert "function isSheetPerfFrameMonitorActive()" in source
    assert "document.visibilityState === 'visible'" in source
    assert "document.hasFocus()" in source
    assert "if (!isSheetPerfFrameMonitorActive())" in monitor_body
    assert "sheetPerfLastFrameTime = null" in monitor_body
    assert "function isSheetPerfLikelyThrottledFrameGap(" in source
    assert "function hasSheetPerfLongTaskNearFrameGap(" in source
    assert "SHEET_PERF_FRAME_GAP_THROTTLE_MIN_MS" in source
    assert "(!sheetPerfLongTaskObserver || hasSheetPerfLongTaskNearFrameGap(sheetPerfLastFrameTime, timestamp))" in monitor_body
    assert "!isSheetPerfLikelyThrottledFrameGap(sheetPerfLastFrameTime, timestamp)" in monitor_body
    assert "sheetPerfLastLongTaskEndTime = entry.startTime + entry.duration" in source
    assert "visibilityState:" in monitor_body
    assert "focused:" in monitor_body


def test_note_sheet_workspace_logs_sheet_perf_events_to_console_when_enabled():
    source = _workspace_source()
    push_start = source.index("function pushSheetPerfLogEntry(")
    push_end = source.index("\nfunction startSheetPerfTrace", push_start)
    push_body = source[push_start:push_end]
    console_start = source.index("function logSheetPerfConsoleEntry(")
    console_end = source.index("\nfunction getSheetPerfHotViewport", console_start)
    console_body = source[console_start:console_end]

    assert "function logSheetPerfConsoleEntry(entry: SheetPerfLogEntry)" in source
    assert "console.info(`[sheet-perf:event] ${JSON.stringify(entry)}`)" in console_body
    assert "if (!sheetPerfLoggingEnabled.value)" in console_body
    assert "logSheetPerfConsoleEntry(nextEntry)" in push_body
    assert push_body.index("queueSheetPerfBackendLogEntry(nextEntry)") < push_body.index("logSheetPerfConsoleEntry(nextEntry)")


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


def test_note_sheet_workspace_binds_real_hot_data_to_preserve_initial_render():
    source = _workspace_source()
    restore_start = source.index("async function restoreInitialDocument(")
    restore_end = source.index("\nasync function handlePageChange", restore_start)
    restore_body = source[restore_start:restore_end]
    enhance_start = source.index("async function applySheetRenderEnhancement(")
    enhance_end = source.index("\ntype SheetContextMenuItem", enhance_start)
    enhance_body = source[enhance_start:enhance_end]

    assert "const EMPTY_SHEET_HOT_GRID_ROWS: SheetRow[] = []" not in source
    assert "const sheetHotInitialGridRows" not in source
    assert ':data="sheetHotGridRows"' in source
    assert ':data="sheetHotInitialGridRows"' not in source
    assert "const hotTableInstanceKey = computed(() => `sheet-${props.workbookId ?? 'standalone'}-${props.sheetId ?? 'inline'}`)" in source
    assert "function ensureHotInitialGridRowsLoaded(reason: string)" in source
    assert "async function waitForHotInitialGridRowsLoaded(reason: string)" in source
    assert "hotInitialDataLoadedContentIdentity" in source
    assert "hotInitialDataLoadedInstance" in source
    assert "function hasLoadedCurrentHotInitialGridRows(" in source
    assert "function getHiddenRowsSignature(" in source
    assert "function markRuntimeHiddenRowsApplied(" in source
    assert "recordSheetPerfEvent('handsontable.initialLoadData'" in source
    assert "function isCurrentHotGridRowsAlreadyLoaded(" in source
    assert "const alreadyLoaded = isCurrentHotGridRowsAlreadyLoaded(hot)" in source
    assert "if (!alreadyLoaded) {\n    loadCurrentHotGridRows(hot)\n  }" in source
    assert "markRuntimeHiddenRowsApplied(sheetFilterHiddenRows.value)" in source
    assert "alreadyLoaded," in source
    assert "function requestHotInitialGridRowsLoaded(reason: string)" in source
    assert "requestHotInitialGridRowsLoaded('hot-instance-ready')" in source
    assert "await waitForHotInitialGridRowsLoaded('restore-initial-document')" in restore_body
    assert "trace?.mark(`initial-hot-load-data-${initialHotLoadDataResult}`)" in restore_body
    assert "await prepareSheetHotViewportBeforeMount('before-hot-mount')" in restore_body
    assert restore_body.index("await prepareSheetHotViewportBeforeMount('before-hot-mount')") < restore_body.index("await waitForHotInitialGridRowsLoaded('restore-initial-document')")
    assert restore_body.index("await waitForHotInitialGridRowsLoaded('restore-initial-document')") < restore_body.index("scheduleSheetRenderEnhancement(")
    assert "ensureHotInitialGridRowsLoaded('render-enhancement')" in enhance_body
    wait_start = source.index("async function waitForHotInitialGridRowsLoaded(reason: string)")
    wait_end = source.index("\nfunction isSheetDocumentHidden", wait_start)
    wait_body = source[wait_start:wait_end]
    assert "requestAnimationFrame" not in wait_body
    row_heights_start = source.index("async function refreshComputedRowHeights(")
    row_heights_end = source.index("\nfunction hasSelection", row_heights_start)
    row_heights_body = source[row_heights_start:row_heights_end]
    assert row_heights_body.index("if (!options.force && !hasLoadedCurrentHotInitialGridRows(hot))") < row_heights_body.index("hot.updateSettings({")
    access_start = source.index("watch(\n  effectiveAccessCapabilities,")
    access_end = source.index("\nwatch(\n  () => props.accessCapabilities", access_start)
    access_body = source[access_start:access_end]
    assert access_body.index("if (!hasLoadedCurrentHotInitialGridRows(hot))") < access_body.index("hot.updateSettings({")
    hidden_rows_start = source.index("watch(\n  sheetFilterHiddenRows,")
    hidden_rows_end = source.index("\nonMounted(", hidden_rows_start)
    hidden_rows_body = source[hidden_rows_start:hidden_rows_end]
    assert hidden_rows_body.index("if (!hasLoadedCurrentHotInitialGridRows(hot))") < hidden_rows_body.index("hot.updateSettings({")
    assert hidden_rows_body.index("const hiddenRowsSignature = getHiddenRowsSignature(hiddenRows)") < hidden_rows_body.index("hot.updateSettings({")
    assert hidden_rows_body.index("hotRuntimeHiddenRowsSignature === hiddenRowsSignature") < hidden_rows_body.index("hot.updateSettings({")
    assert hidden_rows_body.index("markRuntimeHiddenRowsApplied(hiddenRows)") > hidden_rows_body.index("hot.updateSettings({")


def test_note_sheet_workspace_mounts_merge_cells_with_initial_hot_settings():
    source = _workspace_source()
    ready_start = source.index("function markSheetContentReadyForHotMount()")
    ready_end = source.index("\nfunction markSheetContentNotReady", ready_start)
    ready_body = source[ready_start:ready_end]
    not_ready_start = source.index("function markSheetContentNotReady()")
    not_ready_end = source.index("\nlet lastNotifiedUserMatchRunId", not_ready_start)
    not_ready_body = source[not_ready_start:not_ready_end]
    ensure_start = source.index("function ensureHotInitialGridRowsLoaded(reason: string) {")
    ensure_end = source.index("\nasync function waitForHotInitialGridRowsLoaded", ensure_start)
    ensure_body = source[ensure_start:ensure_end]
    merge_start = source.index("const sheetHotRenderMergeCells = computed(() => {")
    merge_end = source.index("\n})", merge_start)
    merge_body = source[merge_start:merge_end]

    assert "hotDeferredMergeCellsPending" not in source
    assert "sheetHotMergeCells.value.length ? sheetHotMergeCells.value : false" in merge_body
    assert "getHotInstance() == null" not in ready_body
    assert "sheetContentReady.value = false" in not_ready_body
    assert "markSheetContentNotReady()" not in not_ready_body.split("{", 1)[1]
    assert "mergeCells.enabled" not in ensure_body


def test_note_sheet_workspace_does_not_render_when_rich_text_editor_is_already_closed():
    source = _workspace_source()
    start = source.index("function cancelRichTextContentEditor()")
    end = source.index("\nfunction focusRichTextContentEditor", start)
    body = source[start:end]

    assert "if (!state.visible && !richTextInlineToolbar.value.visible)" in body
    assert body.index("if (!state.visible && !richTextInlineToolbar.value.visible)") < body.index("renderCurrentHotWithReason('rich-text-editor-close')")


def test_note_sheet_workspace_reuses_normalized_merged_cells_for_lookup():
    source = _workspace_source()
    render_start = source.index("function getRenderableMergedCells()")
    render_end = source.index("\nfunction getNormalizedSheetMergedCells", render_start)
    render_body = source[render_start:render_end]
    lookup_start = source.index("function findMergedCellAtDocumentCell(")
    lookup_end = source.index("\nfunction findMergedCellAtGridCell", lookup_start)
    lookup_body = source[lookup_start:lookup_end]

    assert "const normalizedSheetMergedCells = computed(() => getNormalizedSheetMergedCells())" in source
    assert "const normalized = normalizedSheetMergedCells.value" in render_body
    assert "normalizeMergedCells(" not in render_body
    assert "return normalizedSheetMergedCells.value.find((cell) => (" in lookup_body
    assert "normalizeMergedCells(" not in lookup_body


def test_note_sheet_workspace_skips_merge_cells_clear_before_collection_ready():
    source = _workspace_source()
    type_start = source.index("type SheetHotMergeCellsPlugin = {")
    type_end = source.index("\n}", type_start)
    type_body = source[type_start:type_end]
    clear_start = source.index("function clearHotMergeCellsPlugin(")
    clear_end = source.index("\nfunction shouldClearHotMergeCellsBeforeSettings", clear_start)
    clear_body = source[clear_start:clear_end]

    assert "mergedCellsCollection?: {" in type_body
    assert "clear?: () => void" in type_body
    assert "if (!plugin?.mergedCellsCollection?.clear)" in clear_body
    assert clear_body.index("if (!plugin?.mergedCellsCollection?.clear)") < clear_body.index("plugin.clearCollections?.()")


def test_note_sheet_workspace_skips_irrelevant_action_status_requests_on_initial_load():
    source = _workspace_source()
    start = source.index("function refreshInitialSheetActionStatuses()")
    end = source.index("\nfunction scheduleInitialSheetActionStatusRefresh", start)
    body = source[start:end]

    assert "function hasSheetCellAction(" in source
    assert "function hasRegistrationAsyncActionCells()" in source
    assert "function hasClockinLinkDetectionActionCells()" in source
    assert "if (hasRegistrationAsyncActionCells())" in body
    assert "void refreshUserMatchRunStatus(undefined, { silent: true })" in body
    assert "if (hasClockinLinkDetectionActionCells())" in body
    assert "void refreshClockinLinkDetectionRunStatus(undefined, { silent: true })" in body


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


def test_workbook_resource_view_forces_uncapped_runtime_fill_height_for_virtual_rows():
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
    assert "SHEET_RESOURCE_RUNTIME_MAX_GRID_HEIGHT" not in resource_source
    assert ":runtime-max-grid-height=" not in resource_source
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


def test_note_sheet_workspace_primes_viewport_height_before_hot_mount():
    source = _workspace_source()
    mount_start = source.index("const shouldMountOriginalHotTable = computed(() => (")
    mount_end = source.index("\nconst shouldShowOriginalSheetArea", mount_start)
    mount_body = source[mount_start:mount_end]
    ready_start = source.index("function markSheetContentReadyForHotMount()")
    ready_end = source.index("\nlet lastNotifiedUserMatchRunId", ready_start)
    ready_body = source[ready_start:ready_end]
    prepare_start = source.index("async function prepareSheetHotViewportBeforeMount(")
    prepare_end = source.index("\nfunction handleWindowResize", prepare_start)
    prepare_body = source[prepare_start:prepare_end]
    restore_start = source.index("async function restoreInitialDocument(")
    restore_end = source.index("\nasync function handlePageChange", restore_start)
    restore_body = source[restore_start:restore_end]
    template_start = source.index("<HotTable")
    template_end = source.index(":key=\"hotTableInstanceKey\"", template_start)
    template_body = source[template_start:template_end]

    assert "const sheetHotViewportMountPending = ref(false)" in source
    assert "&& !sheetHotViewportMountPending.value" in mount_body
    assert "beginSheetHotViewportMountGate()" in ready_body
    assert "clearSheetHotViewportMountGate()" in ready_body
    assert "await updateSheetViewportHeight(reason)" in prepare_body
    assert "clearSheetHotViewportMountGate()" in prepare_body
    assert "await nextTick()" in prepare_body
    assert "const viewportPrepareResult = await prepareSheetHotViewportBeforeMount('before-hot-mount')" in restore_body
    assert "trace?.mark(`viewport-before-hot-mount-${viewportPrepareResult}`)" in restore_body
    assert 'v-if="shouldMountOriginalHotTable"' in template_body
    assert 'v-if="isOriginalSheetViewActive && shouldRenderSheetContent"' not in template_body


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
    assert "buildFormulaDisplayStateForRows(normalizedHeaders, normalizedRows, columnConfigs.value, { perfSource:" in body
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
    assert ": buildFormulaDisplayStateForRows(normalizedHeaders, normalizedRows, columnConfigs.value, { perfSource: 'loadSheetDocument' })" in load_body


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


def test_note_sheet_workspace_coalesces_workspace_view_and_height_render():
    source = _workspace_source()
    restore_start = source.index("function restoreSheetWorkspaceViewFromLocalStorage(")
    restore_end = source.index("\nfunction setSheetWorkspaceView", restore_start)
    restore_body = source[restore_start:restore_end]
    switch_start = source.index("function setSheetWorkspaceView(")
    switch_end = source.index("\nfunction handleSheetWorkspaceViewChange", switch_start)
    switch_body = source[switch_start:switch_end]
    height_start = source.index("async function updateSheetViewportHeightNow(")
    height_end = source.index("\nfunction handleWindowResize", height_start)
    height_body = source[height_start:height_end]

    assert "updateSheetViewportHeight('workspaceViewRestore').then((rendered) => {" in restore_body
    assert "const previousView = sheetWorkspaceView.value" in restore_body
    assert "if (!rendered && previousView !== nextView)" in restore_body
    assert "renderCurrentHotWithReason('workspace-view-restore')" in restore_body
    assert "updateSheetViewportHeight('workspaceViewSwitch').then((rendered) => {" in switch_body
    assert "if (!rendered)" in switch_body
    assert "renderCurrentHotWithReason('workspace-view-switch')" in switch_body
    assert "let rendered = false" in height_body
    assert "rendered = renderCurrentHotWithReason('viewport-height-changed')" in height_body
    assert "return rendered" in height_body
    assert "return false" in height_body


def test_note_sheet_workspace_skips_inline_editor_style_sync_when_editor_closed():
    source = _workspace_source()
    start = source.index("function scheduleInlineEditorCellStyleSync()")
    end = source.index("\nfunction areSheetCellsSame", start)
    body = source[start:end]

    assert "if (!getActiveOpenedEditor())" in body
    assert body.index("if (!getActiveOpenedEditor())") < body.index("void nextTick(")
    assert "styleInlineEditorAsCell(editor)" in body


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
    assert "const formulaContextChanged = syncDefinedNamesFromResponse(embeddedDefinedNamesContext)" in restore_body
    assert "normalizedDocumentFormulaDisplayCache.delete(remoteDocument)" in restore_body
    assert "trace?.mark('defined-names-embedded')" in restore_body
    assert "scheduleDefinedNamesSyncAfterSheetLoad(" in restore_body
    assert restore_body.index("syncDefinedNamesFromResponse(embeddedDefinedNamesContext)") < restore_body.index("loadSheetDocument(activeDocument, activeSourceDocument)")


def test_workbook_prefetch_skips_duplicate_workbook_context_for_initial_sheet_detail():
    source = _resource_view_source()
    load_start = source.index("async function loadWorkbookResource()")
    load_end = source.index("\n\nfunction handleSheetTabClick", load_start)
    load_body = source[load_start:load_end]

    assert "fetchNoteSheet(targetSheetId, {" in load_body
    assert "workbookId: targetWorkbookId" in load_body
    assert "includeWorkbookContext: false" in load_body
    assert "includeWorkbookContext: true" not in load_body


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
    payload_start = source.index("def _build_note_sheet_detail_payload(")
    payload_end = source.index("\n\ndef _build_sheet_defined_names_context", payload_start)
    payload_body = source[payload_start:payload_end]

    assert "defined_names_context: Optional[dict[str, Any]] = None" in detail_body
    assert "defined_names_context: dict[str, Any] | None = None" in serialize_body
    assert '"defined_names_context": defined_names_context' in serialize_body
    assert '"workbook": workbook_names' in helper_body
    assert '"worksheets": [{' in helper_body
    assert "_merge_effective_defined_names(workbook_names, worksheet_names)" in helper_body
    assert "include_workbook_context=include_workbook_context" in get_body
    assert "workbook if include_workbook_context else None" not in payload_body
    assert "workbook if payload.include_workbook_context else None" not in query_body
    assert "defined_names_context = _build_sheet_defined_names_context(\n        session,\n        document,\n        workbook,\n    )" in payload_body
    assert "document,\n            workbook," in query_body


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
    assert "sheetDocumentSourceHasFormulaExpressions(remote.document_json)" in restore_body
    assert "function sheetDocumentRowHasFormulaExpression(" in source
    assert "rows.some((row) => sheetDocumentRowHasFormulaExpression(row, headers))" in source
    assert "trace?.mark('formula-source-scan')" in restore_body
    assert "startSheetPerfTrace('sheet.formulaDisplayBuild'" in source
    assert "trace?.mark('engine-build')" in source
    assert "trace?.mark('cell-models')" in source
    assert "const FORMULA_DISPLAY_STATE_CACHE_LIMIT = 8" in source
    assert "const formulaDisplayStateSignatureCache = new Map<string, FormulaDisplayState>()" in source
    assert "function buildFormulaDisplayStateCacheSignature(" in source
    assert "function getFormulaDisplayStateCacheValue(" in source
    assert "function setFormulaDisplayStateCacheValue(" in source
    assert "const cachedFormulaDisplayState = getFormulaDisplayStateCacheValue(cacheSignature)" in source
    assert "trace?.mark(cachedFormulaDisplayState ? 'cache-hit' : 'cache-miss')" in source
    assert "finishTrace('cache-hit'" in source
    assert "setFormulaDisplayStateCacheValue(cacheSignature, nextState)" in source
    assert "function getFormulaEngineColumnCount(" in source
    assert "function formulaRequiresFullWidthEngine(" in source
    assert "\\b(?:INDIRECT|OFFSET)\\s*\\(" in source
    assert "getFormulaReferenceMaxColumnIndex(item.expression)" in source
    assert "row.slice(0, engineColumnCount).map((cellValue)" in source
    assert "engineColumns: engineColumnCount" in source
    assert "perfSource: formulaOptions.perfSource ?? 'normalizeDocument'" in source
    assert "perfSource: 'loadSheetDocument'" in source
    assert "waitForInitialFormulaEnginePreload(loadFormulaEngineClass())" in restore_body
    assert restore_body.index("trace?.mark('formula-source-scan')") < restore_body.index("markBootPerf('note-sheet-workspace.normalize.start')")
    assert restore_body.index("waitForInitialFormulaEnginePreload(loadFormulaEngineClass())") < restore_body.index("markBootPerf('note-sheet-workspace.normalize.start')")
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
        "await prepareSheetHotViewportBeforeMount('before-hot-mount')",
        "trace?.mark(`viewport-before-hot-mount-${viewportPrepareResult}`)",
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


def test_note_sheet_workspace_starts_remote_fetch_before_cached_placeholder_render():
    source = _workspace_source()
    helper_start = source.index("function resolveFetchPaginationPreferenceFromCacheEntry(")
    helper_end = source.index("\nfunction invalidateSheetDocumentCache", helper_start)
    helper_body = source[helper_start:helper_end]
    restore_start = source.index("async function restoreInitialDocument(")
    restore_end = source.index("\nasync function handlePageChange(", restore_start)
    restore_body = source[restore_start:restore_end]

    assert "normalizeSheetViewSettings(entry.document.view_settings, entry.document.columns.length)" in helper_body
    assert "const earlyRemotePromise: Promise<NoteSheetDetail | null> | null = earlyCachedFetchPreference" in restore_body
    assert "fetchNoteSheet(requestSheetId, {" in restore_body
    assert "trace?.mark('fetch-started-before-cache')" in restore_body
    assert restore_body.index("const earlyRemotePromise: Promise<NoteSheetDetail | null> | null = earlyCachedFetchPreference") < restore_body.index("applySheetDocumentCacheEntry(cachedEntry)")
    assert restore_body.index("applySheetDocumentCacheEntry(cachedEntry)") < restore_body.index("await (earlyRemotePromise ?? markBootPerfAsync(")


def test_note_sheet_workspace_skips_duplicate_row_height_refresh_during_sheet_load():
    source = _workspace_source()
    start = source.index("async function restoreInitialDocument(")
    end = source.index("\nasync function handlePageChange(", start)
    body = source[start:end]

    assert "startSheetPerfTrace('sheet.rowHeights'" in source
    assert "refreshComputedRowHeights(" not in body

