import pytest
from backend.core.fanxiu.instrumentation.trial_manual import partition_trial_manual_reward_rows
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


def test_manual_loop_box_is_optional_only_when_live_panel_says_hidden():
    ordinary = [{'_isBoxCfg': False, 'rewardId': 10}]
    box = {'_isBoxCfg': True, 'rewardId': 99}
    assert partition_trial_manual_reward_rows(ordinary, show_box=False) == (ordinary, [])
    assert partition_trial_manual_reward_rows(ordinary+[box], show_box=True) == (ordinary, [box])
    for rows, flag in ((ordinary, True), (ordinary+[box], False), ([{}], False), (ordinary, None)):
        with pytest.raises(FanxiuRuntimeMemoryError, match='奖励类型不完整'):
            partition_trial_manual_reward_rows(rows, show_box=flag)
