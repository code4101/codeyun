from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RICH_TEXT_READER = REPO_ROOT / "frontend/src/components/rich-text/RichTextDocumentReader.vue"
BOOK_READER = REPO_ROOT / "frontend/src/standard/pdf/library/LinuxDoBookReaderDialog.vue"
SKILL_BOOK_READER = REPO_ROOT / "frontend/src/standard/pdf/library/SkillBookReaderDialog.vue"
PANEL_STATE = REPO_ROOT / "frontend/src/standard/pdf/library/readerPanels.ts"


def test_rich_text_reader_supports_toolbar_free_inline_editing() -> None:
    source = RICH_TEXT_READER.read_text(encoding="utf-8")

    assert "editable?: boolean" in source
    assert 'contenteditable="true"' in source
    assert "'content-change': [html: string]" in source
    assert "emit('content-change', rootRef.value.innerHTML)" in source
    assert "initializedEditableDocumentId.value !== documentId" in source


def test_html_book_edits_in_the_reader_without_mounting_note_editor() -> None:
    source = BOOK_READER.read_text(encoding="utf-8")

    assert "NoteEditor" not in source
    assert ':document="editingDocument"' in source
    assert "@content-change=\"articleDraftHtml = $event\"" in source
    assert "<ReaderLayout" in source
    assert 'v-else-if="isArticleBook"' in source
    assert ">完成</el-button>" in source


def test_html_book_articles_use_continuous_flow_instead_of_visual_pages() -> None:
    source = BOOK_READER.read_text(encoding="utf-8")

    assert ".book-document {" in source
    assert "overflow: auto;" in source
    # 正文默认按 820px 可读行宽排版，收栏后由 --reader-reading-max-width 放宽。
    assert "width: min(100%, var(--reader-reading-max-width, 820px));" in source
    assert "上一篇" not in source
    assert "下一篇" not in source
    assert "book-reader-controls" not in source
    assert "'is-paginated': isPaginated" in source
    assert 'v-if="!loading && !errorMessage && isPaginated"' in source
    assert ".book-document.is-paginated" in source
    assert "data-reader-page-state" not in source
    assert "readerTransientPagination" not in source


def test_library_readers_share_visible_activity_and_layout_controls() -> None:
    """常驻活动栏与右上角布局控制取代隐形分隔线手柄。"""
    directory = BOOK_READER.parent
    layout = (directory / "ReaderLayout.vue").read_text(encoding="utf-8")
    controls = (directory / "ReaderLayoutControls.vue").read_text(encoding="utf-8")
    assert not (directory / "ReaderColumnHandle.vue").exists()
    activity = (directory / "ReaderActivityBar.vue").read_text(encoding="utf-8")
    assert 'aria-label="阅读活动栏"' in activity
    assert 'aria-pressed="activeId === item.id"' in activity
    assert "<ReaderActivityBar" in layout
    assert 'v-show="tocVisible"' in layout
    assert 'v-show="outlineVisible"' in layout
    assert "ReaderColumnHandle" not in layout
    assert 'aria-label="切换右侧大纲"' in controls
    assert 'aria-label="切换左侧目录"' in controls
    for reader in (BOOK_READER, SKILL_BOOK_READER):
        source = reader.read_text(encoding="utf-8")
        assert "<ReaderLayout" in source
        assert "<ReaderLayoutControls" in source
        assert "<ReaderTocTree" in source
        assert "reader-gutter" not in source
    pdf_source = (directory.parent / "resource-view/page.vue").read_text(encoding="utf-8")
    assert "<ReaderLayoutControls" in pdf_source

    # 归档书（X / 微博）历史 HTML 自带 640/680px 定宽列，阅读器要把版面宽度收回来。
    book_source = BOOK_READER.read_text(encoding="utf-8")
    assert ".book-document :deep(.x-entry)," in book_source
    assert ".book-document :deep(.weibo-entry)," in book_source
    assert "max-width: none !important;" in book_source
