import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from backend.api import pdf_documents, skill_books
from backend.api.pdf_documents import LIBRARY_BOOKSHELF_RESOURCE_TYPE
from backend.models import (
    LibraryBookAsset,
    LibraryBookPlacement,
    LibraryReadingState,
    PdfBookshelfPlacement,
    PdfLibraryBookshelf,
    ResourceAccessGrant,
    User,
)


SKILL_BOOK_TABLES = [
    User.__table__,
    ResourceAccessGrant.__table__,
    LibraryReadingState.__table__,
    LibraryBookAsset.__table__,
    LibraryBookPlacement.__table__,
    PdfLibraryBookshelf.__table__,
    PdfBookshelfPlacement.__table__,
]


def _create_test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=SKILL_BOOK_TABLES)
    return engine


def _write_skill(root: Path, name: str, body: str, *, description: str = "") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        f'---\nname: "{name}"\ndescription: "{description}"\n---\n\n# {name}\n\n{body}\n',
        encoding="utf-8",
    )
    return skill_path


def test_skill_book_catalog_discovers_main_and_reference_chapters(tmp_path):
    skill_path = _write_skill(tmp_path, "前端UI规范", "正文", description="界面规范")
    references = tmp_path / "前端UI规范" / "references"
    references.mkdir()
    reference_path = references / "案例.md"
    reference_path.write_text("# 视觉案例\n\n内容", encoding="utf-8")

    catalog, lookup = skill_books._scan_skill_book(tmp_path)

    assert catalog.skill_count == 1
    assert catalog.asset_id == skill_books.SKILL_BOOK_ASSET_ID
    assert catalog.chapter_count == 2
    assert catalog.skills[0].name == "前端UI规范"
    assert catalog.skills[0].description == "界面规范"
    assert [chapter.title for chapter in catalog.skills[0].chapters] == ["前端UI规范", "视觉案例"]
    assert set(lookup) == {chapter.id for chapter in catalog.skills[0].chapters}
    first, second = catalog.skills[0].chapters
    first_stat = skill_path.stat()
    second_stat = reference_path.stat()
    assert first.created_at == pytest.approx(skill_books._file_created_at(first_stat))
    assert first.modified_at == pytest.approx(first_stat.st_mtime)
    assert first.updated_at == first.modified_at
    assert second.created_at == pytest.approx(skill_books._file_created_at(second_stat))
    assert second.modified_at == pytest.approx(second_stat.st_mtime)
    assert first.page_start == 1
    assert first.page_end == first.estimated_page_count
    assert second.book_character_start == first.character_count
    assert second.page_start == first.page_end
    assert second.page_end == catalog.estimated_page_count
    assert catalog.page_capacity_units == 1000


def test_skill_book_asset_id_is_accepted_by_unified_library_layout():
    engine = _create_test_engine()
    with Session(engine) as session:
        user = User(username="code4101", hashed_password="test")
        session.add(user)
        session.commit()
        session.refresh(user)

        asset, _placement, shelf, _owner = skill_books._ensure_local_skill_book_asset(session)
        result = pdf_documents.update_library_bookshelf_layout(
            pdf_documents.LibraryBookshelfLayoutUpdateRequest(
                bookshelf_id=shelf.id,
                items=[pdf_documents.LibraryBookshelfLayoutItemPayload(
                    resource_type="book_asset",
                    resource_id=asset.id,
                    shelf_index=2,
                    position_index=0,
                )],
            ),
            session,
            user,
        )

        assert result[0].resource_id == skill_books.SKILL_BOOK_ASSET_ID
        moved = session.exec(
            select(LibraryBookPlacement)
            .where(LibraryBookPlacement.book_asset_id == skill_books.SKILL_BOOK_ASSET_ID)
        ).one()
        assert moved.shelf_index == 2


def test_existing_local_skill_book_read_does_not_commit():
    engine = _create_test_engine()
    with Session(engine) as session:
        user = User(username="code4101", hashed_password="test")
        session.add(user)
        session.commit()
        skill_books._ensure_local_skill_book_asset(session)

        commit_count = 0
        real_commit = session.commit

        def counted_commit():
            nonlocal commit_count
            commit_count += 1
            real_commit()

        session.commit = counted_commit
        skill_books._ensure_local_skill_book_asset(session)

        assert commit_count == 0


def test_owner_can_remove_local_skill_book_without_deleting_skill_sources():
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="code4101", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        asset, placement, _shelf, _owner = skill_books._ensure_local_skill_book_asset(session)

        response = skill_books.delete_local_skill_book(session, owner)

        assert response.status_code == 204
        session.refresh(asset)
        assert asset.metadata_json["library_hidden"] is True
        assert session.get(LibraryBookPlacement, placement.id) is None
        with pytest.raises(HTTPException) as error:
            skill_books._ensure_local_skill_book_asset(session)
        assert getattr(error.value, "status_code", None) == 404


def test_skill_book_catalog_and_content_reflect_file_changes_without_snapshot(tmp_path):
    skill_path = _write_skill(tmp_path, "动态技能", "第一版")
    first_catalog, first_lookup = skill_books._scan_skill_book(tmp_path)
    chapter_id = first_catalog.skills[0].chapters[0].id
    first_path = first_lookup[chapter_id][1]
    assert "第一版" in skill_books._split_frontmatter(skill_books._read_markdown(first_path))[1]

    skill_path.write_text(
        '---\nname: "动态技能"\ndescription: "实时内容"\n---\n\n# 动态技能\n\n第二版已经更新\n',
        encoding="utf-8",
    )

    second_catalog, second_lookup = skill_books._scan_skill_book(tmp_path)
    second_path = second_lookup[chapter_id][1]
    second_body = skill_books._split_frontmatter(skill_books._read_markdown(second_path))[1]

    assert second_catalog.revision != first_catalog.revision
    assert second_catalog.skills[0].description == "实时内容"
    assert "第二版已经更新" in second_body
    assert "第一版" not in second_body


def test_skill_book_scan_cache_reuses_unchanged_sources_and_invalidates_on_edit(
    tmp_path,
    monkeypatch,
):
    skill_path = _write_skill(tmp_path, "缓存技能", "第一版")
    real_scan = skill_books._scan_skill_book
    scan_count = 0

    def counted_scan(*args, **kwargs):
        nonlocal scan_count
        scan_count += 1
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(skill_books, "_scan_skill_book", counted_scan)
    first_catalog, _lookup = skill_books._scan_skill_book_cached(tmp_path)
    second_catalog, _lookup = skill_books._scan_skill_book_cached(tmp_path)

    assert scan_count == 1
    assert second_catalog is first_catalog

    skill_path.write_text(
        '---\nname: "缓存技能"\ndescription: "已更新"\n---\n\n# 缓存技能\n\n第二版内容更长\n',
        encoding="utf-8",
    )
    updated_catalog, _lookup = skill_books._scan_skill_book_cached(tmp_path)

    assert scan_count == 2
    assert updated_catalog.revision != first_catalog.revision
    assert updated_catalog.skills[0].description == "已更新"


def test_skill_book_pagination_uses_cumulative_text_length_across_chapters(tmp_path):
    long_body = "\n\n".join(f"## 小节 {index}\n\n" + ("正文内容" * 80) for index in range(12))
    _write_skill(tmp_path, "长篇技能", long_body)
    references = tmp_path / "长篇技能" / "references"
    references.mkdir()
    (references / "短篇.md").write_text("# 短篇\n\n只有一段。", encoding="utf-8")

    catalog, _lookup = skill_books._scan_skill_book(tmp_path)
    main, reference = catalog.skills[0].chapters

    assert main.estimated_page_count > 1
    assert reference.estimated_page_count == 1
    assert reference.book_character_start == main.character_count
    assert reference.page_start == main.page_end
    assert catalog.estimated_page_count == math.ceil(
        (main.character_count + reference.character_count) / 1000
    )


def test_skill_book_page_format_changes_physical_metadata_not_text_pagination(tmp_path):
    _write_skill(tmp_path, "纸张规格", "正文内容" * 1500)

    a4_catalog, _lookup = skill_books._scan_skill_book(tmp_path)
    a5_catalog, _lookup = skill_books._scan_skill_book(tmp_path, page_format="A5")

    assert a4_catalog.page_format == "A4"
    assert (a4_catalog.page_width_mm, a4_catalog.page_height_mm) == (210.0, 297.0)
    assert {option.value for option in a4_catalog.page_format_options} >= {"A3", "A4", "A5", "B5", "LETTER"}
    assert a5_catalog.page_height_mm == 210.0
    assert a5_catalog.page_capacity_units == a4_catalog.page_capacity_units == 1000
    assert a5_catalog.estimated_page_count == a4_catalog.estimated_page_count


def test_skill_book_reading_position_is_persisted_as_anchor_and_mapped_to_page(tmp_path, monkeypatch):
    _write_skill(tmp_path, "阅读进度", "正文内容" * 900)
    monkeypatch.setenv("CODEYUN_SKILLS_ROOT", str(tmp_path))
    catalog, _lookup = skill_books._scan_skill_book(tmp_path)
    chapter = catalog.skills[0].chapters[0]

    engine = _create_test_engine()
    with Session(engine) as session:
        user = User(username="code4101", hashed_password="test")
        session.add(user)
        session.commit()
        session.refresh(user)

        saved = skill_books.update_local_skill_book_reading_state(
            skill_books.SkillBookReadingStateUpdate(
                chapter_id=chapter.id,
                character_offset=chapter.character_count // 2,
                chapter_revision=chapter.revision,
            ),
            Response(),
            session,
            user,
        )
        loaded = skill_books.get_local_skill_book_reading_state(Response(), session, user)

    assert loaded.chapter_id == chapter.id
    assert loaded.character_offset == saved.character_offset
    assert loaded.current_page == saved.current_page
    assert chapter.page_start <= loaded.current_page <= chapter.page_end


def test_skill_book_page_format_is_persisted_as_user_metadata(tmp_path, monkeypatch):
    _write_skill(tmp_path, "可配置开本", "正文")
    monkeypatch.setenv("CODEYUN_SKILLS_ROOT", str(tmp_path))

    engine = _create_test_engine()
    with Session(engine) as session:
        user = User(username="code4101", hashed_password="test")
        session.add(user)
        session.commit()
        session.refresh(user)

        catalog = skill_books.update_local_skill_book_metadata(
            skill_books.SkillBookMetadataUpdate(page_format="B5", start_date="2026-07"),
            Response(),
            session,
            user,
        )
        state = skill_books.get_local_skill_book_reading_state(Response(), session, user)

    assert catalog.page_format == "B5"
    assert catalog.start_date == "2026-07"
    assert (catalog.page_width_mm, catalog.page_height_mm) == (176.0, 250.0)
    assert state.page_format == "B5"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/local/catalog?bookshelf_id=missing", None),
        ("get", "/local/chapters/private-chapter", None),
        ("get", "/local/my-state", None),
        ("post", "/local/translations/sync", None),
        (
            "put",
            "/local/my-state",
            {"chapter_id": "private-chapter", "character_offset": 0, "chapter_revision": ""},
        ),
        ("put", "/local/metadata", {"page_format": "A4"}),
    ],
)
def test_local_skill_book_endpoints_are_hidden_from_other_users(method, path, payload):
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="code4101", hashed_password="test")
        other_user = User(username="other-user", hashed_password="test")
        session.add(owner)
        session.add(other_user)
        session.commit()
        session.refresh(other_user)

    def override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(skill_books.router)
    app.dependency_overrides[skill_books.get_session] = override_session
    app.dependency_overrides[skill_books.get_current_active_user] = lambda: other_user

    with TestClient(app) as client:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Local skill book not found"}


def test_shared_bookshelf_grants_skill_book_reading_but_not_management(tmp_path, monkeypatch):
    _write_skill(tmp_path, "共享阅读", "共享书柜中的正文")
    monkeypatch.setenv("CODEYUN_SKILLS_ROOT", str(tmp_path))
    engine = _create_test_engine()

    with Session(engine, expire_on_commit=False) as session:
        owner = User(username="code4101", hashed_password="test")
        reader = User(username="shared-reader", hashed_password="test")
        session.add(owner)
        session.add(reader)
        session.commit()
        session.refresh(reader)
        _asset, _placement, shelf, _owner = skill_books._ensure_local_skill_book_asset(session)
        session.add(ResourceAccessGrant(
            resource_type=LIBRARY_BOOKSHELF_RESOURCE_TYPE,
            resource_id=shelf.id,
            subject_key=f"user:{reader.id}",
            subject_type="user",
            subject_user_id=reader.id,
            role="viewer",
            updated_by_user_id=owner.id,
        ))
        session.commit()
        shelf_id = shelf.id

    def override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(skill_books.router)
    app.dependency_overrides[skill_books.get_session] = override_session
    app.dependency_overrides[skill_books.get_current_active_user] = lambda: reader

    with TestClient(app) as client:
        catalog_response = client.get("/local/catalog", params={"bookshelf_id": shelf_id})
        assert catalog_response.status_code == 200
        catalog = catalog_response.json()
        assert catalog["owner_username"] == "code4101"
        assert catalog["is_owned"] is False
        assert catalog["access_role"] == "viewer"
        assert catalog["bookshelf_placement"]["bookshelf_id"] == shelf_id

        catalog_chapter = catalog["skills"][0]["chapters"][0]
        assert catalog_chapter["created_at"] > 0
        assert catalog_chapter["modified_at"] > 0
        assert catalog_chapter["updated_at"] == catalog_chapter["modified_at"]
        chapter_id = catalog_chapter["id"]
        chapter_response = client.get(f"/local/chapters/{chapter_id}")
        assert chapter_response.status_code == 200
        assert "共享书柜中的正文" in chapter_response.json()["markdown"]
        assert chapter_response.json()["translation"]["status"] == "not_needed"

        reading_response = client.put("/local/my-state", json={
            "chapter_id": chapter_id,
            "character_offset": 3,
            "chapter_revision": catalog["skills"][0]["chapters"][0]["revision"],
        })
        assert reading_response.status_code == 200
        assert reading_response.json()["character_offset"] == 3

        metadata_response = client.put("/local/metadata", json={"page_format": "A5"})
        assert metadata_response.status_code == 403

    with Session(engine) as session:
        states = list(session.exec(
            select(LibraryReadingState).where(LibraryReadingState.resource_id == skill_books.SKILL_BOOK_ID)
        ).all())
        assert [(state.user_id, state.character_offset) for state in states] == [(reader.id, 3)]


def test_translation_sync_queues_english_chapters_without_editing_source(tmp_path, monkeypatch):
    source_path = _write_skill(
        tmp_path,
        "english-skill",
        (
            "This guide explains how to install, configure, and operate the application. "
            "It includes production deployment steps, troubleshooting guidance, and examples "
            "for developers who maintain the service."
        ),
    )
    original = source_path.read_text(encoding="utf-8")
    monkeypatch.setenv("CODEYUN_SKILLS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        skill_books,
        "resolve_ai_app_runtime_config",
        lambda **_kwargs: {"provider": "test", "model": "test"},
    )
    captured = {}

    def fake_submit_once(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="translation-task"), True

    monkeypatch.setattr(skill_books, "submit_local_job_once", fake_submit_once)
    engine = _create_test_engine()
    with Session(engine, expire_on_commit=False) as session:
        owner = User(username="code4101", hashed_password="test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
        skill_books._ensure_local_skill_book_asset(session)
        result = skill_books.sync_local_skill_book_translations(session, owner)

    assert result.status == "queued"
    assert result.eligible_count == 1
    assert result.queued_count == 1
    assert captured["job_type"] == "library.skill-book-translation"
    assert captured["payload"] == {"user_id": owner.id}
    assert captured["user_id"] == owner.id
    assert source_path.read_text(encoding="utf-8") == original
