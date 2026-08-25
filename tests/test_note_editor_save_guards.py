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
