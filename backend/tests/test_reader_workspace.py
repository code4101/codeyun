import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from backend.api.reader_workspace import router
from backend.core.access.auth import get_optional_current_user_from_token
from backend.db import get_session
from backend.models import User, ResourceIdentity, LibraryBookAsset


@pytest.fixture
def workspace_client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    ResourceIdentity.__table__.create(engine)
    LibraryBookAsset.__table__.create(engine)
    with Session(engine) as seed:
        seed.add(LibraryBookAsset(id='ebook:2:hash', owner_user_id=1, source_kind='test'))
        seed.commit()
    app = FastAPI()
    app.include_router(router, prefix='/reader-workspace')
    identity = {'id': 1}

    def session():
        with Session(engine) as value:
            yield value

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: User(id=identity['id'], username='test', hashed_password='test') if identity['id'] else None
    with TestClient(app) as client:
        yield client, identity
    engine.dispose()


def identify(client, kind, id):
    response = client.post('/reader-workspace/resources', json={'kind': kind, 'id': id})
    assert response.status_code == 200, response.text
    return response.json()['publicId']


def test_workspace_state_is_not_a_server_api(workspace_client):
    client, _ = workspace_client
    assert client.get('/reader-workspace').status_code == 404
    assert client.post('/reader-workspace/commands', json={'action': 'open'}).status_code == 404


def test_identification_checks_resource_access(workspace_client):
    client, user = workspace_client
    for kind, id in [('ebook', 'missing'), ('skill', 'unknown'), ('pdf', 'invalid')]:
        assert client.post('/reader-workspace/resources', json={'kind': kind, 'id': id}).status_code == 404
    assert client.post('/reader-workspace/resources', json={'kind': 'unknown', 'id': 'a'}).status_code == 422
    user['id'] = 2
    assert client.post('/reader-workspace/resources', json={'kind': 'ebook', 'id': 'ebook:2:hash'}).status_code == 404
    user['id'] = None
    assert client.post('/reader-workspace/resources', json={'kind': 'skill', 'id': 'local-skill'}).status_code == 401


def test_resource_numbers_are_stable_and_resolve_with_access_control(workspace_client):
    client, user = workspace_client
    number = identify(client, 'ebook', 'ebook:2:hash')
    assert isinstance(number, int) and number != 9999
    resolved = client.get(f'/reader-workspace/resources/{number}')
    assert resolved.status_code == 200
    assert resolved.json()['kind'] == 'ebook'
    assert resolved.json()['id'] == 'ebook:2:hash'
    assert identify(client, 'ebook', 'ebook:2:hash') == number
    skill = identify(client, 'skill', 'local-skill')
    assert skill != number
    assert client.get(f'/reader-workspace/resources/{skill}').json()['kind'] == 'skill'
    for identity in [2, None]:
        user['id'] = identity
        assert client.get(f'/reader-workspace/resources/{number}').status_code == 404
        assert client.get(f'/reader-workspace/resources/{skill}').status_code == (200 if identity else 404)
    assert client.get('/reader-workspace/resources/999999').status_code == 404


def test_pdf_number_uses_pdf_access_policy(workspace_client, monkeypatch):
    from fastapi import HTTPException
    from types import SimpleNamespace
    from backend.api import pdf_documents
    client, user = workspace_client
    session_factory = client.app.dependency_overrides[get_session]
    with next(session_factory()) as session:
        session.add(ResourceIdentity(id=58429, resource_type='pdf', legacy_pk='pdf-internal-key'))
        session.commit()
    calls = []
    def get_pdf(pdf_id, session, current_user):
        calls.append((pdf_id, current_user.id if current_user else None))
        if current_user and current_user.id == 2:
            raise HTTPException(403, 'forbidden')
        return SimpleNamespace(title='Public PDF')
    monkeypatch.setattr(pdf_documents, 'get_pdf_document', get_pdf)
    user['id'] = None
    response = client.get('/reader-workspace/resources/58429')
    assert response.status_code == 200
    assert response.json()['kind'] == 'pdf'
    assert response.json()['id'] == '58429'
    assert calls == [(58429, None)]
    user['id'] = 2
    assert client.get('/reader-workspace/resources/58429').status_code == 403


def test_numbers_share_global_registry_and_do_not_depend_on_user(workspace_client):
    client, user = workspace_client
    from backend.core.resources.identity import allocate_resource_id
    session_factory = client.app.dependency_overrides[get_session]
    with next(session_factory()) as session:
        for resource_type in ['workbook', 'sheet', 'document_asset', 'device_file']:
            allocate_resource_id(session, resource_type, 'same-internal-key')
            session.flush()
        session.commit()
    first = identify(client, 'ebook', 'ebook:2:hash')
    assert first > 4
    assert identify(client, 'ebook', 'ebook:2:hash') == first


def test_existing_attendance_ids_are_reserved_and_never_renumbered():
    from backend.models import SheetDocument, WorkbookDocument
    from backend.core.resources.identity import allocate_resource_id
    from backend.core.resources.sheet_identity import allocate_new_workbook_identity, allocate_new_sheet_identity
    engine = create_engine('sqlite://')
    for model in [ResourceIdentity, SheetDocument, WorkbookDocument]:
        model.__table__.create(engine)
    with Session(engine) as session:
        session.add(WorkbookDocument(id='4', numeric_id=4, title='考勤工作簿'))
        session.add(SheetDocument(id='10', numeric_id=10, title='考勤工作表'))
        session.commit()
        book = allocate_resource_id(session, 'ebook', 'book', preferred_id=4)
        session.flush()
        assert book > 10
        sheet_collision = allocate_resource_id(session, 'document_asset', 'image', preferred_id=10)
        session.flush()
        workbook = allocate_new_workbook_identity(session)
        session.flush()
        sheet = allocate_new_sheet_identity(session)
        session.flush()
        assert len({book, sheet_collision, workbook.numeric_id, sheet.numeric_id, 4, 10}) == 6
        assert workbook.numeric_id == workbook.resource_identity_id
        assert session.get(WorkbookDocument, '4').numeric_id == 4
        assert session.get(SheetDocument, '10').numeric_id == 10
    engine.dispose()


def test_parallel_resource_identification_has_unique_stable_numbers(workspace_client):
    client, _ = workspace_client
    session_factory = client.app.dependency_overrides[get_session]
    with next(session_factory()) as session:
        for index in range(8):
            session.add(LibraryBookAsset(id=f'book-{index}', owner_user_id=1, source_kind=f'test-{index}'))
        session.commit()
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(lambda index: identify(client, 'ebook', f'book-{index}'), range(8)))
    assert len(set(numbers)) == 8
    assert [identify(client, 'ebook', f'book-{index}') for index in range(8)] == numbers
