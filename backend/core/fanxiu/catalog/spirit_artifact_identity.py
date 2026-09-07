"""灵器名称与六部位：正式 SpiritWare.inactivatedParts → Item.name → 语言表。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from .resources import resolve_fanxiu_export_root, FanxiuResourceError
from .lua_config import parse_fanxiu_generated_lua_config, load_default_fanxiu_lang_map


@lru_cache(maxsize=4)
def _tables(signatures):
    return tuple(parse_fanxiu_generated_lua_config(filename)['rows'] for filename, _, _ in signatures)


def load_spirit_artifact_templates() -> dict[int, tuple[str, tuple[str, ...]]]:
    """只读正式导出；名称缺失、多个版本同 ID 名称冲突时拒绝猜测。

    返回编号到（官方灵器名、按 inactivatedParts 排序的部位名）。不枚举背包，
    不把模板存在当作游戏已解锁或已装配。源文件变化会重新解析。
    """
    root = resolve_fanxiu_export_root()
    cfg = root / 'by_source/lscripts/generate/cfg'
    ware_paths = list(cfg.glob('spiritware_*/text_assets/SpiritWare.lua'))
    if len(ware_paths) != 1:
        raise FanxiuResourceError('SpiritWare 正式导出必须唯一')
    item_paths = sorted(cfg.glob('item*/text_assets/Item.lua'))
    if not item_paths:
        raise FanxiuResourceError('缺少 Item 正式导出')
    signatures = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in [*ware_paths, *item_paths])
    ware_rows, *item_tables = _tables(signatures)
    lang = load_default_fanxiu_lang_map(root)
    wanted = {item_id for row in ware_rows for item_id in row['inactivatedParts']}
    names = {}
    for table in item_tables:
        for row in table:
            item_id = row.get('id')
            if item_id not in wanted:
                continue
            raw_name = row.get('name')
            name = lang.get(raw_name) if isinstance(raw_name, int) else raw_name
            if not isinstance(name, str) or not name:
                raise FanxiuResourceError(f'灵器部位 {item_id} 缺少名称')
            if item_id in names and names[item_id] != name:
                raise FanxiuResourceError(f'多个 Item 版本对灵器部位 {item_id} 名称不一致')
            names[item_id] = name
    result = {}
    for row in ware_rows:
        raw_name = row['name']
        name = lang.get(raw_name) if isinstance(raw_name, int) else raw_name
        ids = row['inactivatedParts']
        if not name or len(ids) != 6 or len(set(ids)) != 6:
            raise FanxiuResourceError('灵器模板名称或六部位不完整')
        part_names = []
        for item_id in ids:
            full = names.get(item_id, '')
            if not full.startswith(name + '·'):
                raise FanxiuResourceError(f'灵器部位名称不能关联本器：{item_id}')
            part_names.append(full[len(name)+1:])
        result[row['type']] = (name, tuple(part_names))
    return result
