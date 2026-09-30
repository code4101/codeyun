import pytest
from pyxllib.autogui import View

from backend.core.fanxiu.data_annotation.shape_selectors import resolve_shape_path


def test_nested_selector_disambiguates_sibling_badges():
    view = View({"shapes": [
        {"title": title, "children": [{"title": "可领取", "id": title}]}
        for title in ("奖励找回", "周常")
    ]})
    assert resolve_shape_path(view, "奖励找回/可领取").raw["id"] == "奖励找回"
    assert resolve_shape_path(view, "[周常/可领取]").raw["id"] == "周常"
    with pytest.raises(RuntimeError, match="多个目标"):
        resolve_shape_path(view, "可领取")
    with pytest.raises(RuntimeError, match="未命中"):
        resolve_shape_path(view, "不存在/可领取")
