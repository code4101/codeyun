from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHEET_EDITOR_PAGE = PROJECT_ROOT / "frontend/src/standard/notes/sheet-editor/page.vue"
SHEET_WORKSPACE = PROJECT_ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue"


def test_sheet_editor_exposes_permission_filtered_sheet_menu():
    page_source = SHEET_EDITOR_PAGE.read_text(encoding="utf-8")
    workspace_source = SHEET_WORKSPACE.read_text(encoding="utf-8")

    assert "show-export-button" not in page_source
    assert 'v-if="showSheetMenu && canReadSheet"' in workspace_source
    assert "sheet-name-menu-button" in workspace_source
    assert "{{ sheetTitle }}" in workspace_source
    assert '@click="openSheetWorkspaceViewLinkMenu"' in workspace_source
    assert '@contextmenu.prevent="openSheetWorkspaceViewLinkMenu"' in workspace_source

    # Readable sheets can always export and copy their current view link.
    assert "另存为.xlsx" in workspace_source
    assert "copySheetWorkspaceViewLink(sheetWorkspaceView)" in workspace_source

    # Editing and permission management remain capability-gated menu extensions.
    assert 'v-if="canEditConfig"' in workspace_source
    assert 'v-if="canManageAccess"' in workspace_source
