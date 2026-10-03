import pytest
from backend.core.fanxiu.data_annotation.tasks.lingxiao_store import activity_rounds, allowed_store_rows, PERIOD
from backend.core.fanxiu.runtime_gui.activity_template_store import activity_gift_button
from backend.core.fanxiu.data_annotation.tasks import lingxiao_store


def test_server_two_rounds_do_not_imply_a_third_round():
    rounds = activity_rounds('1_10_1|6_10_2',*PERIOD)
    assert [r['round'] for r in rounds] == [1,2]
    assert rounds[0]['end_ms'] == rounds[1]['start_ms'] == 1790956805000
    assert rounds[-1]['end_ms'] == PERIOD[1]
    with pytest.raises(ValueError,match='超出'):
        activity_rounds('1_10_1|6_10_2|11_15_3',*PERIOD)


def test_monitor_stops_at_period_end_without_reading_game(monkeypatch):
    monkeypatch.setattr(lingxiao_store.time, 'time', lambda: PERIOD[1]/1000)
    monkeypatch.setattr(lingxiao_store, 'read_activity_period_runtime_snapshot',
        lambda _: pytest.fail('closed period must not read a new instance'))
    assert lingxiao_store.read_lingxiao_store_monitor()['phase'] == 'closed'


def test_monitor_rejects_new_instance_before_round_config(monkeypatch):
    monkeypatch.setattr(lingxiao_store.time, 'time', lambda: PERIOD[0]/1000)
    monkeypatch.setattr(lingxiao_store, 'read_activity_period_runtime_snapshot',
        lambda _: dict(start_time_ms=PERIOD[0]+86400000,end_time_ms=PERIOD[1]+86400000))
    monkeypatch.setattr(lingxiao_store, 'read_revenue_activity_round_config',
        lambda _: pytest.fail('another period is not authorized'))
    assert lingxiao_store.read_lingxiao_store_monitor()['phase'] == 'instance_changed'


def test_paid_and_other_currency_rows_never_receive_purchase_permission():
    base = dict(activity_id=30000008,gift_type='GRADE_CHANGE',show_condition='',hide_condition='',buy_condition='')
    snapshot = dict(complete=True,items=[
        dict(base,id=1,pay_id=0,costs=''),
        dict(base,id=2,pay_id=0,costs='Item|1_248'),
        dict(base,id=3,pay_id=100,costs=''),
        dict(base,id=4,pay_id=0,costs='Item|3_1'),
    ])
    assert [(r['id'],r['unit_price']) for r in allowed_store_rows(snapshot)] == [(1,0),(2,248)]


def test_title_anchor_projects_whole_button_and_rejects_clipped_card():
    kwargs = dict(title='灵石礼包一',viewport=dict(x=25,w=850),
        title_template=dict(x=80,y=682,w=180,h=55),button_template=dict(x=40,y=1130,w=260,h=85))
    token = dict(text='灵石礼包一',x=395,y=690,w=210,h=40,parent_line_id='a',order=0)
    assert activity_gift_button([token],**kwargs) == (500,1172.5)
    with pytest.raises(ValueError,match='裁剪'):
        activity_gift_button([{**token,'x':750}],**kwargs)
