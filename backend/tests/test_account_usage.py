from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from backend.api.resources import router
from backend.core.access.auth import get_current_active_user
from backend.core.library import storage
from backend.core.resources.account_usage import collect_account_usage
from backend.db import get_session
from backend.models import (User, NoteNode, NoteEdge, SheetDocument, SheetPageSnapshot,
    WorkbookDocument, GraphResource, ResourceIdentity, ResourceAccessGrant,
    PdfDocument, LibraryBookAsset, WorkbookSheetLink)


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "usage.db"}', connect_args={'check_same_thread': False})
    for model in (User, NoteNode, NoteEdge, SheetDocument, WorkbookDocument,
                  SheetPageSnapshot, ResourceIdentity, GraphResource, ResourceAccessGrant,
                  PdfDocument, LibraryBookAsset, WorkbookSheetLink):
        model.__table__.create(engine)
    monkeypatch.setattr(storage, 'get_settings', lambda: SimpleNamespace(data_dir=tmp_path / 'data'))
    with Session(engine) as session:
        session.add_all([User(id=1, username='owner', hashed_password=''),
                         User(id=2, username='viewer', hashed_password='')])
        session.commit()
        yield session, engine
    engine.dispose()


def categories(report):
    return {item.key: item for item in report.categories}


def test_owner_accounting_retains_deleted_and_does_not_count_sharing(database):
    session, _ = database
    session.add_all([
        NoteNode(id='1', user_id=1, content='中文', history=[{'v': '旧内容'}], deleted_at=1),
        NoteNode(id='2', user_id=2, content='private' * 100),
        SheetDocument(id='sheet', owner_user_id=1, document_json={'data': [['中文']]}),
        SheetDocument(id='unowned', owner_user_id=None, owner_key='system', document_json={'big': 'x' * 1000}),
        WorkbookDocument(id='book1', owner_user_id=1),
        WorkbookDocument(id='book2', owner_user_id=2),
        GraphResource(id=100, owner_id=1, kind='document', title='图', content=b'abc', original=b'original', deleted=True),
    ])
    session.commit()
    before = collect_account_usage(session, 1)
    own = categories(before)
    assert own['notes'].resource_count == 1
    assert own['notes'].retained_bytes == own['notes'].data_bytes > len('中文'.encode())
    assert own['project_graph'].retained_bytes == own['project_graph'].total_bytes
    assert own['sheets'].resource_count == 2
    session.add_all([
        ResourceAccessGrant(resource_type='project_graph', resource_id='100', subject_key='user:2', subject_type='user', subject_user_id=2, role='viewer'),
        WorkbookSheetLink(workbook_id='book1', sheet_id='sheet'),
        WorkbookSheetLink(workbook_id='book2', sheet_id='sheet'),
    ])
    session.commit()
    assert collect_account_usage(session, 1).total_bytes == before.total_bytes
    assert categories(collect_account_usage(session, 2))['project_graph'].total_bytes == 0
    graph = session.get(GraphResource, 100)
    graph.content += b'12345'
    session.add(graph)
    note = session.get(NoteNode, '1')
    note.content += '你好'
    session.add(note)
    session.commit()
    after = categories(collect_account_usage(session, 1))
    assert after['project_graph'].total_bytes - own['project_graph'].total_bytes == 5
    assert after['notes'].total_bytes - own['notes'].total_bytes == 6


def test_library_files_external_references_and_empty_account(database):
    session, _ = database
    roots = storage.library_owner_roots(1)
    for root in roots:
        root.mkdir(parents=True)
    (roots[0] / 'one.pdf').write_bytes(b'a' * 50)
    (roots[1] / 'book.json').write_bytes(b'b' * 70)
    other = storage.library_owner_roots(2)[0]
    other.mkdir(parents=True)
    (other / 'private.pdf').write_bytes(b'x' * 300)
    session.add_all([
        PdfDocument(owner_user_id=1, title='hosted', source_entry_id=storage.PDF_HOSTED_ENTRY_ID, size_bytes=999),
        PdfDocument(owner_user_id=1, title='remote', source_device_id='remote', source_absolute_path='/other', size_bytes=1000),
        PdfDocument(owner_user_id=1, title='unknown', source_device_id='remote', source_absolute_path='/unknown'),
    ])
    session.commit()
    report = collect_account_usage(session, 1)
    library = categories(report)['library']
    assert library.file_bytes == 120  # Actual files, not stale metadata or another owner.
    assert library.external_reference_bytes == 1000
    assert library.unknown_external_size_count == 1
    assert library.total_bytes == library.data_bytes + 120
    assert report.total_bytes == sum(item.total_bytes for item in report.categories)
    assert collect_account_usage(session, 3).total_bytes == 0


def test_snapshot_charged_to_sheet_owner_once(database):
    session, _ = database
    session.add(SheetDocument(id='sheet', owner_user_id=1))
    session.add(SheetPageSnapshot(sheet_id='sheet', sheet_version=1, sheet_updated_at=1,
        workbook_id=999, document_json={'data': [['cached']]}))
    session.commit()
    one = categories(collect_account_usage(session, 1))['sheets']
    two = categories(collect_account_usage(session, 2))['sheets']
    assert one.parts[2].count == 1
    assert one.parts[2].bytes > 0
    assert two.parts[2].count == 0


def test_endpoint_always_uses_authenticated_owner(database):
    session, engine = database
    session.add(NoteNode(id='1', user_id=2, content='secret'))
    session.commit()
    app = FastAPI()
    app.include_router(router, prefix='/resources')
    def sessions():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        assert client.get('/resources/account-usage').status_code == 401
        app.dependency_overrides[get_current_active_user] = lambda: User(id=1, username='owner', hashed_password='')
        response = client.get('/resources/account-usage?owner_id=2')
        assert response.status_code == 200
        assert response.json()['owner_id'] == 1
        assert response.json()['total_bytes'] == 0
        assert 'secret' not in response.text
