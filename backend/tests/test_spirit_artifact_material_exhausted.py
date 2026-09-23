"""洗灵材料库存分类的纯函数契约（不模拟游戏画面）。

零库存是纯供给事实，属于预算暂停；目录里没有该道具或同 ID 多行说明当前
灵器/页面与预期不一致，必须继续抛错。分类只在 resolve_advanced_item_stock
一处完成，run_a_collection 的端到端暂停行为须在真实游戏上验收。
"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_cleanse import (
    SpiritArtifactCleanseBlocked,
    SpiritArtifactCleanseErrorCode,
    resolve_advanced_item_stock,
)


def test_zero_stock_is_material_exhausted():
    catalog = {"items": [{"item": 14000006, "name": "洗灵·引仙石", "count": 0}]}
    with pytest.raises(SpiritArtifactCleanseBlocked) as excinfo:
        resolve_advanced_item_stock(catalog, 14000006)
    assert excinfo.value.code == SpiritArtifactCleanseErrorCode.MATERIAL_EXHAUSTED
    assert excinfo.value.evidence["item"] == 14000006
    assert excinfo.value.evidence["count"] == 0


def test_missing_item_is_not_treated_as_exhausted():
    catalog = {"items": [{"item": 14000007, "name": "洗灵·精炼石", "count": 33}]}
    with pytest.raises(SpiritArtifactCleanseBlocked) as excinfo:
        resolve_advanced_item_stock(catalog, 14000006)
    assert excinfo.value.code != SpiritArtifactCleanseErrorCode.MATERIAL_EXHAUSTED


def test_ambiguous_item_is_not_treated_as_exhausted():
    catalog = {"items": [
        {"item": 14000006, "name": "洗灵·引仙石", "count": 1},
        {"item": 14000006, "name": "洗灵·引仙石", "count": 1},
    ]}
    with pytest.raises(SpiritArtifactCleanseBlocked) as excinfo:
        resolve_advanced_item_stock(catalog, 14000006)
    assert excinfo.value.code != SpiritArtifactCleanseErrorCode.MATERIAL_EXHAUSTED


def test_available_stock_returns_the_catalog_row():
    row = {"item": 14000006, "name": "洗灵·引仙石", "count": 2}
    assert resolve_advanced_item_stock({"items": [row]}, 14000006) is row
