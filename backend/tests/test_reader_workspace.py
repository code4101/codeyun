import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from backend.api.reader_workspace import router
from backend.core.access.auth import get_current_active_user, get_optional_current_user_from_token
from backend.db import get_session
from backend.models import AppSetting, User, ResourceIdentity, LibraryBookAsset


@pytest.fixture
def workspace_client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    AppSetting.__table__.create(engine)
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
    app.dependency_overrides[get_current_active_user] = lambda: User(id=identity['id'], username='test', hashed_password='test')
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: User(id=identity['id'], username='test', hashed_password='test') if identity['id'] else None
    with TestClient(app) as client:
        yield client, identity
    engine.dispose()


def command(client, action, **values):
    response = client.post('/reader-workspace/commands', json={'action': action, **values})
    assert response.status_code == 200, response.text
    return response.json()


def test_restore_open_deduplicate_and_preserve_reader_options(workspace_client):
    client, _ = workspace_client
    command(client, 'open', tab={'kind': 'ebook', 'id': 'a', 'title': 'A', 'readingMode': 'paginated'})
    command(client, 'open', tab={'kind': 'pdf', 'id': '42', 'title': 'B'})
    # 新页面从服务端恢复；打开 C 只追加，不提交陈旧的 A/B 快照。
    restored = client.get('/reader-workspace').json()
    assert len(restored['tabs']) == 2
    result = command(client, 'open', tab={'kind': 'ebook', 'id': 'c'})
    assert [tab['id'] for tab in result['tabs']] == ['a', '42', 'c']
    assert result['active'] == 'ebook:c'
    result = command(client, 'open', tab={'kind': 'ebook', 'id': 'a'})
    assert len(result['tabs']) == 3
    assert result['active'] == 'ebook:a'
    assert result['tabs'][0]['title'] == 'A'
    assert result['tabs'][0]['readingMode'] == 'paginated'


def test_close_reorder_title_and_layout_do_not_reopen_tabs(workspace_client):
    client, _ = workspace_client
    for id in ['a', 'b', 'c']:
        command(client, 'open', tab={'kind': 'ebook', 'id': id})
    result = command(client, 'move', key='ebook:c', before='ebook:a')
    assert [tab['id'] for tab in result['tabs']] == ['c', 'a', 'b']
    result = command(client, 'close', key='ebook:c')
    assert result['active'] == 'ebook:a'
    result = command(client, 'title', key='ebook:c', title='late response')
    assert [tab['id'] for tab in result['tabs']] == ['a', 'b']
    layout = {'version': 2, 'regions': {'left': {'size': 360}}}
    command(client, 'layout', layout=layout)
    assert client.get('/reader-workspace').json()['layout'] == layout
    command(client, 'close', key='ebook:a')
    result = command(client, 'close', key='ebook:b')
    assert result['tabs'] == [] and result['active'] == ''
    assert result['layout'] == layout


def test_users_have_separate_workspaces(workspace_client):
    client, identity = workspace_client
    command(client, 'open', tab={'kind': 'pdf', 'id': '1'})
    identity['id'] = 2
    assert client.get('/reader-workspace').json()['tabs'] == []
    command(client, 'open', tab={'kind': 'ebook', 'id': 'private'})
    identity['id'] = 1
    assert [tab['id'] for tab in client.get('/reader-workspace').json()['tabs']] == ['1']


def test_invalid_reader_kind_is_rejected(workspace_client):
    client, _ = workspace_client
    response = client.post('/reader-workspace/commands', json={'action': 'open', 'tab': {'kind': 'arbitrary-component', 'id': 'x'}})
    assert response.status_code == 422


def test_parallel_open_commands_merge_instead_of_overwriting(workspace_client):
    client, _ = workspace_client
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: command(client, 'open', tab={'kind': 'ebook', 'id': str(index)}), range(12)))
    assert len(results) == 12
    restored = client.get('/reader-workspace').json()
    assert {tab['id'] for tab in restored['tabs']} == {str(index) for index in range(12)}


def test_resource_numbers_are_stable_and_resolve_with_access_control(workspace_client):
    client, user = workspace_client
    state = command(client, 'open', tab={'kind': 'ebook', 'id': 'ebook:2:hash', 'publicId': 9999})
    number = state['tabs'][0]['publicId']
    assert isinstance(number, int) and number != 9999
    resolved = client.get(f'/reader-workspace/resources/{number}')
    assert resolved.status_code == 200
    assert resolved.json()['kind'] == 'ebook'
    assert resolved.json()['id'] == 'ebook:2:hash'
    command(client, 'close', key='ebook:ebook:2:hash')
    reopened = command(client, 'open', tab={'kind': 'ebook', 'id': 'ebook:2:hash'})
    assert reopened['tabs'][0]['publicId'] == number
    assert client.get('/reader-workspace').json()['tabs'][0]['publicId'] == number
    skill = command(client, 'open', tab={'kind': 'skill', 'id': 'local-skill'})['tabs'][1]['publicId']
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
    first = command(client, 'open', tab={'kind': 'ebook', 'id': 'ebook:2:hash'})['tabs'][0]['publicId']
    assert first > 4
    user['id'] = 2
    second = command(client, 'open', tab={'kind': 'ebook', 'id': 'ebook:2:hash'})['tabs'][0]['publicId']
    assert second == first


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
