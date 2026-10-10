"""Owned temporary source/cache SQLite files exercise the real public refresh."""
import json
import sqlite3
import subprocess
import sys
import time

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select
from backend.core.codex import sessions
from backend.models import CodexTextCacheRoot, CodexTextCacheThread, CodexTextCacheMessage, CodexTextCacheTurn


@pytest.fixture
def refresh(tmp_path, monkeypatch):
    root = tmp_path / 'owned-source'
    root.mkdir()
    rollout = root / 'thread.jsonl'
    events = [
        {'type':'response_item','timestamp':'2026-10-10T01:00:00Z','payload':
         {'type':'message','role':'user','content':[{'type':'input_text','text':'question'}]}},
        {'type':'response_item','timestamp':'2026-10-10T01:00:01Z','payload':
         {'type':'message','role':'assistant','phase':'final','content':[{'type':'output_text','text':'answer'}]}},
        {'type':'event_msg','timestamp':'2026-10-10T01:00:02Z','payload':
         {'type':'task_complete','turn_id':'turn-native','last_agent_message':'answer'}},
    ]
    rollout.write_text('\n'.join(map(json.dumps,events)),encoding='utf-8')
    with sqlite3.connect(root/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, created_at REAL, updated_at REAL, cwd TEXT, title TEXT, archived INTEGER, first_user_message TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                   ('thread',str(rollout),100.,200.,str(tmp_path),'Original',0,'question'))
    bind=create_engine('sqlite:///'+str(tmp_path/'owned-cache.sqlite'),connect_args={'timeout':30})
    with bind.connect() as c:c.exec_driver_sql('PRAGMA journal_mode=WAL')
    SQLModel.metadata.create_all(bind,tables=[m.__table__ for m in
        (CodexTextCacheRoot,CodexTextCacheThread,CodexTextCacheMessage,CodexTextCacheTurn)])
    monkeypatch.setattr(sessions,'engine',bind)
    monkeypatch.setattr(sessions,'_CODEX_REFRESH_WAIT_SECONDS',.1)
    yield bind,root,rollout
    bind.dispose()


def change_source(root,title='Refreshed'):
    # A global-state signature deterministically invalidates metadata, even on
    # filesystems whose native SQLite mtime update resolution differs.
    (root/'.codex-global-state.json').write_text(json.dumps({'version':title}))
    with sqlite3.connect(root/'state_5.sqlite') as db:
        db.execute('UPDATE threads SET title=?',(title,))


def test_original_public_detail_keeps_dirty_caller_and_returns_fresh_cache(refresh):
    bind,root,_=refresh
    first=sessions.build_codex_thread_detail(str(root),'thread')
    assert first['thread']['title']=='Original' and first['message_count']==2
    with Session(bind) as caller:
        thread=caller.exec(select(CodexTextCacheThread)).one()
        cached_root=caller.exec(select(CodexTextCacheRoot)).one()
        thread.title='caller pending'
        cached_root.refreshed_at=999.
        caller.add(CodexTextCacheMessage(root_key=thread.root_key,thread_id='thread',seq=99,role='user',text='pending'))
        transaction=caller.get_transaction()
        tracked=[]
        def changed(session,*args):
            if session is caller:tracked.append(session)
        for name in ('before_flush','after_commit','after_rollback'):event.listen(Session,name,changed)
        try:
            change_source(root)
            result=sessions.build_codex_thread_detail(str(root),'thread',session=caller)
        finally:
            for name in ('before_flush','after_commit','after_rollback'):event.remove(Session,name,changed)
        assert not tracked and caller.get_transaction() is transaction
        assert thread.title=='caller pending' and thread in caller.dirty
        assert cached_root.refreshed_at==999. and len(caller.new)==1
        assert result['thread']['title']=='Refreshed' and result['message_count']==2


def test_original_completed_turns_refreshes_and_requires_native_completion(refresh):
    _,root,rollout=refresh
    assert sessions.read_codex_completed_turns('thread',root_dir=str(root))[0]['turn_id']=='turn-native'
    rollout.write_text(rollout.read_text().split('"type": "event_msg"')[0].rsplit('\n',1)[0],encoding='utf-8')
    assert sessions.read_codex_completed_turns('thread',root_dir=str(root))==[]


def test_parsing_has_no_write_lock_and_source_change_aborts_publication(refresh,monkeypatch):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    change_source(root)
    original=sessions._load_rollout_messages
    def parse(path):
        with bind.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            c.rollback()
        result=original(path)
        (root/'.codex-global-state.json').write_text('{"changed":true}')
        return result
    # Force a changed rollout while metadata and all cached rows are staged.
    rollout=root/'thread.jsonl';rollout.write_text(rollout.read_text()+'\n')
    monkeypatch.setattr(sessions,'_load_rollout_messages',parse)
    with pytest.raises(sessions.CodexCacheSourceChangedError):sessions.build_codex_thread_detail(str(root),'thread')
    with Session(bind) as reader:
        assert reader.exec(select(CodexTextCacheThread)).one().title=='Original'
        assert len(reader.exec(select(CodexTextCacheMessage)).all())==2


@pytest.mark.parametrize('caller_writer',[False,True])
def test_writer_conflict_is_bounded_explicit_and_does_not_rollback_writer(refresh,caller_writer):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    change_source(root)
    with Session(bind) as writer:
        writer.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row=writer.exec(select(CodexTextCacheThread)).one();row.title='pending';writer.flush()
        transaction=writer.get_transaction()
        start=time.monotonic()
        with pytest.raises(sessions.CodexCacheRefreshBusyError,match='refresh_busy'):
            sessions.build_codex_thread_detail(str(root),'thread',session=writer if caller_writer else None)
        assert time.monotonic()-start<2.
        assert writer.get_transaction() is transaction and writer.is_active
        assert writer.exec(select(CodexTextCacheThread)).one().title=='pending'
        writer.rollback()
    assert sessions.build_codex_thread_detail(str(root),'thread')['thread']['title']=='Refreshed'


def test_mid_publication_failure_rolls_back_all_cache_tables(refresh,monkeypatch):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    change_source(root)
    original=sessions._replace_thread_text_cache
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        args[0].flush()
        raise RuntimeError('publication fault')
    monkeypatch.setattr(sessions,'_replace_thread_text_cache',fail)
    with pytest.raises(RuntimeError,match='publication fault'):sessions.build_codex_thread_detail(str(root),'thread')
    with Session(bind) as reader:
        assert reader.exec(select(CodexTextCacheThread)).one().title=='Original'
        assert len(reader.exec(select(CodexTextCacheMessage)).all())==2
        assert len(reader.exec(select(CodexTextCacheTurn)).all())==1


def test_separate_process_lock_uses_same_engine_root_identity(refresh,tmp_path):
    bind,root,_=refresh
    ready=tmp_path/'ready';release=tmp_path/'release'
    script='''import sys,time
from pathlib import Path
from sqlmodel import create_engine
from backend.core.codex import sessions
bind=create_engine(sys.argv[1]);root=Path(sys.argv[2]).resolve()
with sessions._cache_refresh_lock(bind,sessions._path_match_key(str(root))):
 Path(sys.argv[3]).touch()
 deadline=time.monotonic()+10
 while not Path(sys.argv[4]).exists() and time.monotonic()<deadline:time.sleep(.02)
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(bind.url),str(root),str(ready),str(release)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+8
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert ready.exists()
        start=time.monotonic()
        with pytest.raises(sessions.CodexCacheRefreshBusyError):sessions.build_codex_thread_detail(str(root),'thread')
        assert time.monotonic()-start<2
    finally:
        release.touch()
        out,err=process.communicate(timeout=12)
    assert process.returncode==0,err.decode()
    assert sessions.build_codex_thread_detail(str(root),'thread')['message_count']==2

def test_removed_thread_publishes_deletion_across_all_tables(refresh):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    with sqlite3.connect(root/'state_5.sqlite') as db:db.execute('DELETE FROM threads')
    (root/'.codex-global-state.json').write_text('{"deleted":true}')
    with pytest.raises(KeyError):sessions.build_codex_thread_detail(str(root),'thread')
    with Session(bind) as reader:
        for model in (CodexTextCacheThread,CodexTextCacheMessage,CodexTextCacheTurn):
            assert reader.exec(select(model)).all()==[]
        assert reader.exec(select(CodexTextCacheRoot)).one().global_state_size is not None


def test_original_completed_turns_reports_busy_instead_of_stale_completion(refresh):
    bind,root,_=refresh
    sessions.read_codex_completed_turns('thread',root_dir=str(root))
    change_source(root)
    with bind.connect() as writer:
        writer.exec_driver_sql('BEGIN IMMEDIATE')
        with pytest.raises(sessions.CodexCacheRefreshBusyError):
            sessions.read_codex_completed_turns('thread',root_dir=str(root))
        writer.rollback()


def test_source_changed_during_publication_rolls_back(refresh,monkeypatch):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    change_source(root)
    original=sessions._replace_thread_text_cache
    def change(*args,**kwargs):
        original(*args,**kwargs)
        (root/'.codex-global-state.json').write_text('{"changed_again":true}')
    monkeypatch.setattr(sessions,'_replace_thread_text_cache',change)
    with pytest.raises(sessions.CodexCacheSourceChangedError):sessions.build_codex_thread_detail(str(root),'thread')
    with Session(bind) as reader:
        assert reader.exec(select(CodexTextCacheThread)).one().title=='Original'
        assert len(reader.exec(select(CodexTextCacheMessage)).all())==2

def test_exclusive_sqlite_lock_bounds_staging_reader_and_restores_timeout(refresh):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    with bind.connect() as writer:
        writer.exec_driver_sql('PRAGMA journal_mode=DELETE');writer.commit()
        writer.exec_driver_sql('BEGIN EXCLUSIVE')
        start=time.monotonic()
        with pytest.raises(sessions.CodexCacheRefreshBusyError,match='cache reader'):
            sessions.build_codex_thread_detail(str(root),'thread')
        assert time.monotonic()-start<2
        writer.rollback()
    with bind.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA busy_timeout').scalar_one()==30000


@pytest.mark.parametrize('kind',['overview','workload','compact'])
def test_other_refreshing_queries_do_not_autoflush_caller(refresh,kind):
    bind,root,_=refresh
    sessions.build_codex_thread_detail(str(root),'thread')
    with Session(bind) as caller:
        thread=caller.exec(select(CodexTextCacheThread)).one()
        thread.title='pending'
        transaction=caller.get_transaction()
        change_source(root)
        if kind=='overview':
            result=sessions.build_codex_overview(str(root),session=caller)
            assert result['groups'][0]['threads'][0]['title']=='Refreshed'
        else:
            result=sessions.build_codex_workload(str(root),session=caller,
                compact=kind=='compact',include_segments=kind!='compact')
            assert result['total_threads']==1
        assert thread in caller.dirty and thread.title=='pending'
        assert caller.get_transaction() is transaction
