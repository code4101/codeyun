from datetime import date
import pytest

from backend.core.fanxiu.activity.xutian_population_planning import plan_xutian_yandi_day


@pytest.mark.parametrize('population,expected_day', [
    (50, 4), (100, 4), (101, 3), (500, 3),
    (501, 2), (2000, 2), (2001, 1),
])
def test_latest_day_retains_first_day_active_population(population, expected_day):
    result = plan_xutian_yandi_day(
        start_date=date(2026, 10, 14), first_day_population=population,
        map_definitions=[{'id': 90000 + i, 'sort': i} for i in range(1, 5)],
        thresholds=[{'heavenId': 90000 + i, 'threshold': cap}
                    for i, cap in enumerate([2000, 500, 100, 0], 1)],
    )
    assert result['selected_date'] == date(2026, 10, 13 + expected_day).isoformat()
    assert result['expected_heaven'] == 90000 + expected_day


def test_zero_population_cannot_authorize_a_decree():
    with pytest.raises(ValueError):
        plan_xutian_yandi_day(start_date=date(2026, 10, 14),
                             first_day_population=0, map_definitions=[], thresholds=[])


def test_later_larger_cap_does_not_restore_eliminated_players():
    result = plan_xutian_yandi_day(
        start_date=date(2026, 10, 14), first_day_population=1000,
        map_definitions=[{'id': 90000 + i, 'sort': i} for i in range(1, 5)],
        thresholds=[{'heavenId': 90000 + i, 'threshold': cap}
                    for i, cap in enumerate([500, 2000, 100, 0], 1)],
    )
    assert result['selected_date'] == '2026-10-14'
