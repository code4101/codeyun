import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine

from backend.api import pdf_documents
from backend.models import (
    LibraryBookAsset,
    LibraryBookPlacement,
    PdfBookshelfPlacement,
    PdfDocument,
    PdfLibraryBookshelf,
    PdfUserState,
    ResourceAccessGrant,
    User,
)


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    for model in (
        User,
        PdfDocument,
        PdfLibraryBookshelf,
        PdfBookshelfPlacement,
        PdfUserState,
        ResourceAccessGrant,
        LibraryBookAsset,
        LibraryBookPlacement,
    ):
        model.__table__.create(engine, checkfirst=True)
    return engine


def _user(username: str) -> User:
    return User(username=username, hashed_password="test")


def _seed_shared_shelf(session: Session):
    owner = _user("owner")
    viewer = _user("viewer")
    outsider = _user("outsider")
    session.add(owner)
    session.add(viewer)
    session.add(outsider)
    session.commit()
    session.refresh(owner)
    session.refresh(viewer)
    session.refresh(outsider)

    document = PdfDocument(
        numeric_id=101,
        title="私有图书.pdf",
        owner_user_id=owner.id,
        source_device_id="owner-device",
        source_absolute_path="D:/private/私有图书.pdf",
    )
    shelf = PdfLibraryBookshelf(user_id=owner.id, name="思想")
    session.add(document)
    session.add(shelf)
    session.commit()
    session.refresh(document)
    session.refresh(shelf)

    session.add(PdfBookshelfPlacement(
        pdf_document_id="101",
        user_id=owner.id,
        bookshelf_id=shelf.id,
        shelf_index=2,
        position_index=3,
        orientation="cover_front",
    ))
    session.add(ResourceAccessGrant(
        resource_type=pdf_documents.LIBRARY_BOOKSHELF_RESOURCE_TYPE,
        resource_id=shelf.id,
        subject_key=f"user:{viewer.id}",
        subject_type="user",
        subject_user_id=viewer.id,
        role="viewer",
        updated_by_user_id=owner.id,
    ))
    session.commit()
    return owner, viewer, outsider, document, shelf


def test_shared_bookshelf_grants_contextual_pdf_read_and_owner_layout():
    with Session(_engine()) as session:
        owner, viewer, outsider, document, shelf = _seed_shared_shelf(session)

        viewer_access = pdf_documents._resolve_pdf_resource_access(session, document, viewer)
        outsider_access = pdf_documents._resolve_pdf_resource_access(session, document, outsider)
        summary = pdf_documents._serialize_pdf_summary(
            session,
            document,
            current_user=viewer,
            access=viewer_access,
            placement_user_id=owner.id,
        )
        shared_shelf = pdf_documents._serialize_shared_bookshelf(
            session,
            shelf,
            viewer,
            pdf_documents._resolve_bookshelf_resource_access(session, shelf, viewer),
        )

        assert viewer_access.role == "viewer"
        assert viewer_access.capabilities.can_update_state is True
        assert outsider_access.role == "none"
        assert summary.bookshelf_placement is not None
        assert summary.bookshelf_placement.shelf_index == 2
        assert summary.bookshelf_placement.position_index == 3
        assert summary.bookshelf_placement.orientation == "cover_front"
        assert summary.source_device_id == ""
        assert summary.source_absolute_path == ""
        assert shared_shelf.owner_username == "owner"
        assert shared_shelf.book_count == 1


def test_direct_pdf_deny_overrides_bookshelf_inheritance_and_revocation_is_immediate():
    with Session(_engine()) as session:
        owner, viewer, _outsider, document, shelf = _seed_shared_shelf(session)
        deny = ResourceAccessGrant(
            resource_type=pdf_documents.PDF_RESOURCE_TYPE,
            resource_id="101",
            subject_key=f"user:{viewer.id}",
            subject_type="user",
            subject_user_id=viewer.id,
            role="deny",
            updated_by_user_id=owner.id,
        )
        session.add(deny)
        session.commit()

        assert pdf_documents._resolve_pdf_resource_access(session, document, viewer).role == "deny"

        session.delete(deny)
        shelf_grant = pdf_documents._fetch_resource_grants(
            session,
            shelf.id,
            resource_type=pdf_documents.LIBRARY_BOOKSHELF_RESOURCE_TYPE,
        )[0]
        session.delete(shelf_grant)
        session.commit()

        assert pdf_documents._resolve_pdf_resource_access(session, document, viewer).role == "none"


def test_pdf_viewer_cannot_reshare_another_users_book_through_a_shelf():
    with Session(_engine()) as session:
        owner, viewer, outsider, document, _shelf = _seed_shared_shelf(session)
        viewer_shelf = PdfLibraryBookshelf(user_id=viewer.id, name="转发书柜")
        session.add(viewer_shelf)
        session.commit()
        session.refresh(viewer_shelf)
        session.add(PdfBookshelfPlacement(
            pdf_document_id="101",
            user_id=viewer.id,
            bookshelf_id=viewer_shelf.id,
        ))
        session.add(ResourceAccessGrant(
            resource_type=pdf_documents.LIBRARY_BOOKSHELF_RESOURCE_TYPE,
            resource_id=viewer_shelf.id,
            subject_key=f"user:{outsider.id}",
            subject_type="user",
            subject_user_id=outsider.id,
            role="viewer",
            updated_by_user_id=viewer.id,
        ))
        session.commit()

        assert pdf_documents._bookshelf_owner_can_reshare_pdf(session, viewer_shelf, document) is False
        assert pdf_documents._resolve_pdf_resource_access(session, document, outsider).role == "none"


def test_shared_bookshelf_viewer_content_token_keeps_viewer_identity():
    with Session(_engine()) as session:
        _owner, viewer, outsider, document, _shelf = _seed_shared_shelf(session)

        token = pdf_documents._create_pdf_content_token(document, viewer)
        decoded = pdf_documents._decode_pdf_content_token(
            session,
            pdf_documents._require_pdf_numeric_id(document),
            token,
        )

        assert decoded.id == document.id

        outsider_token = pdf_documents._create_pdf_content_token(document, outsider)
        with pytest.raises(HTTPException) as error:
            pdf_documents._decode_pdf_content_token(
                session,
                pdf_documents._require_pdf_numeric_id(document),
                outsider_token,
            )
        assert error.value.status_code == 403
