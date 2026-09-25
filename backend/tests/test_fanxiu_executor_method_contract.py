"""静态核对组合后的执行器方法引用，不启动 Runtime 或模拟游戏界面。"""

import ast
import builtins
import inspect
import symtable
import textwrap
from collections import defaultdict
from pathlib import Path

from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor


class _MethodCalls(ast.NodeVisitor):
    def __init__(self):
        self.calls = set()
        self.assigned = set()

    def visit_ClassDef(self, node):
        # 嵌套观察器有自己的 self，不属于外层 Executor。
        pass

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == "self" and isinstance(node.ctx, ast.Store):
            self.assigned.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node):
        target = node.func
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            self.calls.add(target.attr)
        self.generic_visit(node)


def test_executor_business_composition_has_no_missing_self_methods():
    references = []
    assigned = set()
    for cls in BehaviorTreeExecutor.__mro__:
        if cls is object:
            continue
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
        class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        for method in class_node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            visitor = _MethodCalls()
            visitor.visit(method)
            assigned.update(visitor.assigned)
            references.extend((cls.__name__, method.name, name) for name in visitor.calls)

    missing = [
        f"{owner}.{method} -> self.{name}"
        for owner, method, name in references
        if name not in assigned and not hasattr(BehaviorTreeExecutor, name)
    ]
    assert not missing, "\n".join(sorted(missing))


def test_executor_business_methods_have_one_owner():
    owners = defaultdict(list)
    for cls in BehaviorTreeExecutor.__mro__:
        if cls is object:
            continue
        for name, value in vars(cls).items():
            if name.startswith("__"):
                continue
            if inspect.isfunction(value) or isinstance(value, (classmethod, staticmethod)):
                owners[name].append(cls.__name__)
    duplicates = {name: classes for name, classes in owners.items() if len(classes) > 1}
    assert not duplicates, f"业务方法应有单一所属模块，避免继承顺序隐式选择实现：{duplicates}"


def test_executor_composed_modules_resolve_runtime_globals():
    missing = []
    visited = set()
    for cls in BehaviorTreeExecutor.__mro__:
        if cls is object:
            continue
        module = inspect.getmodule(cls)
        if module.__name__ in visited:
            continue
        visited.add(module.__name__)
        path = Path(inspect.getfile(cls))
        table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
        available = set(vars(module)) | set(vars(builtins))
        pending = [table]
        while pending:
            scope = pending.pop()
            for symbol in scope.get_symbols():
                if symbol.is_global() and symbol.is_referenced() and symbol.get_name() not in available:
                    missing.append(f"{module.__name__}:{scope.get_lineno()} {symbol.get_name()}")
            pending.extend(scope.get_children())
    assert not missing, "\n".join(sorted(missing))
