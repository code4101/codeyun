from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks import (
    xianzang_task_claimable,
)


def _tokens(*texts: str) -> list[dict[str, str]]:
    return [{"text": text} for text in texts]


def test_claimable_requires_explicit_lingqu_control():
    # #450 实际画面：只有“已完成”和进度，没有“领取”控件 → 不可点击领取。
    assert xianzang_task_claimable(_tokens(
        "蓬莱仙藏", "炼宝试炼二", "游历20次", "19/20",
        "炼宝试炼八", "参与宗门灵泉1次",
        "登录游戏", "已完成", "任务", "商店",
    )) is False


def test_claimable_detected_when_lingqu_present():
    assert xianzang_task_claimable(_tokens("炼宝试炼一", "已完成", "领取")) is True


def test_claimable_ignores_whitespace_and_layout():
    assert xianzang_task_claimable(_tokens("领 取")) is True
    assert xianzang_task_claimable([]) is False
