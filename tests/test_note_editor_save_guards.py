from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_note_editors_send_expected_fields_and_do_not_prejudge_websocket_conflicts():
    shared_editor = (REPO_ROOT / "frontend/src/components/SharedNoteEditor.vue").read_text(encoding="utf-8")
    doc_page = (REPO_ROOT / "frontend/src/standard/notes/doc-view/page.vue").read_text(encoding="utf-8")
    detail_panel = (REPO_ROOT / "frontend/src/components/NoteDetailPanel.vue").read_text(encoding="utf-8")

    assert "buildEditableNoteExpectedFields" in shared_editor
    assert "expected_fields: expectedFields" in doc_page
    assert "expected_fields: expectedFields" in detail_panel
    assert "client_instance_id: getSaveClientInstanceId()" in doc_page
    assert "docRemoteConflictActive = true\n      ElMessage" not in doc_page
    assert "文档已被其他人更新" not in doc_page
    assert "pendingDraftResolutionNoteId" in shared_editor
    assert "buildScopedNoteDraftStorageKey" in shared_editor
    assert "getSaveClientInstanceId()" in shared_editor
    assert "合并同时发生的编辑" in shared_editor
    assert "confirmButtonText: '保留我的编辑'" in shared_editor
    assert "cancelButtonText: '使用服务器版本'" in shared_editor
    assert "kind: 'conflict' as const" in doc_page
    assert "kind: 'conflict' as const" in detail_panel


def test_note_editor_draft_is_immediately_durable_and_dirty_state_is_reactive():
    auto_save = (REPO_ROOT / "frontend/src/utils/useAutoSave.ts").read_text(encoding="utf-8")
    note_editor = (REPO_ROOT / "frontend/src/components/NoteEditor.vue").read_text(encoding="utf-8")

    mark_dirty = auto_save.split("const markDirty =", 1)[1].split("const flush =", 1)[0]
    dirty_computed = auto_save.split("const hasUnsavedChanges =", 1)[1].split(");", 1)[0]
    handle_change = note_editor.split("const handleChange =", 1)[1].split("const syncValueFromEditor =", 1)[0]

    assert "persistDraft(latestSnapshot);" in mark_dirty
    assert "scheduleDraftPersist(Math.min(delayMs, 600))" not in mark_dirty
    assert "void saveStatus.value;" in dirty_computed
    assert "if (suppressModelDrivenChange.value) return" not in handle_change
    assert "nextHtml === normalizeEditorInputHtml(props.modelValue)" in handle_change
