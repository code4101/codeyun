"""唯一Kernel稳定Cell边界的两项Runtime修复安装器；不重载模块、不访问游戏。"""
from __future__ import annotations

import ast
import builtins
import dis
import hashlib
import inspect
from pathlib import Path
import sys
import threading
from types import CodeType, FunctionType
import __future__
from bisect import bisect_right

from . import runtime_memory


_METHODS = (('LuaJitReader', 'numeric_fields'), ('MumuProcessMemory', 'readable_region'))
_LOCK = threading.Lock()


def _globals_used(code):
    names = {instruction.argval for instruction in dis.get_instructions(code)
             if instruction.opname == 'LOAD_GLOBAL'}
    for nested in code.co_consts:
        if isinstance(nested, CodeType):
            names.update(_globals_used(nested))
    return names


def refresh_runtime_memory_methods(*, apply: bool = False,
                                   expected_source_sha256: str | None = None,
                                   exclusive_runtime_access: bool = False) -> dict:
    """预检或原位安装磁盘上的numeric_fields/readable_region修复。

    首先调用无参预检，随后传apply=True、返回的source_sha256和
    exclusive_runtime_access=True安装。独占参数是调用方保证，不负责获取
    Scheduler权限：只能由唯一Kernel在上个业务Cell结束后执行，且无其他线程
    正在或可能开始Runtime读取。当前画面可以保留，不点击、不重启、不清缓存。
    活跃Runtime栈检测只是额外拒绝条件，不能替代调用方的并发屏障。

    只编译白名单方法，不执行模块顶层。保留类、LuaRef、方法函数对象及旧绑定
    方法身份；只替换现有函数的code/doc，并补充标准库bisect_right依赖。
    原globals和签名必须一致，不允许闭包/装饰器；失败回滚已安装方法。
    旧memory实例的索引由新readable_region懒建；不修改已有字节缓存或假装刷新。
    若类布局/全局协议需要变化，此入口拒绝，不扩白名单掩盖重启需求。
    已在真实Kernel Cell83安装，类型身份保持不变，数字索引四锚与七箱文本
    fresh观察通过。此证据不外推未来源码兼容性或区域索引的实际性能收益。
    """
    with _LOCK:
        source_path = Path(runtime_memory.__file__).resolve()
        source = source_path.read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if apply and (exclusive_runtime_access is not True or expected_source_sha256 != digest):
            raise RuntimeError('安装须明确独占Runtime，且源文件SHA256与预检一致')
        tree = ast.parse(source.decode('utf-8-sig'), filename=str(source_path))
        selected = []
        for class_name, method_name in _METHODS:
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
            methods = ([node for node in classes[0].body if isinstance(node, ast.FunctionDef)
                        and node.name == method_name] if len(classes) == 1 else [])
            if len(methods) != 1 or methods[0].decorator_list:
                raise RuntimeError('白名单方法缺失、重复或带装饰器，不能原位更新')
            node = methods[0]
            if any(not isinstance(default, ast.Constant)
                   for default in (*node.args.defaults, *[x for x in node.args.kw_defaults if x is not None])):
                raise RuntimeError('白名单方法默认参数不是常量')
            selected.append(node)
        temporary = {}
        module = ast.Module(body=selected, type_ignores=[])
        exec(compile(module, str(source_path), 'exec',
                     flags=__future__.annotations.compiler_flag, dont_inherit=True),
             vars(runtime_memory), temporary)
        prepared = []
        for class_name, method_name in _METHODS:
            old = getattr(getattr(runtime_memory, class_name), method_name)
            new = temporary[method_name]
            if (not isinstance(old, FunctionType) or old.__globals__ is not vars(runtime_memory)
                    or old.__code__.co_freevars or new.__code__.co_freevars):
                raise RuntimeError('现有方法globals/闭包身份不支持原位修复')
            def parameters(fn):
                return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]
            if parameters(old) != parameters(new):
                raise RuntimeError('方法调用签名已改变，不能局部更新')
            missing = _globals_used(new.__code__) - vars(runtime_memory).keys() - vars(builtins).keys() - {'bisect_right'}
            if missing:
                raise RuntimeError(f'新方法需要尚未安装的全局协议：{sorted(missing)}')
            prepared.append((old, new))
        report = {'status': 'ready', 'source_sha256': digest,
                  'methods': [f'{c}.{m}' for c, m in _METHODS],
                  'class_identity_preserved': True, 'game_accessed': False}
        if not apply:
            return report
        for frame in sys._current_frames().values():
            while frame is not None:
                if frame.f_globals is vars(runtime_memory):
                    raise RuntimeError('检测到活跃Runtime调用栈，须等待读取结束')
                frame = frame.f_back
        # All definitions have been validated before publication. Caller owns
        # exclusion from Runtime reads; this lock only serializes maintenance.
        absent = object()
        original_bisect = vars(runtime_memory).get('bisect_right', absent)
        originals = [(fn, fn.__code__, fn.__doc__) for fn, _ in prepared]
        try:
            runtime_memory.bisect_right = bisect_right
            for old, new in prepared:
                old.__code__ = new.__code__
                old.__doc__ = new.__doc__
        except BaseException:
            for fn, code, doc in originals:
                fn.__code__, fn.__doc__ = code, doc
            if original_bisect is absent:
                vars(runtime_memory).pop('bisect_right', None)
            else:
                runtime_memory.bisect_right = original_bisect
            raise
        return {**report, 'status': 'installed'}
