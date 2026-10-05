import pytest

from backend.core.fanxiu.data_annotation.tasks.national_celebration import resolve_partner_level


@pytest.mark.parametrize("values, expected", [
    (["54", "54", "54"], "54"),
    (["04", "54", "54"], "54"),
    (["", "55", "55"], "55"),
])
def test_independent_partner_badges_require_majority(values, expected):
    assert resolve_partner_level(values) == expected


@pytest.mark.parametrize("values", [["54", "55", ""], ["4", "54", ""], ["", "55", ""]])
def test_ambiguous_or_single_badge_is_unknown(values):
    with pytest.raises(ValueError, match="多数一致"):
        resolve_partner_level(values)
