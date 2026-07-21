from pathlib import Path

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from backend.api import skill_books
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
    _write_skill(tmp_path, "前端UI规范", "正文", description="界面规范")
    references = tmp_path / "前端UI规范" / "references"
    references.mkdir()
    (references / "案例.md").write_text("# 视觉案例\n\n内容", encoding="utf-8")

    catalog, lookup = skill_books._scan_skill_book(tmp_path)

    assert catalog.skill_count == 1
    assert catalog.chapter_count == 2
    assert catalog.skills[0].name == "前端UI规范"
    assert catalog.skills[0].description == "界面规范"
    assert [chapter.title for chapter in catalog.skills[0].chapters] == ["前端UI规范", "视觉案例"]
    assert set(lookup) == {chapter.id for chapter in catalog.skills[0].chapters}
    first, second = catalog.skills[0].chapters
    assert first.page_start == 1
    assert first.page_end == first.estimated_page_count
    assert second.page_start == first.page_end + 1
    assert second.page_end == catalog.estimated_page_count
    assert catalog.page_capacity_units == 44 * 30


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


def test_skill_book_pagination_accounts_for_layout_and_chapter_boundaries(tmp_path):
    long_body = "\n\n".join(f"## 小节 {index}\n\n" + ("正文内容" * 80) for index in range(12))
    _write_skill(tmp_path, "长篇技能", long_body)
    references = tmp_path / "长篇技能" / "references"
    references.mkdir()
    (references / "短篇.md").write_text("# 短篇\n\n只有一段。", encoding="utf-8")

    catalog, _lookup = skill_books._scan_skill_book(tmp_path)
    main, reference = catalog.skills[0].chapters

    assert main.estimated_page_count > 1
    assert reference.estimated_page_count == 1
    assert reference.page_start == main.page_end + 1
    assert catalog.estimated_page_count == main.estimated_page_count + reference.estimated_page_count
    assert main.reading_unit_count > main.character_count


def test_skill_book_defaults_to_a4_and_page_format_changes_physical_metadata_and_pagination(tmp_path):
    _write_skill(tmp_path, "纸张规格", "正文内容" * 1500)

    a4_catalog, _lookup = skill_books._scan_skill_book(tmp_path)
    a5_catalog, _lookup = skill_books._scan_skill_book(tmp_path, page_format="A5")

    assert a4_catalog.page_format == "A4"
    assert (a4_catalog.page_width_mm, a4_catalog.page_height_mm) == (210.0, 297.0)
    assert {option.value for option in a4_catalog.page_format_options} >= {"A3", "A4", "A5", "B5", "LETTER"}
    assert a5_catalog.page_height_mm == 210.0
    assert a5_catalog.page_capacity_units < a4_catalog.page_capacity_units
    assert a5_catalog.estimated_page_count > a4_catalog.estimated_page_count


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
            skill_books.SkillBookMetadataUpdate(page_format="B5"),
            Response(),
            session,
            user,
        )
        state = skill_books.get_local_skill_book_reading_state(Response(), session, user)

    assert catalog.page_format == "B5"
    assert (catalog.page_width_mm, catalog.page_height_mm) == (176.0, 250.0)
    assert state.page_format == "B5"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/local/catalog?bookshelf_id=missing", None),
        ("get", "/local/chapters/private-chapter", None),
        ("get", "/local/my-state", None),
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

        chapter_id = catalog["skills"][0]["chapters"][0]["id"]
        chapter_response = client.get(f"/local/chapters/{chapter_id}")
        assert chapter_response.status_code == 200
        assert "共享书柜中的正文" in chapter_response.json()["markdown"]

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
