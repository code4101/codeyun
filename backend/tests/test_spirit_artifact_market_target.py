"""静态目录关联与授权边界；不模拟Runtime、画面或游戏动作。"""
from dataclasses import replace

import pytest

from backend.core.fanxiu.catalog.spirit_artifact_market import project_spirit_artifact_market_targets
from backend.core.fanxiu.catalog.resources import FanxiuResourceError
from backend.core.fanxiu.data_annotation.tasks import spirit_artifact_reset_target as target_api
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_preparation import SpiritArtifactStageCandidate
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_reset_plan import (
    SpiritArtifactResetPlanEntry, SpiritArtifactResetSupply,
)


# 正式ExchangeShop 40014及对应SpiritWareItem/Item的最小稳定解析字段。
ROW = dict(goodsId=40014, itemId=14001506, scopeType=6, type=3,
           secondTag=10, goodsNum=1, cost=['Item|15100001_80'])
ITEMS = {14001506: dict(type=3, parts=3, quality=6)}
CARDS = {'14001506': dict(id=14001506, name='弥罗宝光幢·环', quality=6)}


def test_catalog_resolves_names_identity_and_price_from_rows():
    target, = project_spirit_artifact_market_targets([ROW], ITEMS, CARDS)
    assert (target.ware_id, target.part, target.base_id, target.goods_id) == (3, 3, 14001506, 40014)
    assert (target.artifact_name, target.name, target.cost_item_id, target.unit_price) == (
        '弥罗宝光幢', '弥罗宝光幢·环', 15100001, 80)
    changed, = project_spirit_artifact_market_targets([{**ROW, 'cost': ['Item|15100001_90']}], ITEMS, CARDS)
    assert changed.unit_price == 90
    assert project_spirit_artifact_market_targets([{**ROW, 'secondTag': 11}], ITEMS, CARDS) == ()


@pytest.mark.parametrize('rows', [
    [ROW, {**ROW, 'goodsId': 999}],
    [{**ROW, 'cost': ['Item|15100001_80', 'Item|1_1']}],
    [{**ROW, 'goodsNum': 2}],
])
def test_ambiguous_or_unsupported_supply_is_not_silently_selected(rows):
    with pytest.raises(FanxiuResourceError):
        project_spirit_artifact_market_targets(rows, ITEMS, CARDS)


def test_preparation_preserves_authorization_and_current_plan(monkeypatch):
    candidates = project_spirit_artifact_market_targets([ROW], ITEMS, CARDS)
    monkeypatch.setattr(target_api, 'load_spirit_artifact_market_targets', lambda: candidates)
    entry = SpiritArtifactResetPlanEntry(
        SpiritArtifactStageCandidate('错升', 3, 3, 'ready'), 'old', 14001506, 5,
        supplies=(SpiritArtifactResetSupply('market', 14001506, 40014, 1, 15100001, 80),),
        action='obtain_raw')
    result = target_api.prepare_spirit_artifact_market_reset(entry, cost_item_id=15100001, currency_limit=80)
    assert result.entry is entry
    assert (result.request.goods_id, result.request.name, result.request.currency_limit) == (
        40014, '弥罗宝光幢·环', 80)
    for candidate, currency, budget in (
        (entry, 15100001, 79), (entry, 1, 80),
        (replace(entry, base_id=14002106), 15100001, 80),
        (replace(entry, supplies=()), 15100001, 80),
        (replace(entry, action='choose_source'), 15100001, 80),
    ):
        with pytest.raises(ValueError):
            target_api.prepare_spirit_artifact_market_reset(candidate, cost_item_id=currency, currency_limit=budget)
