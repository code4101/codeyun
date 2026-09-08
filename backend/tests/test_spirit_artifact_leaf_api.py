"""旧签名诊断的纯算法，不模拟游戏。"""
import ast

from backend.core.fanxiu.instrumentation.spirit_artifact_leaf_api import compare_leaf_function_api


def declaration(source):
    return ast.parse(source).body[0]


def test_reports_old_required_parameters_instead_of_business_failure():
    def run(*, a_codes, b_codes):
        pass
    result = compare_leaf_function_api(run, declaration('def run(*, a_codes=None, b_codes=None): pass'))
    assert result['differences'] == ['a_codes:required_changed', 'b_codes:required_changed']


def test_reports_changed_literal_default_and_parameter_kind():
    def run(limit=80):
        pass
    result = compare_leaf_function_api(run, declaration('def run(*, limit=90): pass'))
    assert result['differences'] == ['parameter_names_or_kinds', 'limit:default_changed']


def test_does_not_evaluate_default_expression_or_claim_body_freshness():
    def run(limit=None):
        raise AssertionError('function must not execute')
    result = compare_leaf_function_api(run, declaration('def run(limit=unknown_function()): pass'))
    assert result['status'] == 'signature_matches_body_unverified'
    assert result['unverified_defaults'] == ['limit']


def test_variadic_and_optional_none_are_not_required():
    def run(arg, /, *args, value=None, **kwargs):
        pass
    result = compare_leaf_function_api(run, declaration('def run(arg, /, *args, value=None, **kwargs): pass'))
    assert result['differences'] == []
