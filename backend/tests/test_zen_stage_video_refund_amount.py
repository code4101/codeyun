import pytest

from backend.core.attendance.nianzhu_course_sheets import (
    CURRENT_RULE,
    VIDEO_RULE_SYSTEM_ZEN_STAGE,
    VideoConfigItem,
    _load_video_config,
    _make_table_document_from_dicts,
    _attendance_zen_clockin_refund_formula,
    _attendance_zen_video_refund_rule,
    _highlight_video_refund_for_item,
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
