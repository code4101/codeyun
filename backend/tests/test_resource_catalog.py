from sqlmodel import Session, create_engine
from backend.core.resources.catalog import list_owned_resources, resolve_resource
from backend.core.library.identity import ensure_library_asset_identity
from backend.models import (GraphResource, LibraryBookAsset, NoteNode, PdfDocument,
    ResourceAccessGrant, ResourceIdentity, SheetDocument, User, WorkbookDocument)


def test_one_catalog_for_standard_resources_preserves_numbers_and_private_ownership():
    engine = create_engine('sqlite://')
    models = [User, ResourceIdentity, ResourceAccessGrant, GraphResource, LibraryBookAsset,
              NoteNode, PdfDocument, SheetDocument, WorkbookDocument]
    for model in models:
        model.__table__.create(engine)
    with Session(engine) as s:
        user = User(id=1, username='owner', hashed_password='')
        other = User(id=2, username='other', hashed_password='', is_superuser=True)
        s.add(user); s.add(other)
        for rid, kind in enumerate(['note','workbook','sheet','pdf','ebook','project_graph'], 10):
            s.add(ResourceIdentity(id=rid, resource_type=kind, legacy_pk=str(rid)))
        s.add(NoteNode(id='10', numeric_id=10, user_id=1, title='Note'))
        s.add(WorkbookDocument(id='11', numeric_id=11, owner_user_id=1, title='Workbook'))
        s.add(SheetDocument(id='12', numeric_id=12, owner_user_id=1, title='Sheet'))
        s.add(PdfDocument(id=13, numeric_id=13, owner_user_id=1, title='PDF'))
        book = LibraryBookAsset(id='14', owner_user_id=1, source_kind='test', title='Book')
        s.add(book)
        s.add(GraphResource(id=15, owner_id=1, kind='document', title='Graph'))
        s.commit()
        assert ensure_library_asset_identity(s, book) == 14
        rows = list_owned_resources(s, 1)
        assert [r['id'] for r in rows] == list(range(10,16))
        assert len({r['type'] for r in rows}) == 6
        assert list_owned_resources(s, 2) == []
        assert resolve_resource(s, 15, other) is None
        s.add(ResourceAccessGrant(resource_type='project_graph', resource_id='15',
            subject_key='user:2', subject_type='user', subject_user_id=2, role='viewer'))
        s.commit()
        assert resolve_resource(s, 15, other)['role'] == 'viewer'
        assert resolve_resource(s, 15, user)['role'] == 'manager'
        assert s.get(ResourceIdentity, 11).id == 11
        assert s.get(ResourceIdentity, 12).id == 12
        from backend.migrations.manager import v115_register_graph_and_library_resources
        v115_register_graph_and_library_resources(s)
        v115_register_graph_and_library_resources(s)
        assert [r['id'] for r in list_owned_resources(s, 1)] == list(range(10,16))
    engine.dispose()
