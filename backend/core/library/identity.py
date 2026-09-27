"""Number library assets when persisted, not when somebody first opens a tab."""
from sqlmodel import Session, select
from backend.models import LibraryBookAsset, ResourceIdentity
from backend.core.resources.identity import allocate_resource_id, RESOURCE_TYPE_EBOOK, RESOURCE_TYPE_SKILL_BOOK


def ensure_library_asset_identity(session: Session, asset: LibraryBookAsset) -> int:
    kind, key = (RESOURCE_TYPE_SKILL_BOOK, 'local-skill') if asset.id == 'skill-book:local-skills' else (RESOURCE_TYPE_EBOOK, asset.id)
    resource_id = allocate_resource_id(session, kind, key)
    session.flush()
    return resource_id


def register_existing_library_assets() -> dict:
    """Idempotently fill missing book IDs; existing IDs and owners never change."""
    from backend.db import engine
    with Session(engine) as session:
        before = {r.id for r in session.exec(select(ResourceIdentity)).all()}
        rows = session.exec(select(LibraryBookAsset)).all()
        ids = [ensure_library_asset_identity(session, row) for row in rows]
        session.commit()
        return {'assets': len(rows), 'registered': len(set(ids) - before)}
