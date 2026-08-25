import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from backend.api import linux_do_books, pdf_documents
from backend.models import (
    LibraryBookAsset,
    LibraryAnnotation,
    LibraryBookPlacement,
    LibraryFolder,
    LibraryReadingState,
    PdfBookshelfPlacement,
    PdfLibraryBookshelf,
    User,
)
from backend.core.library.linux_do_book import LinuxDoBookDocument, LinuxDoTocItem


def test_rebuild_html_book_toc_preserves_parent_article_relationship():
    soup = BeautifulSoup(
        """
        <article data-article-id="issue-405">
          <h1>405 资源，社会公平与算力</h1>
        </article>
        <article
          data-article-id="issue-405-yaoqizhi-ai-next-level"
          data-parent-article-id="issue-405"
        >
          <h1>姚期智万字长文演讲！解析“AI 研究的下一个层次”</h1>
          <h2>导读</h2>
        </article>
        """,
        "html.parser",
    )

    toc = linux_do_books._rebuild_html_book_toc(soup)

    assert [(item.anchor, item.parent_anchor, item.level) for item in toc] == [
        ("issue-405", None, 1),
        ("issue-405-yaoqizhi-ai-next-level", "issue-405", 2),
    ]


def _create_test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=[
        User.__table__,
        PdfLibraryBookshelf.__table__,
        LibraryBookAsset.__table__,
        LibraryAnnotation.__table__,
        LibraryBookPlacement.__table__,
        LibraryReadingState.__table__,
        LibraryFolder.__table__,
        PdfBookshelfPlacement.__table__,
    ])
    return engine


def test_owner_can_delete_dynamic_book_and_related_library_rows(monkeypatch, tmp_path):
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="delete-owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        shelf = PdfLibraryBookshelf(user_id=owner.id, name="1")
        asset = LibraryBookAsset(
            id="dynamic:delete-owner:1",
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            owner_user_id=owner.id,
            source_kind="epub:test",
        )
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=shelf.id,
        )
        state = LibraryReadingState(
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            resource_id=asset.id,
            user_id=owner.id,
        )
        session.add(shelf)
        session.add(asset)
        session.add(placement)
        session.add(state)
        session.commit()
        monkeypatch.setattr(linux_do_books, "_asset_storage_dir", lambda _asset: tmp_path / "book-cache")

        response = linux_do_books.delete_book(asset.id, session, owner)

        assert response.status_code == 204
        assert session.get(LibraryBookAsset, asset.id) is None
        assert session.get(LibraryBookPlacement, placement.id) is None
        assert session.get(LibraryReadingState, state.id) is None


def test_linux_do_book_placement_can_move_to_shelf_and_folder():
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        bookshelf = PdfLibraryBookshelf(user_id=owner.id, name="书柜 1")
        session.add(bookshelf)
        session.commit()
        session.refresh(bookshelf)
        folder = LibraryFolder(
            owner_user_id=owner.id,
            bookshelf_id=bookshelf.id,
            name="薄册",
            shelf_index=3,
            position_index=2,
        )
        asset = LibraryBookAsset(
            id="linux-do:owner:1",
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            owner_user_id=owner.id,
            source_kind="linux-do-topic:1",
        )
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=bookshelf.id,
        )
        session.add(folder)
        session.add(asset)
        session.add(placement)
        session.commit()

        moved = linux_do_books.update_book_placement(
            asset.id,
            linux_do_books.LinuxDoBookPlacementUpdate(
                bookshelf_id=bookshelf.id,
                shelf_index=2,
                position_index=7,
                orientation="spine_vertical",
            ),
            session,
            owner,
        )
        assert (moved.shelf_index, moved.position_index, moved.folder_id) == (2, 7, None)

        paginated = linux_do_books.update_book_placement(
            asset.id,
            linux_do_books.LinuxDoBookPlacementUpdate(
                bookshelf_id=bookshelf.id,
                shelf_index=2,
                position_index=7,
                orientation="spine_vertical",
                article_reading_mode="paginated",
            ),
            session,
            owner,
        )
        assert paginated.article_reading_mode == "paginated"

        moved_into_folder = linux_do_books.update_book_placement(
            asset.id,
            linux_do_books.LinuxDoBookPlacementUpdate(
                bookshelf_id=bookshelf.id,
                shelf_index=0,
                position_index=4,
                orientation="spine_vertical",
                folder_id=folder.id,
            ),
            session,
            owner,
        )
        assert moved_into_folder.shelf_index == folder.shelf_index
        assert moved_into_folder.position_index == 4
        assert moved_into_folder.folder_id == folder.id
        assert moved_into_folder.article_reading_mode == "paginated"

        inherited = linux_do_books.update_book_placement(
            asset.id,
            linux_do_books.LinuxDoBookPlacementUpdate(
                bookshelf_id=bookshelf.id,
                shelf_index=0,
                position_index=4,
                orientation="spine_vertical",
                folder_id=folder.id,
                article_reading_mode=None,
            ),
            session,
            owner,
        )
        assert inherited.article_reading_mode is None


def test_linux_do_book_metadata_updates_only_visible_fields(monkeypatch):
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="metadata-owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        bookshelf = PdfLibraryBookshelf(user_id=owner.id, name="书柜 1")
        session.add(bookshelf)
        session.commit()
        session.refresh(bookshelf)
        asset = LibraryBookAsset(
            id="linux-do:metadata-owner:1",
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            owner_user_id=owner.id,
            source_kind="linux-do-topic:1",
            title="旧书名",
            author="旧作者",
            cover_color="#294f6d",
        )
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=bookshelf.id,
        )
        session.add(asset)
        session.add(placement)
        session.commit()
        document = LinuxDoBookDocument(
            topic_id=1,
            title="源书名",
            author="源作者",
            source_url="https://linux.do/t/topic/1",
            content_html="<p>正文</p>",
            content_markdown="正文",
            toc=[],
            revision="revision",
            post_count=1,
            selected_reply_count=0,
            imported_at=1.0,
            estimated_page_count=20,
        )
        monkeypatch.setattr(linux_do_books, "_read_document", lambda _asset: document)

        asset.metadata_json = {"thickness_mm_override": 6.5}
        updated = linux_do_books.update_book_metadata(
            asset.id,
            linux_do_books.LinuxDoBookMetadataUpdate(
                title="新书名",
                author="",
                start_date="1989-06",
                cover_color="#123456",
            ),
            session,
            owner,
        )

        assert updated.title == "新书名"
        assert updated.author == ""
        assert updated.start_date == "1989-06"
        assert updated.cover_color == "#123456"
        session.refresh(asset)
        assert asset.metadata_json == {
            "thickness_mm_override": 6.5,
            "start_date": "1989-06",
        }


def test_linux_do_book_start_date_accepts_partial_precision_and_rejects_invalid_dates():
    assert linux_do_books.LinuxDoBookMetadataUpdate(
        title="书",
        start_date="1989",
    ).start_date == "1989"
    assert linux_do_books.LinuxDoBookMetadataUpdate(
        title="书",
        start_date="1989-06-15",
    ).start_date == "1989-06-15"

    with pytest.raises(ValueError, match="日期无效"):
        linux_do_books.LinuxDoBookMetadataUpdate(
            title="书",
            start_date="1989-02-30",
        )


def test_book_list_uses_metadata_only_and_filters_bookshelf_in_query(monkeypatch):
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="list-owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        first_shelf = PdfLibraryBookshelf(user_id=owner.id, name="书柜 1")
        second_shelf = PdfLibraryBookshelf(user_id=owner.id, name="书柜 2")
        session.add(first_shelf)
        session.add(second_shelf)
        session.commit()
        session.refresh(first_shelf)
        session.refresh(second_shelf)

        for index, shelf in enumerate((first_shelf, second_shelf), start=1):
            asset = LibraryBookAsset(
                id=f"linux-do:list-owner:{index}",
                resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
                owner_user_id=owner.id,
                source_kind=f"linux-do-topic:{index}",
                title=f"第 {index} 本",
                author="作者",
                cover_color="#294f6d",
                metadata_json={
                    "topic_id": index,
                    "book_kind": "linux-do",
                    "source_url": f"https://linux.do/t/topic/{index}",
                    "revision": f"revision-{index}",
                    "toc_count": index + 1,
                    "post_count": index + 2,
                    "selected_reply_count": index,
                    "estimated_page_count": index * 10,
                    "imported_at": float(index),
                },
            )
            session.add(asset)
            session.add(LibraryBookPlacement(
                book_asset_id=asset.id,
                user_id=owner.id,
                bookshelf_id=shelf.id,
            ))
            if index == 1:
                session.add(LibraryReadingState(
                    resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
                    resource_id=asset.id,
                    user_id=owner.id,
                    chapter_id="issue-42",
                    character_offset=321,
                    state_json={"current_page": 7, "page_count": 10},
                    updated_at=123.0,
                ))
        session.commit()

        monkeypatch.setattr(
            linux_do_books,
            "_read_document",
            lambda _asset: pytest.fail("列表接口不应读取正文"),
        )

        summaries = linux_do_books.list_books(first_shelf.id, session, owner)

        assert [summary.title for summary in summaries] == ["第 1 本"]
        assert summaries[0].toc_count == 2
        assert summaries[0].selected_reply_count == 1
        assert summaries[0].estimated_page_count == 10
        assert summaries[0].reading_state is not None
        assert summaries[0].reading_state.chapter_id == "issue-42"
        assert summaries[0].reading_state.current_page == 7
        assert summaries[0].reading_state.page_count == 10


def test_unified_library_layout_orders_dynamic_books_and_folders_together():
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="layout-owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        bookshelf = PdfLibraryBookshelf(user_id=owner.id, name="书柜 1")
        session.add(bookshelf)
        session.commit()
        session.refresh(bookshelf)
        folder = LibraryFolder(
            owner_user_id=owner.id,
            bookshelf_id=bookshelf.id,
            name="薄册",
            shelf_index=0,
            position_index=0,
        )
        asset = LibraryBookAsset(
            id="linux-do:layout-owner:1",
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            owner_user_id=owner.id,
            source_kind="linux-do-topic:2",
        )
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=bookshelf.id,
            shelf_index=0,
            position_index=1,
        )
        session.add(folder)
        session.add(asset)
        session.add(placement)
        session.commit()

        result = pdf_documents.update_library_bookshelf_layout(
            pdf_documents.LibraryBookshelfLayoutUpdateRequest(
                bookshelf_id=bookshelf.id,
                items=[
                    pdf_documents.LibraryBookshelfLayoutItemPayload(
                        resource_type="book_asset",
                        resource_id=asset.id,
                        shelf_index=1,
                        position_index=0,
                    ),
                    pdf_documents.LibraryBookshelfLayoutItemPayload(
                        resource_type="folder",
                        resource_id=folder.id,
                        shelf_index=1,
                        position_index=1,
                    ),
                ],
            ),
            session,
            owner,
        )

        assert [(item.resource_type, item.position_index) for item in result] == [
            ("book_asset", 0),
            ("folder", 1),
        ]
        session.refresh(placement)
        session.refresh(folder)
        assert (placement.shelf_index, placement.position_index, placement.folder_id) == (1, 0, None)
        assert (folder.shelf_index, folder.position_index) == (1, 1)


def test_brainstorm_article_can_update_html_with_revision_check(monkeypatch):
    engine = _create_test_engine()
    written_documents: list[LinuxDoBookDocument] = []
    with Session(engine) as session:
        owner = User(username="brainstorm-owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        bookshelf = PdfLibraryBookshelf(user_id=owner.id, name="1")
        session.add(bookshelf)
        session.commit()
        session.refresh(bookshelf)
        asset = LibraryBookAsset(
            id="brainstorm:test",
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            owner_user_id=owner.id,
            source_kind="brainstorm:test",
            title="头脑风暴",
            author="owner",
            metadata_json={
                "book_kind": "brainstorm",
                "articles": [{"id": "first", "title": "旧标题"}],
            },
        )
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=bookshelf.id,
        )
        session.add(asset)
        session.add(placement)
        session.commit()

        document = LinuxDoBookDocument(
            topic_id=-1,
            title="头脑风暴",
            author="owner",
            source_url="http://localhost/",
            content_html='<article data-article-id="first"><h1 id="first">旧标题</h1><p>旧正文</p></article>',
            content_markdown="",
            toc=[LinuxDoTocItem(title="旧标题", number="", level=1, anchor="first")],
            revision="old-revision",
            post_count=1,
            selected_reply_count=0,
            imported_at=1.0,
        )
        monkeypatch.setattr(linux_do_books, "_read_document", lambda _asset: document)
        monkeypatch.setattr(
            linux_do_books,
            "_write_document",
            lambda _user_id, next_document: written_documents.append(next_document),
        )

        updated = linux_do_books.update_html_book_article(
            asset.id,
            "first",
            linux_do_books.HtmlBookArticleUpdate(
                content_html='<h1>新标题</h1><p>新正文</p><script>alert(1)</script>',
                revision="old-revision",
            ),
            session,
            owner,
        )

        assert updated.toc[0].title == "新标题"
        assert updated.toc[0].anchor == "first"
        assert "新正文" in updated.content_html
        assert "<script" not in updated.content_html
        assert updated.content_markdown == ""
        assert updated.revision != "old-revision"
        assert written_documents[-1].revision == updated.revision

        with pytest.raises(HTTPException) as conflict:
            linux_do_books.update_html_book_article(
                asset.id,
                "first",
                linux_do_books.HtmlBookArticleUpdate(
                    content_html="<h1>再次修改</h1>",
                    revision="old-revision",
                ),
                session,
                owner,
            )
        assert getattr(conflict.value, "status_code", None) == 409


def test_article_collection_appends_articles_and_keeps_sections_inside_article(monkeypatch):
    engine = _create_test_engine()
    stored_documents: dict[int, LinuxDoBookDocument] = {}

    def write_document(user_id: int, document: LinuxDoBookDocument) -> None:
        stored_documents[user_id] = document

    def read_document(asset: LibraryBookAsset) -> LinuxDoBookDocument:
        return stored_documents[asset.owner_user_id]

    monkeypatch.setattr(linux_do_books, "_write_document", write_document)
    monkeypatch.setattr(linux_do_books, "_read_document", read_document)

    with Session(engine) as session:
        owner = User(username="collection-owner", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)

        first = linux_do_books.upsert_article_collection_entry(
            session=session,
            current_user=owner,
            collection_source_kind="article-collection:party-history-politics",
            collection_title="党史政治文选",
            article_id="resolution-1981",
            article_title="关于建国以来党的若干历史问题的决议",
            article_content_html="<h2>第一章</h2><p>正文</p>",
            article_author="中国共产党中央委员会",
            article_date="1981-06-27",
        )
        second = linux_do_books.upsert_article_collection_entry(
            session=session,
            current_user=owner,
            collection_source_kind="article-collection:party-history-politics",
            collection_title="党史政治文选",
            article_id="another-article",
            article_title="另一篇文章",
            article_content_html="<h1>另一篇文章</h1><h2>文章提纲</h2><p>内容</p>",
        )
        replaced = linux_do_books.upsert_article_collection_entry(
            session=session,
            current_user=owner,
            collection_source_kind="article-collection:party-history-politics",
            collection_title="党史政治文选",
            article_id="resolution-1981",
            article_title="关于建国以来党的若干历史问题的决议",
            article_content_html="<h2>修订后的第一章</h2><p>新正文</p>",
            article_author="中国共产党中央委员会",
            article_date="1981-06-27",
        )

        assert first.id == second.id == replaced.id
        assert replaced.title == "党史政治文选"
        assert replaced.book_kind == "article-collection"
        assert replaced.capabilities.can_edit_content is True
        document = stored_documents[owner.id]
        assert [item.title for item in document.toc] == [
            "关于建国以来党的若干历史问题的决议",
            "另一篇文章",
        ]
        soup = BeautifulSoup(document.content_html, "html.parser")
        articles = soup.select("article[data-article-id]")
        assert len(articles) == 2
        assert articles[0].find("h1").get_text(strip=True) == "关于建国以来党的若干历史问题的决议"
        assert articles[0].find("h2").get_text(strip=True) == "修订后的第一章"
        assert len(articles[1].find_all("h1")) == 1
        assert articles[1].find("h2").get_text(strip=True) == "文章提纲"


def test_owned_text_ebook_source_can_be_read_and_rebuilt(monkeypatch, tmp_path):
    engine = _create_test_engine()
    written: list[tuple[LinuxDoBookDocument, str]] = []
    with Session(engine) as session:
        owner = User(username="ebook-editor", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        bookshelf = PdfLibraryBookshelf(user_id=owner.id, name="1")
        session.add(bookshelf)
        session.commit()
        session.refresh(bookshelf)
        asset = LibraryBookAsset(
            id="ebook:editor:text",
            resource_type=linux_do_books.BOOK_RESOURCE_TYPE,
            owner_user_id=owner.id,
            source_kind="ebook:test",
            title="旧标题",
            metadata_json={
                "topic_id": -1,
                "book_kind": "ebook",
                "format": "text",
                "original_filename": "旧标题.txt",
            },
        )
        placement = LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=owner.id,
            bookshelf_id=bookshelf.id,
        )
        session.add(asset)
        session.add(placement)
        session.commit()

        storage_dir = tmp_path / "book"
        storage_dir.mkdir()
        (storage_dir / "source.txt").write_bytes("旧正文".encode("gb18030"))
        document = LinuxDoBookDocument(
            topic_id=-1,
            title="旧标题",
            author="",
            source_url="",
            content_html='<article data-article-id="article-1"><h1 id="article-1">旧标题</h1><p>旧正文</p></article>',
            content_markdown="旧正文",
            toc=[LinuxDoTocItem(title="旧标题", number="", level=1, anchor="article-1")],
            revision="old-revision",
            post_count=1,
            selected_reply_count=0,
            imported_at=1.0,
        )
        monkeypatch.setattr(linux_do_books, "_asset_storage_dir", lambda _asset: storage_dir)
        monkeypatch.setattr(linux_do_books, "_read_document", lambda _asset: document)
        monkeypatch.setattr(
            linux_do_books,
            "_write_imported_ebook",
            lambda _asset, next_document, _imported, source_path: written.append(
                (next_document, source_path.read_text(encoding="utf-8")),
            ),
        )

        source = linux_do_books.get_ebook_source(asset.id, session, owner)
        assert source.content == "旧正文"
        assert source.format == "text"

        updated = linux_do_books.update_ebook_source(
            asset.id,
            linux_do_books.EbookSourceUpdate(
                content="第一段\n\n第二段",
                revision="old-revision",
            ),
            session,
            owner,
        )
        assert "第一段" in updated.content_html
        assert "第二段" in updated.content_html
        assert updated.revision != "old-revision"
        assert written[-1][1] == "第一段\n\n第二段"

        with pytest.raises(HTTPException) as conflict:
            linux_do_books.update_ebook_source(
                asset.id,
                linux_do_books.EbookSourceUpdate(
                    content="冲突",
                    revision="wrong-revision",
                ),
                session,
                owner,
            )
        assert conflict.value.status_code == 409
