"""资源权限边界与每日组件身份；真机行为以运行验收为准。"""
from datetime import datetime
import pytest
from backend.core.fanxiu.data_annotation.tasks.field_transform_upgrade import actionable_rows
from backend.core.fanxiu.data_annotation.tasks.resource_daily_plan import iter_resource_component_plan
from backend.core.fanxiu.runtime_gui.field_transform import field_transform_model_points


def row(**changes):
    return dict(transform_id=4001, maximum=False, free=False,
                cost_item_id=391100001, cost_count=1, **changes)


def test_only_free_or_sufficient_dedicated_material_is_actionable():
    item = row()
    assert actionable_rows([item], {391100001: 0}) == []
    assert actionable_rows([item], {391100001: 1}) == [item]
    free = {**item, 'free': True}
    assert actionable_rows([free], {391100001: 0}) == [free]
    assert actionable_rows([{**item, 'maximum': True}], {391100001: 999}) == []


@pytest.mark.parametrize('item_id,count', [(3, 1), (391100002, 1), (391100001, 0)])
def test_currency_wrong_material_or_invalid_cost_fails_even_when_free(item_id, count):
    with pytest.raises(RuntimeError, match='未经确认'):
        actionable_rows([{**row(), 'cost_item_id': item_id, 'cost_count': count, 'free': True}], {item_id: 999})


def test_new_internal_component_has_independent_daily_receipt_identity():
    plan = list(iter_resource_component_plan(datetime(2026, 10, 3, 7)))
    found = [p for p in plan if p['stage_id'] == 'field-transform-upgrade']
    assert len(found) == 1
    assert found[0]['group'] == 'cultivate'
    assert found[0]['cycle'] == '2026-10-03'
    assert found[0]['eligible'] is True


def test_model_alignment_uses_observed_level_and_excludes_partial_models():
    points = field_transform_model_points([
        dict(text='0', x=680, y=1150, w=30, h=40),
        dict(text='0', x=210, y=260, w=30, h=40),
        dict(text='1000', x=200, y=600, w=40, h=30),
        dict(text='气血+0', x=200, y=900, w=100, h=30),
    ], levels={0}, viewport=dict(x=50,y=180,w=800,h=1180),
        left=dict(x=230,w=100), right=dict(x=540,w=140), gap=180)
    assert points == [(280, 990)]
