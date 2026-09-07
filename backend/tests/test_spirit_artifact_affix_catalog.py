"""只检验生成配置解析、严格字段和内容指纹，不模拟 Runtime。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.instrumentation.spirit_artifact_affix_catalog import (
    decode_affix_packed_row, validate_affix_rule, affix_catalog_fingerprint,
)


def test_packed_full_uses_proven_default_and_preserves_explicit_zero():
    # 正式 SpiritWareCleanse.lua：full 索引4，普通行省略；_key2null[4]=0。
    indexes = {'id': 1, 'full': 4, 'max': 7}
    table = {'fields': {1: 110001, 7: 8000}, 'array': []}
    assert decode_affix_packed_row(table, indexes, {'full': 0}, indexes) == {
        'id': 110001, 'full': 0, 'max': 8000}
    assert decode_affix_packed_row(table, indexes, {}, indexes)['full'] is None
    table['fields']['full'] = 0
    assert decode_affix_packed_row(table, indexes, {'full': 1}, indexes)['full'] == 0
    table = {'fields': {}, 'array': [None, 110001, None, None, 1]}
    assert decode_affix_packed_row(table, indexes, {'full': 0}, ('full',))['full'] == 1


def rule():
    return {'id': 110001, 'max': 8000, 'full': 0, 'type': 1, 'code': 'ATTACK'}


@pytest.mark.parametrize('patch', [{'max': 0}, {'full': None}, {'full': False},
    {'type': None}, {'code': None}, {'code': ''}, {'max': float('nan')}])
def test_invalid_rule_never_becomes_default_zero(patch):
    with pytest.raises(ValueError):
        validate_affix_rule({**rule(), **patch}, name='攻击', name_raw=123)


def test_name_provenance_and_order_independent_fingerprint():
    first = validate_affix_rule(rule(), name='攻击', name_raw=123)
    second = validate_affix_rule({**rule(), 'id': 110002, 'type': 3, 'code': ''},
        name='灵器无双', name_raw='灵器无双')
    assert first['name_source'] == 'exported_language'
    assert second['name_source'] == 'runtime_string'
    rules = {110001: first, 110002: second}
    fingerprint = affix_catalog_fingerprint(rules, 120.)
    assert fingerprint == affix_catalog_fingerprint(dict(reversed(list(rules.items()))), 120.)
    changed = deepcopy(rules)
    changed[110001]['max'] = 10000
    assert fingerprint != affix_catalog_fingerprint(changed, 120.)
    assert fingerprint != affix_catalog_fingerprint(rules, 125.)
