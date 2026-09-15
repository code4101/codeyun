from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RICH_TEXT_READER = REPO_ROOT / "frontend/src/components/rich-text/RichTextDocumentReader.vue"
BOOK_READER = REPO_ROOT / "frontend/src/standard/pdf/library/LinuxDoBookReaderDialog.vue"
SKILL_BOOK_READER = REPO_ROOT / "frontend/src/standard/pdf/library/SkillBookReaderDialog.vue"
PANEL_HANDLE = REPO_ROOT / "frontend/src/standard/pdf/library/ReaderColumnHandle.vue"
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
    assert "'has-page-outline': isArticleBook" in source
    assert 'v-if="isArticleBook"' in source
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


def test_library_readers_can_collapse_side_columns() -> None:
    """三栏阅读器的左右栏各自可收起：手柄长在分割线上，悬浮才出现，收栏宽度还给正文。"""
    state_source = PANEL_STATE.read_text(encoding="utf-8")
    handle_source = PANEL_HANDLE.read_text(encoding="utf-8")

    assert "codeyun.library.reader-panels.v1" in state_source
    assert "export const readerTocVisible = ref(" in state_source
    assert "export const readerOutlineVisible = ref(" in state_source
    # 手柄默认不可见，悬浮分割线或键盘聚焦时才浮现，且不额外常驻控件。
    assert "opacity: 0;" in handle_source
    assert ".reader-column-handle:hover .reader-column-handle-button" in handle_source
    assert ".reader-column-handle:focus-within .reader-column-handle-button" in handle_source
    # 整条分割线都是热区：只看鼠标到线段的距离，按钮落点跟随鼠标在线段上的位置。
    assert "@pointermove=\"trackPointer\"" in handle_source
    assert "--reader-handle-shift" in handle_source
    # 热区两侧都留容错（侧栏侧 12px + 正文侧窄带），离开时延迟收起，避免擦边闪烁。
    assert "var(--reader-handle-reach, 12px)" in handle_source
    assert "scheduleHideHandle" in handle_source
    assert "HANDLE_HIDE_DELAY" in handle_source
    # 窄屏三栏变上下行，分割线随之水平，手柄要跟着转 90°。
    assert "translateX(var(--reader-handle-shift, 0px)) rotate(90deg);" in handle_source

    for reader in (BOOK_READER, SKILL_BOOK_READER):
        source = reader.read_text(encoding="utf-8")
        assert "import ReaderColumnHandle from './ReaderColumnHandle.vue'" in source
        assert "readerOutlineVisible" in source
        # 收起只切换可见性，搜索词与滚动位置等栏内状态仍保留在 DOM 中。
        assert 'v-show="readerTocVisible"' in source
        assert 'v-show="readerOutlineVisible"' in source
        # 宽屏用列轨道重排，窄屏用行轨道重排，两种布局都要覆盖隐藏态。
        assert "@media (min-width: 981px)" in source
        assert "is-toc-hidden" in source
        assert "is-outline-hidden" in source
        # 手柄窄带把正文让出滚动视口边界，所以不会盖住正文滚动条。
        assert "margin: 0 var(--reader-gutter, 20px);" in source

    # 归档书（X / 微博）历史 HTML 自带 640/680px 定宽列，阅读器要把版面宽度收回来。
    book_source = BOOK_READER.read_text(encoding="utf-8")
    assert ".book-document :deep(.x-entry)," in book_source
    assert ".book-document :deep(.weibo-entry)," in book_source
    assert "max-width: none !important;" in book_source
