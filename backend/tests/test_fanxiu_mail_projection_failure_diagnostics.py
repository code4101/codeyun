"""Actual isolated SQLite lock/rollback and diagnostic storage; no game calls."""
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, create_engine
from backend.models import FanxiuMailRecord
from backend.core.fanxiu.mail import runtime_sync
from backend.core.fanxiu.mail.projection_diagnostics import read_mail_projection_diagnostics


@pytest.mark.parametrize('diagnostic_unavailable,bad_logging',[(False,False),(True,False),(True,True)])
def test_real_write_lock_retains_each_phase_and_original_final_error(tmp_path,monkeypatch,diagnostic_unavailable,bad_logging):
    engine=create_engine('sqlite:///'+(tmp_path/'mail.sqlite').as_posix(),connect_args={'timeout':0.02})
    FanxiuMailRecord.__table__.create(engine)
    monkeypatch.setattr(runtime_sync,'ensure_fanxiu_mail_table',lambda:None)
    monkeypatch.setattr(runtime_sync,'load_fanxiu_mail_envelope_titles',lambda:{})
    monkeypatch.setattr(runtime_sync,'_load_mail_item_name_index',lambda:{})
    from backend.core.fanxiu.mail import store
    monkeypatch.setattr(store,'ensure_fanxiu_mail_table',lambda:None)
    errors=[]
    event.listen(engine,'handle_error',lambda context: errors.append(context.sqlalchemy_exception))
    target=tmp_path/'diagnostics.json'
    if diagnostic_unavailable:
        obstruction=tmp_path/'existing-file'; obstruction.write_text('retained',encoding='utf-8')
        target=obstruction/'diagnostics.json'
    if bad_logging:
        import logging
        def broken_warning(*args,**kwargs):
            raise RuntimeError('isolated broken logging handler')
        for name in ('backend.core.fanxiu.mail.runtime_sync','backend.core.fanxiu.mail.projection_diagnostics'):
            monkeypatch.setattr(logging.getLogger(name),'warning',broken_warning)
    snapshot={'complete':True,'decoded_count':1,'captured_at':'2026-10-11 02:25:00',
              'sequence_fingerprint':'exact-source-snapshot','items':[{
                  'id':'isolated-mail','type':1,'title':'private-title-canary',
                  'content':'private-content-canary','reward_getted':False,'locked':False,
                  'has_attachment':False,'rewards':[]}]}
    class CountingSession(Session):
        rollback_count=0
        def rollback(self):
            self.rollback_count+=1
            return super().rollback()
    with engine.connect() as blocker, CountingSession(engine) as session:
        blocker.exec_driver_sql('BEGIN IMMEDIATE')
        with pytest.raises(OperationalError) as caught:
            runtime_sync.sync_fanxiu_mail_runtime_snapshot(session,snapshot,diagnostics_path=target)
        assert caught.value is errors[-1]
        assert session.rollback_count==2
        attempts=caught.value.mail_projection_attempts
        assert [x['write_lock_retry'] for x in attempts]==[True,True,False]
        assert [x['retry_delay_seconds'] for x in attempts]==[0.2,0.4,0]
        records=[x['failure_diagnostics'] for x in attempts]
        assert len({r['sync_id'] for r in records})==1
        assert [r['attempt_number'] for r in records]==[1,2,3]
        assert all(r['failed_phase']=='load_absent_autoflush' for r in records)
        assert all(r['sqlite_errorcode']==5 and r['elapsed_seconds']>=0 for r in records)
        assert all(r['projection_timings']['load_absent_autoflush']>0 for r in records)
        assert all(r['evidence_persisted'] is (not diagnostic_unavailable) for r in records)
        import traceback
        assert any(frame.name=='_project_mail_runtime_snapshot_once'
                   for frame in traceback.extract_tb(caught.value.__traceback__))
        if diagnostic_unavailable:
            assert obstruction.read_text(encoding='utf-8')=='retained'
        else:
            saved=read_mail_projection_diagnostics(path=target)
            assert saved['records']==records and saved['lock_holder_proven'] is False
            text=target.read_text(encoding='utf-8')
            assert 'private-title-canary' not in text and 'private-content-canary' not in text
        blocker.rollback()
    engine.dispose()


def test_bounded_failure_journal_serializes_two_real_processes(tmp_path):
    import json
    import statistics
    import subprocess
    import sys
    from backend.core.fanxiu.mail.projection_diagnostics import record_mail_projection_failure
    script = """
import sys,json,time
from backend.core.fanxiu.mail.projection_diagnostics import record_mail_projection_failure
success=[];failure=[];seconds=[]
for index in range(40):
    record={'sync_id':sys.argv[2], 'attempt_number':index+1,
            'failed_phase':'commit_flush', 'projection_timings':{'commit_flush':0.01},
            'evidence_persisted':True}
    started=time.perf_counter()
    stored=record_mail_projection_failure(record,path=sys.argv[1])
    seconds.append(time.perf_counter()-started)
    (success if stored else failure).append(index+1)
print(json.dumps({'success':success,'failure':failure,'seconds':seconds}))
"""
    pressure_rates=[];pressure_seconds=[]
    for run, concurrent in enumerate([False,True,True,True]):
        target=tmp_path/f'shared-{run}.json'
        for index in range(64):
            assert record_mail_projection_failure({'sync_id':'seed','attempt_number':index+1},path=target)
        children=[];counts=[]
        def collect(child):
            stdout,stderr=child.communicate(timeout=30)
            assert child.returncode==0,stderr
            counts.append(json.loads(stdout))
        try:
            for index in range(2):
                child=subprocess.Popen([sys.executable,'-c',script,str(target),str(index)],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                children.append(child)
                if not concurrent:
                    collect(child)
            if concurrent:
                for child in children:
                    collect(child)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill();child.communicate()
        successful={(str(pid),index) for pid,row in enumerate(counts) for index in row['success']}
        failed={(str(pid),index) for pid,row in enumerate(counts) for index in row['failure']}
        assert len(successful)+len(failed)==80 and not successful.intersection(failed)
        for row in counts:
            assert sorted(row['success']+row['failure'])==list(range(1,41))
        saved=read_mail_projection_diagnostics(path=target)
        retained={(r['sync_id'],r['attempt_number']) for r in saved['records']}
        seed={('seed',index) for index in range(1,65)}
        assert retained.issubset(seed|successful) and not retained.intersection(failed)
        assert len(retained)==len(saved['records'])==saved['retention_limit']==64
        assert len(retained)+saved['evicted_record_count']==64+len(successful)
        if not concurrent:
            assert not failed and len(successful)==80
        else:
            pressure_rates.append(len(failed)/80)
            pressure_seconds.extend(value for row in counts for value in row['seconds'])
    print(json.dumps({'pressure_rounds':3,'calls':len(pressure_seconds),
        'publication_wall_mean_seconds':statistics.fmean(pressure_seconds),
        'publication_wall_std_seconds':statistics.pstdev(pressure_seconds),
        'failure_rate_mean':statistics.fmean(pressure_rates),
        'failure_rate_std':statistics.pstdev(pressure_rates),
        'failure_rates':pressure_rates}))
