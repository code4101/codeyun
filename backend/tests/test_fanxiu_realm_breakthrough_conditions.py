"""Native realm-condition gate (LeijieItem rules); pure data, no game."""
from backend.core.fanxiu.instrumentation.realm_breakthrough import evaluate_realm_breakthrough_conditions

CONDITIONS = ['ATTR|7739004_960', 'CoreBaseMaxLevel|206']
CORE = {206: {'type': 2, 'parts': 6}}


def _eval(conditions=CONDITIONS, attributes=None, core=CORE, holes=None):
    return evaluate_realm_breakthrough_conditions(
        conditions, attributes={7739004: 203372} if attributes is None else attributes,
        core_bases=core, highest_open_part_by_type={2: 5} if holes is None else holes)


def test_five_of_six_holes_is_unmet_even_with_enough_shenshi():
    result = _eval()
    assert [row['met'] for row in result['conditions']] == [True, False]
    assert result['all_conditions_met'] is False


def test_all_met_only_when_sixth_part_open():
    assert _eval(holes={2: 6})['all_conditions_met'] is True


def test_missing_facts_unknown_kinds_and_malformed_entries_are_never_met():
    assert _eval(attributes={})['all_conditions_met'] is None
    assert _eval(holes={})['all_conditions_met'] is None
    assert _eval(core={})['all_conditions_met'] is None
    assert _eval(conditions=CONDITIONS + ['GFL|30'], holes={2: 6})['all_conditions_met'] is None
    assert _eval(conditions=['ATTR|7739004'])['all_conditions_met'] is None
    assert _eval(conditions=[])['all_conditions_met'] is None
    assert _eval(attributes={7739004: True})['conditions'][0]['met'] is None


def test_non_finite_or_negative_attribute_values_are_unknown_not_met():
    for bad in (float('inf'), float('nan'), -1):
        result = _eval(attributes={7739004: bad}, holes={2: 6})
        assert result['conditions'][0]['met'] is None and result['all_conditions_met'] is None
