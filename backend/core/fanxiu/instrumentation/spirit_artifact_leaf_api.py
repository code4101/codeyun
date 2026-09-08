"""只读检查长驻Kernel的洗灵叶模块API；不导入业务模块，不自动reload。"""
from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
import sys


_LEAVES = ('spirit_artifact_yinxian', 'spirit_artifact_a_collection')
_PREFIX = 'backend.core.fanxiu.data_annotation.tasks.'
_MISSING = object()


def compare_leaf_function_api(function, definition: ast.FunctionDef) -> dict:
    """纯比较已加载函数与磁盘AST签名，不执行函数或求值默认表达式。

    只检查调用形状、必填性和简单常量默认值；签名相同不证明函数体已更新。
    非常量默认值保留未核实，不能为检查一致性而执行源码中的函数调用。
    """
    args = definition.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + args.defaults
    declared = []
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        declared.append((arg.arg, 'POSITIONAL_ONLY' if index < len(args.posonlyargs)
                         else 'POSITIONAL_OR_KEYWORD', default))
    if args.vararg:
        declared.append((args.vararg.arg, 'VAR_POSITIONAL', None))
    declared.extend((arg.arg, 'KEYWORD_ONLY', default)
                    for arg, default in zip(args.kwonlyargs, args.kw_defaults))
    if args.kwarg:
        declared.append((args.kwarg.arg, 'VAR_KEYWORD', None))
    loaded = list(inspect.signature(function, follow_wrapped=False).parameters.values())
    differences, unverified = [], []
    if [(p.name, p.kind.name) for p in loaded] != [(name, kind) for name, kind, _ in declared]:
        differences.append('parameter_names_or_kinds')
    by_name = {p.name: p for p in loaded}
    for name, kind, default in declared:
        parameter = by_name.get(name)
        if parameter is None or kind.startswith('VAR_'):
            continue
        if (parameter.default is inspect.Parameter.empty) != (default is None):
            differences.append(name + ':required_changed')
        elif default is not None:
            value = default.value if isinstance(default, ast.Constant) else _MISSING
            if (value is _MISSING or type(value) not in (type(None), bool, int, float, str, bytes)
                    or type(parameter.default) not in (type(None), bool, int, float, str, bytes)):
                unverified.append(name)
            elif type(parameter.default) is not type(value) or parameter.default != value:
                differences.append(name + ':default_changed')
    return {'function': definition.name,
            'status': 'api_changed' if differences else 'signature_matches_body_unverified',
            'differences': differences, 'unverified_defaults': unverified}


def inspect_spirit_artifact_leaf_apis() -> dict:
    """在当前解释器检查已加载洗灵叶API与磁盘声明；无游戏/导入/reload副作用。

    只有在真正执行游戏的唯一Kernel中调用，结果才描述该Kernel；在外部Python
    中未加载是正常结果，不等于游戏Kernel状态。发现api_changed先处理叶模块
    版本，不把旧接口报错解释成新业务需求。刷新应由主线在Cell独占边界按依赖
    顺序yinxian→a_collection进行；不得沿此提示自动reload底层类型模块。
    此检查不验证函数体版本、已捕获的旧函数引用、装饰器或非常量默认表达式。
    诊断功能已离线验证；尚待真实Kernel研发入口采用。
    """
    directory = Path(__file__).resolve().parents[1] / 'data_annotation/tasks'
    modules = []
    for leaf in _LEAVES:
        name = _PREFIX + leaf
        loaded = sys.modules.get(name)
        if loaded is None:
            modules.append({'module': name, 'status': 'not_loaded', 'functions': []})
            continue
        path = directory / (leaf + '.py')
        source = path.read_bytes()
        tree = ast.parse(source.decode('utf-8-sig'), filename=str(path))
        functions = []
        for definition in tree.body:
            if not isinstance(definition, ast.FunctionDef) or definition.name.startswith('_'):
                continue
            function = vars(loaded).get(definition.name)
            if not inspect.isfunction(function):
                functions.append({'function': definition.name, 'status': 'api_changed',
                                  'differences': ['function_missing_or_replaced']})
            else:
                functions.append(compare_leaf_function_api(function, definition))
        modules.append({'module': name, 'source_sha256': hashlib.sha256(source).hexdigest(),
                        'status': 'api_changed' if any(f['status'] == 'api_changed' for f in functions)
                        else 'signature_matches_body_unverified', 'functions': functions})
    return {'read_only': True, 'game_accessed': False, 'modules': modules,
            'api_changed': any(m['status'] == 'api_changed' for m in modules)}
