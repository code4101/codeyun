from backend.core.attendance.nianzhu_course_sheets import (
    VIDEO_RULE_SYSTEM_ZEN_STAGE,
    VideoConfigItem,
    _ensure_nianzhu_attendance_schema,
    _ensure_video_progress_columns,
    _ensure_video_progress_column_order,
    _lesson_display_header_from_video_item,
    _lesson_header_from_video_item,
    _remove_legacy_collapsed_video_progress_columns,
    _requires_attendance_tracking_meta_columns,
    _sync_row_local_managed_formulas,
    ensure_attendance_course_columns_visible,
)


def test_jueguan_course_does_not_require_tracking_meta_columns():
    assert not _requires_attendance_tracking_meta_columns({}, course_name="第47届觉观")


def test_jueguan_schema_removes_tracking_meta_columns():
    document = {
        "columns": ["姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间"],
        "rows": [["张三", "当前规则", "一组", "追踪中", ""]],
        "grid_rows": [["姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间"]],
        "field_row_index": 0,
    }

    next_document, summary = _ensure_nianzhu_attendance_schema(document, course_name="第47届觉观")

    assert next_document["columns"] == ["姓名"]
    assert next_document["rows"] == [["张三"]]
    assert summary["schema_removed_columns"] == ["规则版本", "追踪分组", "追踪状态", "冻结时间"]


def test_plain_nianzhu_course_does_not_require_tracking_meta_columns():
    assert not _requires_attendance_tracking_meta_columns({}, course_name="第41届念住")


def test_chuangguan_course_still_requires_tracking_meta_columns():
    assert _requires_attendance_tracking_meta_columns({}, course_name="d250106念住闯关")


def test_zen_stage_course_does_not_require_tracking_meta_columns():
    assert not _requires_attendance_tracking_meta_columns({}, course_name="修道班13期1阶")


def test_zen_stage_schema_removes_tracking_meta_columns():
    document = {
        "columns": ["姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间"],
        "rows": [["张三", "当前规则", "一组", "追踪中", ""]],
        "grid_rows": [["姓名", "规则版本", "追踪分组", "追踪状态", "冻结时间"]],
        "field_row_index": 0,
    }

    next_document, summary = _ensure_nianzhu_attendance_schema(document, course_name="修道班13期1阶")

    assert next_document["columns"] == ["姓名"]
    assert next_document["rows"] == [["张三"]]
    assert summary["schema_removed_columns"] == ["规则版本", "追踪分组", "追踪状态", "冻结时间"]


def test_sync_row_local_managed_formulas_replaces_literal_zen_guest_value():
    document = {
        "columns": ["姓名", "禅客", "完成视频数", "打卡数"],
        "rows": [["张三", 0, 11, 7]],
        "grid_rows": [["姓名", "禅客", "完成视频数", "打卡数"], ["张三", 0, 11, 7]],
        "field_row_index": 0,
        "data_start_row": 1,
    }
    row = list(document["rows"][0])

    updated_cells = _sync_row_local_managed_formulas(
        document,
        row,
        columns=document["columns"],
        document_row=1,
        row_number=2,
    )

    assert updated_cells == 1
    assert row[1] == '=IF(AND(C2>=11,D2>=7),"是","")'


def test_video_progress_columns_follow_config_order_after_late_lesson_insert():
    def item(order_index: int, week: int, title: str) -> VideoConfigItem:
        return VideoConfigItem(
            order_index=order_index,
            lesson_id=str(order_index),
            course_key=f"title:{title}",
            lesson_name=f"第{week}周={title}",
            item_type="课次",
            lesson_number=None,
            rule_system=VIDEO_RULE_SYSTEM_ZEN_STAGE,
            participates_refund=True,
            participates_score=False,
            rules_by_version={},
            text_rules_by_version={},
        )

    document = {
        "columns": ["姓名", "第一课", "第二课", "第四课", "第三课"],
        "column_ids": ["name", "one", "two", "four", "three"],
        "rows": [["张三", "v1", "v2", "v4", "v3"]],
        "grid_rows": [["姓名", "第一课", "第二课", "第四课", "第三课"]],
    }
    video_config = [
        item(1, 1, "第一课"),
        item(2, 2, "第二课"),
        item(3, 2, "第三课"),
        item(4, 3, "第四课"),
    ]

    next_document, moved_headers = _ensure_video_progress_column_order(document, video_config)

    assert moved_headers == ["第三课"]
    assert next_document["columns"] == ["姓名", "第一课", "第二课", "第三课", "第四课"]
    assert next_document["column_ids"] == ["name", "one", "two", "three", "four"]
    assert next_document["rows"] == [["张三", "v1", "v2", "v3", "v4"]]


def test_zen_stage_lesson_header_does_not_collapse_to_instructor_name():
    item = VideoConfigItem(
        order_index=1,
        lesson_id="22093",
        course_key="title:20260326Pm132627-佛教行仪-二时临斋仪+拜愿-贤世法师",
        lesson_name="第4周=20260326Pm132627-佛教行仪-二时临斋仪+拜愿-贤世法师",
        item_type="视频",
        lesson_number=None,
        rule_system=VIDEO_RULE_SYSTEM_ZEN_STAGE,
        participates_refund=True,
        participates_score=False,
        rules_by_version={},
        text_rules_by_version={},
    )

    assert _lesson_header_from_video_item(item).endswith("二时临斋仪+拜愿-贤世法师")
    assert _lesson_display_header_from_video_item(item) == "佛教行仪 · 二时临斋仪+拜愿 · 贤世法师"


def test_legacy_instructor_column_is_removed_before_full_lesson_column_is_inserted():
    item = VideoConfigItem(
        order_index=1,
        lesson_id="22093",
        course_key="title:佛教行仪-二时临斋仪+拜愿-贤世法师",
        lesson_name="第4周=佛教行仪-二时临斋仪+拜愿-贤世法师",
        item_type="视频",
        lesson_number=None,
        rule_system=VIDEO_RULE_SYSTEM_ZEN_STAGE,
        participates_refund=True,
        participates_score=False,
        rules_by_version={},
        text_rules_by_version={},
    )
    document = {
        "columns": ["姓名", "贤世法师"],
        "rows": [["张三", "准时完成"]],
        "grid_rows": [["姓名", "贤世法师"], ["张三", "准时完成"]],
    }

    migrated, removed = _remove_legacy_collapsed_video_progress_columns(document, [item])
    migrated, inserted = _ensure_video_progress_columns(migrated, [item])

    assert removed == ["贤世法师"]
    assert inserted == ["佛教行仪-二时临斋仪+拜愿-贤世法师"]


def test_attendance_course_columns_are_visible_without_exposing_internal_columns():
    document = {
        "columns": ["用户ID", "第07课", "第08课", "结营分享会"],
        "column_configs": {
            "用户ID": {"hidden": True},
            "第08课": {"hidden": True, "restore_index": 2},
            "结营分享会": {"hidden": True, "note": "附加课，不做考勤"},
        },
    }

    next_document, changed_headers = ensure_attendance_course_columns_visible(document)

    assert changed_headers == ["第08课", "结营分享会"]
    assert next_document["column_configs"]["用户ID"]["hidden"] is True
    assert "第08课" not in next_document["column_configs"]
    assert next_document["column_configs"]["结营分享会"] == {"note": "附加课，不做考勤"}
