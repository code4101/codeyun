"""红包相对几何的确定性契约；真实识别、点击与领取在游戏中验收。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_redpacket import redpacket_group_logo_point


def test_avatar_moves_with_badge_and_uses_current_asset_geometry():
    template_badge = dict(x=178, y=1189, w=38, h=38)
    template_logo = dict(x=98, y=1195, w=118, h=118)
    window = dict(x=81, y=616, w=757, h=710)
    assert redpacket_group_logo_point(dict(x=178, y=1025), template_badge, template_logo, window) == (157, 1090)
    template_logo["x"] += 10
    assert redpacket_group_logo_point(dict(x=178, y=1025), template_badge, template_logo, window) == (167, 1090)


def test_avatar_outside_window_is_not_clickable():
    with pytest.raises(RuntimeError, match="窗口外"):
        redpacket_group_logo_point(
            dict(x=178, y=1300), dict(x=178, y=1189),
            dict(x=98, y=1195, w=118, h=118), dict(x=81, y=616, w=757, h=710),
        )
