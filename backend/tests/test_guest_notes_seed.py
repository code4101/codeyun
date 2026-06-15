from sqlmodel import Session, create_engine, select

from backend.core.notes.guest import GUEST_NOTES_USERNAME, ensure_guest_notes_user, ensure_guest_star_notes_seed
from backend.models import AppSetting, NoteEdge, NoteNode, ResourceIdentity, User


def test_guest_star_notes_seed_uses_numeric_primary_keys():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    for table in [User, AppSetting, ResourceIdentity, NoteNode, NoteEdge]:
        table.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        user = ensure_guest_notes_user(session)
        assert user.username == GUEST_NOTES_USERNAME

        ensure_guest_star_notes_seed(session, user)

        notes = session.exec(select(NoteNode).where(NoteNode.user_id == user.id)).all()
        assert notes
        assert all(str(note.id or "").isdecimal() for note in notes)
        assert {note.legacy_id for note in notes if note.legacy_id}

        edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user.id)).all()
        assert edges
        assert all(str(edge.source_id).isdecimal() and str(edge.target_id).isdecimal() for edge in edges)
