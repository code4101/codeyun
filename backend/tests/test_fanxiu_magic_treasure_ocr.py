from backend.api.fanxiu import _build_magic_treasure_item_from_ocr_document


def _shape(text: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "label": f'{{"text":"{text}"}}',
        "points": [[x1, y1], [x2, y2]],
        "group_id": None,
        "shape_type": "rectangle",
        "flags": {},
    }


def test_magic_treasure_ocr_ignores_far_right_fabao_origin_label() -> None:
    document = {
        "shapes": [
            _shape("灵", 86, 42, 112, 68),
            _shape("璃水珠", 126, 40, 244, 70),
            _shape("法宝来历", 520, 36, 636, 70),
            _shape("品质：", 112, 88, 168, 114),
            _shape("仙品五星", 176, 88, 286, 114),
            _shape("品阶：", 112, 128, 168, 154),
            _shape("二十一阶", 176, 128, 288, 154),
            _shape("二十二阶", 364, 128, 478, 154),
        ],
    }

    item, lines = _build_magic_treasure_item_from_ocr_document(document)

    assert item["type"] == "灵力"
    assert item["name"] == "璃水珠"
    assert item["quality"] == 6
    assert item["rank"] == 21
    assert any("法宝来历" in line for line in lines)


def test_magic_treasure_ocr_ignores_far_right_partial_fabao_origin_fragments() -> None:
    document = {
        "shapes": [
            _shape("攻", 86, 42, 112, 68),
            _shape("乌龙夺", 126, 40, 248, 70),
            _shape("法宝来", 458, 38, 548, 68),
            _shape("法宝来", 556, 38, 646, 68),
            _shape("品质：", 112, 88, 168, 114),
            _shape("仙品五星", 176, 88, 286, 114),
            _shape("品阶：", 112, 128, 168, 154),
            _shape("二十三阶", 176, 128, 288, 154),
            _shape("二十四阶", 364, 128, 478, 154),
        ],
    }

    item, _ = _build_magic_treasure_item_from_ocr_document(document)

    assert item["type"] == "攻击"
    assert item["name"] == "乌龙夺"
    assert item["quality"] == 6
    assert item["rank"] == 23


def test_magic_treasure_ocr_hard_trims_repeated_fabao_suffix() -> None:
    document = {
        "shapes": [
            _shape("防", 86, 42, 112, 68),
            _shape("观天镜", 126, 40, 248, 70),
            _shape("法宝", 458, 38, 518, 68),
            _shape("法宝", 524, 38, 584, 68),
            _shape("品质：", 112, 88, 168, 114),
            _shape("仙品五星", 176, 88, 286, 114),
            _shape("品阶：", 112, 128, 168, 154),
            _shape("二十三阶", 176, 128, 288, 154),
            _shape("二十四阶", 364, 128, 478, 154),
        ],
    }

    item, _ = _build_magic_treasure_item_from_ocr_document(document)

    assert item["type"] == "防御"
    assert item["name"] == "观天镜"


def test_magic_treasure_ocr_picks_leftmost_quality_when_next_level_is_visible() -> None:
    document = {
        "shapes": [
            _shape("攻", 86, 42, 112, 68),
            _shape("武生典藏戏偶", 126, 40, 318, 70),
            _shape("法宝来历", 520, 36, 636, 70),
            _shape("品质：", 112, 88, 168, 114),
            _shape("仙品一星", 176, 88, 286, 114),
            _shape("仙品二星", 364, 88, 474, 114),
            _shape("品阶：", 112, 128, 168, 154),
            _shape("八阶", 176, 128, 236, 154),
            _shape("九阶", 364, 128, 424, 154),
        ],
    }

    item, _ = _build_magic_treasure_item_from_ocr_document(document)

    assert item["type"] == "攻击"
    assert item["name"] == "武生典藏戏偶"
    assert item["quality"] == 2
    assert item["rank"] == 8


def test_magic_treasure_ocr_treats_yuanman_rank_as_one() -> None:
    document = {
        "shapes": [
            _shape("辅", 86, 42, 112, 68),
            _shape("明光灯", 126, 40, 248, 70),
            _shape("品质：", 112, 88, 168, 114),
            _shape("珍品", 176, 88, 246, 114),
            _shape("品阶：", 112, 128, 168, 154),
            _shape("圆满", 176, 128, 236, 154),
        ],
    }

    item, _ = _build_magic_treasure_item_from_ocr_document(document)

    assert item["type"] == "辅助"
    assert item["name"] == "明光灯"
    assert item["quality"] == 0
    assert item["rank"] == 1


def test_magic_treasure_ocr_treats_plain_shenpin_as_first_star() -> None:
    document = {
        "shapes": [
            _shape("灵", 86, 42, 112, 68),
            _shape("古·虚天鼎", 126, 40, 286, 70),
            _shape("品质：", 112, 88, 168, 114),
            _shape("神品", 176, 88, 246, 114),
            _shape("品阶：", 112, 128, 168, 154),
            _shape("六十三阶", 176, 128, 300, 154),
        ],
    }

    item, _ = _build_magic_treasure_item_from_ocr_document(document)

    assert item["type"] == "灵力"
    assert item["name"] == "古·虚天鼎"
    assert item["quality"] == 8
    assert item["rank"] == 63
