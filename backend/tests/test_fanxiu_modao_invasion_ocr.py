import pytest

from backend.api.fanxiu import (
    _build_modao_invasion_exchange_items_from_ocr_document,
    _build_modao_invasion_personal_rankings_from_ocr_document,
    _build_shouyuan_exploration_income_speed_from_ocr_document,
)


def _shape(text: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "label": f'{{"text":"{text}"}}',
        "points": [[x1, y1], [x2, y2]],
        "group_id": None,
        "shape_type": "rectangle",
        "flags": {},
    }


def test_modao_invasion_ocr_extracts_multiple_exchange_rows_from_one_screenshot() -> None:
    document = {
        "shapes": [
            _shape("兑换宝阁", 120, 26, 308, 78),
            _shape("当前拥有位面魔晶：", 42, 108, 258, 142),
            _shape("144865", 298, 108, 392, 142),
            _shape("活动期间累计位面魔晶：", 38, 154, 326, 188),
            _shape("144865", 360, 154, 454, 188),
            _shape("建木果·绝品", 96, 244, 316, 278),
            _shape("活动内限购：20", 474, 244, 650, 278),
            _shape("所需：", 110, 288, 182, 322),
            _shape("1000", 264, 288, 338, 322),
            _shape("5折", 28, 372, 74, 412),
            _shape("20", 52, 404, 86, 434),
            _shape("通玄残简·大罗", 96, 372, 352, 412),
            _shape("活动内限购：4", 500, 372, 644, 412),
            _shape("所需：", 110, 420, 182, 454),
            _shape("5000", 264, 420, 338, 454),
            _shape("10000", 388, 420, 482, 454),
        ],
    }

    items, lines = _build_modao_invasion_exchange_items_from_ocr_document(document)

    assert [item["name"] for item in items] == ["建木果·绝品", "通玄残简·大罗"]
    assert [item["magic_crystal_cost"] for item in items] == [1000, 5000]
    assert [item["purchase_limit"] for item in items] == [20, 4]
    assert any("当前拥有位面魔晶" in line for line in lines)
    assert any("通玄残简·大罗" in line for line in lines)


def test_modao_invasion_ocr_ignores_strikethrough_original_price_when_ocr_merges_numbers() -> None:
    document = {
        "shapes": [
            _shape("5折", 28, 372, 74, 412),
            _shape("20", 52, 404, 86, 434),
            _shape("通玄残简·大罗", 96, 372, 352, 412),
            _shape("活动内限购：4", 500, 372, 644, 412),
            _shape("所需：", 110, 420, 182, 454),
            _shape("500010000", 264, 420, 482, 454),
        ],
    }

    items, _ = _build_modao_invasion_exchange_items_from_ocr_document(document)

    assert len(items) == 1
    assert items[0]["name"] == "通玄残简·大罗"
    assert items[0]["magic_crystal_cost"] == 5000
    assert items[0]["purchase_limit"] == 4


def test_modao_invasion_ocr_raises_when_no_exchange_rows_are_found() -> None:
    document = {
        "shapes": [
            _shape("兑换宝阁", 120, 26, 308, 78),
            _shape("当前拥有位面魔晶：", 42, 108, 258, 142),
            _shape("144865", 298, 108, 392, 142),
        ],
    }

    with pytest.raises(ValueError, match="未能从截图中识别可导入的兑换条目"):
        _build_modao_invasion_exchange_items_from_ocr_document(document)


def test_modao_invasion_personal_ranking_ocr_extracts_rank_name_plane_and_merit() -> None:
    document = {
        "shapes": [
            _shape("1", 20, 80, 52, 126),
            _shape("福泽丶叶秋", 94, 80, 286, 126),
            _shape("除魔功勋：", 386, 80, 556, 126),
            _shape("20443646", 580, 80, 736, 126),
            _shape("福泽天下", 96, 126, 248, 164),
            _shape("3", 20, 182, 52, 228),
            _shape("春风、子墨尘心", 94, 182, 336, 228),
            _shape("除魔功勋：", 386, 182, 556, 228),
            _shape("8616919", 580, 182, 712, 228),
            _shape("心向往之", 96, 228, 236, 266),
        ],
    }

    items, lines = _build_modao_invasion_personal_rankings_from_ocr_document(document)

    assert [item["rank"] for item in items] == [1, 3]
    assert [item["name"] for item in items] == ["福泽丶叶秋", "春风、子墨尘心"]
    assert [item["plane"] for item in items] == ["福泽天下", "心向往之"]
    assert [item["merit"] for item in items] == [20443646, 8616919]
    assert any("福泽丶叶秋" in line for line in lines)
    assert any("心向往之" in line for line in lines)


def test_modao_invasion_personal_ranking_ocr_raises_when_no_score_rows_are_found() -> None:
    document = {
        "shapes": [
            _shape("个人榜", 120, 26, 308, 78),
            _shape("福泽天下", 96, 126, 248, 164),
        ],
    }

    with pytest.raises(ValueError, match="未能从截图中识别可导入的个人榜名次"):
        _build_modao_invasion_personal_rankings_from_ocr_document(document)


def test_shouyuan_exploration_income_speed_ocr_extracts_summary_values() -> None:
    document = {
        "shapes": [
            _shape("第96次探查", 48, 200, 210, 236),
            _shape("寻后获得了大量丹药。(积分+144)", 48, 246, 430, 282),
            _shape("第100次探查", 48, 590, 230, 626),
            _shape("寻后获得了大量丹药。(积分+185功勋+300)", 48, 636, 560, 672),
            _shape("总共获得宝物", 48, 724, 240, 760),
            _shape("15099", 48, 806, 126, 838),
            _shape("1", 170, 806, 190, 838),
            _shape("14", 274, 806, 312, 838),
            _shape("总共获得积分：", 48, 874, 270, 910),
            _shape("27987", 320, 874, 404, 910),
            _shape("总共获得功勋：", 48, 950, 270, 986),
            _shape("27100", 320, 950, 404, 986),
        ],
    }

    item, lines = _build_shouyuan_exploration_income_speed_from_ocr_document(document)

    assert item["search_count"] == 100
    assert item["beast_crystal"] == 15099
    assert item["score"] == 27987
    assert item["merit"] == 27100
    assert item["remark"] == ""
    assert item["captured_date"]
    assert any("总共获得宝物" in line for line in lines)

