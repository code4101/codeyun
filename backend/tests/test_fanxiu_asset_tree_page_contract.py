from pathlib import Path


ASSET_TREE_PAGE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "standard"
    / "fanxiu"
    / "data-annotation"
    / "page.vue"
)
FANXIU_API = ASSET_TREE_PAGE.parents[5] / "backend" / "api" / "fanxiu.py"
FANXIU_FRONTEND_API = ASSET_TREE_PAGE.parents[3] / "api" / "fanxiu.ts"


def test_asset_tree_conflict_preserves_local_edit_and_does_not_report_success():
    """A real cross-tab conflict must not discard the user's current draft."""

    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    conflict_start = source.index("if (getHttpStatus(error) === 409)")
    conflict_end = source.index("console.error(error);", conflict_start)
    conflict_handler = source[conflict_start:conflict_end]

    assert "return false;" in conflict_handler
    assert "saveFanxiuDataAnnotationAssetTree(entryId" not in conflict_handler
    assert "mergeAssetTreeNodes(tree" not in conflict_handler
    assert "assetTree.value =" not in conflict_handler


def test_asset_tree_writes_are_serialized_through_one_queue():
    """Debounced and immediate saves must never issue concurrent whole-tree PUTs."""

    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    assert "let assetTreeSaveChain: Promise<boolean> = Promise.resolve(true);" in source
    assert "const enqueueAssetTreeSave = () =>" in source
    assert "assetTreeSaveChain.then(async () =>" in source
    assert "const saveAssetTreeNow = async () =>" in source
    assert source.count("flushAssetTreeToBackend(") == 1
    assert "const entryId = assetTreeLoadedEntryId;" in source


def test_entry_switch_waits_for_the_loaded_tree_save():
    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    handler = source.split("const handleEntryChange = async () => {", 1)[1].split(
        "const handleWindowChange", 1
    )[0]

    assert "if (assetTreeDirty && assetTreeLoadedEntryId)" in handler
    assert "const persisted = await saveAssetTreeNow();" in handler
    assert "selectedEntryId.value = assetTreeLoadedEntryId;" in handler


def test_asset_tree_background_refresh_uses_backend_folder_placement():
    """A newer backend tree is authoritative for node directory placement."""

    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    refresh_start = source.index("const refreshEntryAssetTreeIfChanged = async () =>")
    refresh_end = source.index("const normalizeDiscriminatorGroups", refresh_start)
    refresh_handler = source[refresh_start:refresh_end]

    assert "assetTree.value = latestTree;" in refresh_handler
    assert "mergeEntryAssetTrees(assetTree.value, backendTree)" not in refresh_handler


def test_asset_tree_image_preview_recovers_without_cross_entry_cache_pollution():
    """Transient image failures must retry and stale entry requests must not poison previews."""

    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")

    assert "`${entryId}\\u0000${image.id}\\u0000${image.filename || ''}`" in source
    assert "const assetImagePreviewRequests = new Map<string, Promise<string>>()" in source
    assert "const retryDelays = [0, 300, 900]" in source
    assert "options.force || attempt > 0 ? Date.now() + attempt : undefined" in source
    assert "requestEpoch !== assetImagePreviewEpoch || selectedEntryId.value !== entryId" in source
    assert "await validateAssetImageObjectUrl(previewUrl)" in source
    assert "window.addEventListener('online', recoverSelectedImagePreviewWhenAvailable)" in source
    assert "document.addEventListener('visibilitychange', recoverSelectedImagePreviewWhenAvailable)" in source


def test_asset_tree_image_response_and_forced_retry_bypass_stale_browser_cache():
    backend_source = FANXIU_API.read_text(encoding="utf-8")
    frontend_api_source = FANXIU_FRONTEND_API.read_text(encoding="utf-8")

    assert 'headers={"Cache-Control": "private, no-store"}' in backend_source
    assert 'headers={"Cache-Control": "no-store"}' in backend_source
    assert "cacheBust?: number" in frontend_api_source
    assert "...(cacheBust ? { _: cacheBust } : {})" in frontend_api_source


def test_live_frame_uses_transport_heartbeat_and_server_fresh_save():
    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    frontend_api_source = FANXIU_FRONTEND_API.read_text(encoding="utf-8")

    assert "const streamEnabled = ref(false);" in source
    assert "const windowViewMode = ref<WindowViewMode>('off');" in source
    assert "getFanxiuGameWindow2FrameStatus" in source
    assert "frameHeartbeatReady.value" in source
    assert "sampleLiveFrameSignature" not in source
    assert "fresh_capture: true" in source
    assert "ensureLiveStreamReadyForCapture" not in source
    assert "'/fanxiu/game-window2/frame-status'" in frontend_api_source


def test_saved_frame_and_asset_node_use_one_backend_transaction():
    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")

    save_start = source.index("const saveCurrentFrame = async () =>")
    save_end = source.index("const burstFramePayload", save_start)
    save_handler = source[save_start:save_end]
    assert "asset_node: node" in save_handler
    assert "base_revision: assetTreeBackendRevision.value" in save_handler
    assert "applySavedFrameTransaction" in save_handler
    assert "addSavedFrameToAssetTree(node)" not in save_handler
    assert "saveAssetTreeNow();" not in save_handler.split("saveFanxiuDataAnnotationFrame", 1)[1]


def test_live_frame_status_reset_is_not_recursive():
    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    reset_body = source.split("const resetFrameStatusTracking = () => {", 1)[1].split("};", 1)[0]

    assert "resetActualFps();" in reset_body
    assert "resetFrameStatusTracking();" not in reset_body


def test_live_frame_recovery_uses_backend_readiness_and_sustained_failure():
    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    status_body = source.split("const applyFrameStatus = (status: FanxiuGameWindow2FrameStatus) => {", 1)[1].split("\n};", 1)[0]

    assert "if (status.ready)" in status_body
    assert "FRAME_UNHEALTHY_GRACE_MS" in status_body
    assert "status.consecutive_failures > 0" not in status_body
    assert "画面帧长时间未更新" in status_body


def test_scene_identity_does_not_silently_select_image_matching():
    """Identity membership and recognition modality are separate decisions."""

    source = ASSET_TREE_PAGE.read_text(encoding="utf-8")
    toggle_body = source.split(
        "const cycleSelectedShapeSceneIdentityRole = () => {", 1
    )[1].split("\n};", 1)[0]
    recorded_shape_body = source.split("const createRecordedGameShape = (", 1)[1].split(
        "applyGameMacroShapeAnnotation", 1
    )[0]

    assert "markFirstShapeAsSceneIdentity" not in source
    assert "!shapePrimaryMatchKind(shape)" in toggle_body
    assert "请先选择图像或 OCR 识别方式，再标记场景" in toggle_body
    assert "shape.imageMatchRole" not in toggle_body
    assert "imageMatchRole: 'off'" in recorded_shape_body
    assert "isSceneIdentity: false" in recorded_shape_body
    assert "sceneIdentityRole: 'off'" in recorded_shape_body
