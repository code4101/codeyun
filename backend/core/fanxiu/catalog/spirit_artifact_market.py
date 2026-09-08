"""珍宝阁红本体静态目录；只解析正式导出，不声明当前可见、可买或库存数量。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .item import load_fanxiu_item_runtime_index
from .lua_config import parse_fanxiu_generated_lua_config
from .resources import FanxiuResourceError, resolve_fanxiu_export_root
from .spirit_artifact_wash_rules import load_spirit_artifact_wash_rules


@dataclass(frozen=True)
class SpiritArtifactMarketTarget:
    ware_id: int
    part: int
    base_id: int
    goods_id: int
    name: str
    artifact_name: str
    cost_item_id: int
    unit_price: int


def project_spirit_artifact_market_targets(
    shop_rows: Sequence[Mapping[str, Any]],
    items: Mapping[int, Mapping[str, Any]],
    cards: Mapping[str, Mapping[str, Any]],
) -> tuple[SpiritArtifactMarketTarget, ...]:
    """纯关联已解析 ExchangeShop/SpiritWareItem/Item；歧义及不支持费用报错。

    珍宝阁当前导出路由为 scopeType=6/type=3/secondTag=10，只支持一次一件
    红本体和单种物品货币。价格、商品ID、部件名称均来自目录，不写死80或商品表。
    这是来源候选，不可转换成 available=1 或跳过 Runtime 购买详情门禁。
    """
    result, positions, goods_ids = [], set(), set()
    for row in shop_rows:
        if (row.get('scopeType'), row.get('type'), row.get('secondTag')) != (6, 3, 10):
            continue
        base_id = row.get('itemId')
        item = items.get(base_id)
        if item is None or item.get('quality') != 6:
            continue
        ware, part, goods = item.get('type'), item.get('parts'), row.get('goodsId')
        costs = row.get('cost')
        cost = (re.fullmatch(r'Item\|([1-9][0-9]*)_([1-9][0-9]*)', costs[0])
                if isinstance(costs, (list, tuple)) and len(costs) == 1
                and isinstance(costs[0], str) else None)
        if (any(type(value) is not int or value <= 0 for value in (ware, part, goods, base_id))
                or part not in range(1, 7) or row.get('goodsNum') != 1 or cost is None):
            raise FanxiuResourceError('珍宝阁本体身份、数量或费用结构不支持')
        card = cards.get(str(base_id), {})
        name = card.get('name')
        if (card.get('id') != base_id or card.get('quality') != 6
                or not isinstance(name, str) or name.count('·') != 1
                or any(not segment.strip() for segment in name.split('·'))):
            raise FanxiuResourceError('红本体物品名称/品质无法与正式目录关联')
        if (ware, part) in positions or goods in goods_ids:
            raise FanxiuResourceError('同部位市场商品或商品ID重复，不能自行选购')
        positions.add((ware, part))
        goods_ids.add(goods)
        result.append(SpiritArtifactMarketTarget(
            ware, part, base_id, goods, name, name.split('·')[0],
            int(cost[1]), int(cost[2])))
    return tuple(sorted(result, key=lambda target: (target.ware_id, target.part)))


@lru_cache(maxsize=4)
def _exchange_rows(filename: str, stamp: int, size: int) -> tuple[Mapping[str, Any], ...]:
    path = Path(filename)
    rows = parse_fanxiu_generated_lua_config(path)['rows']
    current = path.stat()
    if (current.st_mtime_ns, current.st_size) != (stamp, size):
        raise FanxiuResourceError('解析期间ExchangeShop导出变化')
    return tuple(rows)


def load_spirit_artifact_market_targets(*, export_root: str | Path | None = None
                                      ) -> tuple[SpiritArtifactMarketTarget, ...]:
    """加载静态候选；缺失/多版本报错，不隐式导出、写文件或访问游戏。

    只缓存ExchangeShop解析结果，源文件mtime/size变化后重解析；灵器规则和
    物品目录复用各自公共加载器的失效机制。当前可购性由动作提供方重新观察。
    """
    root = resolve_fanxiu_export_root(export_root)
    paths = list((root / 'by_source/lscripts/generate/cfg').glob(
        'commonshop_*/text_assets/ExchangeShop.lua'))
    if len(paths) != 1:
        raise FanxiuResourceError(f'ExchangeShop正式导出必须唯一，实际{len(paths)}')
    path = paths[0]
    stat = path.stat()
    return project_spirit_artifact_market_targets(
        _exchange_rows(str(path), stat.st_mtime_ns, stat.st_size),
        load_spirit_artifact_wash_rules(export_root=root)['items_by_base_id'],
        load_fanxiu_item_runtime_index(export_root=root, rebuild_missing=False)['cards_by_id'])
