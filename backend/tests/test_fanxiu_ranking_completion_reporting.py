"""Real isolated receipts/SQLite queue; no Runtime, production configuration or sender."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.fanxiu.data_annotation.kernel_scheduler_results import record_scheduler_cell_result
from backend.core.fanxiu.activity import ranking_completion_reporting as reporting


IDENTITY = dict(instance_key='magic:original-instance', checkpoint_kind='exchange_tail_0030', business_date='2026-10-11')


def setup(tmp_path, *, enabled=True):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('fanxiu', 'account', 'group@chatroom')
    store.subscribe_codex_thread('domain-binding', 'fanxiu', [], enabled=True)
    config = tmp_path / 'reporting.json'
    reporting.configure_ranking_completion_reporting(hook_key='fanxiu', account_id='account',
        chat_id='group@chatroom', thread_id='domain-binding', enabled=enabled,
        configuration_path=config, store=store)
    return store, config


def receipt(tmp_path, attempt='a'*32, *, status='completed', closing=False):
    result = dict(status=status, purchases=[dict(quantity=16, unit_price=1000)],
        planning=dict(planned_spend_tokens=16000, planned_remaining_tokens=1, closing_goods_reached=closing),
        currency_remaining=1)
    record_scheduler_cell_result('ranking-lifecycle', attempt, cell_result={'status':'success'},
        business_result={'checkpoint_results':[dict(checkpoint={**IDENTITY,'activity_type':'magic-invasion'}, result=result)]},
        scheduler_result='success', started_at='start', finished_at='finish', directory=tmp_path / 'receipts')
    return attempt


def publish(tmp_path, store, config, attempt='a'*32):
    return reporting.publish_committed_ranking_attempt('ranking-lifecycle', attempt,
        configuration_path=config, directory=tmp_path / 'receipts', store=store)


def test_default_disabled_never_constructs_store_or_reads_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, '_store', lambda _: pytest.fail('disabled publication opened store'))
    assert publish(tmp_path, None, tmp_path/'absent.json')['status'] == 'disabled'
    assert not list(tmp_path.iterdir())


def test_concurrent_and_new_attempt_deduplicate_business_checkpoint(tmp_path):
    store, config = setup(tmp_path)
    receipt(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: publish(tmp_path,store,config), range(6)))
    assert all(r['status']=='recorded' for r in results)
    second = receipt(tmp_path,'b'*32)
    publish(tmp_path,store,config,second)
    notifications = store.pending_codex_notifications()
    assert len(notifications)==1
    assert '原最终商品预算目标未达' in notifications[0]['text']
    assert '余额1。' in notifications[0]['text']
    assert 'a'*32 in notifications[0]['text'] and 'b'*32 not in notifications[0]['text']
    assert store.deliveries('fanxiu')==[]
    assert reporting.read_ranking_completion_report(**IDENTITY,store=AgentStore(store.path))['attempt_id']=='a'*32


def test_missing_and_pending_success_receipts_do_not_admit(tmp_path):
    store, config = setup(tmp_path)
    assert publish(tmp_path,store,config)['status']=='pending_evidence'
    receipt(tmp_path,status='pending')
    assert publish(tmp_path,store,config)['status']=='not_applicable'
    assert store.pending_codex_notifications()==[]


def test_disabled_subscription_recovery_and_frozen_uncertain_delivery(tmp_path):
    store, config = setup(tmp_path)
    receipt(tmp_path)
    store.subscribe_codex_thread('domain-binding','fanxiu',[],enabled=False)
    with pytest.raises(ValueError,match='已启用'):
        publish(tmp_path,store,config)
    store.subscribe_codex_thread('domain-binding','fanxiu',[],enabled=True)
    publish(tmp_path,store,config)
    row = store.pending_codex_notifications()[0]
    reply_id=store.reserve_codex_notification(row['id'])
    store.finish_reply(reply_id,'uncertain',{'reason':'unknown acceptance'})
    assert publish(tmp_path,AgentStore(store.path),config)['reports'][0]['status']=='uncertain'
    assert store.pending_codex_notifications()==[]
    assert reporting.read_ranking_completion_report(**IDENTITY,store=store)['status']=='uncertain'


def test_crash_after_atomic_admission_recovers_exact_identity(tmp_path, monkeypatch):
    store, config = setup(tmp_path)
    receipt(tmp_path)
    real=store.enqueue_codex_completion
    def crash(*args,**kwargs):
        real(*args,**kwargs)
        raise OSError('crash after admission')
    monkeypatch.setattr(store,'enqueue_codex_completion',crash)
    with pytest.raises(OSError):
        publish(tmp_path,store,config)
    assert reporting.read_ranking_reporting_obligation('ranking-lifecycle','a'*32,
        configuration_path=config)['status']=='error'
    restarted=AgentStore(store.path)
    assert publish(tmp_path,restarted,config)['status']=='recorded'
    assert len(restarted.pending_codex_notifications())==1


def test_disabled_recovery_preserves_durable_obligation_without_admission(tmp_path):
    store, config=setup(tmp_path,enabled=False)
    receipt(tmp_path)
    reporting.record_ranking_reporting_obligation('ranking-lifecycle','a'*32,status='error',error='queue unavailable',
        configuration_path=config,directory=tmp_path/'receipts')
    assert publish(tmp_path,store,config)['status']=='disabled'
    assert reporting.read_ranking_reporting_obligation('ranking-lifecycle','a'*32,configuration_path=config)['error']=='queue unavailable'
    assert store.pending_codex_notifications()==[]


def test_sent_acceptance_is_distinct_from_archive_proof(tmp_path):
    store, config = setup(tmp_path)
    receipt(tmp_path,closing=True)
    publish(tmp_path,store,config)
    row=store.pending_codex_notifications()[0]
    reply=store.reserve_codex_notification(row['id'])
    store.finish_reply(reply,'sent',{'native_acceptance':True})
    assert reporting.read_ranking_completion_report(**IDENTITY,store=store)['status']=='sent'
    assert publish(tmp_path,store,config)['reports'][0]['status']=='sent'
    # Test the verifier's stored proof contract, not a real WeChat archive.
    store.set_meta('codex_result_verification:'+str(row['id']),{'status':'verified','local_ids':[42]})
    assert reporting.read_ranking_completion_report(**IDENTITY,store=store)['archive_verification']['local_ids']==[42]


def test_foreign_binding_and_destination_changes_rejected(tmp_path):
    store, config=setup(tmp_path)
    with pytest.raises(ValueError):
        reporting.configure_ranking_completion_reporting(hook_key='fanxiu',account_id='account',chat_id='foreign',
            thread_id='domain-binding',configuration_path=config,store=store)
