from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RICH_TEXT_READER = REPO_ROOT / "frontend/src/components/rich-text/RichTextDocumentReader.vue"
BOOK_READER = REPO_ROOT / "frontend/src/standard/pdf/library/LinuxDoBookReaderDialog.vue"


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
    assert "'has-page-outline': isArticleBook" in source
    assert 'v-if="isArticleBook"' in source
    assert ">完成</el-button>" in source


def test_html_book_articles_use_continuous_flow_instead_of_visual_pages() -> None:
    source = BOOK_READER.read_text(encoding="utf-8")

    assert ".book-document {" in source
    assert "overflow: auto;" in source
    assert "width: min(100%, 820px);" in source
    assert "上一篇" not in source
    assert "下一篇" not in source
    assert "book-reader-controls" not in source
    assert "'is-paginated': isPaginated" in source
    assert 'v-if="!loading && !errorMessage && isPaginated"' in source
    assert ".book-document.is-paginated" in source
    assert "data-reader-page-state" not in source
    assert "readerTransientPagination" not in source
