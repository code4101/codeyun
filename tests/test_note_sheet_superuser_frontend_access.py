from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sheet_workspace_superuser_overrides_all_resource_capabilities():
    source = (ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue").read_text(
        encoding="utf-8"
    )

    access_start = source.index("const effectiveAccessCapabilities = computed")
    access_end = source.index("const canUseLocalView", access_start)
    access_block = source[access_start:access_end]
    assert "userStore.isAdmin" in access_block
    assert access_block.index("userStore.isAdmin") < access_block.index("props.accessCapabilities")
    assert "? FULL_ACCESS_CAPABILITIES" in access_block


def test_workbook_resource_actions_treat_every_superuser_as_manager():
    source = (ROOT / "frontend/src/standard/notes/resource-view/page.vue").read_text(encoding="utf-8")

    assert "userStore.isAdmin || workbook.value?.access?.capabilities.can_edit_config" in source
    assert "userStore.isAdmin || workbook.value?.access?.capabilities.can_manage_access" in source
    assert "userStore.isAdmin || sheetTabContextMenuSheet.value?.access?.capabilities.can_edit_config" in source
    assert "userStore.isAdmin || sheetTabContextMenuSheet.value?.access?.capabilities.can_manage_access" in source


def test_sheet_copy_paste_treats_headers_as_cells_not_column_configuration():
    source = (ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue").read_text(
        encoding="utf-8"
    )

    paste_start = source.index("function handleBeforePaste")
    paste_end = source.index("function shouldTrimWhitespaceForColumn", paste_start)
    paste_block = source[paste_start:paste_end]
    assert "const startsInHeader" in paste_block
    assert "applyClipboardCellMetaToPasteTarget" in paste_block
    assert "if (!canEditConfig.value)" not in paste_block


def test_sheet_clipboard_copies_cell_content_and_presentation_not_actions():
    source = (ROOT / "frontend/src/standard/notes/components/NoteSheetWorkspace.vue").read_text(
        encoding="utf-8"
    )

    clone_start = source.index("function cloneClipboardCellMeta")
    clone_end = source.index("function consumePendingClipboardLinkMetaPatchResults", clone_start)
    clone_block = source[clone_start:clone_end]
    assert "cell_type" in clone_block
    assert "link" in clone_block
    assert "style" in clone_block
    assert "rich_text" in clone_block
    assert "action" not in clone_block
