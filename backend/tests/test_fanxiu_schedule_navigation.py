from datetime import date, datetime

import pytest

from backend.core.fanxiu.data_annotation.schedule_navigation import (
    activity_card_covers_date,
    activity_card_covers_moment,
    classify_activity_card,
    parse_schedule_header,
    resolve_schedule_activity_target,
    resolve_schedule_runtime_activity_targets,
    runtime_activity_entities_for_date,
    select_schedule_activity,
)


def _line(text: str, x: float, y: float = 200, w: float = 80, h: float = 30):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


HEADER = [
    _line("08月11日", 90),
    _line("今天", 280, w=70),
    _line("08月13日", 405),
    _line("08月14日", 560),
    _line("08月15日", 715),
    _line("凡人历", 115, y=260, w=90),
    _line("凡人历", 270, y=260, w=90),
    _line("凡人历", 425, y=260, w=90),
    _line("凡人历", 580, y=260, w=90),
    _line("凡人历", 735, y=260, w=90),
]


def test_header_uses_today_as_relative_time_origin() -> None:
    header = parse_schedule_header(HEADER)

    assert header.today_index == 1
    assert header.x_for_day_offset(-1) == pytest.approx(160)
    assert header.x_for_day_offset(0) == pytest.approx(315)
    assert header.x_for_day_offset(2) == pytest.approx(625)
    with pytest.raises(ValueError, match="不在可见表头内"):
        header.x_for_day_offset(-2)


def test_activity_target_combines_column_x_with_activity_row_y() -> None:
    target = resolve_schedule_activity_target(
        header_lines=HEADER,
        calendar_lines=[
            _line("兽渊探秘", 175, y=585, w=134, h=38),
            _line("云梦试剑", 568, y=585, w=127, h=36),
        ],
        activity_pattern=r"兽渊(探秘)?",
        day_offset=0,
    )

    # 名称绘制在跨日卡片左侧，点击点仍必须使用今天列的 x。
    assert target.x == pytest.approx(315)
    assert target.y == pytest.approx(604)
    assert target.matched_text == "兽渊探秘"


def test_legacy_single_target_refuses_ambiguous_rows() -> None:
    with pytest.raises(RuntimeError, match="2 个候选行"):
        resolve_schedule_activity_target(
            header_lines=HEADER,
            calendar_lines=[
                _line("炼体法相", 100, y=370),
                _line("炼体法相", 700, y=470),
            ],
            activity_pattern="炼体法相",
        )


def test_activity_card_period_verifies_selected_instance() -> None:
    text = "兽渊探秘 活动时间：08月11日-08月12日 奖励预览 前往参与"

    assert activity_card_covers_date(text, date(2026, 8, 12))
    assert not activity_card_covers_date(text, date(2026, 8, 13))


def test_activity_card_clock_window_supports_daily_and_overnight_events() -> None:
    daily = "魔祖 活动时间：12:30:00-13:00:00 奖励预览 前往参与"
    overnight = "夜间活动 活动时间：23:50-00:10 前往参与"

    assert activity_card_covers_moment(daily, datetime(2026, 8, 12, 12, 31))
    assert not activity_card_covers_moment(daily, datetime(2026, 8, 12, 13, 1))
    assert activity_card_covers_moment(overnight, datetime(2026, 8, 12, 23, 55))
    assert activity_card_covers_moment(overnight, datetime(2026, 8, 13, 0, 5))
    assert not activity_card_covers_moment(overnight, datetime(2026, 8, 13, 12, 0))


def test_activity_card_accepts_fullwidth_hyphen_from_magic_card_ocr() -> None:
    text = "魔道入侵跨服[8] 活动时间：08月22日－08月22日 奖励预览 前往参与"

    assert activity_card_covers_moment(text, datetime(2026, 8, 22, 19, 1))


def test_schedule_header_uses_unique_date_when_today_ocr_is_missing() -> None:
    header = parse_schedule_header(
        [
            _line("08月21日", 100),
            _line("凡人历", 100),
            _line("08月22日", 200),
            _line("凡人历", 200),
            _line("08月23日", 300),
            _line("凡人历", 300),
        ],
        anchor_date=date(2026, 8, 22),
    )

    assert header.today_index == 1


def test_active_card_is_a_distinct_projection_without_title_or_date() -> None:
    projection = classify_activity_card(
        [_line("当前积分：28.27万 我的团队 活动倒计时：11:42:14 进入活动", 80)],
        r"兽渊(探秘)?",
        target_date=date(2026, 8, 12),
    )

    assert projection.kind == "active"
    assert not projection.exact_match


def _millis(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)


def test_runtime_activity_name_aligns_noisy_calendar_ocr() -> None:
    entities = runtime_activity_entities_for_date(
        {
            "available": True,
            "items": [
                {
                    "activityId": 32080001,
                    "id": 32080001400002,
                    "name": "魔道入侵",
                    "startTime": _millis("2026-08-11 10:00:00"),
                    "endTime": _millis("2026-08-13 22:00:00"),
                }
            ],
        },
        r"魔道入侵",
        target_date=date(2026, 8, 12),
    )

    targets = resolve_schedule_runtime_activity_targets(
        header_lines=HEADER,
        calendar_lines=[
            _line("常规", 120, y=410),
            _line("魔道人侵", 210, y=510),
            _line("兽渊探秘", 500, y=610),
        ],
        runtime_entities=entities,
    )

    assert len(targets) == 1
    assert targets[0].matched_text == "魔道人侵"
    assert targets[0].runtime_key.startswith("32080001")
    assert 0.55 <= targets[0].alignment_score < 1.0


def test_runtime_activity_qualifier_disambiguates_duplicate_magic_invasion_rows() -> None:
    entities = runtime_activity_entities_for_date(
        {
            "available": True,
            "items": [
                {
                    "activityId": 1070011,
                    "id": 1070011400004,
                    "name": "魔道入侵",
                    "littleName": "(预赛)",
                    "startTime": _millis("2026-08-21 10:00:00"),
                    "endTime": _millis("2026-08-21 22:00:00"),
                },
                {
                    "activityId": 8070001,
                    "id": 8070001400004,
                    "name": "魔道入侵",
                    "littleName": "跨服[8]",
                    "startTime": _millis("2026-08-22 10:00:00"),
                    "endTime": _millis("2026-08-22 22:00:00"),
                },
            ],
        },
        r"魔道入侵",
        target_date=date(2026, 8, 21),
    )

    assert len(entities) == 1
    assert entities[0].name == "魔道入侵 (预赛)"

    targets = resolve_schedule_runtime_activity_targets(
        header_lines=HEADER,
        calendar_lines=[
            _line("魔道入侵", 180, y=370, w=140),
            _line("(预赛)", 190, y=406, w=100),
            _line("魔道入侵", 180, y=477, w=140),
            _line("跨服[8]", 190, y=514, w=120),
        ],
        runtime_entities=entities,
    )

    assert len(targets) == 1
    assert targets[0].runtime_key.startswith("1070011")
    assert "预赛" in targets[0].matched_text
    assert targets[0].y == pytest.approx(385)


def test_runtime_activity_qualifier_does_not_confuse_cross_server_counts() -> None:
    entities = runtime_activity_entities_for_date(
        {
            "available": True,
            "items": [
                {
                    "activityId": 8070001,
                    "id": 8070001400004,
                    "name": "魔道入侵",
                    "littleName": "跨服[8]",
                    "startTime": _millis("2026-08-22 10:00:00"),
                    "endTime": _millis("2026-08-22 22:00:00"),
                }
            ],
        },
        r"魔道入侵",
        target_date=date(2026, 8, 22),
    )

    targets = resolve_schedule_runtime_activity_targets(
        header_lines=HEADER,
        calendar_lines=[
            _line("魔道入侵", 180, y=370, w=140),
            _line("跨服[16]", 190, y=406, w=120),
            _line("魔道入侵", 180, y=477, w=140),
            _line("跨服[8]", 190, y=514, w=120),
        ],
        runtime_entities=entities,
    )

    assert len(targets) == 1
    assert targets[0].runtime_key.startswith("8070001")
    assert "跨服[8]" in targets[0].matched_text
    assert targets[0].y == pytest.approx(492)


def test_runtime_activity_without_concrete_period_is_not_authoritative_for_click() -> None:
    entities = runtime_activity_entities_for_date(
        {"available": True, "items": [{"activityId": 1, "name": "兽渊探秘"}]},
        r"兽渊",
        target_date=date(2026, 8, 12),
    )

    assert entities == ()


def test_card_uses_runtime_identity_with_imperfect_ocr() -> None:
    entities = runtime_activity_entities_for_date(
        {
            "available": True,
            "items": [
                {
                    "activityId": 32080001,
                    "id": 2,
                    "name": "魔道入侵",
                    "startTime": _millis("2026-08-11 10:00:00"),
                    "endTime": _millis("2026-08-13 22:00:00"),
                }
            ],
        },
        r"魔道入侵",
        target_date=date(2026, 8, 12),
    )

    projection = classify_activity_card(
        [_line("魔道人侵 活动时间：08月11日-08月13日 前往参与", 80)],
        r"魔道入侵",
        target_date=date(2026, 8, 12),
        runtime_entities=entities,
    )

    assert projection.exact_match
    assert projection.runtime_key.startswith("32080001")
    assert 0.55 <= projection.name_score < 1.0
    assert projection.covers_moment
    assert projection.rejection_reason == "exact"


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def test_select_activity_enters_unique_runtime_aligned_calendar_cell() -> None:
    class Runtime:
        clicked_points = []
        clicked_shapes = []
        logs = []
        runner = None

        def __init__(self):
            self.runner = self

        def _log(self, kind, message):
            self.logs.append((kind, message))

        def cur_frame(self, *, update=False):
            return "frame"

        def ocr_fragments_in_shapes(self, _scene, shapes, **_kwargs):
            return HEADER if shapes == ["表头"] else [
                _line("兽渊探秘", 175, y=585, w=134, h=38)
            ]

        def paged_content_snapshot(self, *_args, **_kwargs):
            return {"lines": [_line("云梦试剑 活动时间：08月13日-08月14日", 80)]}

        def click_frame_point(self, *_args):
            self.clicked_points.append(_args)

        def wait_action_settle(self, _seconds):
            if False:
                yield None

        def find_paged_content(self, _scene, predicate, _shape):
            page = {
                "frame": "beast-card-frame",
                "lines": [
                    _line("兽渊探秘 活动时间：08月11日-08月12日 前往参与", 80)
                ],
            }
            assert predicate(page)
            if False:
                yield None
            return page

        def click_shape(self, *args, **kwargs):
            self.clicked_shapes.append((args, kwargs))

    runtime = Runtime()
    schedule = {
        "available": True,
        "items": [
            {
                "activityId": 4150001,
                "id": 4150001400002,
                "name": "兽渊探秘",
                "startTime": _millis("2026-08-11 10:00:00"),
                "endTime": _millis("2026-08-12 22:00:00"),
            }
        ],
    }

    selected = _finish(
        select_schedule_activity(
            runtime,
            r"兽渊探秘",
            enter=True,
            runtime_schedule=schedule,
            require_runtime_alignment=True,
            now=datetime(2026, 8, 12, 12, 0, 0),
        )
    )

    assert selected.matched_text == "兽渊探秘"
    assert runtime.clicked_points == [(66, selected.x, selected.y)]
    assert runtime.clicked_shapes == []


def test_select_activity_failure_reports_each_card_rejection_reason() -> None:
    class Runtime:
        def cur_frame(self, *, update=False):
            return "frame"

        def ocr_fragments_in_shapes(self, _scene, shapes, **_kwargs):
            return HEADER if shapes == ["表头"] else []

        def paged_content_snapshot(self, *_args, **_kwargs):
            return {
                "lines": [
                    _line("云梦试剑 活动时间：08月13日-08月14日", 80)
                ]
            }

        def find_paged_content(self, _scene, predicate, _shape):
            assert not predicate(
                {
                    "lines": [
                        _line("兽渊探秘 活动时间：08月13日-08月14日", 80)
                    ]
                }
            )
            if False:
                yield None
            return None

    schedule = {
        "available": True,
        "items": [
            {
                "activityId": 4150001,
                "id": 4150001400002,
                "name": "兽渊探秘",
                "startTime": _millis("2026-08-11 10:00:00"),
                "endTime": _millis("2026-08-12 22:00:00"),
            }
        ],
    }

    with pytest.raises(RuntimeError) as exc_info:
        _finish(
            select_schedule_activity(
                Runtime(),
                r"兽渊探秘",
                runtime_schedule=schedule,
                require_runtime_alignment=True,
                now=datetime(2026, 8, 12, 12, 0, 0),
            )
        )

    message = str(exc_info.value)
    assert "p1" in message and "runtime_gui_score_below_threshold" in message
    assert "p2" in message and "date_or_time_mismatch" in message


def test_select_current_clock_card_does_not_require_calendar_label() -> None:
    class Runtime:
        clicked_shapes = []
        pager_calls = 0

        def cur_frame(self, *, update=False):
            return "mozu-frame"

        def ocr_fragments_in_shapes(self, _scene, shapes, **_kwargs):
            if shapes == ["表头"]:
                return HEADER
            return [
                _line("灵宠竞武", 100, y=390),
                _line("兽渊探秘", 100, y=590),
                _line("常规", 100, y=690),
            ]

        def paged_content_snapshot(self, *_args, **_kwargs):
            return {
                "frame": "mozu-frame",
                "lines": [
                    _line(
                        "魔祖 活动时间：12:30:00-13:00:00 奖励预览 前往参与",
                        80,
                    )
                ],
            }

        def find_paged_content(self, *_args, **_kwargs):
            self.pager_calls += 1
            if False:
                yield None
            return None

        def click_shape(self, *args, **kwargs):
            self.clicked_shapes.append((args, kwargs))

    runtime = Runtime()
    selected = _finish(
        select_schedule_activity(
            runtime,
            r"魔祖",
            enter=True,
            runtime_schedule={"available": False, "items": []},
            now=datetime(2026, 8, 12, 12, 31),
        )
    )

    assert "魔祖" in selected.matched_text
    assert selected.y == 0.0
    assert runtime.pager_calls == 0
    assert runtime.clicked_shapes[0][0][1] == "活动卡片/前往"
    assert runtime.clicked_shapes[0][1]["frame_data_url"] == "mozu-frame"
