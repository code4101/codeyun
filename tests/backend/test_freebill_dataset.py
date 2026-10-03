from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.freebill import dataset
from backend.core.freebill.core import (
    import_bill_records, list_freebill_records, list_freebill_raw_files,
    list_freebill_interpret_rules, upsert_freebill_record_manual_overrides,
)
from backend.core.freebill.sheet import refresh_freebill_sheet_workbook
from backend.models import User


@pytest.fixture
def isolated_datasets(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, 'get_settings', lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setenv('CODEYUN_FREEBILL_WORK_DIR', str(tmp_path / 'personal'))


def test_demo_coverage_edit_reset_and_user_isolation(isolated_datasets):
    import_bill_records([dict(source='微信', trade_no='PRIVATE', amount=12345,
                             direction='支出', product_name='私人账单')], filename='personal')
    with dataset.freebill_dataset_scope('demo', 1):
        dataset.ensure_demo_dataset()
        rows = list_freebill_records(limit=50)['items']
        assert len(rows) == 18
        assert {r['standard_nature'] for r in rows} == {'常规', '借贷', '理财', '转账', '流水'}
        assert {r['standard_direction'] for r in rows} == {'支出', '收入', '收支'}
        assert len(list_freebill_raw_files()['items']) == 3
        upsert_freebill_record_manual_overrides('DEMO-0001', {'amount': 999})
        dataset.ensure_demo_dataset()
        assert next(r for r in list_freebill_records(limit=50)['items'] if r['trade_no'] == 'DEMO-0001')['amount'] == 999
    with dataset.freebill_dataset_scope('demo', 2):
        dataset.ensure_demo_dataset()
        assert next(r for r in list_freebill_records(limit=50)['items'] if r['trade_no'] == 'DEMO-0001')['amount'] == 28.5
    with dataset.freebill_dataset_scope('demo', 1):
        dataset.ensure_demo_dataset(reset=True)
        assert next(r for r in list_freebill_records(limit=50)['items'] if r['trade_no'] == 'DEMO-0001')['amount'] == 28.5
        assert len(list_freebill_interpret_rules()['rules']) == 3
    assert list_freebill_records()['items'][0]['trade_no'] == 'PRIVATE'
    with pytest.raises(ValueError):
        dataset.ensure_demo_dataset(reset=True)


def test_workbooks_are_separate(isolated_datasets, session):
    user = User(username='demo-tester', hashed_password='pw', is_active=True)
    session.add(user)
    session.commit()
    session.refresh(user)
    personal = refresh_freebill_sheet_workbook(session, user_id=user.id)
    with dataset.freebill_dataset_scope('demo', user.id):
        dataset.ensure_demo_dataset()
        demo = refresh_freebill_sheet_workbook(session, user_id=user.id)
    assert personal['workbook']['id'] != demo['workbook']['id']
    assert {s['sheet_id'] for s in personal['sheets']}.isdisjoint(s['sheet_id'] for s in demo['sheets'])
    assert demo['sheets'][0]['row_count'] == 18


def test_api_scope_propagates_to_worker_and_blocks_real_sync(isolated_datasets):
    from backend.api.freebill import router
    from backend.core.access.auth import get_current_active_user

    app = FastAPI()
    app.include_router(router, prefix='/freebill')
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[router.dependencies[0].dependency] = lambda: None
    with TestClient(app) as client:
        response = client.get('/freebill/records?dataset=demo')
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 18
        assert client.get('/freebill/records?dataset=personal').json()['total'] == 0
        assert client.post('/freebill/sources/wechat-local/sync?dataset=demo').status_code == 400
        assert client.post('/freebill/demo/reset?dataset=personal').status_code == 400
        assert client.post('/freebill/demo/reset?dataset=demo').status_code == 200
