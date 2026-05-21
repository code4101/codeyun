import numpy as np

import backend.api.fanxiu as fanxiu_api
from backend.api.fanxiu import (
    SPIRIT_ARTIFACT_CARD_REGIONS,
    _build_spirit_artifact_attribute_recognition,
    _build_spirit_artifact_market_recognition,
    _build_spirit_artifact_rank_recognition,
    _build_spirit_artifact_storage_bag_recognition,
    _fill_missing_spirit_artifact_ranks_from_card_crops,
)


def _shape(text: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "label": f'{{"text":"{text}"}}',
        "points": [[x1, y1], [x2, y2]],
        "group_id": None,
        "shape_type": "rectangle",
        "flags": {},
    }


def _fill_region(frame: np.ndarray, region: tuple[float, float, float, float], bgr: tuple[int, int, int]) -> None:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = region
    frame[int(height * y1):int(height * y2), int(width * x1):int(width * x2)] = bgr


def test_spirit_artifact_rank_recognition_maps_left_and_right_parts() -> None:
    frame = np.zeros((1200, 1000, 3), dtype=np.uint8)
    for index, region in enumerate(SPIRIT_ARTIFACT_CARD_REGIONS):
        _fill_region(frame, region, (0, 220, 220) if index == 3 else (0, 0, 220))

    document = {
        "shapes": [
            _shape("4阶", 180, 280, 230, 310),
            _shape("8阶", 180, 425, 230, 455),
            _shape("4阶", 180, 570, 230, 600),
            _shape("11阶", 650, 425, 720, 455),
            _shape("10阶", 650, 570, 720, 600),
            _shape("灵器效果", 435, 705, 560, 735),
            _shape("血晶摩诃剑部件", 230, 780, 430, 810),
        ],
    }

    result = _build_spirit_artifact_rank_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "血晶摩诃剑"
    assert [(item["part_name"], item["rank"], item["realm"]) for item in result["parts"]] == [
        ("柄", 4, 0),
        ("刃", 8, 0),
        ("穗", 4, 0),
        ("鞘", 0, 0),
        ("珠", 11, 0),
        ("纹", 10, 0),
    ]
    assert result["parts"][3]["quality"] == "yellow"


def test_spirit_artifact_rank_recognition_skips_wrong_scene() -> None:
    frame = np.zeros((1200, 1000, 3), dtype=np.uint8)
    document = {"shapes": [_shape("血晶摩诃剑部件", 230, 780, 430, 810)]}

    result = _build_spirit_artifact_rank_recognition(document, frame)

    assert result["matched"] is False
    assert result["reason"] == "未识别到灵器效果，已跳过"
    assert result["parts"] == []


def test_spirit_artifact_rank_recognition_keeps_blue_purple_realm_zero() -> None:
    frame = np.zeros((1200, 1000, 3), dtype=np.uint8)
    for region in SPIRIT_ARTIFACT_CARD_REGIONS:
        _fill_region(frame, region, (220, 80, 170))

    document = {
        "shapes": [
            _shape("11阶", 180, 280, 250, 310),
            _shape("14阶", 180, 425, 250, 455),
            _shape("4阶", 180, 570, 230, 600),
            _shape("8阶", 650, 280, 700, 310),
            _shape("8阶", 650, 425, 700, 455),
            _shape("6阶", 650, 570, 700, 600),
            _shape("灵器效果", 435, 705, 560, 735),
            _shape("弥罗宝光幢部件", 230, 780, 430, 810),
        ],
    }

    result = _build_spirit_artifact_rank_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "弥罗宝光幢"
    assert [(item["rank"], item["realm"], item["quality"]) for item in result["parts"]] == [
        (11, 0, "blue_purple"),
        (14, 0, "blue_purple"),
        (4, 0, "blue_purple"),
        (8, 0, "blue_purple"),
        (8, 0, "blue_purple"),
        (6, 0, "blue_purple"),
    ]


def test_spirit_artifact_rank_recognition_ignores_unlisted_colors() -> None:
    frame = np.zeros((1200, 1000, 3), dtype=np.uint8)
    for region in SPIRIT_ARTIFACT_CARD_REGIONS:
        _fill_region(frame, region, (0, 180, 0))

    document = {
        "shapes": [
            _shape("9阶", 180, 280, 230, 310),
            _shape("8阶", 180, 425, 230, 455),
            _shape("7阶", 180, 570, 230, 600),
            _shape("6阶", 650, 280, 700, 310),
            _shape("5阶", 650, 425, 700, 455),
            _shape("4阶", 650, 570, 700, 600),
            _shape("灵器效果", 435, 705, 560, 735),
            _shape("青冥岁月灯部件", 230, 780, 430, 810),
        ],
    }

    result = _build_spirit_artifact_rank_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "青暝岁月灯"
    assert [(item["rank"], item["realm"], item["quality"]) for item in result["parts"]] == [
        (0, 0, "unknown"),
        (0, 0, "unknown"),
        (0, 0, "unknown"),
        (0, 0, "unknown"),
        (0, 0, "unknown"),
        (0, 0, "unknown"),
    ]


def test_spirit_artifact_rank_recognition_matches_fuzzy_artifact_name() -> None:
    frame = np.zeros((1200, 1000, 3), dtype=np.uint8)
    for region in SPIRIT_ARTIFACT_CARD_REGIONS:
        _fill_region(frame, region, (0, 0, 220))

    document = {
        "shapes": [
            _shape("3阶", 180, 280, 230, 310),
            _shape("灵器效果", 435, 705, 560, 735),
            _shape("青瞑岁月灯部件", 230, 780, 430, 810),
        ],
    }

    result = _build_spirit_artifact_rank_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "青暝岁月灯"
    assert result["parts"][0]["part_name"] == "盏"
    assert result["parts"][0]["rank"] == 3


def test_spirit_artifact_crop_fallback_only_runs_for_ranked_qualities(monkeypatch) -> None:
    frame = np.zeros((1200, 1000, 3), dtype=np.uint8)
    calls = 0

    def fake_extract(*args, **kwargs) -> int:
        nonlocal calls
        calls += 1
        return 9

    monkeypatch.setattr(fanxiu_api, "_extract_spirit_artifact_card_rank_from_crop", fake_extract)
    payload = {
        "matched": True,
        "parts": [
            {"rank": 0, "quality": "unknown"},
            {"rank": 0, "quality": "yellow"},
            {"rank": 0, "quality": "red"},
            {"rank": 0, "quality": "blue_purple"},
            {"rank": 3, "quality": "red"},
            {"rank": 0, "quality": "green"},
        ],
    }

    result = _fill_missing_spirit_artifact_ranks_from_card_crops(payload, frame)

    assert calls == 2
    assert [item["rank"] for item in result["parts"]] == [0, 0, 9, 9, 3, 0]


def test_spirit_artifact_attribute_recognition_converts_values_to_percent() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("血晶摩诃剑·柄自动洗炼", 350, 600, 620, 630),
            _shape("当前附加属性", 220, 680, 360, 710),
            _shape("最新附加属性", 600, 680, 740, 710),
            _shape("攻击：6748", 210, 735, 330, 765),
            _shape("灵力：79.59万", 210, 790, 360, 820),
            _shape("暴击：19731", 210, 845, 340, 875),
            _shape("通过洗炼获得", 600, 845, 760, 875),
            _shape("暴击附伤：6712", 210, 900, 380, 930),
            _shape("普攻增伤：20742", 210, 955, 390, 985),
            _shape("防御：11200", 210, 1010, 350, 1040),
        ],
    }

    result = _build_spirit_artifact_attribute_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "血晶摩诃剑"
    assert result["part_name"] == "柄"
    assert result["common_stats"] == {
        "attack": "67%",
        "spirit_power": "66%",
        "defense": "112%",
    }
    assert result["exclusive_stats"] == {
        "暴击": "66%",
        "暴击附伤": "67%",
    }
    assert {item["label"] for item in result["attributes"]} == {"攻击", "灵力", "暴击", "暴击附伤", "守御"}


def test_spirit_artifact_attribute_recognition_reads_peerless_slots() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("弥罗宝光幢·焰自动洗炼", 350, 600, 620, 630),
            _shape("当前附加属性", 220, 680, 360, 710),
            _shape("灵器无双：25%", 210, 735, 360, 765),
            _shape("灵器无双：30%", 210, 790, 360, 820),
            _shape("法宝附伤：60000", 210, 845, 390, 875),
        ],
    }

    result = _build_spirit_artifact_attribute_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "弥罗宝光幢"
    assert result["part_name"] == "焰"
    assert result["artifact_peerless_1"] == 25
    assert result["artifact_peerless_2"] == 30
    assert result["exclusive_stats"] == {"法宝附伤": "100%"}


def test_spirit_artifact_attribute_recognition_ignores_quality_prefixes() -> None:
    frame = np.zeros((1500, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("血晶摩诃剑·刃自动洗炼", 350, 600, 620, 630),
            _shape("当前附加属性", 220, 680, 360, 710),
            _shape("满攻击：10000", 210, 735, 360, 765),
            _shape("满灵力：120万", 210, 790, 360, 820),
            _shape("满暴击：30000", 210, 845, 360, 875),
            _shape("巅暴击附伤：10000", 210, 900, 410, 930),
            _shape("满灵器无双：25%", 210, 955, 390, 985),
            _shape("气血：76.95万", 210, 1010, 360, 1040),
            _shape("满守御：10000", 210, 1055, 360, 1085),
        ],
    }

    result = _build_spirit_artifact_attribute_recognition(document, frame)

    assert result["matched"] is True
    assert result["artifact_name"] == "血晶摩诃剑"
    assert result["part_name"] == "刃"
    assert result["artifact_peerless_1"] == 25
    assert result["common_stats"] == {
        "attack": "100%",
        "spirit_power": "100%",
        "health": "64%",
        "defense": "100%",
    }
    assert result["exclusive_stats"] == {
        "暴击": "100%",
        "暴击附伤": "100%",
    }


def test_spirit_artifact_market_recognition_extracts_visible_items() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("珍宝阁", 230, 170, 360, 220),
            _shape("灵器", 390, 250, 460, 290),
            _shape("835", 820, 55, 890, 85),
            _shape("血晶摩诃剑·珠", 330, 390, 520, 420),
            _shape("兑换所需：80", 330, 445, 500, 475),
            _shape("血晶摩诃剑·纹", 330, 520, 520, 550),
            _shape("兑换所需：80", 330, 575, 500, 605),
            _shape("天月落星幡·印", 330, 650, 540, 680),
            _shape("兑换所需：80", 330, 705, 500, 735),
        ],
    }

    result = _build_spirit_artifact_market_recognition(document, frame)

    assert result["matched"] is True
    assert result["market_currency_count"] == 835
    assert result["items"] == [
        {"order": 1, "artifact_name": "血晶摩诃剑", "part_name": "珠", "cost": 80},
        {"order": 2, "artifact_name": "血晶摩诃剑", "part_name": "纹", "cost": 80},
        {"order": 3, "artifact_name": "天月落星幡", "part_name": "印", "cost": 80},
    ]


def test_spirit_artifact_market_recognition_skips_wrong_scene() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {"shapes": [_shape("血晶摩诃剑·珠", 330, 390, 520, 420)]}

    result = _build_spirit_artifact_market_recognition(document, frame)

    assert result["matched"] is False
    assert result["reason"] == "未识别到珍宝阁，已跳过"
    assert result["items"] == []


def test_spirit_artifact_storage_bag_recognition_matches_partial_choice_names() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("3", 230, 180, 260, 210),
            _shape("灵器部件自选箱", 290, 180, 520, 210),
            _shape("摩诃剑珠", 310, 300, 430, 330),
            _shape("落星幡纹", 310, 360, 450, 390),
            _shape("岁月灯荧", 310, 420, 450, 450),
            _shape("回复丹", 310, 480, 430, 510),
        ],
    }

    result = _build_spirit_artifact_storage_bag_recognition(document, frame)

    assert result["matched"] is True
    assert result["items"] == [
        {
            "order": 1,
            "title": "灵器部件自选箱",
            "quantity": 3,
            "choices": [
                {"order": 1, "raw_name": "摩诃剑珠", "artifact_name": "血晶摩诃剑", "part_name": "珠"},
                {"order": 2, "raw_name": "落星幡纹", "artifact_name": "天月落星幡", "part_name": "纹"},
                {"order": 3, "raw_name": "岁月灯荧", "artifact_name": "青暝岁月灯", "part_name": "荧"},
            ],
        }
    ]


def test_spirit_artifact_storage_bag_recognition_splits_joined_choice_line() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("弥罗自选宝匣·贰", 280, 180, 520, 210),
            _shape("从以下列表中选择奖励", 260, 260, 560, 290),
            _shape("弥罗宝光幢·座弥罗宝光幢·珠弥罗宝光幢·纹", 250, 340, 760, 370),
        ],
    }

    result = _build_spirit_artifact_storage_bag_recognition(document, frame)

    assert result["matched"] is True
    assert result["items"][0]["title"] == "弥罗自选宝匣·贰"
    assert result["items"][0]["choices"] == [
        {"order": 1, "raw_name": "弥罗宝光幢·座", "artifact_name": "弥罗宝光幢", "part_name": "座"},
        {"order": 2, "raw_name": "弥罗宝光幢·珠", "artifact_name": "弥罗宝光幢", "part_name": "珠"},
        {"order": 3, "raw_name": "弥罗宝光幢·纹", "artifact_name": "弥罗宝光幢", "part_name": "纹"},
    ]


def test_spirit_artifact_storage_bag_recognition_matches_realm_upgrade_box() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {
        "shapes": [
            _shape("弥罗升品宝匣·壹", 280, 180, 520, 210),
            _shape("境界要求：无限制", 270, 235, 520, 265),
            _shape("装有灵器升品石的宝匣", 270, 290, 560, 320),
            _shape("从以下列表中选择奖励", 260, 360, 560, 390),
            _shape("宝光幢焰曜仙镜宝光幢柱曜仙镜宝光幢环曜仙镜", 250, 450, 760, 480),
        ],
    }

    result = _build_spirit_artifact_storage_bag_recognition(document, frame)

    assert result["matched"] is True
    assert result["items"][0]["title"] == "弥罗升品宝匣·壹"
    assert result["items"][0]["choices"] == [
        {"order": 1, "raw_name": "宝光幢·焰曜仙镜", "artifact_name": "弥罗宝光幢", "part_name": "焰"},
        {"order": 2, "raw_name": "宝光幢·柱曜仙镜", "artifact_name": "弥罗宝光幢", "part_name": "柱"},
        {"order": 3, "raw_name": "宝光幢·环曜仙镜", "artifact_name": "弥罗宝光幢", "part_name": "环"},
    ]


def test_spirit_artifact_storage_bag_recognition_skips_wrong_scene() -> None:
    frame = np.zeros((1400, 1000, 3), dtype=np.uint8)
    document = {"shapes": [_shape("血晶摩诃剑·珠", 330, 390, 520, 420)]}

    result = _build_spirit_artifact_storage_bag_recognition(document, frame)

    assert result["matched"] is False
    assert result["reason"] == "未识别到自选箱标题"
    assert result["items"] == []
