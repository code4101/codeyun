"""Medicine gain parsing and aggregate model-consistency gates; pure data, no game."""
import pytest

from backend.core.fanxiu.instrumentation.medicine import parse_medicine_gain_text, reconcile_medicine_aggregate


def test_gain_text_uses_native_ratio_unit_for_percent():
    gains = parse_medicine_gain_text('点击使用按钮可前往服用，服用可获得：\n气血+6000\n气血加成+0.02%')
    assert [(g['name'], g['value'], g['percent']) for g in gains] == [('气血', 6000.0, False), ('气血加成', 2.0, True)]


def test_client_rich_text_markup_is_stripped():
    client = ('服用可获得：\n<color=#9e1e09>天资</color>+<color=#2a4b10>2</color>\n'
              '<color=#9e1e09>气血加成</color>+<color=#21420b>1%</color>')
    assert parse_medicine_gain_text(client) == parse_medicine_gain_text('天资+2\n气血加成+1%')
    assert [g['value'] for g in parse_medicine_gain_text(client)] == [2.0, 100.0]


def _codes():
    return {'气血': [1001], '气血加成': [1002], '致命增伤': [11001, 99001], 'x': [7]}


def _med(medicine_id, taken, text, max_times=-1, item_id=None):
    return {'medicine_id': medicine_id, 'item_id': item_id or medicine_id, 'max_times': max_times, 'taken': taken,
            'gains': parse_medicine_gain_text(text)}


def test_valid_model_with_unbounded_and_over_cap_is_consistent():
    medicines = [_med(1, 501, '气血+6000', item_id=9070310), _med(2, 20, '气血+100', max_times=10)]
    observed = {1001: 501 * 6000 + 10 * 100 + 10 * 100 * 0.3}
    model = reconcile_medicine_aggregate(medicines, observed=observed, attribute_codes=_codes(), over_cap_weight=0.3)
    assert model['all_model_consistent'] and model['evidence_kind'] == 'model_consistency_not_individual_measurement'
    # Under this arithmetic model, dropping the unbounded item's term no longer fits.
    lower = {1001: observed[1001] - 501 * 6000}
    assert not reconcile_medicine_aggregate(medicines, observed=lower, attribute_codes=_codes(),
                                            over_cap_weight=0.3)['all_model_consistent']


def test_missing_observed_is_unknown_not_zero():
    # Reviewer counterexample: observed={} used to reconcile via get(...,0) and default tolerance.
    model = reconcile_medicine_aggregate([_med(1, 1, 'x+1')], observed={}, attribute_codes=_codes(), over_cap_weight=.3)
    attr = model['attributes'][0]
    assert attr['state'] == 'observed_missing' and attr['residual'] is None and not attr['model_consistent']
    assert not model['all_model_consistent'] and 'observed_empty' in model['blocked_by']


def test_explicit_zero_differs_from_missing_and_unexplained_nonzero_blocks():
    med = [_med(1, 2, '气血+5')]
    ok = reconcile_medicine_aggregate(med, observed={1001: 10, 5003: 0}, attribute_codes=_codes(), over_cap_weight=.3)
    assert ok['all_model_consistent'] and {a['attribute_code']: a['state'] for a in ok['attributes']}[5003] == 'explicit_zero'
    bad = reconcile_medicine_aggregate(med, observed={1001: 10, 5003: 4}, attribute_codes=_codes(), over_cap_weight=.3)
    assert not bad['all_model_consistent'] and 'attribute_not_consistent' in bad['blocked_by']
    wrong_zero = reconcile_medicine_aggregate(med, observed={1001: 0}, attribute_codes=_codes(), over_cap_weight=.3)
    assert not wrong_zero['all_model_consistent']


def test_unparsed_unmapped_and_empty_history_block_consistency():
    unparsed = reconcile_medicine_aggregate([_med(1, 3, '无属性文本'), _med(2, 2, '气血+5')], observed={1001: 10},
                                            attribute_codes=_codes(), over_cap_weight=.3)
    assert unparsed['unparsed_item_ids'] == [1] and not unparsed['all_model_consistent']
    ambiguous = reconcile_medicine_aggregate([_med(3, 2, '致命增伤+2%')], observed={},
                                             attribute_codes=_codes(), over_cap_weight=.3)
    assert ambiguous['unmapped_gain_names'] == {'致命增伤': 2} and not ambiguous['all_model_consistent']
    resolved = reconcile_medicine_aggregate([_med(3, 2, '致命增伤+2%')], observed={11001: 400},
                                            attribute_codes=_codes(), over_cap_weight=.3)
    assert resolved['all_model_consistent']
    empty = reconcile_medicine_aggregate([_med(4, 0, '气血+5')], observed={1001: 0},
                                         attribute_codes=_codes(), over_cap_weight=.3)
    assert 'no_taken_history' in empty['blocked_by'] and not empty['all_model_consistent']


@pytest.mark.parametrize('observed, medicines', [
    ({1001: True}, [_med(1, 1, '气血+1')]),
    ({1001: float('nan')}, [_med(1, 1, '气血+1')]),
    ({1001: 1}, [{**_med(1, 1, '气血+1'), 'taken': None}]),
    ({1001: 1}, [_med(1, 1, '气血+1'), _med(1, 1, '气血+1')]),
])
def test_invalid_values_or_duplicate_identities_are_rejected(observed, medicines):
    with pytest.raises(ValueError):
        reconcile_medicine_aggregate(medicines, observed=observed, attribute_codes=_codes(), over_cap_weight=.3)
