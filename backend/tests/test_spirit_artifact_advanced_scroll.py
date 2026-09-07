"""只测试滚动经验的作用域和复用条件，不模拟列表、OCR 或点击。"""

from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_advanced_scroll import (
    AdvancedScrollKey, AdvancedScrollMemory, advanced_scroll_layout,
)


def catalog():
    return {'pid': 10, 'process_start_ticks': 20, 'ware_id': 1,
            'items': [{'item': 100, 'sort': 1, 'name': '洗灵甲', 'shortDes': '甲', 'count': 4},
                      {'item': 200, 'sort': 2, 'name': '洗灵乙', 'shortDes': '乙', 'count': 5}]}


def test_route_is_learned_not_a_global_four_scroll_constant():
    memory = AdvancedScrollMemory()
    key = AdvancedScrollKey('layout', ((100, 20, 30),), 200)
    assert memory.route(key) is None
    memory.remember(key, ('down', 'down', 'down'))
    assert memory.route(key) == ('down',) * 3
    assert memory.route(AdvancedScrollKey('layout', ((100, 20, 40),), 200)) is None
    assert memory.route(AdvancedScrollKey('layout', key.start, 100)) is None
    assert AdvancedScrollMemory().route(key) is None


def test_failure_discards_experience_and_exit_prevents_reuse():
    memory = AdvancedScrollMemory()
    key = AdvancedScrollKey('layout', ((100, 20, 30),), 200)
    memory.remember(key, ('down',))
    memory.forget(key)
    assert memory.route(key) is None
    memory.close()
    with pytest.raises(RuntimeError, match='离开'):
        memory.route(key)


@pytest.mark.parametrize('directions', [('left',), ('down',) * 31])
def test_route_is_bounded(directions):
    with pytest.raises(ValueError):
        AdvancedScrollMemory().remember(AdvancedScrollKey('layout', ((100, 2, 3),), 200), directions)


def test_unknown_start_is_never_learned():
    with pytest.raises(ValueError):
        AdvancedScrollMemory().remember(AdvancedScrollKey('layout', (), 200), ('down',))


def test_layout_binding_excludes_counts_but_includes_process_order_and_geometry():
    original = catalog()
    shape = {'id': 'list', 'height': .6}
    first = advanced_scroll_layout(original, shape)
    counts = deepcopy(original)
    counts['items'][0]['count'] = 3
    assert advanced_scroll_layout(counts, shape) == first
    for field, value in [('pid', 11), ('process_start_ticks', 21), ('ware_id', 2)]:
        changed = {**original, field: value}
        assert advanced_scroll_layout(changed, shape) != first
    assert advanced_scroll_layout({**original, 'items': original['items'][::-1]}, shape) != first
    assert advanced_scroll_layout(original, {**shape, 'height': .7}) != first


def test_real_title_sample_handles_character_tokens_and_missing_middle_dot():
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_advanced_scroll import find_advanced_item_title
    # 2026-09-07 #712 实际 OCR 的标题/库存片段：中点未输出，拥有分成两个字。
    title = [('洗', 234, 29, 0), ('灵', 256, 29, 1), ('巅', 315, 29, 3),
             ('峰', 342, 29, 4), ('石', 373, 27, 5)]
    tokens = [dict(text=text, x=x, y=644, w=w, h=26, parent_line_id='line-9', order=order)
              for text, x, w, order in title]
    tokens += [dict(text=text, x=x, y=642, w=31, h=33, parent_line_id='line-10', order=order)
               for order, (text, x) in enumerate([('拥', 566), ('有', 592)])]
    match = find_advanced_item_title(tokens, '洗灵·巅峰石')
    assert match is not None and match.point() == (317, 657)
    assert find_advanced_item_title(tokens[:5], '洗灵·巅峰石') is None
