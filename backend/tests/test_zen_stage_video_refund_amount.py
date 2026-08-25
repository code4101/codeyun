import pytest

from backend.core.attendance.nianzhu_course_sheets import (
    CURRENT_RULE,
    VIDEO_RULE_SYSTEM_ZEN_STAGE,
    VideoConfigItem,
    _clockin_output_fields,
    _load_video_config,
    _make_table_document_from_dicts,
    _merge_progressive_zen_catalog_rows,
    _remove_video_data_for_lesson_ids,
    _zen_catalog_resource_id,
    _attendance_refund_period_count_expression,
    _attendance_zen_clockin_refund_formula,
    _attendance_zen_clockin_refund_specs,
    _attendance_zen_video_refund_rule,
    _highlight_video_refund_for_item,
)
from backend.core.attendance.progress_style import (
    REFUND_PROGRESS_FULL_COLOR,
    highlight_threshold_refund_progress,
)


def _item() -> VideoConfigItem:
    return VideoConfigItem(
        order_index=1,
        lesson_id="1",
        course_key="title:义理堂6",
        lesson_name="第1周=义理堂6",
        item_type="课次",
        lesson_number=None,
        rule_system=VIDEO_RULE_SYSTEM_ZEN_STAGE,
        participates_refund=True,
        participates_score=False,
        rules_by_version={},
        text_rules_by_version={},
    )


def test_zen_stage_uses_course_refund_amount_instead_of_global_twenty():
    amount, _color = _highlight_video_refund_for_item(
        _item(),
        CURRENT_RULE,
        "准时完成",
        zen_stage_refund_amount=18,
    )
    assert amount == 18


def test_zen_stage_delayed_completion_refunds_zero():
    amount, _color = _highlight_video_refund_for_item(
        _item(),
        CURRENT_RULE,
        "延1周完成",
        zen_stage_refund_amount=18,
    )
    assert amount == 0


def test_zen_stage_rejects_missing_stage_specific_refund_amount():
    with pytest.raises(ValueError, match="本阶第3行配置"):
        _highlight_video_refund_for_item(
            _item(),
            CURRENT_RULE,
            "准时完成",
        )


def test_zen_stage_rule_is_parsed_from_row_3_note_and_validated():
    document = {
        "data_start_row": 3,
        "grid_rows": [[""], [""], ["39课×18元=702元"]],
    }

    assert _attendance_zen_video_refund_rule(
        document,
        ["视频应返款"],
        expected_refund_count=39,
    ) == (39, 18, 702)


def test_zen_stage_rule_can_use_hidden_legacy_incentive_metadata():
    document = {
        "data_start_row": 3,
        "grid_rows": [[""], [""], ["视频完成情况"]],
        "source_meta": {
            "incentive_policy": "disabled_hidden",
            "legacy_incentive_rules": {"video": "49课*15元=735元"},
        },
    }

    assert _attendance_zen_video_refund_rule(
        document,
        ["视频应返款"],
        expected_refund_count=49,
    ) == (49, 15, 735)


def test_zen_stage_progressive_release_uses_full_course_rule():
    document = {
        "data_start_row": 3,
        "grid_rows": [[""], [""], ["24课*30元=720元"]],
    }

    assert _attendance_zen_video_refund_rule(
        document,
        ["视频应返款"],
        expected_refund_count=10,
        expected_total_count=24,
    ) == (24, 30, 720)


def test_zen_stage_rule_rejects_note_that_disagrees_with_video_config():
    document = {
        "data_start_row": 3,
        "grid_rows": [[""], [""], ["39课*18元=702元"]],
    }

    with pytest.raises(ValueError, match="计费课数与视频配置不一致"):
        _attendance_zen_video_refund_rule(
            document,
            ["视频应返款"],
            expected_refund_count=38,
        )


def test_zen_stage_clockin_formula_is_derived_from_row_3_notes():
    columns = ["打卡应返款", "共学打卡", "共修打卡-闻思门", "共修打卡-禅门", "共修打卡-日常行门"]
    document = {
        "data_start_row": 3,
        "grid_rows": [
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            [
                "共学90元；三项共修各40元，共120元",
                "9次*每次10元=90元",
                "1次20元，3次40元",
                "1次20元，3次40元",
                "1次20元，3次40元",
            ],
        ],
    }

    formula, limit = _attendance_zen_clockin_refund_formula(
        document,
        columns,
        row_number=4,
    )

    assert limit == 210
    assert "MIN(IFERROR(VALUE(B4),0),9)*10" in formula
    assert formula.count(">=3,40") == 3
    assert formula.count(">=1,20") == 3


def test_zen_stage_clockin_static_colors_follow_refund_tiers():
    columns = ["共学打卡", "共修打卡-随念门"]
    document = {
        "data_start_row": 3,
        "grid_rows": [
            ["", ""],
            ["", ""],
            ["每周一次，共9次*每次10元=90元", "1次20元，3次40元"],
        ],
    }

    colearn, practice = _attendance_zen_clockin_refund_specs(document, columns)

    assert [(rule.threshold, rule.refund_amount) for rule in colearn.rules] == [
        (float(count), float(count * 10)) for count in range(1, 10)
    ]
    assert [(rule.threshold, rule.refund_amount) for rule in practice.rules] == [
        (1.0, 20.0),
        (3.0, 40.0),
    ]

    assert highlight_threshold_refund_progress(list(colearn.rules), 0)[1] is None
    colearn_one_color = highlight_threshold_refund_progress(list(colearn.rules), 1)[1]
    colearn_five_color = highlight_threshold_refund_progress(list(colearn.rules), 5)[1]
    assert colearn_one_color not in {None, REFUND_PROGRESS_FULL_COLOR}
    assert colearn_five_color not in {None, colearn_one_color, REFUND_PROGRESS_FULL_COLOR}
    assert highlight_threshold_refund_progress(list(colearn.rules), 9)[1] == REFUND_PROGRESS_FULL_COLOR

    assert highlight_threshold_refund_progress(list(practice.rules), 1)[1] not in {
        None,
        REFUND_PROGRESS_FULL_COLOR,
    }
    assert highlight_threshold_refund_progress(list(practice.rules), 3)[1] == REFUND_PROGRESS_FULL_COLOR


def test_zen_stage_clockin_columns_match_stable_suffix_when_course_titles_differ():
    config_document = _make_table_document_from_dicts(
        columns=["name"],
        rows=[
            {"name": "d260712禅宗13期一阶-共学打卡"},
            {"name": "d260712禅宗13期一阶-共修打卡-随念门"},
        ],
        numeric_columns=set(),
        page_size=200,
    )

    assert _clockin_output_fields(
        config_document,
        ["共学打卡", "共修打卡-随念门"],
        course_name="修道班13期1阶",
    ) == [
        ("d260712禅宗13期一阶-共学打卡", "共学打卡", 0),
        ("d260712禅宗13期一阶-共修打卡-随念门", "共修打卡-随念门", 1),
    ]


def test_zen_stage_colearn_week_cap_only_limits_refund_formula():
    columns = ["打卡应返款", "已返款", "共学打卡", "共修打卡"]
    document = {
        "data_start_row": 3,
        "grid_rows": [
            ["", "", "", ""],
            ["", "", "", ""],
            ["", '="第"&返款周期&"周"', "9次*每次10元=90元", "1次20元，3次40元"],
        ],
    }

    formula, _limit = _attendance_zen_clockin_refund_formula(
        document,
        columns,
        row_number=4,
        colearn_period_reference=_attendance_refund_period_count_expression(document, columns),
    )

    assert "MIN(IFERROR(VALUE(C4),0),9,IFERROR(VALUE(MID($B$3,2,LEN($B$3)-2)),0))*10" in formula
    assert "IFERROR(VALUE(D4),0)>=3,40" in formula


def test_zen_stage_clockin_formula_accepts_plain_practice_column():
    columns = ["打卡应返款", "共学打卡", "共修打卡"]
    document = {
        "data_start_row": 3,
        "grid_rows": [
            ["", "", ""],
            ["", "", ""],
            [
                "共学与共修每次5元",
                "16次*每次5元=80元",
                "16周*每周5元=80元",
            ],
        ],
    }

    formula, limit = _attendance_zen_clockin_refund_formula(
        document,
        columns,
        row_number=4,
    )

    assert limit == 160
    assert "MIN(IFERROR(VALUE(B4),0),16)*5" in formula
    assert "MIN(IFERROR(VALUE(C4),0),16)*5" in formula


def test_zen_stage_clockin_colors_can_use_hidden_legacy_incentive_metadata():
    columns = ["打卡应返款", "共学打卡", "共修打卡"]
    document = {
        "data_start_row": 3,
        "grid_rows": [
            ["", "", ""],
            ["", "", ""],
            ["", "累计打卡次数", "累计共修周数"],
        ],
        "source_meta": {
            "incentive_policy": "disabled_hidden",
            "legacy_incentive_rules": {
                "clockin": {
                    "共学打卡": "16次*每次5元=80元",
                    "共修打卡": "16周*每周5元=80元",
                },
            },
        },
    }

    formula, limit = _attendance_zen_clockin_refund_formula(
        document,
        columns,
        row_number=4,
    )

    assert limit == 160
    assert "MIN(IFERROR(VALUE(B4),0),16)*5" in formula
    assert "MIN(IFERROR(VALUE(C4),0),16)*5" in formula


def test_xiudaoban_7qi_5jie_excludes_lecture_from_refund_count():
    document = _make_table_document_from_dicts(
        columns=["lesson_id", "lesson_name"],
        rows=[
            {"lesson_id": "1", "lesson_name": "第1周=佛教史1"},
            {"lesson_id": "2", "lesson_name": "第3周=佛教史讲座"},
            {"lesson_id": "3", "lesson_name": "第3周=心经"},
        ],
        numeric_columns=set(),
        page_size=200,
        source_meta={"course_name": "d260517修道班7期5阶"},
    )

    items = _load_video_config(document)

    assert [item.participates_refund for item in items] == [True, False, True]


def test_video_config_preserves_sheet_order_when_late_lesson_id_is_larger():
    document = _make_table_document_from_dicts(
        columns=["lesson_id", "lesson_name"],
        rows=[
            {"lesson_id": "22086", "lesson_name": "第2周=神经心理学1"},
            {"lesson_id": "22091", "lesson_name": "第2周=佛教行仪过堂礼仪"},
            {"lesson_id": "22087", "lesson_name": "第3周=印度佛教史2"},
        ],
        numeric_columns=set(),
        page_size=200,
        source_meta={"course_name": "修道班13期1阶"},
    )

    items = _load_video_config(document)

    assert [item.lesson_id for item in items] == ["22086", "22091", "22087"]
    assert [item.order_index for item in items] == [1, 2, 3]


def test_progressive_zen_catalog_adds_only_new_published_lessons():
    existing_url = (
        "https://admin.xiaoe-tech.com/t/community_admin/miniCommunity#/course_detail_page?"
        "course_id=course_current&resource_id=v_existing&p_id=chap_1&type=3"
    )
    new_url = (
        "https://admin.xiaoe-tech.com/t/community_admin/miniCommunity#/course_detail_page?"
        "course_id=course_current&resource_id=v_new&p_id=chap_2&type=3"
    )
    existing_rows = [{
        "lesson_id": 100,
        "start_date": "2026-07-12 00:00:00",
        "end_date": "2026-11-22 00:00:00",
        "next_update": "2026-07-19 00:00:00",
        "lesson_id2": existing_url,
        "shop_id": 2,
        "lesson_name": "d260712禅宗13期一阶-第1周=已人工规范标题",
        "video_duration": "",
    }]

    rows, summary = _merge_progressive_zen_catalog_rows(
        existing_rows,
        {
            "第1周": [{"name": "平台原始长标题", "url": existing_url}],
            "第2周": [{"name": "新发布课程", "url": new_url}],
        },
        course_name="d260712禅宗13期一阶",
        expected_lesson_count=24,
    )

    assert rows[0]["lesson_name"] == "d260712禅宗13期一阶-第1周=已人工规范标题"
    assert rows[1]["lesson_id"] == 101
    assert rows[1]["lesson_name"] == "d260712禅宗13期一阶-第2周=新发布课程"
    assert rows[1]["start_date"] == "2026-07-19 00:00:00"
    assert rows[1]["next_update"] == "2026-07-26 00:00:00"
    assert summary["added_lesson_count"] == 1
    assert summary["published_lesson_count"] == 2
    assert summary["published_through_week"] == 2


def test_progressive_zen_catalog_uses_latest_directory_as_authority():
    existing_rows = [{
        "lesson_id": 100,
        "start_date": "2026-07-12 00:00:00",
        "end_date": "2026-11-22 00:00:00",
        "next_update": "2026-07-19 00:00:00",
        "lesson_id2": "https://example.test/?resource_id=v_existing",
        "shop_id": 2,
        "lesson_name": "d260712禅宗13期一阶-第1周=已配置课程",
        "video_duration": "",
    }]

    rows, summary = _merge_progressive_zen_catalog_rows(
        existing_rows,
        {"第2周": [{"name": "最新目录课次", "url": "https://example.test/?resource_id=v_new"}]},
        course_name="d260712禅宗13期一阶",
        expected_lesson_count=24,
    )

    assert [row["lesson_id"] for row in rows] == [101]
    assert summary["added_lesson_count"] == 1
    assert summary["removed_lesson_count"] == 1


def test_progressive_zen_catalog_replaces_inherited_resource_at_same_week_position():
    def url(resource_id):
        return f"https://example.test/?resource_id={resource_id}"

    existing_rows = [
        {
            "lesson_id": 22092,
            "start_date": "2026-08-02 00:00:00",
            "end_date": "2026-11-22 00:00:00",
            "next_update": "2026-08-09 00:00:00",
            "lesson_id2": url("v_old"),
            "shop_id": 2,
            "lesson_name": "d260712禅宗13期一阶-第4周=印度佛教史4",
            "video_duration": "",
        },
        {
            "lesson_id": 22093,
            "start_date": "2026-08-02 00:00:00",
            "end_date": "2026-11-22 00:00:00",
            "next_update": "2026-08-09 00:00:00",
            "lesson_id2": url("v_same"),
            "shop_id": 2,
            "lesson_name": "d260712禅宗13期一阶-第4周=旧显示名",
            "video_duration": "",
        },
    ]

    rows, summary = _merge_progressive_zen_catalog_rows(
        existing_rows,
        {"第4周": [
            {"name": "印度佛教史3", "url": url("v_new")},
            {"name": "平台原始标题", "url": url("v_same")},
        ]},
        course_name="d260712禅宗13期一阶",
        expected_lesson_count=24,
    )

    assert rows[0]["lesson_id"] == 22092
    assert _zen_catalog_resource_id(rows[0]["lesson_id2"]) == "v_new"
    assert rows[0]["lesson_name"].endswith("第4周=印度佛教史3")
    assert rows[1]["lesson_name"].endswith("第4周=旧显示名")
    assert summary["replaced_lesson_count"] == 1
    assert summary["replaced_lessons"][0]["old_resource_id"] == "v_old"
    assert summary["removed_lesson_count"] == 0


def test_replaced_progressive_lesson_clears_only_its_old_video_data():
    document = _make_table_document_from_dicts(
        columns=["lesson_data_id", "lesson_id", "user_id2"],
        rows=[
            {"lesson_data_id": 1, "lesson_id": 22091, "user_id2": "u1"},
            {"lesson_data_id": 2, "lesson_id": 22092, "user_id2": "u2"},
            {"lesson_data_id": 3, "lesson_id": 22092, "user_id2": "u3"},
            {"lesson_data_id": 4, "lesson_id": 22093, "user_id2": "u4"},
        ],
        numeric_columns={"lesson_data_id", "lesson_id"},
        page_size=200,
    )

    next_document, removed = _remove_video_data_for_lesson_ids(document, {22092})
    rows = next_document["rows"]

    assert removed == 2
    assert [row[1] for row in rows] == [22091, 22093]
    assert [row[0] for row in rows] == [1, 2]
