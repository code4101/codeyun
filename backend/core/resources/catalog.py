"""Standard resource catalog, keyed exclusively by ResourceIdentity.id.

Identity, ownership and placement are orthogonal: the global registry owns the
number; each resource provider owns its content/creator metadata; ResourceAccessGrant
owns explicit sharing rules. Renaming, moving or sharing never reallocates an ID.
Existing notebook/workbook/sheet IDs and domain-specific inheritance stay intact.
"""
from dataclasses import dataclass
from sqlalchemy import String, and_, cast, literal, or_, union_all
from sqlmodel import Session, select
from backend.models import (GraphResource, LibraryBookAsset, NoteNode, PdfDocument,
    ResourceAccessGrant, ResourceIdentity, SheetDocument, User, WorkbookDocument)

ROLE_RANK = {'deny': 0, 'viewer': 1, 'editor': 2, 'manager': 3}


def resource_role(session: Session, resource_type: str, resource_id: int,
                  owner_id: int | None, user: User | None) -> str:
    """Private by default. Explicit user grants override public grants, including deny.

    System admin status alone does not make somebody an owner of a private file.
    Domain admin/parent-inheritance policies remain with their existing endpoints.
    """
    if user is not None and user.id == owner_id:
        return 'manager'
    grants = session.exec(select(ResourceAccessGrant).where(
        ResourceAccessGrant.resource_type == resource_type,
        ResourceAccessGrant.resource_id == str(resource_id))).all()
    roles = {g.subject_key: g.role for g in grants}
    for subject in ([f'user:{user.id}'] if user else []) + ['anonymous']:
        if subject in roles:
            return roles[subject] if roles[subject] in ROLE_RANK else 'deny'
    return 'deny'


@dataclass(frozen=True)
class ResourceProvider:
    model: type
    owner_field: str
    identity_field: str = 'numeric_id'


# The same catalog can feed resource explorers without understanding editor formats.
STANDARD_RESOURCES = {
    'note': ResourceProvider(NoteNode, 'user_id'),
    'workbook': ResourceProvider(WorkbookDocument, 'owner_user_id'),
    'sheet': ResourceProvider(SheetDocument, 'owner_user_id'),
    'pdf': ResourceProvider(PdfDocument, 'owner_user_id'),
    'ebook': ResourceProvider(LibraryBookAsset, 'owner_user_id', 'legacy_pk'),
    'skill_book': ResourceProvider(LibraryBookAsset, 'owner_user_id', 'local_skill'),
    'project_graph': ResourceProvider(GraphResource, 'owner_id', 'id'),
}


def resource_catalog_query():
    queries = []
    for kind, provider in STANDARD_RESOURCES.items():
        model = provider.model
        if provider.identity_field == 'local_skill':
            join = and_(model.id == 'skill-book:local-skills', ResourceIdentity.legacy_pk == 'local-skill')
        else:
            join = model.id == ResourceIdentity.legacy_pk if provider.identity_field == 'legacy_pk' else getattr(model, provider.identity_field) == ResourceIdentity.id
        if provider.identity_field == 'numeric_id':
            join = or_(join, and_(model.numeric_id == None, cast(model.id, String) == ResourceIdentity.legacy_pk))
        query = select(ResourceIdentity.id.label('id'), literal(kind).label('type'),
            model.title.label('title'), getattr(model, provider.owner_field).label('ownerId'))\
            .join(model, join).where(ResourceIdentity.resource_type == kind)
        if hasattr(model, 'deleted_at'):
            query = query.where(model.deleted_at == None)
        if model is GraphResource:
            query = query.where(GraphResource.deleted == False)
        queries.append(query)
    return union_all(*queries).subquery()


def list_owned_resources(session: Session, owner_id: int, *, after: int = 0, limit: int = 100):
    catalog = resource_catalog_query()
    rows = session.execute(select(catalog).where(catalog.c.ownerId == owner_id,
        catalog.c.id > after).order_by(catalog.c.id).limit(limit)).mappings().all()
    return [dict(row) for row in rows]


def resolve_resource(session: Session, resource_id: int, user: User):
    catalog = resource_catalog_query()
    row = session.execute(select(catalog).where(catalog.c.id == resource_id)).mappings().first()
    if row is None:
        return None
    role = resource_role(session, row['type'], row['id'], row['ownerId'], user)
    return {**row, 'role': role} if ROLE_RANK[role] else None
