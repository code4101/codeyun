"""实机祈愿页 OCR 文本的稳定解析契约。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import parse_prayer_round_progress
from backend.core.fanxiu.prayer_cycle import prayer_entry_category
from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import prayer_rows_need_score_scroll
from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import native_prayer_round_progress
from backend.core.fanxiu.data_annotation.tasks.prayer_task_progress import project_final_prayer_round_claim


def test_prayer_panel_waits_for_natural_loading_with_fresh_snapshots(monkeypatch):
    from backend.core.fanxiu.instrumentation import prayer_task_panel as provider
    from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    calls, sleeps = [], []
    result = {'complete': True, 'activity_id': 4502000}

    def read(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise FanxiuRuntimeMemoryError('loading', code='prayer_panel_not_loaded')
        return result

    monkeypatch.setattr(provider, 'read_ui_runtime_snapshot', read)
    monkeypatch.setattr(provider.time, 'sleep', sleeps.append)
    assert provider.read_prayer_task_panel(expected_activity_id=4502000) is result
    assert len(calls) == 2 and sleeps == [0.3]


@pytest.mark.parametrize('code', ['prayer_panel_incomplete', 'runtime_incomplete'])
def test_prayer_panel_preserves_first_integrity_error_without_retry(monkeypatch, code):
    from backend.core.fanxiu.instrumentation import prayer_task_panel as provider
    from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    error = FanxiuRuntimeMemoryError('first evidence', code=code)
    calls = []

    def read(*args, **kwargs):
        calls.append(1)
        raise error

    monkeypatch.setattr(provider, 'read_ui_runtime_snapshot', read)
    with pytest.raises(FanxiuRuntimeMemoryError) as caught:
        provider.read_prayer_task_panel(expected_activity_id=4502000)
    assert caught.value is error and len(calls) == 1


def test_prayer_panel_zero_budget_preserves_loading_error(monkeypatch):
    from backend.core.fanxiu.instrumentation import prayer_task_panel as provider
    from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    error = FanxiuRuntimeMemoryError('loading', code='prayer_panel_not_loaded')

    def read(*args, **kwargs):
        raise error

    monkeypatch.setattr(provider, 'read_ui_runtime_snapshot', read)
    with pytest.raises(FanxiuRuntimeMemoryError) as caught:
        provider.read_prayer_task_panel(expected_activity_id=4502000, wait_seconds=0)
    assert caught.value is error


def test_actual_native_capped_rows_do_not_replace_current_score():
    panel=dict(complete=True,source='active_prayer_task_panel_complete_list',activity_id=4502000,
        current_round=5,rows=[
            dict(config_round=5,target_score=14000,current_score=None,finished=True),
            dict(config_round=5,target_score=22000,current_score=22000,finished=True),
            dict(config_round=5,target_score=28000,current_score=22172,finished=False),
            dict(config_round=5,target_score=100000,current_score=22172,finished=False)])
    result=native_prayer_round_progress(panel)
    assert (result['current_score'],result['round_target_score'],result['round_score_gap'])==(22172,100000,77828)
    panel['rows'][-1]['current_score']=22173
    with pytest.raises(ValueError,match='不一致'):
        native_prayer_round_progress(panel)


def test_native_progress_requires_explicit_other_activity_identity():
    # Pure projection contract; these values do not claim live 淬体 acceptance.
    panel = dict(complete=True, source='active_prayer_task_panel_complete_list',
                 activity_id=4502010, current_round=2,
                 rows=[dict(config_round=2, target_score=120000,
                            current_score=40000, finished=False)])
    with pytest.raises(ValueError, match='完整原生'):
        native_prayer_round_progress(panel)
    result = native_prayer_round_progress(panel, expected_activity_id=4502010)
    assert (result['round_target_score'], result['round_score_gap']) == (120000, 80000)
    with pytest.raises(ValueError, match='完整原生'):
        native_prayer_round_progress(panel, expected_activity_id=4502010, total_rounds=1)


def test_real_alchemy_targets_are_bound_to_round_and_unknown_round_is_blocked():
    targets = {"3": 80000, "4": 100000}
    third = parse_prayer_round_progress("奖励轮次：3/5", "", "80000/80000", maximum_task_score=targets)
    fourth = parse_prayer_round_progress("奖励轮次：4/5", "再获得7积分可获得",
        "33993/90000\n33993/10万", maximum_task_score=targets)
    assert third['round_score_gap'] == 0
    assert (fourth['round_target_score'], fourth['round_score_gap']) == (100000, 66007)
    with pytest.raises(ValueError, match="第5轮最高档规则尚未核实"):
        parse_prayer_round_progress("奖励轮次：5/5", "", "0/1000", maximum_task_score=targets)


def test_capped_intermediate_rows_require_scroll_but_loaded_score_does_not():
    assert prayer_rows_need_score_scroll("48000/48000", maximum_task_score=160000)
    assert not prayer_rows_need_score_scroll("160000/160000", maximum_task_score=160000)
    assert not prayer_rows_need_score_scroll("26674/32000", maximum_task_score=160000)
    assert not prayer_rows_need_score_scroll("", maximum_task_score=160000)
    assert not prayer_rows_need_score_scroll("48001/48000", maximum_task_score=160000)


def test_real_herb_prayer_entry_maps_to_alchemy_without_accepting_merged_labels():
    assert prayer_entry_category("灵草祈愿") == "炼丹"
    assert prayer_entry_category("仙花祈愿") == "仙花"
    assert prayer_entry_category("零元购/灵草祈愿") is None
    assert prayer_entry_category("未知祈愿") is None


def test_header_reward_gap_is_separate_from_maximum_task_score():
    r = parse_prayer_round_progress("奖励轮次：4/5", "再获得18326积分可获得",
        "累计获得32000仙花积分\n80\n300\n26674/32000\n获取积分\n累计获得38000仙花积分\n80\n300",
        maximum_task_score=160000)
    assert (r['current_round'], r['total_rounds'], r['current_score']) == (4, 5, 26674)
    assert (r['round_target_score'], r['round_score_gap']) == (160000, 133326)
    assert r['header_reward_score_gap'] == 18326


def test_reward_quantities_cannot_be_concatenated_into_score():
    with pytest.raises(ValueError, match="进度条数字"):
        parse_prayer_round_progress("奖励轮次：4/5", "再获得18326积分可获得",
            "累计获得32000仙花积分8030026674/32000", maximum_task_score=160000)


def test_claimed_low_tier_does_not_override_current_score_and_units_are_parsed():
    r = parse_prayer_round_progress("奖励轮次：4/5", "再获得18326积分可获得",
        "26674/16万\n获取积分\n累计获得1000仙花积分\n1000/1000\n已领取", maximum_task_score=160000)
    assert (r['current_score'], r['round_target_score'], r['round_score_gap']) == (26674, 160000, 133326)


def test_header_can_be_missing_but_claimed_only_rows_cannot_prove_current_score():
    r = parse_prayer_round_progress("奖励轮次：4/5", "", "26674/32000", maximum_task_score=160000)
    assert r['header_reward_score_gap'] is None
    assert r['round_score_gap'] == 133326
    with pytest.raises(ValueError, match="观察不完整"):
        parse_prayer_round_progress("奖励轮次：4/5", "", "1000/1000\n已领取", maximum_task_score=160000)


def _claimed_panel_row(task_id, *, config_round=5, raw_target=100000, offset=0):
    return dict(ui_index=task_id, task_id=task_id, config_activity_id=4502000,
                config_round=config_round, raw_progress=None, raw_target=raw_target,
                round_offset=offset, current_score=None, target_score=raw_target - offset,
                finished=True, is_claimed=True)


def _final_claim_panel(rows):
    return dict(complete=True, source='active_prayer_task_panel_complete_list',
                activity_id=4502000, current_round=5, rows=rows,
                all_rounds_claimed=True, claimed_rounds=[1,2,3,4,5],
                cycle_claimed_task_count=len(rows) * 5)


@pytest.mark.parametrize('replacement', [
    {'all_rounds_claimed':False}, {'claimed_rounds':[5]},
    {'claimed_rounds':[True,2,3,4,5]}, {'cycle_claimed_task_count':0},
])
def test_final_claim_requires_all_five_rounds_witness(replacement):
    panel = {**_final_claim_panel([_claimed_panel_row(1)]), **replacement}
    with pytest.raises(ValueError, match='完整周期'):
        project_final_prayer_round_claim(panel)


def test_final_round_claim_fact_accepts_all_claimed_native_rows():
    rows = [_claimed_panel_row(1, raw_target=14000, offset=1),
            _claimed_panel_row(2, raw_target=22000, offset=2),
            _claimed_panel_row(3, raw_target=28000, offset=3),
            _claimed_panel_row(4, raw_target=100000, offset=5)]
    fact = project_final_prayer_round_claim(_final_claim_panel(rows))
    assert fact['final_round_claimed'] is True
    assert fact['claimed_task_count'] == 4
    assert (fact['current_round'], fact['total_rounds']) == (5, 5)
    assert fact['activity_id'] == 4502000


def test_final_round_claim_fact_rejects_full_but_unclaimed_rows():
    # 第5轮进度满分（finished=True）但仍有一项 is_claimed=False，不得假成功。
    unclaimed = {**_claimed_panel_row(2), 'is_claimed': False,
                 'finished': True, 'current_score': 22172, 'raw_progress': 22172}
    rows = [_claimed_panel_row(1), unclaimed]
    with pytest.raises(ValueError, match='全部领奖'):
        project_final_prayer_round_claim(_final_claim_panel(rows))


def test_final_round_claim_fact_does_not_trust_non_bool_claim_flag():
    rows = [_claimed_panel_row(1), {**_claimed_panel_row(2), 'is_claimed': 1}]
    with pytest.raises(ValueError, match='全部领奖'):
        project_final_prayer_round_claim(_final_claim_panel(rows))


def test_final_round_claim_fact_rejects_wrong_identity_round_and_duplicates():
    with pytest.raises(ValueError, match='原生任务列表'):
        project_final_prayer_round_claim(_final_claim_panel([_claimed_panel_row(1)]),
                                         expected_activity_id=4502010)
    with pytest.raises(ValueError, match='最后一轮'):
        project_final_prayer_round_claim({**_final_claim_panel([_claimed_panel_row(1)]),
                                          'current_round': 4})
    with pytest.raises(ValueError, match='最终轮任务行'):
        project_final_prayer_round_claim(_final_claim_panel([_claimed_panel_row(1, config_round=4)]))
    with pytest.raises(ValueError, match='身份重复'):
        project_final_prayer_round_claim(_final_claim_panel([_claimed_panel_row(1),
                                                            _claimed_panel_row(1)]))
