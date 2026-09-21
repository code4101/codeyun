from __future__ import annotations

import pytest

from backend.core.fanxiu.runtime_gui.skill_tree import (
    align_tree_progress,
    first_unfilled_node,
    ordered_tree_nodes,
    tree_progress_candidates,
    tree_progress_direction,
    tree_spatial_progress_candidates,
)


def _cui_feng_tree():
    def node(index, level, max_level):
        return {'id': f'g42-{index}', 'x': 0, 'y': index, 'level': level, 'max_level': max_level}

    nodes = [node(i, 20, 20) for i in range(11)]
    nodes.append(node(11, 9, 20))
    nodes.extend(node(i, 20, 20) for i in range(12, 14))
    nodes.extend(node(i, 0, 20) for i in range(14, 25))
    nodes.append(node(25, 0, 1))
    return nodes


def _labels(texts):
    return [{'text': text} for text in texts]


def test_target_is_global_first_unfilled():
    target = first_unfilled_node(ordered_tree_nodes(_cui_feng_tree()))

    assert target['id'] == 'g42-11'


def test_repeated_zero_run_yields_all_candidate_windows():
    ordered = ordered_tree_nodes(_cui_feng_tree())
    candidates = tree_progress_candidates(ordered, _labels(['0/20'] * 8))

    assert len(candidates) == 4
    assert [next(iter(candidate)) for candidate in candidates] == [
        'g42-14',
        'g42-15',
        'g42-16',
        'g42-17',
    ]


def test_candidates_after_target_scroll_up():
    ordered = ordered_tree_nodes(_cui_feng_tree())
    indexes = {node['id']: i for i, node in enumerate(ordered)}
    candidates = tree_progress_candidates(ordered, _labels(['0/20'] * 8))

    assert tree_progress_direction(indexes['g42-11'], candidates, indexes) == 'up'


def test_unique_window_before_target_scroll_down():
    ordered = ordered_tree_nodes(_cui_feng_tree())
    indexes = {node['id']: i for i, node in enumerate(ordered)}
    candidates = tree_progress_candidates(ordered, _labels(['满'] * 11))

    assert len(candidates) == 1
    assert candidates[0]['g42-0']['text'] == '满'
    assert tree_progress_direction(indexes['g42-11'], candidates, indexes) == 'down'


def test_multiple_candidates_before_target_scroll_down():
    ordered = ordered_tree_nodes(_cui_feng_tree())
    indexes = {node['id']: i for i, node in enumerate(ordered)}
    candidates = tree_progress_candidates(ordered, _labels(['满'] * 8))

    assert len(candidates) == 4
    assert tree_progress_direction(indexes['g42-11'], candidates, indexes) == 'down'


def test_candidate_containing_target_is_ambiguous_for_direction():
    ordered = ordered_tree_nodes(_cui_feng_tree())
    indexes = {node['id']: i for i, node in enumerate(ordered)}
    labels = _labels(['满', '9/20', '满', '满', '0/20', '0/20', '0/20', '0/20'])
    candidates = tree_progress_candidates(ordered, labels)

    assert len(candidates) == 1
    assert 'g42-11' in candidates[0]
    with pytest.raises(ValueError):
        tree_progress_direction(indexes['g42-11'], candidates, indexes)


def test_align_returns_single_confident_mapping():
    ordered = ordered_tree_nodes(_cui_feng_tree())
    labels = _labels(['满', '9/20', '满', '满', '0/20', '0/20', '0/20', '0/20'])

    aligned = align_tree_progress(ordered, labels)

    assert aligned['g42-11']['text'] == '9/20'
    assert aligned['g42-10']['text'] == '满'


def test_align_rejects_ambiguous_repeated_window():
    ordered = ordered_tree_nodes(_cui_feng_tree())

    with pytest.raises(ValueError):
        align_tree_progress(ordered, _labels(['0/20'] * 8))


def test_short_or_unmatched_window_raises():
    ordered = ordered_tree_nodes(_cui_feng_tree())

    with pytest.raises(ValueError):
        tree_progress_candidates(ordered, _labels(['满']))
    with pytest.raises(ValueError):
        tree_progress_candidates(ordered, _labels(['7/20', '7/20']))


def _xuanji_tree():
    def node(index, level, max_level):
        return {'id': f'xj-{index}', 'x': 0, 'y': index, 'level': level, 'max_level': max_level}

    return [
        node(0, 5, 5),
        node(1, 2, 2),
        node(2, 4, 5),
        node(3, 5, 5),
        node(4, 0, 5),
    ]


def test_numeric_full_levels_locate_unique_window():
    ordered = ordered_tree_nodes(_xuanji_tree())
    candidates = tree_progress_candidates(ordered, _labels(['5/5', '2/2']))

    assert len(candidates) == 1
    assert candidates[0]['xj-0']['text'] == '5/5'
    assert candidates[0]['xj-1']['text'] == '2/2'


def test_denominator_difference_does_not_match():
    ordered = ordered_tree_nodes([
        {'id': 'a', 'x': 0, 'y': 0, 'level': 2, 'max_level': 5},
        {'id': 'b', 'x': 0, 'y': 1, 'level': 0, 'max_level': 5},
    ])

    with pytest.raises(ValueError):
        tree_progress_candidates(ordered, _labels(['2/2', '0/5']))

    matched = tree_progress_candidates(ordered, _labels(['2/5', '0/5']))
    assert len(matched) == 1


def test_full_and_numeric_full_mixed_still_aligned():
    ordered = ordered_tree_nodes([
        {'id': 'mx-0', 'x': 0, 'y': 0, 'level': 20, 'max_level': 20},
        {'id': 'mx-1', 'x': 0, 'y': 1, 'level': 5, 'max_level': 5},
        {'id': 'mx-2', 'x': 0, 'y': 2, 'level': 2, 'max_level': 2},
        {'id': 'mx-3', 'x': 0, 'y': 3, 'level': 12, 'max_level': 20},
        {'id': 'mx-4', 'x': 0, 'y': 4, 'level': 0, 'max_level': 20},
    ])
    candidates = tree_progress_candidates(ordered, _labels(['满', '5/5', '2/2', '12/20', '0/20']))

    assert len(candidates) == 1
    assert candidates[0]['mx-0']['text'] == '满'
    assert candidates[0]['mx-1']['text'] == '5/5'
    assert candidates[0]['mx-2']['text'] == '2/2'


def _grid(levels, xs, ys, prefix='sp'):
    nodes = []
    for row, y in enumerate(ys):
        for column, x in enumerate(xs):
            level, max_level = levels[row][column]
            nodes.append({
                'id': f'{prefix}-{row}-{column}',
                'x': x,
                'y': y,
                'level': level,
                'max_level': max_level,
            })
    return nodes


def _screen(text, cx, cy, w=31, h=31):
    return {'text': text, 'x': cx-w/2, 'y': cy-h/2, 'w': w, 'h': h}


def _three_row_grid(prefix):
    return _grid(
        [
            [(2, 2), (2, 2), (2, 2)],
            [(1, 1), (1, 1), (1, 1)],
            [(5, 5), (5, 5), (5, 5)],
        ],
        [52, 300, 548],
        [0, 244, 488],
        prefix,
    )


def _project(x, y, scale=1.2, offset_x=89.0, offset_y=505.5):
    return scale*x + offset_x, scale*y + offset_y


def test_spatial_alignment_tolerates_an_entire_missed_middle_row():
    nodes = _three_row_grid('miss')
    labels = []
    for x in (52, 548):
        cx, cy = _project(x, 0)
        labels.append(_screen('2/2', cx, cy))
    for x in (52, 300, 548):
        cx, cy = _project(x, 488)
        labels.append(_screen('5/5', cx, cy))

    candidates = tree_spatial_progress_candidates(nodes, labels)

    assert len(candidates) == 1
    aligned = candidates[0]
    assert aligned['miss-0-0']['text'] == '2/2'
    assert aligned['miss-0-2']['text'] == '2/2'
    assert aligned['miss-2-0']['text'] == '5/5'
    assert aligned['miss-2-1']['text'] == '5/5'
    assert aligned['miss-2-2']['text'] == '5/5'
    assert 'miss-1-0' not in aligned
    assert 'miss-1-1' not in aligned
    assert 'miss-1-2' not in aligned


def test_spatial_repeated_pattern_returns_every_candidate():
    nodes = _grid(
        [[(2, 2), (2, 2)], [(1, 1), (1, 1)], [(2, 2), (2, 2)], [(1, 1), (1, 1)]],
        [52, 548],
        [0, 244, 488, 732],
        'rep',
    )
    labels = []
    for x in (52, 548):
        cx, cy = _project(x, 0)
        labels.append(_screen('2/2', cx, cy))
    for x in (52, 548):
        cx, cy = _project(x, 244)
        labels.append(_screen('1/1', cx, cy))

    candidates = tree_spatial_progress_candidates(nodes, labels)

    assert len(candidates) == 2
    assert {next(iter(sorted(candidate))) for candidate in candidates} == {'rep-0-0', 'rep-2-0'}


def test_spatial_rejects_progress_mismatch():
    nodes = _three_row_grid('bad')
    labels = []
    for x in (52, 548):
        cx, cy = _project(x, 0)
        labels.append(_screen('2/2', cx, cy))
    for x in (52, 300, 548):
        cx, cy = _project(x, 488)
        labels.append(_screen('4/5' if x == 300 else '5/5', cx, cy))

    with pytest.raises(ValueError):
        tree_spatial_progress_candidates(nodes, labels)


def test_spatial_rejects_nonuniform_geometry():
    nodes = _three_row_grid('skew')
    labels = []
    for x in (52, 548):
        cx, cy = _project(x, 0, offset_y=505.5)
        labels.append(_screen('2/2', cx, cy))
    for x in (52, 300, 548):
        cx, cy = 1.2*x + 89.0, 2.0*488 + 505.5
        labels.append(_screen('5/5', cx, cy))

    with pytest.raises(ValueError):
        tree_spatial_progress_candidates(nodes, labels)


def test_spatial_rejects_labels_without_two_rows_and_columns():
    nodes = _three_row_grid('shape')
    one_row = [
        _screen('2/2', _project(x, 0)[0], _project(x, 0)[1])
        for x in (52, 300, 548)
    ]
    with pytest.raises(ValueError):
        tree_spatial_progress_candidates(nodes, one_row)
    one_column = [
        _screen(text, _project(52, y)[0], _project(52, y)[1])
        for text, y in (('2/2', 0), ('1/1', 244), ('5/5', 488))
    ]
    with pytest.raises(ValueError):
        tree_spatial_progress_candidates(nodes, one_column)


def test_spatial_rejects_tolerance_spanning_several_nodes():
    nodes = _grid([[(2, 2), (2, 2)], [(1, 1), (1, 1)]], [52, 300], [0, 244], 'wide')
    labels = [
        _screen('2/2', 100.0, 100.0, h=600),
        _screen('2/2', 500.0, 100.0, h=600),
        _screen('1/1', 100.0, 500.0, h=600),
    ]

    with pytest.raises(ValueError):
        tree_spatial_progress_candidates(nodes, labels)


def test_spatial_tolerance_uses_projected_units():
    nodes = _grid([[(2, 2), (2, 2)], [(1, 1), (1, 1)]], [0, 1], [0, 1], 'units')
    labels = [_screen('2/2', 100, 100), _screen('2/2', 300, 100),
              _screen('1/1', 100, 300), _screen('1/1', 300, 300)]
    assert len(tree_spatial_progress_candidates(nodes, labels)) == 1
def test_row_priority_skips_expensive_and_locked_nodes_without_short_circuit():
    from backend.core.fanxiu.runtime_gui.skill_tree import first_upgradeable_node
    nodes = [
        {'id': 1, 'x': 0, 'y': 0, 'level': 10, 'max_level': 20, 'unlocked': True, 'costs': {7: 500000}},
        {'id': 2, 'x': 1, 'y': 0, 'level': 0, 'max_level': 20, 'unlocked': True, 'costs': {7: 200000}},
        {'id': 3, 'x': 0, 'y': 1, 'level': 0, 'max_level': 20, 'unlocked': True, 'costs': {7: 100000}},
    ]
    assert first_upgradeable_node(nodes, {7: 300000})['id'] == 2
    nodes[1]['unlocked'] = False
    assert first_upgradeable_node(nodes, {7: 300000})['id'] == 3
    assert first_upgradeable_node(nodes, {7: 99999}) is None
    nodes[1]['unlocked'] = True
    assert first_upgradeable_node(nodes, {7: 500000})['id'] == 1

