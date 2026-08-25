from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from backend.api import library_annotations
from backend.models import LibraryAnnotation, LibraryBookAsset, User


def _create_test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=[
        User.__table__,
        LibraryBookAsset.__table__,
        LibraryAnnotation.__table__,
    ])
    return engine


def test_annotation_lifecycle_is_scoped_to_owner():
    engine = _create_test_engine()
    with Session(engine) as session:
        owner = User(username="annotation-owner", hashed_password="test")
        outsider = User(username="annotation-outsider", hashed_password="test")
        session.add(owner)
        session.add(outsider)
        session.commit()
        session.refresh(owner)
        session.refresh(outsider)
        asset = LibraryBookAsset(
            id="ebook:annotation-owner:1",
            owner_user_id=owner.id,
            source_kind="epub:test",
            title="可批注电子书",
        )
        session.add(asset)
        session.commit()

        created = library_annotations.create_annotation(
            library_annotations.LibraryAnnotationCreate(
                resource_id=asset.id,
                chapter_id="chapter-1",
                quote_text="被选中的原文",
                prefix_text="这是",
                suffix_text="后面的内容",
                start_offset=12,
                end_offset=19,
                source_revision="revision-1",
                comment_text="我的批注",
                kind="comment",
            ),
            session,
            owner,
        )

        assert created.comment_text == "我的批注"
        assert created.kind == "comment"
        listed = library_annotations.list_annotations(
            resource_type="rich-text",
            resource_id=asset.id,
            chapter_id="chapter-1",
            session=session,
            current_user=owner,
        )
        assert [item.id for item in listed] == [created.id]

        updated = library_annotations.update_annotation(
            created.id,
            library_annotations.LibraryAnnotationUpdate(comment_text=""),
            session,
            owner,
        )
        assert updated.kind == "highlight"
        assert updated.comment_text == ""

        try:
            library_annotations.list_annotations(
                resource_type="rich-text",
                resource_id=asset.id,
                chapter_id=None,
                session=session,
                current_user=outsider,
            )
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("非图书拥有者不应读取批注")

        response = library_annotations.delete_annotation(created.id, session, owner)
        assert response.status_code == 204
        assert session.get(LibraryAnnotation, created.id) is None
