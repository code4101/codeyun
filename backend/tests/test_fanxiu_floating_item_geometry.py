from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
    BehaviorTreeRuntime,
    FloatingItemInstance,
)
from pyxllib.autogui import Shape, View


def _geometry_fixture(*, item_y: float):
    view = View({"id": 482, "width": 100, "height": 100, "shapes": []})
    container = Shape(
        {"id": "container", "title": "材料选项列表", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.6},
        parent_view=view,
    )
    template = Shape(
        {"id": "template", "title": "材料选项模板", "x": 0.2, "y": 0.2, "w": 0.4, "h": 0.2},
        parent_view=view,
    )
    field = Shape(
        {"id": "field", "title": "材料等级", "x": 0.25, "y": 0.25, "w": 0.3, "h": 0.1},
        parent_view=view,
        parent_shape=template,
    )
    item = FloatingItemInstance(
        view=view,
        template_shape=template,
        anchor_shape=field,
        anchor_box={"x": 25.0, "y": item_y + 5.0, "w": 30.0, "h": 10.0},
        item_box={"x": 20.0, "y": item_y, "w": 40.0, "h": 20.0},
    )
    runtime = object.__new__(BehaviorTreeRuntime)
    return runtime, item, field, container


@pytest.mark.parametrize(
    ("item_y", "expected"),
    [
        pytest.param(5.0, True, id="exact-top-boundary"),
        pytest.param(55.0, True, id="exact-bottom-boundary"),
        pytest.param(4.0, False, id="one-pixel-above"),
        pytest.param(56.0, False, id="one-pixel-below"),
    ],
)
def test_floating_item_click_field_requires_full_container_containment(
    item_y,
    expected,
) -> None:
    runtime, item, field, container = _geometry_fixture(item_y=item_y)

    # Template field is x=25,y=25 relative to the original view while the
    # materialized item starts at x=20,item_y.  field_box must preserve the
    # 5px offset before applying the container boundary check.
    assert item.field_box(field) == {
        "x": 25.0,
        "y": item_y + 5.0,
        "w": 30.0,
        "h": 10.0,
    }
    assert runtime.floating_item_field_is_fully_inside(item, field, container) is expected
