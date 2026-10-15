"""Native choose-page row projection; pure data, no game."""
from backend.core.fanxiu.instrumentation.medicine import project_medicine_choose_rows


def _row(item_id, sort, show=0):
    return {'medicine_id': item_id - 9070000, 'item_id': item_id, 'max_times': -1, 'show': show, 'sort': sort}


def test_held_rows_first_then_sort_and_show1_requires_stock():
    rows = [_row(9070309, 1), _row(9070310, 3), _row(9070312, 2), _row(9070399, 0, show=1)]
    out = project_medicine_choose_rows(rows, item_counts={9070310: 1, 9070312: 1}, role_level=250,
                                       required_realm_level=1)
    assert [r['item_id'] for r in out] == [9070312, 9070310, 9070309]
    assert [r['held_count'] for r in out] == [1, 1, 0] and not any(r['realm_masked'] for r in out)


def test_realm_mask_follows_role_level():
    out = project_medicine_choose_rows([_row(9070310, 1)], item_counts={}, role_level=50, required_realm_level=51)
    assert out[0]['realm_masked'] is True
