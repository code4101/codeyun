import pytest

from backend.core.fanxiu.data_annotation.tasks.xianyuan_execution import DailyXianyuanTaskMixin


@pytest.mark.parametrize(
    ("text", "done"),
    [
        ("挑战仙缘 １/１", True),
        ("挑战仙缘 0/1", False),
        ("仙缘人物 已完成", True),
        ("仙缘斗法 5/5", False),
        ("挑战仙缘 0/0", False),
    ],
)
def test_daily_xianyuan_progress_is_not_duel_progress(text, done):
    assert DailyXianyuanTaskMixin()._daily_xianyuan_progress_done(text) is done


def test_row_progress_uses_the_target_vertical_band():
    lines = [
        {"text": "挑战仙缘", "y": 90, "h": 20},
        {"text": "１/１", "y": 95, "h": 10},
        {"text": "仙缘斗法 5/5", "y": 500, "h": 20},
    ]
    parser = DailyXianyuanTaskMixin()
    assert parser._daily_xianyuan_row_progress(lines, 100) == (1, 1)
    assert parser._daily_xianyuan_row_progress(lines, 510) is None
