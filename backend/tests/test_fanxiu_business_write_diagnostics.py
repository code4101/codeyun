"""Owned TEMP SQLite/provider evidence; no production lock or game simulation."""
import json
import os
import subprocess
import sys
import time
import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, create_engine
from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.business_data import upsert_fanxiu_business_records
from backend.core.fanxiu.business_write_diagnostics import read_business_write_diagnostics


@pytest.fixture
def database(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'owned.sqlite'), connect_args={'timeout': 0.05})
    FanxiuPacketBusinessRecord.__table__.create(engine)
    yield engine, tmp_path / 'diagnostics.json'
    engine.dispose()


def row(value=1):
    return {'domain': 'owned-test', 'record_key': 'item-1', 'captured_at': '2026-10-11',
            'payload': {'private_payload_marker': value}, 'source_kind': 'test'}


def test_success_and_noop_keep_business_semantics_and_no_payload(database):
    engine, path = database
    with Session(engine) as session:
        assert upsert_fanxiu_business_records(session, [row()], diagnostics_path=path)['created'] == 1
        assert upsert_fanxiu_business_records(session, [row()], diagnostics_path=path)['skipped_duplicate'] == 1
    status = read_business_write_diagnostics(path=path)
    assert len(status['records']) == 2 and not status['lock_holder_proven']
    first, second = status['records']
    assert first['phase_seconds']['commit_flush'] >= 0
    assert 'commit_flush' not in second['phase_seconds']
    assert all(r['status'] == 'success' and r['source']['function'] ==
               'test_success_and_noop_keep_business_semantics_and_no_payload' for r in status['records'])
    assert 'private_payload_marker' not in json.dumps(status)


def test_real_owned_lock_preserves_original_error_and_stage(database):
    engine, path = database
    with engine.connect() as holder:
        holder.exec_driver_sql('BEGIN IMMEDIATE')  # Only this fixture's owned database.
        with Session(engine) as session:
            with pytest.raises(OperationalError) as failure:
                upsert_fanxiu_business_records(session, [row()], diagnostics_path=path)
            assert 'locked' in str(failure.value.orig)
            session.rollback()
        holder.rollback()
    status = read_business_write_diagnostics(path=path)
    record = status['records'][0]
    assert record['status'] == 'error' and record['phase'] == 'commit_flush'
    assert record['error_category'] == 'database_locked' and record['driver_error_code'] == 5
    assert record['elapsed_seconds'] >= record['phase_seconds']['commit_flush']
    assert not status['lock_holder_proven']


def test_unavailable_diagnostic_does_not_replace_commit_result(database, tmp_path, caplog):
    engine, _ = database
    file = tmp_path / 'not-a-directory'
    file.write_text('owned', encoding='utf-8')
    with Session(engine) as session:
        result = upsert_fanxiu_business_records(session, [row()], diagnostics_path=file / 'journal.json')
    assert result['created'] == 1
    assert 'diagnostic unavailable' in caplog.text


def test_real_process_writes_keep_both_operation_identities(database, tmp_path):
    engine, journal = database
    release = tmp_path / 'release'
    script = r'''
import sys,time
from pathlib import Path
from sqlmodel import Session,create_engine
from backend.core.fanxiu.business_data import upsert_fanxiu_business_records
database,journal,ready,release,key=sys.argv[1:]
class HeldCommitSession(Session):
    def commit(self):
        Path(ready).write_text('ready')
        deadline=time.monotonic()+8
        while not Path(release).exists():
            if time.monotonic()>deadline:raise TimeoutError('owned test release timeout')
            time.sleep(.01)
        return super().commit()
engine=create_engine('sqlite:///'+database)
try:
    with HeldCommitSession(engine) as session:
        result=upsert_fanxiu_business_records(session,[dict(domain='owned-process-test',
            record_key=key,payload={'value':1},source_kind='test')],diagnostics_path=Path(journal))
        assert result['created']==1
finally:engine.dispose()
'''
    processes = []
    ready_paths = []
    try:
        for index in range(2):
            ready = tmp_path / ('ready-' + str(index))
            ready_paths.append(ready)
            processes.append(subprocess.Popen([sys.executable, '-c', script, str(engine.url.database),
                str(journal), str(ready), str(release), str(index)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        deadline = time.monotonic() + 8
        while not all(p.exists() for p in ready_paths):
            assert all(p.poll() is None for p in processes), 'owned child failed before commit boundary'
            assert time.monotonic() < deadline, 'owned child readiness timeout'
            time.sleep(.01)
        running = read_business_write_diagnostics(path=journal)['records']
        assert len(running) == 2 and len({r['pid'] for r in running}) == 2
        assert len({r['operation_id'] for r in running}) == 2
        assert all(r['status'] == 'running' for r in running)
    finally:
        release.write_text('release', encoding='utf-8')
        for process in processes:
            try:
                output, error = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                output, error = process.communicate()
            assert process.returncode == 0, error.decode('utf-8', errors='replace')
    final = read_business_write_diagnostics(path=journal)
    assert len(final['records']) == 2
    assert {r['operation_id'] for r in final['records']} == {r['operation_id'] for r in running}
    assert all(r['status'] == 'success' and r['result']['created'] == 1 for r in final['records'])
    assert not final['lock_holder_proven']
