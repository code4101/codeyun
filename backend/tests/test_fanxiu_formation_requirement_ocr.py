from backend.api.fanxiu import (
    _build_formation_effect_details_from_ocr_document,
    _build_formation_requirements_from_ocr_document,
)


def _shape(text: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "label": f'{{"text":"{text}"}}',
        "points": [[x1, y1], [x2, y2]],
        "group_id": None,
        "shape_type": "rectangle",
        "flags": {},
    }


def test_formation_requirement_ocr_builds_multiple_pairs_and_merges_same_condition() -> None:
    document = {
        "shapes": [
            _shape("【东极】", 80, 30, 150, 56),
            _shape("守御+400", 156, 30, 270, 56),
            _shape("入阵1个绝品法宝", 86, 66, 282, 92),
            _shape("【北极】", 80, 122, 150, 148),
            _shape("灵力+72000", 156, 122, 304, 148),
            _shape("入阵法宝的阶数合计十二阶", 86, 158, 390, 184),
            _shape("【阵技·黄泉】", 80, 214, 194, 240),
            _shape("攻击加成+4%", 200, 214, 360, 240),
            _shape("入阵法宝的阶数合计十二阶", 86, 250, 390, 276),
        ],
    }

    requirements, lines = _build_formation_requirements_from_ocr_document(document)

    assert lines == [
        "【东极】守御+400",
        "入阵1个绝品法宝",
        "【北极】灵力+72000",
        "入阵法宝的阶数合计十二阶",
        "【阵技·黄泉】攻击加成+4%",
        "入阵法宝的阶数合计十二阶",
    ]
    assert requirements == [
        {
            "text": "入阵1个绝品法宝",
            "effect_text": "【东极】守御+400",
        },
        {
            "text": "入阵法宝的阶数合计十二阶",
            "effect_text": "【北极】灵力+72000；【阵技·黄泉】攻击加成+4%",
        },
    ]


def test_formation_requirement_ocr_requires_at_least_one_condition_line() -> None:
    document = {
        "shapes": [
            _shape("【东极】", 80, 30, 150, 56),
            _shape("守御+400", 156, 30, 270, 56),
        ],
    }

    try:
        _build_formation_requirements_from_ocr_document(document)
    except ValueError as exc:
        assert str(exc) == "未能从截图中识别触发条件"
    else:
        raise AssertionError("expected ValueError when OCR document has no condition line")


def test_formation_requirement_ocr_supports_dianliang_style_conditions() -> None:
    document = {
        "shapes": [
            _shape("【阵魂·清穹】", 80, 30, 220, 56),
            _shape("净光", 226, 30, 292, 56),
            _shape("点亮四条件法效果激活技能", 86, 66, 350, 92),
            _shape("【阵魂·元灵】", 80, 122, 220, 148),
            _shape("天光", 226, 122, 292, 148),
            _shape("点亮阵法效果【阵技·青天】激活技能", 86, 158, 432, 184),
        ],
    }

    requirements, lines = _build_formation_requirements_from_ocr_document(document)

    assert lines == [
        "【阵魂·清穹】净光",
        "点亮四条件法效果激活技能",
        "【阵魂·元灵】天光",
        "点亮阵法效果【阵技·青天】激活技能",
    ]
    assert requirements == [
        {
            "text": "点亮四条件法效果激活技能",
            "effect_text": "【阵魂·清穹】净光",
        },
        {
            "text": "点亮阵法效果【阵技·青天】激活技能",
            "effect_text": "【阵魂·元灵】天光",
        },
    ]


def test_formation_requirement_ocr_supports_formation_rank_gate_and_strips_progress_suffix() -> None:
    document = {
        "shapes": [
            _shape("【阵魂·青云】", 80, 30, 220, 56),
            _shape("核心·追狼", 226, 30, 360, 56),
            _shape("阵法神通达到二阶并且入阵仙品狼首玉如意（0/1）", 86, 66, 520, 92),
        ],
    }

    requirements, lines = _build_formation_requirements_from_ocr_document(document)

    assert lines == [
        "【阵魂·青云】核心·追狼",
        "阵法神通达到二阶并且入阵仙品狼首玉如意（0/1）",
    ]
    assert requirements == [
        {
            "text": "阵法神通达到二阶并且入阵仙品狼首玉如意",
            "effect_text": "【阵魂·青云】核心·追狼",
        },
    ]


def test_formation_requirement_ocr_strips_progress_suffix_with_spaces() -> None:
    document = {
        "shapes": [
            _shape("【阵魂·归墟】", 80, 30, 220, 56),
            _shape("核心·奔袭", 226, 30, 360, 56),
            _shape("阵法神通达到六阶并且入阵绝品以上法宝合计三十六阶 ( 246 / 36 )", 86, 66, 620, 92),
        ],
    }

    requirements, lines = _build_formation_requirements_from_ocr_document(document)

    assert lines == [
        "【阵魂·归墟】核心·奔袭",
        "阵法神通达到六阶并且入阵绝品以上法宝合计三十六阶(246/36)",
    ]
    assert requirements == [
        {
            "text": "阵法神通达到六阶并且入阵绝品以上法宝合计三十六阶",
            "effect_text": "【阵魂·归墟】核心·奔袭",
        },
    ]


def test_formation_requirement_ocr_strips_progress_suffix_without_closing_bracket() -> None:
    document = {
        "shapes": [
            _shape("【阵魂·忘川】", 80, 30, 220, 56),
            _shape("疾驰", 226, 30, 292, 56),
            _shape("入阵1个神品白玉棋石或黑邃棋石（0/1", 86, 66, 520, 92),
        ],
    }

    requirements, lines = _build_formation_requirements_from_ocr_document(document)

    assert lines == [
        "【阵魂·忘川】疾驰",
        "入阵1个神品白玉棋石或黑邃棋石（0/1",
    ]
    assert requirements == [
        {
            "text": "入阵1个神品白玉棋石或黑邃棋石",
            "effect_text": "【阵魂·忘川】疾驰",
        },
    ]


def test_formation_requirement_ocr_normalizes_effect_brackets_and_colon() -> None:
    document = {
        "shapes": [
            _shape("[东极]", 80, 30, 160, 56),
            _shape(": 守御+400", 166, 30, 294, 56),
            _shape("入阵1个绝品法宝", 86, 66, 282, 92),
        ],
    }

    requirements, _lines = _build_formation_requirements_from_ocr_document(document)

    assert requirements == [
        {
            "text": "入阵1个绝品法宝",
            "effect_text": "【东极】守御+400",
        },
    ]


def test_formation_requirement_ocr_supports_multiline_condition_text() -> None:
    document = {
        "shapes": [
            _shape("【阵魂·归墟】", 80, 30, 220, 56),
            _shape("核心·奔袭", 226, 30, 360, 56),
            _shape("阵法神通达到六阶并且入阵绝品以上法宝合计", 86, 66, 520, 92),
            _shape("三十六阶（246/36）", 86, 98, 260, 124),
            _shape("【阵魂·忘川】", 80, 154, 220, 180),
            _shape("疾驰", 226, 154, 292, 180),
            _shape("阵法神通达到七阶并且入阵仙品三星狼首玉如", 86, 190, 520, 216),
            _shape("意（0/1）", 86, 222, 180, 248),
        ],
    }

    requirements, lines = _build_formation_requirements_from_ocr_document(document)

    assert lines == [
        "【阵魂·归墟】核心·奔袭",
        "阵法神通达到六阶并且入阵绝品以上法宝合计",
        "三十六阶（246/36）",
        "【阵魂·忘川】疾驰",
        "阵法神通达到七阶并且入阵仙品三星狼首玉如",
        "意（0/1）",
    ]
    assert requirements == [
        {
            "text": "阵法神通达到六阶并且入阵绝品以上法宝合计三十六阶",
            "effect_text": "【阵魂·归墟】核心·奔袭",
        },
        {
            "text": "阵法神通达到七阶并且入阵仙品三星狼首玉如意",
            "effect_text": "【阵魂·忘川】疾驰",
        },
    ]


def test_formation_effect_detail_ocr_supports_multiline_effect_description() -> None:
    document = {
        "shapes": [
            _shape("->名字", 80, 30, 150, 56),
            _shape("净光", 86, 66, 146, 92),
            _shape("->效果", 80, 122, 150, 148),
            _shape("自身每拥有1层【八极】，", 86, 158, 330, 184),
            _shape("分光造成的伤害提升20%，", 86, 190, 350, 216),
            _shape("最多可生效8层", 86, 222, 250, 248),
        ],
    }

    effect_details, lines = _build_formation_effect_details_from_ocr_document(document)

    assert lines == [
        "->名字",
        "净光",
        "->效果",
        "自身每拥有1层【八极】，",
        "分光造成的伤害提升20%，",
        "最多可生效8层",
    ]
    assert effect_details == [
        {
            "effect_name": "净光",
            "effect_detail": "自身每拥有1层【八极】，分光造成的伤害提升20%，最多可生效8层",
        },
    ]
