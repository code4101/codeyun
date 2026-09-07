"""已导出洗灵配置的静态索引；不代表当前客户端版本，不访问 Runtime。"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .lua_config import parse_fanxiu_generated_lua_config
from .resources import FanxiuResourceError, resolve_fanxiu_export_root

_NAMES = ('SpiritWareCleanse', 'SpiritWareItem', 'SpiritWarePreview', 'Attribute')


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


@lru_cache(maxsize=8)
def _discover(root: str, directory_stamp: int, spirit_dir: str) -> tuple[Path, ...]:
    cfg = Path(root) / 'by_source/lscripts/generate/cfg'
    paths = []
    for name in _NAMES:
        if spirit_dir and name != 'Attribute':
            candidates = [Path(spirit_dir) / (name + '.lua')]
        else:
            prefix = 'attribute' if name == 'Attribute' else 'spiritware'
            candidates = list(cfg.glob(f'{prefix}_*/text_assets/{name}.lua'))
        candidates = [p.resolve() for p in candidates if p.is_file()]
        if len(candidates) != 1:
            raise FanxiuResourceError(f'{name} 导出必须唯一，实际 {len(candidates)}；请明确导出来源')
        paths.append(candidates[0])
    return tuple(paths)


@lru_cache(maxsize=4)
def _load(signatures: tuple[tuple[str, int, int], ...]) -> Mapping[str, Any]:
    tables, sources = {}, {}
    for name, (filename, stamp, size) in zip(_NAMES, signatures):
        path = Path(filename)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = parse_fanxiu_generated_lua_config(path)['rows']
        stat = path.stat()
        if (stat.st_mtime_ns, stat.st_size) != (stamp, size):
            raise FanxiuResourceError(f'解析期间源文件发生变化：{path}')
        if not rows:
            raise FanxiuResourceError(f'配置解析为空：{path}')
        tables[name] = rows
        sources[name] = {'path': filename, 'sha256': digest, 'mtime_ns': stamp, 'size': size}
    attributes = {r['code']: r['id'] for r in tables['Attribute']}
    cleanse = {}
    for row in tables['SpiritWareCleanse']:
        key = row['id']
        if key in cleanse or not isinstance(row.get('max'), (int, float)) or row['max'] <= 0:
            raise FanxiuResourceError(f'洗灵词条 ID 重复或 max 无效：{key}')
        # 缺失 full/code 保持 None，不把 Lua 缺失字段臆测为零或普通属性。
        cleanse[key] = {k: row.get(k) for k in ('id', 'max', 'code', 'full', 'type', 'group')}
    previews = {}
    for row in tables['SpiritWarePreview']:
        previews.setdefault(row['group'], []).append(row)
    wares, items = {}, {}
    for row in tables['SpiritWareItem']:
        if row['itemId'] in items:
            raise FanxiuResourceError(f"灵器本体配置 ID 重复：{row['itemId']}")
        # 原始条件按本体保留；不能从灵器级 A 类并集反推客户端准入。
        items[row['itemId']] = row
        ware, part = row['type'], row['parts']
        if part not in range(1, 7):
            raise FanxiuResourceError(f'未支持的部位：{ware}-{part}')
        current = wares.setdefault(ware, {'core_codes': set(), 'parts': {}})
        piece = current['parts'].setdefault(part, {'base_score_full': 150 if part >= 5 else 100,
                                                 'preview_groups': set(), 'core_codes': set()})
        group = row.get('previewGroup')
        if group is not None:
            if group not in previews:
                raise FanxiuResourceError(f'部件引用不存在的 previewGroup：{group}')
            piece['preview_groups'].add(group)
        # 核心集合由客户端突破条件中的明确属性 ID 得出，不按灵器编号硬编码。
        for w, p, attribute, minimum in re.findall(
            r'SpiritWareAttribute\|(\d+)_(\d+)_(\d+)_(\d+)', row.get('cleanseUpgradeCondition', ''),
        ):
            if (int(w), int(p)) != (ware, part) or int(attribute) not in attributes:
                raise FanxiuResourceError(f'突破条件属性映射不完整：{ware}-{part}')
            piece['core_codes'].add(attributes[int(attribute)])
            current['core_codes'].add(attributes[int(attribute)])
    for current in wares.values():
        current['core_codes'] = sorted(current['core_codes'])
        current['a_codes'] = sorted(set(current['core_codes']) | {'ATTACK'})
        for piece in current['parts'].values():
            piece['core_codes'] = sorted(piece['core_codes'])
            piece['preview_groups'] = sorted(piece['preview_groups'])
    fingerprint = hashlib.sha256(''.join(sources[n]['sha256'] for n in _NAMES).encode()).hexdigest()
    return _freeze({'cleanse_by_id': cleanse, 'items_by_base_id': items,
                    'wares': wares, 'preview_by_group': previews,
                    'attribute_codes': attributes, 'sources': sources, 'fingerprint': fingerprint,
                    'runtime_verified': False, 'version_status': 'export_only_not_current_runtime_verified',
                    'base_score_source': 'business_convention_parts_1_4_100_parts_5_6_150',
                    'coverage': {'rows': {n: len(r) for n, r in tables.items()},
                                 'ware_ids': sorted(wares), 'cleanse_ids': sorted(cleanse)}})


def load_spirit_artifact_wash_rules(
    *, export_root: str | Path | None = None, spirit_config_dir: str | Path | None = None,
) -> Mapping[str, Any]:
    """加载只读静态规则、来源指纹和覆盖范围，无游戏或文件写入副作用。

    默认从正式导出根中唯一的 generate/cfg 表发现；缺失或多版本歧义报错。
    spirit_config_dir 可显式指定另一次已导出的三张 SpiritWare 表目录，
    Attribute 仍来自 export_root。不隐式使用 TEMP，不代替资源导出流程。
    首次解析后仅 stat 四个源文件；mtime/size 变化才重算 SHA256、重新解析。
    配置父目录 mtime 变化才重新发现目录；结果不可修改，适合一次取得供循环复用。

    cleanse.max 是该词条配置基准，preview max/maxUpgrade 保留原样；
    部位 100/150 为业务归一化满分，不等于直接把所有属性 max 乘 1.5。
    core_codes 来自突破条件；a_codes 按业务约定加攻击；均待当前 Runtime 比对。
    items_by_base_id 保留原始本体配置及 cleanseUpgradeCondition，不将策略目标
    改写为客户端准入，也不将不同本体的条件合并成一个通用门槛。
    """
    root = resolve_fanxiu_export_root(export_root)
    cfg = root / 'by_source/lscripts/generate/cfg'
    stamp = cfg.stat().st_mtime_ns if cfg.is_dir() else 0
    paths = _discover(str(root), stamp, str(Path(spirit_config_dir).resolve()) if spirit_config_dir else '')
    signatures = []
    for path in paths:
        stat = path.stat()
        signatures.append((str(path), stat.st_mtime_ns, stat.st_size))
    return _load(tuple(signatures))


def spirit_artifact_ware_ids() -> frozenset[int]:
    """正式配置支持的编号集合；不是已解锁、已装配或已自然加载集合。"""
    return frozenset(load_spirit_artifact_wash_rules()['wares'])
