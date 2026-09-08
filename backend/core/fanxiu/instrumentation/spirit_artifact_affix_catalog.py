"""一次性词条配置目录；只读已加载配置，不请求游戏、不写文件。

覆盖仅限 affix 规则，不能据此认定本体归属、324件配置或各器 A 集合已验证。
指定词条111022已真实读取验证默认 full=0、max=8000；全量目录、
末尾代际核验及热更新场景仍待真实 Runtime 验收，不能外推为全集已验证。
Attribute 客户端 ID 索引快路径尚未真实验收；命中/拒绝/回退计数随诊断返回。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, manager_index_fields, resolve_lua_global_manager_root, table_ref
from .ui_runtime_context import read_ui_runtime_snapshot

_METHODS = frozenset({'DBMgr', 'GetConfigTable', 'GetConfigTableByIdWithLog', 'Inst_get'})
_TABLES = ('SpiritWare.SpiritWareCleanse', 'SpiritWare.ConfigValue', 'Attribute.Attribute')


def _catalog_error(message: str) -> FanxiuRuntimeMemoryError:
    # 已证明的配置缺失/歧义/代际变化是终止观察，不触发底层 transient 重读。
    return FanxiuRuntimeMemoryError(message, code='affix_config_invalid')


def affix_catalog_fingerprint(rules: dict[int, dict], ratio_percent: float) -> str:
    """纯内容指纹，与进程、行遍历顺序及采集时间无关。"""
    content = {'ratio_percent': ratio_percent, 'rules': [rules[k] for k in sorted(rules)]}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def decode_affix_packed_row(table: dict, indexes: dict, defaults: dict, wanted) -> dict:
    """纯解析：直接字段→数字字段/packed array→已证明的 Runtime 默认值。

    defaults 由客户端 __index closure 的 _key2null 提供，不使用导出默认值。
    缺失不等于0；保持 None 交字段契约拒绝。显式零、空串和 False 不被吞。
    """
    result = {}
    for key in wanted:
        value = table['fields'].get(key)
        index = as_int(indexes.get(key))
        if value is None and index is not None:
            value = table['fields'].get(index)
            if value is None and 0 <= index < len(table['array']):
                value = table['array'][index]
        result[key] = defaults.get(key) if value is None else value
    return result


def validate_affix_rule(row: dict, *, name: str, name_raw: Any) -> dict:
    """纯契约：规则身份、正上限、明确 full/type 与名称来源，拒绝未知默认。"""
    values = {key: as_int(row.get(key)) for key in ('id', 'max', 'full', 'type')}
    if (values['id'] is None or values['id'] <= 0 or values['max'] is None or values['max'] <= 0
            or values['full'] is None or values['full'] < 0 or values['type'] is None
            or values['type'] <= 0 or not name
            or (values['type'] != 3 and not isinstance(row.get('code'), str))
            or (values['type'] != 3 and not row.get('code'))):
        raise ValueError('词条规则缺少有效 id/max/full/type/code/name')
    return {**row, **values, 'name': name, 'name_raw': name_raw,
            'name_source': 'runtime_string' if isinstance(name_raw, str) else 'exported_language'}


class _Rows:
    def __init__(self, ctx):
        self.ctx, self.reader = ctx, ctx.reader
        self.indexes, self.tables, self.members, self.direct = {}, {}, {}, {}
        self.default_cache, self.default_examples = {}, {}

        def configs(reader, address):
            manager = manager_index_fields(reader, address, _METHODS)
            inst = reader.fields(manager.get('inst'))
            tables = reader.dictionary_fields(inst.get('ConfigDic'))
            if any(table_ref(tables.get(key)) is None for key in _TABLES):
                raise _catalog_error('词条目录依赖配置尚未自然加载')
            return {key: tables[key] for key in _TABLES}

        root, _, environment = resolve_lua_global_manager_root(ctx.memory,
            manager_key='spirit-artifact-affix-db', state_address=ctx.binding.state_address,
            global_name='DBMgr', required_methods=_METHODS, validate=configs)
        self.roots = configs(self.reader, root)
        self.manager_root = root
        self.client_indexes = None
        self.lookup_counts = {'client_index_hit': 0, 'client_index_rejected': 0,
                              'full_index_fallback': 0}
        self.identity = (ctx.memory.pid, ctx.memory.process_start_ticks,
                         ctx.binding.state_address, environment, root)
        idx = table_ref(self.reader.state_string_field(environment, 's_globalCfgIdx',
            state_address=ctx.binding.state_address))
        if idx is None:
            raise _catalog_error('配置索引根尚未加载')
        self.index_roots = {}
        for key, ref in self.roots.items():
            current = idx
            for name in key.split('.'):
                current = table_ref(self.reader.state_string_field(current.address, name,
                    state_address=ctx.binding.state_address))
                if current is None:
                    raise _catalog_error(f'{key} 字段索引尚未加载')
            self.index_roots[key] = current.address
            self.indexes[key] = {k: as_int(v) for k, v in self.reader.fields(current).items()
                                 if isinstance(k, str) and as_int(v) is not None}
            if not self.indexes[key]:
                raise _catalog_error(f'{key} 字段索引为空')
            table = self.reader.table(ref.address)
            self.direct[key] = {i: v for i, v in enumerate(table['array']) if v is not None}
            for k, v in table['fields'].items():
                if k in self.direct[key] and self.direct[key][k] != v:
                    raise _catalog_error(f'{key} array/hash 同键冲突')
                self.direct[key][k] = v
            entries = [(('array', i), value) for i, value in enumerate(table['array']) if value is not None]
            entries += [(('hash', repr(k)), value) for k, value in table['fields'].items()]
            self.members[key] = tuple(sorted((repr(k), v.address) for k, v in entries if table_ref(v)))
            self.tables[key] = {v.address: v for _, v in entries if table_ref(v)}
            if not self.tables[key]:
                raise _catalog_error(f'{key} 没有配置行')
        self.by_id = {}

    def signature(self):
        return (self.identity, tuple((k, self.roots[k].address, self.index_roots[k],
            tuple(sorted(self.indexes[k].items())), self.members[k]) for k in _TABLES))

    def defaults(self, key, ref):
        row = self.reader.table(ref.address)
        meta = row.get('metatable')
        if not meta:
            return {}
        cache_key = (key, meta)
        if cache_key in self.default_cache:
            return self.default_cache[cache_key]
        function = self.reader.table(meta)['fields'].get('__index')
        if not isinstance(function, LuaRef) or function.kind != 'function':
            raise _catalog_error('生成配置默认值不是已支持的 __index closure')
        captured = self.reader.lua_closure_upvalues(function.address)['upvalues']
        tables = {v.address: self.reader.table(v.address) for v in captured if table_ref(v)}
        indexes = self.indexes[key]
        index_matches = [t for t in tables.values()
                         if all(as_int(t['fields'].get(k)) == i for k, i in indexes.items())]
        default_matches = [t for t in tables.values()
            if len(t['array']) > max(indexes.values())
            and any(isinstance(t['array'][i], str) for i in indexes.values())]
        if len(index_matches) != 1 or len(default_matches) != 1:
            raise _catalog_error('生成配置 index/default 捕获表不唯一，拒绝猜默认值')
        result = {k: default_matches[0]['array'][i] for k, i in indexes.items()}
        self.default_cache[cache_key] = result
        self.default_examples[cache_key] = (ref, function.address, result)
        return result

    def decode(self, key, ref, wanted):
        table = self.reader.table(ref.address)
        direct = decode_affix_packed_row(table, self.indexes[key], {}, wanted)
        if any(v is None for v in direct.values()):
            return decode_affix_packed_row(table, self.indexes[key], self.defaults(key, ref), wanted)
        return direct

    def index(self, key):
        if key not in self.by_id:
            result = {}
            for ref in self.tables[key].values():
                uid = self.decode(key, ref, ('id',))['id']
                if not isinstance(uid, (int, str)) or isinstance(uid, bool) or uid in result:
                    raise _catalog_error(f'{key} 配置行 id 缺失或重复')
                result[uid] = ref
            self.by_id[key] = result
        return self.by_id[key]

    def row(self, key, uid, wanted):
        # 优先复用客户端自然加载的 ID 字典；不调用 Lua、不促成配置加载。
        # 每次观察重新取得索引，旧行须通过当前成员及 id 验证才可使用。
        ref = table_ref(self.direct[key].get(uid))
        if ref is not None and self.decode(key, ref, ('id',))['id'] != uid:
            ref = None
        if ref is None and key == 'Attribute.Attribute' and key not in self.by_id:
            if self.client_indexes is None:
                manager = manager_index_fields(self.reader, self.manager_root, _METHODS)
                inst = self.reader.fields(manager.get('inst'))
                dictionaries = self.reader.dictionary_fields(inst.get('ClientTabDictionary'))
                self.client_indexes = self.reader.dictionary_fields(dictionaries.get(f'{key}_-_id'))
            candidate = table_ref(self.client_indexes.get(uid))
            if candidate is not None:
                if (candidate.address in self.tables[key]
                        and self.decode(key, candidate, ('id',))['id'] == uid):
                    ref = candidate
                    self.lookup_counts['client_index_hit'] += 1
                else:
                    self.lookup_counts['client_index_rejected'] += 1
        if ref is None:
            if key not in self.by_id:
                self.lookup_counts['full_index_fallback'] += 1
            ref = self.index(key).get(uid)
        if ref is None:
            raise _catalog_error(f'{key}[{uid}] 尚未加载')
        return self.decode(key, ref, wanted)


def read_affix_configuration(*, cleanse_ids: list[int] | None,
                             expected_cleanse_ids: list[int] | None = None,
                             verify_generation: bool = False) -> dict:
    """提供方共享采集实现；全量入口要求 verify_generation，不隐式落盘。"""
    for values in (cleanse_ids, expected_cleanse_ids):
        if values is not None and any(type(v) is not int or v <= 0 for v in values):
            raise ValueError('词条 ID 必须为正整数')
    started, started_at = time.monotonic(), time.time()
    timings = {}

    def observe(ctx):
        at = time.monotonic()
        rows = _Rows(ctx)
        timings['bind_and_members'] = time.monotonic() - at
        at = time.monotonic()
        runtime_ids = list(rows.index('SpiritWare.SpiritWareCleanse')) if cleanse_ids is None else None
        if runtime_ids is not None and any(type(uid) is not int or uid <= 0 for uid in runtime_ids):
            raise _catalog_error('词条目录包含非正整数 ID')
        if runtime_ids is not None:
            runtime_ids.sort()
        requested = runtime_ids if cleanse_ids is None else sorted(set(cleanse_ids))
        if runtime_ids is not None and not set(requested) <= set(runtime_ids):
            raise _catalog_error(f'请求词条未加载：{sorted(set(requested)-set(runtime_ids))}')
        ratio_raw = rows.row('SpiritWare.ConfigValue', 'Item5_MaxRatio', ('value',))['value']
        try:
            ratio = float(ratio_raw)
        except (TypeError, ValueError) as exc:
            raise _catalog_error('词条比例缺失或无效') from exc
        if isinstance(ratio_raw, bool) or not math.isfinite(ratio) or ratio <= 0:
            raise _catalog_error('词条比例必须为有限正数')
        from backend.core.fanxiu.catalog.lua_config import load_default_fanxiu_lang_map
        lang = load_default_fanxiu_lang_map()
        rules = {}
        for uid in requested:
            data = rows.row('SpiritWare.SpiritWareCleanse', uid,
                            ('id', 'max', 'full', 'code', 'selfName', 'type'))
            raw_name = data['selfName'] if data['type'] == 3 else rows.row(
                'Attribute.Attribute', data['code'], ('name',))['name']
            name = raw_name if isinstance(raw_name, str) else lang.get(as_int(raw_name), '')
            try:
                rules[uid] = validate_affix_rule(data, name=name, name_raw=raw_name)
            except ValueError as exc:
                raise _catalog_error(f'词条 {uid} 规则不完整：{exc}') from exc
        timings['decode_and_names'] = time.monotonic() - at
        result = dict(rules=rules, ratio_percent=ratio, pid=ctx.memory.pid,
            process_start_ticks=ctx.memory.process_start_ticks, runtime_ids=runtime_ids,
            requested_ids=requested, source='runtime_dbmgr_spiritware_config', read_only=True,
            diagnostics={'reader': ctx.reader.diagnostics(), 'memory': ctx.memory.diagnostics(),
                         'row_lookup': dict(rows.lookup_counts)})
        return result, rows.signature(), rows.default_examples

    result, signature, examples = read_ui_runtime_snapshot([], observe)
    if verify_generation:
        at = time.monotonic()
        def verify(ctx):
            fresh = _Rows(ctx)
            if fresh.signature() != signature:
                raise _catalog_error('词条采集期间进程或配置根/成员代际变化')
            for (key, meta), (ref, function_address, defaults) in examples.items():
                if ref.address not in fresh.tables[key] or fresh.defaults(key, ref) != defaults:
                    raise _catalog_error('词条采集期间配置默认值变化')
                if (key, meta) not in fresh.default_examples or fresh.default_examples[(key, meta)][1] != function_address:
                    raise _catalog_error('词条采集期间默认值 closure 变化')
        read_ui_runtime_snapshot([], verify)
        timings['fresh_generation_check'] = time.monotonic() - at
    expected = set(expected_cleanse_ids) if expected_cleanse_ids is not None else None
    actual = set(result['runtime_ids']) if result['runtime_ids'] is not None else None
    returned = set(result['rules'])
    result.update(complete=actual is not None and returned == actual, scope='affix_only',
        generation_verified=verify_generation, captured_at=time.time(), started_at=started_at,
        expected_ids=sorted(expected) if expected is not None else None,
        missing_expected_ids=sorted(expected-actual) if expected is not None and actual is not None else [],
        additional_runtime_ids=sorted(actual-expected) if expected is not None and actual is not None else [],
        expected_coverage_matches=(actual == expected) if expected is not None and actual is not None else None,
        returned_ids=sorted(returned), missing_requested_ids=sorted(set(result['requested_ids'])-returned),
        coverage_counts={'runtime': len(actual) if actual is not None else None,
            'requested': len(result['requested_ids']), 'returned': len(returned),
            'expected': len(expected) if expected is not None else None},
        content_fingerprint=affix_catalog_fingerprint(result['rules'], result['ratio_percent']),
        consistency=('fresh_process_roots_members_defaults_checked_not_atomic_row_values'
                     if verify_generation else 'single_observation_no_fresh_generation_check'),
        timings={**timings, 'total': time.monotonic()-started})
    return result
