"""真实页面文本解析契约；不模拟游戏或领奖交互。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import parse_task_reward_progress


@pytest.mark.parametrize("text, expected", [
    ("8240/10000", False), ("10000/10000", True), ("12000｜10000", True),
    ("0/100", False), ("", None), ("10000", None), ("1/0", None),
    ("100/200 300", None), ("已领取", False), ("已完成", None),
    ("352.63万/393.6万", False), ("393.6万/393.6万", True),
    ("1亿/9999万", True), ("0/1万", False), ("3.5万 4万", None),
    ("118.71万/118.71万", True), ("118.71万7118.71万", None),
])
def test_reward_progress(text, expected):
    assert parse_task_reward_progress(text) is expected


def test_claimed_label_is_owned_by_page_adapter():
    assert parse_task_reward_progress("已完成", claimed_texts=("已完成",)) is False
