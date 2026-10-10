"""Real owned SQLite concurrency contracts; no native Codex database fixtures."""
import os
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel, Session, create_engine, select
from backend.core.codex import sessions
from backend.models import CodexTextCacheRoot, CodexTextCacheThread, CodexTextCacheMessage


@pytest.fixture
def cache(tmp_path, monkeypatch):
    source = tmp_path / 'unavailable-native-source'
    root_key = os.path.normcase(os.path.normpath(str(source.resolve())))
    engine = create_engine('sqlite:///' + str(tmp_path / 'owned-cache.sqlite'),
                           connect_args={'timeout': .05, 'check_same_thread': False})
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    SQLModel.metadata.create_all(engine, tables=[m.__table__ for m in
        (CodexTextCacheRoot, CodexTextCacheThread, CodexTextCacheMessage)])
    with Session(engine) as session:
        session.add(CodexTextCacheRoot(root_key=root_key, root_dir=str(source), refreshed_at=10.))
        session.add(CodexTextCacheThread(root_key=root_key,thread_id='thread',title='Committed',
                    cwd='/project',project_label='Project',refreshed_at=11.))
        for seq, role in [(2,'assistant'),(1,'user')]:
            session.add(CodexTextCacheMessage(root_key=root_key,thread_id='thread',seq=seq,
                        role=role,text='text'+str(seq)))
        session.commit()
    monkeypatch.setattr(sessions,'engine',engine)
    def forbidden(*args, **kwargs):raise AssertionError('readonly detail refreshed source')
    for name in ('_ensure_codex_text_cache','_load_thread_rows','_load_rollout_messages'):
        monkeypatch.setattr(sessions,name,forbidden)
    yield engine, str(source), root_key
    engine.dispose()


@contextmanager
def read_events(engine):
    statements=[];commits=[];rollbacks=[];flushes=[]
    def sql(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    def commit(session):commits.append(session)
    def rollback(session):rollbacks.append(session)
    def flush(session,context,instances):flushes.append(session)
    event.listen(engine,'before_cursor_execute',sql)
    for kind,listener in [('after_commit',commit),('after_rollback',rollback),('before_flush',flush)]:event.listen(Session,kind,listener)
    try:yield statements,commits,rollbacks,flushes
    finally:
        event.remove(engine,'before_cursor_execute',sql)
        for kind,listener in [('after_commit',commit),('after_rollback',rollback),('before_flush',flush)]:event.remove(Session,kind,listener)


def test_cached_detail_reads_one_committed_snapshot_under_real_writer_lock(cache):
    engine,source,key=cache
    with engine.connect() as writer:
        writer.exec_driver_sql('BEGIN IMMEDIATE')
        writer.exec_driver_sql("UPDATE codextextcachethread SET title='Uncommitted' WHERE thread_id='thread'")
        with read_events(engine) as events:
            result=sessions.read_cached_codex_thread_detail(source,'thread')
        writer.rollback()
    statements,commits,rollbacks,flushes=events
    assert len(statements)==1 and statements[0].lstrip().upper().startswith('SELECT')
    assert not commits and not flushes
    assert result['available'] and result['thread']['title']=='Committed'
    assert [m['seq'] for m in result['messages']]==[1,2]
    assert result['cache']=={'mode':'cached','source_freshness':'not_checked',
                             'root_refreshed_at':10.,'thread_refreshed_at':11.}


def test_dirty_caller_identity_map_is_neither_flushed_nor_used_as_cache(cache):
    engine,source,key=cache
    with Session(engine) as caller:
        thread=caller.exec(select(CodexTextCacheThread)).one()
        root=caller.get(CodexTextCacheRoot,key)
        thread.title='Pending unrelated caller work'
        caller.add(CodexTextCacheMessage(root_key=key,thread_id='thread',seq=3,role='user',text='Pending'))
        root.refreshed_at=999.
        transaction=caller.get_transaction()
        with engine.connect() as writer:
            writer.exec_driver_sql('BEGIN IMMEDIATE')
            writer.exec_driver_sql("UPDATE codextextcachethread SET title='Other writer pending' WHERE thread_id='thread'")
            with read_events(engine) as events:
                result=sessions.read_cached_codex_thread_detail(source,'thread',session=caller)
            writer.rollback()
        assert caller.get_transaction() is transaction
        assert thread in caller.dirty and root in caller.dirty and len(caller.new)==1
        assert result['cache']['root_refreshed_at']==10.
        assert result['thread']['title']=='Committed' and result['message_count']==2
        assert len(events[0])==1 and not any(events[1:])


@pytest.mark.parametrize('thread,source_missing,reason', [('missing',False,'cache_thread_missing'),
                                                         ('thread',True,'cache_root_missing')])
def test_missing_cache_never_attempts_native_refresh(cache,thread,source_missing,reason):
    engine,source,key=cache
    result=sessions.read_cached_codex_thread_detail(source+'-missing' if source_missing else source,thread)
    assert not result['available'] and result['reason']==reason
    assert result['cache']['source_freshness']=='not_checked'


def test_existing_cached_thread_with_zero_messages_is_available(cache):
    engine,source,key=cache
    with Session(engine) as session:
        session.add(CodexTextCacheThread(root_key=key,thread_id='empty',title='Empty'))
        session.commit()
    result=sessions.read_cached_codex_thread_detail(source,'empty')
    assert result['available'] and result['message_count']==0 and result['messages']==[]


def test_cache_read_errors_are_visible_not_blanket_stale_fallback(cache):
    engine,source,key=cache
    with engine.begin() as connection:connection.exec_driver_sql('DROP TABLE codextextcachemessage')
    with pytest.raises(OperationalError):sessions.read_cached_codex_thread_detail(source,'thread')


def test_shared_detail_serialization_matches_refreshing_entry(cache,monkeypatch,tmp_path):
    engine,source,key=cache
    rollout=tmp_path/'empty-rollout.jsonl';rollout.write_text('',encoding='utf-8')
    with Session(engine) as session:
        row=session.exec(select(CodexTextCacheThread)).one();row.rollout_path=str(rollout);session.commit()
    monkeypatch.setattr(sessions,'_ensure_codex_text_cache',lambda *a,**kw:{'root_key':key,'root_dir':source})
    cached=sessions.read_cached_codex_thread_detail(source,'thread')
    refreshing=sessions.build_codex_thread_detail(source,'thread')
    assert {k:v for k,v in cached.items() if k not in ('available','cache')}==refreshing
