from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.scheduler import repair_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.redpacket_state import classify_redpacket_runtime_routes
from backend.core.fanxiu.data_annotation.tasks import daily_redpacket as module
from backend.core.fanxiu.data_annotation.tasks.daily_redpacket import DailyRedpacketTaskMixin


def _yield_return(value):
    if False:
        yield None
    return value


class _Runner(DailyRedpacketTaskMixin):
    def __init__(self):
        self.recorded = []
        self.logs = []

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.recorded.append((task_id, next_time))

    def _log(self, kind, message):
        self.logs.append((kind, message))

    @staticmethod
    def _daily_redpacket_runtime_candidates():
        return _runtime_snapshot([])


class _GroupRunner(_Runner):
    def _match_shape(self, *_args, **_kwargs):
        return {"matched": True, "resolved_box": {"x": 30, "y": 60, "w": 10, "h": 10}}


class _OcrRunner(_Runner):
    def _find_shape(self, _image, title):
        return {"title": title, "x": 0, "y": 0, "w": 300, "h": 300}

    def _box(self, shape, _image):
        return shape

    def _shared_spatial_ocr_result(self, _ctx, _frame):
        return {
            "tokens": [
                {"text": "红", "x": 90, "y": 100, "w": 10, "h": 20, "parent_line_id": "b"},
                {"text": "包", "x": 100, "y": 100, "w": 10, "h": 20, "parent_line_id": "b"},
                {"text": "奖", "x": 30, "y": 40, "w": 10, "h": 20, "parent_line_id": "a"},
                {"text": "赏", "x": 40, "y": 40, "w": 10, "h": 20, "parent_line_id": "a"},
            ]
        }


class _ClaimedOcrRunner(_OcrRunner):
    def _shared_spatial_ocr_result(self, _ctx, _frame):
        tokens = []

        def add_line(line_id, text, x, y):
            for index, char in enumerate(text):
                tokens.append({
                    "text": char,
                    "x": x + index * 20,
                    "y": y,
                    "w": 18,
                    "h": 30,
                    "parent_line_id": line_id,
                    "line_order": len(tokens),
                    "order": index,
                })

        add_line("claimed-title", "首领累杀奖赏", 80, 40)
        add_line("claimed-state", "已领取", 130, 90)
        add_line("history", "你领取了车老妖的红包", 50, 150)
        add_line("active-title", "首领累杀奖赏", 80, 240)
        return {"tokens": tokens}


class _WrappedFirstPlaceOcrRunner(_OcrRunner):
    def _shared_spatial_ocr_result(self, _ctx, _frame):
        tokens = []

        def add_line(line_id, text, x, y):
            for index, char in enumerate(text):
                tokens.append({
                    "text": char,
                    "x": x + index * 20,
                    "y": y,
                    "w": 18,
                    "h": 30,
                    "parent_line_id": line_id,
                    "line_order": len(tokens),
                    "order": index,
                })

        add_line("title", "【炼体法相】预赛第一", 80, 100)
        add_line("icon", "福", 20, 125)
        add_line("continuation", "名", 220, 140)
        return {"tokens": tokens}


class _Runtime:
    def __init__(self, view_ids):
        self.view_ids = iter(view_ids)
        self.clicks = []

    def wait_view(self, *_views, **_kwargs):
        yield "waiting"
        return SimpleNamespace(id=next(self.view_ids))

    def wait_click(self, view, shape, **_kwargs):
        self.clicks.append((view, shape))
        yield "clicked"

    def wait_action_settle(self, _seconds):
        yield "settled"


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _runtime_snapshot(items):
    pending_uids = [str(item["uid"]) for item in items]
    return {
        "available": True,
        "complete": True,
        "pending_count": len(items),
        "items": list(items),
        "pending_uids": pending_uids,
        "sources": {
            "chat": {
                "available": True,
                "complete": True,
                "trigger_complete": True,
                "items": list(items),
            }
        },
        "evidence_levels": {
            "structural": True,
            "semantic": True,
            "trigger": bool(items),
            "claimability": False,
            "action": False,
        },
        "trigger_ready": bool(items),
    }


def _qmch_item(*, uid="qmch-uid", channel=101, sub_id=20050134, **overrides):
    item = {
        "uid": uid,
        "id": 5022,
        "channel": channel,
        "sub_channel_id": sub_id,
        "event_type": 9033,
        "event_key": "qmch_reward",
        "classification": "special_event_gui_deep_check_candidate",
        "trigger_candidate": True,
        "action_authorized": False,
    }
    item.update(overrides)
    return item


def test_redpacket_runtime_route_preserves_fresh_qmch_identity():
    plan = classify_redpacket_runtime_routes(
        _runtime_snapshot([_qmch_item()])
    )

    assert plan["status"] == "ready"
    assert plan["route"] == "qmch_reward"
    assert plan["channel"] == 101
    assert plan["sub_id"] == 20050134
    assert plan["uids"] == ["qmch-uid"]
    assert plan["ordinary_chat_items"] == []


def test_redpacket_runtime_route_prioritizes_qmch_and_defers_ordinary_chat():
    ordinary = {
        "uid": "ordinary-uid",
        "id": 520,
        "channel": 6,
        "sub_channel_id": 0,
        "trigger_candidate": True,
        "action_authorized": False,
    }

    plan = classify_redpacket_runtime_routes(
        _runtime_snapshot([ordinary, _qmch_item()])
    )

    assert plan["route"] == "qmch_reward"
    assert plan["ordinary_chat_items"] == []
    assert plan["deferred_ordinary_uids"] == ["ordinary-uid"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_type": 9032},
        {"channel": 6},
        {"sub_id": 0},
        {"classification": "special_event_expired"},
        {"trigger_candidate": False},
    ],
)
def test_redpacket_partial_qmch_identity_fails_closed_without_ordinary_fallback(overrides):
    sub_id = overrides.pop("sub_id", 20050134)
    item = _qmch_item(sub_id=sub_id, **overrides)

    plan = classify_redpacket_runtime_routes(_runtime_snapshot([item]))

    assert plan["status"] == "fail_closed"
    assert plan["route"] == "blocked"
    assert plan["ordinary_chat_items"] == []
    assert plan["reason"] == "runtime_route_identity_ambiguous"


def test_daily_redpacket_qmch_route_missing_handler_fails_before_old_gui_flow(
    tmp_path,
    monkeypatch,
):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")

    class Runtime:
        def cur_frame(self, **_kwargs):
            pytest.fail("QMCH route must not reach the old #395/#332 flow")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime(), raising=False)
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_runtime_candidates",
        lambda: _runtime_snapshot([_qmch_item()]),
    )
    monkeypatch.setattr(runner, "_execute_daily_qmch_reward_route", None)

    with pytest.raises(RuntimeError, match="专用福入口 handler 未配置"):
        _consume(runner._execute_daily_redpacket_task(
            {"asset_tree_path": path},
            None,
            {},
        ))


def test_daily_redpacket_qmch_route_dispatches_dedicated_handler_only(
    tmp_path,
    monkeypatch,
):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    runtime = object()
    calls = []

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime, raising=False)
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_runtime_candidates",
        lambda: _runtime_snapshot([_qmch_item()]),
    )

    def dedicated_handler(actual_runtime, ctx, stop_event, payload, route_plan):
        calls.append((actual_runtime, ctx, stop_event, payload, route_plan))
        if False:
            yield None
        return {"result": "success", "route": "qmch_reward"}

    monkeypatch.setattr(
        runner,
        "_execute_daily_qmch_reward_route",
        dedicated_handler,
        raising=False,
    )

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {},
    ))

    assert result == {"result": "success", "route": "qmch_reward"}
    assert len(calls) == 1
    assert calls[0][0] is runtime
    assert calls[0][4]["channel"] == 101
    assert calls[0][4]["sub_id"] == 20050134


def test_daily_redpacket_qmch_rewarded_is_idempotent_zero_gui_actions(
    tmp_path,
    monkeypatch,
):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    terminal = _qmch_item(
        detail_loaded=True,
        exclusion_reasons=["server_rewarded"],
    )

    class Runtime:
        def cur_frame(self, **_kwargs):
            pytest.fail("rewarded qmch must not perform any GUI action")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: Runtime(), raising=False)
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_runtime_candidates",
        lambda: _runtime_snapshot([terminal]),
    )

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {},
    ))

    assert result["result"] == "success"
    assert result["opened_count"] == 0
    assert "幂等零动作" in result["message"]


def test_daily_redpacket_qmch_terminal_does_not_starve_deferred_ordinary(
    monkeypatch,
):
    runner = _Runner()
    terminal = _qmch_item(
        detail_loaded=True,
        exclusion_reasons=["server_rewarded"],
    )
    ordinary = {
        "uid": "ordinary-uid",
        "channel": 103,
        "sub_channel_id": 7,
    }
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_runtime_candidates",
        lambda: _runtime_snapshot([terminal, ordinary]),
    )

    plan = runner._daily_redpacket_runtime_route_plan()

    assert plan["route"] == "ordinary_chat"
    assert plan["uids"] == ["ordinary-uid"]
    assert plan["deferred_ordinary_uids"] == []
    assert [item["uid"] for item in plan["terminal_qmch_items"]] == ["qmch-uid"]


def test_daily_redpacket_qmch_terminal_requires_loaded_same_uid_reward_fact():
    wrong_uid = _qmch_item(
        uid="other",
        detail_loaded=True,
        exclusion_reasons=["detail_rewarded"],
    )
    missing_detail = _qmch_item(
        detail_loaded=False,
        exclusion_reasons=["server_rewarded"],
    )

    assert _Runner._daily_qmch_terminal_items(_runtime_snapshot([wrong_uid])) == [wrong_uid]
    assert _Runner._daily_qmch_terminal_items(_runtime_snapshot([missing_detail])) == []


def test_daily_redpacket_qmch_sends_and_opens_once_then_requires_same_uid_terminal(
    monkeypatch,
):
    runner = _Runner()
    actions = []

    class Runtime:
        def cur_frame(self, **_kwargs):
            return "frame"

        def click_frame_point(self, view, x, y):
            actions.append(("point", view, x, y))

        def click_shape_center(self, view, shape, **kwargs):
            actions.append(("shape_click", view, shape, kwargs))

        def click_shape_center_fast(self, view, shape, **kwargs):
            actions.append(("shape_click_fast", view, shape, kwargs))

        def wait_view(self, *views, **_kwargs):
            yield "wait_view"
            for view in (332, 390, 397, 30):
                if view in views:
                    return SimpleNamespace(id=view)
            return SimpleNamespace(id=30)

        def click_shape_center_then_view(self, view, shape, target, **_kwargs):
            actions.append(("tab", view, shape, target))
            yield "tab"
            return SimpleNamespace(id=target)

        def wait_shape(self, view, shape, **_kwargs):
            actions.append(("shape", view, shape))
            yield "shape"
            return "frame"

        def wait_click(self, view, shape, **_kwargs):
            actions.append(("click", view, shape))
            yield "click"

        def wait_action_settle(self, _seconds):
            yield "settle"

        def ocr_text(self, **_kwargs):
            return "吉签启鸿运，佳奖落君身！祝贺道友抽得大奖"

    runtime = Runtime()
    row = SimpleNamespace(x=100, y=200, w=80, h=40)
    monkeypatch.setattr(
        runner,
        "_daily_qmch_copy_box",
        lambda *_args: {"x": 10, "y": 20, "w": 30, "h": 40},
    )
    monkeypatch.setattr(
        runner,
        "_wait_daily_qmch_activity_row",
        lambda *_args, **_kwargs: _yield_return(("frame", row)),
    )
    active = _runtime_snapshot([_qmch_item()])
    terminal = _runtime_snapshot([
        _qmch_item(detail_loaded=True, exclusion_reasons=["detail_rewarded"])
    ])
    snapshots = iter([active, terminal])
    monkeypatch.setattr(runner, "_daily_redpacket_runtime_candidates", lambda: next(snapshots))
    monkeypatch.setattr(
        module,
        "read_chat_channel_gui_target",
        lambda channel, sub_id: {
            "channel": channel,
            "sub_channel_id": sub_id,
            "group_type": 1,
            "tab_label": "活动",
            "anchors": ["吉签启鸿运"],
        },
    )
    monkeypatch.setattr(
        module,
        "read_repeated_chat_phrase",
        lambda *_args, **_kwargs: {
            "ready": True,
            "phrase": "吉签启鸿运，佳奖落君身！祝贺道友抽得大奖",
        },
    )
    result = _consume(runner._execute_daily_qmch_reward_route(
        runtime,
        {},
        None,
        {"transition_timeout_seconds": 3, "poll_seconds": 0.2},
        {
            "route": "qmch_reward",
            "channel": 101,
            "sub_id": 20050134,
            "uids": ["qmch-uid"],
        },
    ))

    assert result["opened_count"] == 1
    assert ("click", 34, "聊天") in actions
    assert ("point", 673, 25.0, 40.0) in actions
    assert ("click", 390, "发送") in actions
    assert [item for item in actions if item == ("click", 397, "开")] == [
        ("click", 397, "开")
    ]
    assert actions[-3:] == [
        ("shape_click", 672, "弹窗外背景", {"x_ratio": 0.1}),
        ("tab", 30, "返回", 332),
        ("tab", 332, "返回", 34),
    ]


def test_daily_redpacket_qmch_copy_icon_accepts_one_parent_scoped_candidate(monkeypatch):
    runner = _Runner()
    card = {"title": "鸿运福签卡片"}
    copy_shape = {"title": "复制", "pixelTolerance": 30}
    monkeypatch.setattr(
        runner,
        "_find_shape",
        lambda _image, title: card if title == "鸿运福签卡片" else copy_shape,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_match_shape",
        lambda *_args, **_kwargs: {
            "matched": False,
            "matches": [
                {
                    "crop_similarity": 66.0,
                    "box": {"x": 500, "y": 510, "w": 60, "h": 57},
                }
            ],
        },
        raising=False,
    )

    assert runner._daily_qmch_copy_box(
        {"images": {673: {"id": 673}}},
        "frame",
    ) == {"x": 500, "y": 510, "w": 60, "h": 57}


def test_daily_redpacket_qmch_runtime_tab_mismatch_has_zero_gui_actions(monkeypatch):
    runner = _Runner()

    class Runtime:
        def __getattr__(self, name):
            pytest.fail(f"Runtime tab mismatch must fail before GUI action: {name}")

    monkeypatch.setattr(
        module,
        "read_chat_channel_gui_target",
        lambda _channel, _sub_id: {"group_type": 2, "tab_label": "群聊"},
    )

    with pytest.raises(RuntimeError, match="未对齐活动 Tab"):
        _consume(runner._execute_daily_qmch_reward_route(
            Runtime(),
            {},
            None,
            {},
            {
                "route": "qmch_reward",
                "channel": 101,
                "sub_id": 20050134,
                "uids": ["qmch-uid"],
            },
        ))


def test_daily_redpacket_qmch_copied_phrase_mismatch_never_sends_or_opens(monkeypatch):
    runner = _Runner()
    actions = []

    class Runtime:
        def cur_frame(self, **_kwargs):
            return "frame"

        def click_frame_point(self, view, x, y):
            actions.append(("point", view, x, y))

        def wait_view(self, *views, **_kwargs):
            yield "wait"
            for view in (332, 390, 397, 30):
                if view in views:
                    return SimpleNamespace(id=view)
            return SimpleNamespace(id=30)

        def click_shape_center_then_view(self, *_args, **_kwargs):
            yield "tab"
            return SimpleNamespace(id=332)

        def wait_shape(self, *_args, **_kwargs):
            yield "shape"

        def click_shape_center(self, view, shape, **_kwargs):
            actions.append((view, shape))

        def wait_click(self, view, shape, **_kwargs):
            actions.append(("click", view, shape))
            yield "click"

        def ocr_text(self, **_kwargs):
            return "无法识别的内容"

    monkeypatch.setattr(
        runner,
        "_daily_qmch_copy_box",
        lambda *_args: {"x": 0, "y": 0, "w": 10, "h": 10},
    )
    monkeypatch.setattr(
        runner,
        "_wait_daily_qmch_activity_row",
        lambda *_args, **_kwargs: _yield_return(("frame", SimpleNamespace(x=0, y=0, w=10, h=10))),
    )
    monkeypatch.setattr(
        module,
        "read_chat_channel_gui_target",
        lambda channel, sub_id: {
            "channel": channel,
            "sub_channel_id": sub_id,
            "group_type": 1,
            "tab_label": "活动",
            "anchors": ["吉签启鸿运"],
        },
    )
    monkeypatch.setattr(
        module,
        "read_repeated_chat_phrase",
        lambda *_args, **_kwargs: {
            "ready": True,
            "phrase": "吉签启鸿运，佳奖落君身！祝贺道友抽得大奖",
        },
    )
    with pytest.raises(RuntimeError, match="#390 未回读"):
        _consume(runner._execute_daily_qmch_reward_route(
            Runtime(),
            {},
            None,
            {},
            {
                "route": "qmch_reward",
                "channel": 101,
                "sub_id": 20050134,
                "uids": ["qmch-uid"],
            },
        ))

    assert ("click", 390, "发送") not in actions
    assert ("click", 397, "开") not in actions


def test_daily_redpacket_has_business_owned_trigger_description():
    tasks = default_data_annotation_scheduler_tasks()
    assert not any(task["id"] == "legacy-daily-zongmen-redpacket" for task in tasks)
    task = next(task for task in tasks if task["id"] == "daily-redpacket")
    assert task["task_type"] == "daily_redpacket"
    assert task["label"] == "日常_红包"
    assert task["trigger_description"] == "动态"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 600
    assert task["payload"]["interval_seconds"] == 43200


def test_daily_redpacket_repair_strips_old_trigger_fields_without_migration_rules():
    defaults = default_data_annotation_scheduler_tasks()
    repaired, changed = repair_data_annotation_scheduler_tasks(
        [
            {
                "id": "legacy-daily-zongmen-redpacket",
                "task_type": "legacy_daily_task",
                "label": "日常_宗门红包",
                "enabled": True,
                "schedule_kind": "daily",
                "schedule_times": ["05:00", "12:00"],
            }
        ],
        default_tasks=defaults,
        facts={},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 22, 12, 0, 0),
    )

    assert changed is True
    legacy = next(task for task in repaired if task["id"] == "legacy-daily-zongmen-redpacket")
    assert "enabled" not in legacy
    assert "schedule_kind" not in legacy
    assert "schedule_times" not in legacy
    replacement = next(task for task in repaired if task["id"] == "daily-redpacket")
    assert "enabled" not in replacement
    assert replacement["trigger_description"] == "动态"


def test_daily_redpacket_repair_keeps_explicit_error_retry_interval():
    defaults = default_data_annotation_scheduler_tasks()
    existing = dict(next(task for task in defaults if task["id"] == "daily-redpacket"))
    existing["cooldown_seconds"] = 300
    existing["error_retry_delay_seconds"] = 1800
    existing["payload"] = {"interval_seconds": 1800}

    repaired, changed = repair_data_annotation_scheduler_tasks(
        [existing],
        default_tasks=defaults,
        facts={},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 22, 12, 0, 0),
    )

    task = next(task for task in repaired if task["id"] == "daily-redpacket")
    assert changed is True
    assert task["error_retry_delay_seconds"] == 1800
    assert task["payload"]["interval_seconds"] == 1800


def test_daily_redpacket_is_registered_as_scheduler_job_without_scene_policy():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_redpacket")
    assert definition is not None
    assert definition.label == "日常_红包"
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


def test_daily_redpacket_normal_completion_advances_twelve_hours(monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: datetime(2026, 7, 22, 12, 0, 0))
    runner = _Runner()
    result = runner._daily_redpacket_result({}, "本轮无红包")
    assert result["result"] == "success"
    assert "next_time" not in result
    assert runner.recorded == [
        ("daily-redpacket", "2026-07-23 00:00:00")
    ]


def test_daily_redpacket_business_failure_also_advances_twelve_hours(monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: datetime(2026, 7, 22, 12, 0, 0))
    runner = _Runner()

    result = runner._daily_redpacket_result({}, "Runtime 群未命中")

    assert result["result"] == "success"
    assert "next_time" not in result
    assert runner.recorded == [
        ("daily-redpacket", "2026-07-23 00:00:00")
    ]


def test_daily_redpacket_ocr_uses_regex_and_returns_reading_order():
    runner = _OcrRunner()
    matches = runner._daily_redpacket_ocr_targets({}, {"id": 30}, "frame")
    assert [item["matched_text"] for item in matches] == ["奖赏", "红包"]
    assert (matches[0]["x"], matches[0]["y"]) == (40.0, 50.0)


def test_daily_redpacket_ocr_pattern_accepts_wrapped_first_place_message():
    assert module.REDPACKET_OCR_PATTERN.search("8跨【天河仙会】获赠第")


def test_daily_redpacket_ocr_pattern_accepts_boss_kill_ocr_variants():
    assert module.REDPACKET_OCR_PATTERN.search("首领累杀奖赏").group(0) == "首领累杀"
    assert module.REDPACKET_OCR_PATTERN.search("首领猎杀").group(0) == "首领猎杀"


def test_daily_redpacket_ocr_excludes_claimed_card_and_history_notice():
    runner = _ClaimedOcrRunner()

    matches = runner._daily_redpacket_ocr_targets({}, {"id": 30}, "frame")

    assert [(item["matched_text"], item["line_text"]) for item in matches] == [
        ("首领累杀", "首领累杀奖赏"),
    ]
    assert matches[0]["y"] == 255.0


def test_daily_redpacket_ocr_accepts_first_place_before_wrapped_name_suffix():
    runner = _WrappedFirstPlaceOcrRunner()

    matches = runner._daily_redpacket_ocr_targets({}, {"id": 30}, "frame")

    assert [(item["matched_text"], item["line_text"]) for item in matches] == [
        ("第一", "【炼体法相】预赛第一"),
    ]


def test_daily_redpacket_selects_latest_eligible_card():
    target = _Runner._select_daily_redpacket_ocr_target([
        {"matched_text": "红包", "x": 100.0, "y": 300.0},
        {"matched_text": "奖赏", "x": 120.0, "y": 700.0},
    ])

    assert target == {"matched_text": "奖赏", "x": 120.0, "y": 700.0}


def test_daily_redpacket_maps_ocr_text_to_card_envelope_lane():
    runner = _OcrRunner()

    point = runner._daily_redpacket_card_click_point(
        {"id": 30},
        {"matched_text": "第一名", "x": 610.0, "y": 1210.0},
    )

    assert point == (105.0, 1210.0)


def test_daily_redpacket_tries_only_safe_hotspots_on_the_same_card():
    runner = _OcrRunner()

    points = runner._daily_redpacket_card_click_points(
        {"id": 30},
        {"matched_text": "第一名", "x": 240.0, "y": 1210.0},
    )

    assert points == [
        (105.0, 1210.0),
        (186.0, 1210.0),
        (240.0, 1210.0),
    ]


def test_daily_redpacket_waits_for_same_live_ocr_target_twice_before_click_authority(monkeypatch):
    runner = _OcrRunner()
    frames = iter(["loading", "card-1", "loading-again", "card-2", "card-3"])
    targets_by_frame = {
        "loading": [],
        "card-1": [{"matched_text": "首领累杀", "x": 400.0, "y": 1104.0}],
        "loading-again": [],
        "card-2": [{"matched_text": "首领累杀", "x": 400.0, "y": 1180.0}],
        "card-3": [{"matched_text": "首领累杀", "x": 400.0, "y": 1180.0}],
    }

    class Runtime:
        def cur_frame(self, *, update):
            assert update is True
            return next(frames)

        def wait_action_settle(self, _seconds):
            if False:
                yield None
            return "success"

    monkeypatch.setattr(
        runner,
        "_daily_redpacket_ocr_targets",
        lambda _ctx, _image, frame: targets_by_frame[frame],
    )

    frame, targets = _consume(
        runner._wait_daily_redpacket_ocr_targets(
            Runtime(),
            {"images": {30: {"id": 30}}},
            timeout_seconds=5.0,
            poll_seconds=0.0,
        )
    )

    assert frame == "card-3"
    assert targets[0]["y"] == 1180.0


def test_daily_redpacket_group_click_uses_runtime_row_alignment(monkeypatch):
    runner = _Runner()
    ordinary = {
        "uid": "ordinary-uid",
        "id": 520,
        "channel": 6,
        "sub_channel_id": 0,
        "trigger_candidate": True,
        "action_authorized": False,
    }
    tokens = [
        {
            "text": char,
            "x": 20 + index * 12,
            "y": 40,
            "w": 10,
            "h": 20,
            "parent_line_id": "preview",
            "order": index,
        }
        for index, char in enumerate("恭喜参加宗门镇邪")
    ]
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {
            "uids": frozenset({"ordinary-uid"}),
            "pending_count": 1,
            "snapshot": _runtime_snapshot([ordinary]),
        },
    )
    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda *_args: {"tokens": tokens},
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "read_chat_channel_gui_target",
        lambda *_args: {"anchors": ["宗门镇邪"], "tab_label": "群聊"},
    )

    class Runtime:
        def __init__(self):
            self.clicks = []

        def shape(self, _view, path):
            assert path == "窗口"
            return SimpleNamespace(box=lambda: {"x": 0, "y": 0, "w": 300, "h": 300})

        def click_shape_center_then_view(self, view, shape, target, **_kwargs):
            assert (view, shape, target) == (332, "群聊", 332)
            yield "tab"
            return SimpleNamespace(id=332)

        def cur_frame(self, *, update):
            assert update is True
            return "frame"

        def click_frame_point(self, view, x, y):
            self.clicks.append((view, x, y))

        def wait_action_settle(self, _seconds):
            yield "settled"

    runtime = Runtime()
    result = _consume(runner._find_and_click_daily_redpacket_group(
        runtime, {}, max_scrolls=0, settle_seconds=0.1
    ))
    assert result["channel"] == 6
    assert result["sub_channel_id"] == 0
    assert result["anchor"] == "宗门镇邪"
    assert runtime.clicks == [(332, 165.0, 50.0)]


def test_daily_redpacket_runtime_row_anchor_negative_never_clicks(monkeypatch):
    runner = _Runner()
    ordinary = {
        "uid": "ordinary-uid",
        "id": 520,
        "channel": 6,
        "sub_channel_id": 0,
        "trigger_candidate": True,
        "action_authorized": False,
    }
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {
            "uids": frozenset({"ordinary-uid"}),
            "pending_count": 1,
            "snapshot": _runtime_snapshot([ordinary]),
        },
    )
    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda *_args: {"tokens": []},
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "read_chat_channel_gui_target",
        lambda *_args: {"anchors": ["宗门镇邪"], "tab_label": "群聊"},
    )

    class Runtime:
        def shape(self, _view, path):
            assert path == "窗口"
            return SimpleNamespace(box=lambda: {"x": 0, "y": 0, "w": 300, "h": 300})

        def click_shape_center_then_view(self, view, shape, target, **_kwargs):
            assert (view, shape, target) == (332, "群聊", 332)
            yield "tab"
            return SimpleNamespace(id=332)

        def cur_frame(self, *, update):
            assert update is True
            return "frame"

        def click_frame_point(self, *_args):
            pytest.fail("unmatched Runtime row anchor must not click a chat row")

    result = _consume(runner._find_and_click_daily_redpacket_group(
        Runtime(),
        {},
        max_scrolls=0,
        settle_seconds=0.1,
    ))

    assert result is None


def test_daily_redpacket_initial_group_gate_scans_bounded_list_once(monkeypatch):
    runner = _Runner()
    calls = []

    def find_group(_runtime, _ctx, *, max_scrolls, settle_seconds):
        calls.append((max_scrolls, settle_seconds))
        if False:
            yield None
        return {"scroll_index": 9}

    monkeypatch.setattr(
        runner,
        "_find_and_click_daily_redpacket_group",
        find_group,
    )

    result = _consume(runner._wait_and_click_daily_redpacket_group(
        object(),
        {},
        timeout_seconds=60,
        poll_seconds=0.8,
        max_scrolls=12,
    ))

    assert result == {"scroll_index": 9}
    assert calls == [(12, 0.8)]


def test_daily_redpacket_world_visual_gate_negative_never_clicks(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: datetime(2026, 7, 22, 12, 0, 0))
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")

    class Runtime:
        def cur_frame(self, *, update):
            assert update is True
            return "world-frame"

        def __getattr__(self, name):
            pytest.fail(f"negative #395 gate must not probe GUI via {name}")

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: Runtime(),
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_quick_gate",
        lambda *_args, **_kwargs: {"matched": False},
    )
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_runtime_candidates",
        lambda: _runtime_snapshot([]),
    )

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {},
    ))

    assert result["result"] == "success"
    assert "#395[红包] 阴性且无新鲜完整 Runtime 候选" in result["message"]
    assert "next_time" not in result


def test_daily_redpacket_world_gate_matches_395_redpacket_shape_without_scene_identification(monkeypatch):
    runner = _Runner()
    redpacket_shape = {
        "title": "红包",
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "imageMatchRole": "required",
    }
    image = {"id": 395, "shapes": [redpacket_shape]}
    calls = []

    monkeypatch.setattr(
        runner,
        "_find_shape",
        lambda actual_image, title: redpacket_shape
        if actual_image is image and title == "红包"
        else None,
        raising=False,
    )

    def match_shape(ctx, actual_image, shape, frame, *, condition):
        calls.append((ctx, actual_image, shape, frame, condition))
        return {"matched": True, "similarity": 96.0}

    monkeypatch.setattr(runner, "_match_shape", match_shape, raising=False)
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda *_args, **_kwargs: pytest.fail("红包业务门卫不得识别 #395 场景"),
        raising=False,
    )

    result = runner._daily_redpacket_quick_gate({"images": {395: image}}, "live-world-frame")

    assert result["matched"] is True
    assert calls == [
        ({"images": {395: image}}, image, redpacket_shape, "live-world-frame", "image")
    ]


def test_daily_redpacket_chat_visual_gate_negative_returns_success_without_deeper_clicks(
    tmp_path,
    monkeypatch,
):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    calls = []

    class Runtime:
        def cur_frame(self, *, update):
            assert update is True
            return "world-frame"

        def click_shape_center(self, scene_id, shape):
            calls.append(("click", scene_id, shape))

        def wait_view(self, *views, **_kwargs):
            if False:
                yield None
            return SimpleNamespace(id=views[0])

        def click_shape_center_then_view(self, scene_id, shape, target, **_kwargs):
            calls.append(("shape", scene_id, shape, target))
            if False:
                yield None
            return SimpleNamespace(id=target)

        def wait_shape(self, scene_id, shape, **_kwargs):
            if (scene_id, shape) == (395, "聊天"):
                if False:
                    yield None
                return
            assert (scene_id, shape) == (332, "红包")
            if False:
                yield None
            raise TimeoutError("not visible")

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime, raising=False)
    monkeypatch.setattr(runner, "_daily_redpacket_quick_gate", lambda *_args: {"matched": True})
    monkeypatch.setattr(
        runner,
        "_close_daily_redpacket_chat_to_world",
        lambda *_args, **_kwargs: _yield_return(SimpleNamespace(id=34)),
    )
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {
            "uids": frozenset({"uid-1", "uid-2"}),
            "pending_count": 2,
        },
    )
    monkeypatch.setattr(
        runner,
        "_wait_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("not visible")),
    )

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {"redpacket_confirm_seconds": 1},
    ))

    assert result["result"] == "success"
    assert "chat_gui_unaligned" in result["message"]
    assert result["unclaimable_uids"] == ["uid-1", "uid-2"]
    assert calls == [
        ("click", 395, "聊天"),
        ("shape", 332, "全部", 332),
    ]


def test_daily_redpacket_switches_from_preserved_contacts_tab_to_chat(tmp_path, monkeypatch):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    calls = []

    class Runtime:
        def cur_frame(self, *, update):
            assert update is True
            return "world-frame"

        def click_shape_center(self, scene_id, shape):
            calls.append(("click", scene_id, shape))

        def wait_view(self, *views, **_kwargs):
            if False:
                yield None
            return SimpleNamespace(id=333)

        def click_shape_center_then_view(self, scene_id, shape, target, **_kwargs):
            calls.append(("shape", scene_id, shape, target))
            if False:
                yield None
            return SimpleNamespace(id=target)

        def wait_shape(self, scene_id, shape, **_kwargs):
            if (scene_id, shape) == (395, "聊天"):
                if False:
                    yield None
                return
            assert (scene_id, shape) == (332, "红包")
            if False:
                yield None
            raise TimeoutError("not visible")

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime, raising=False)
    monkeypatch.setattr(runner, "_daily_redpacket_quick_gate", lambda *_args: {"matched": True})
    monkeypatch.setattr(
        runner,
        "_close_daily_redpacket_chat_to_world",
        lambda *_args, **_kwargs: _yield_return(SimpleNamespace(id=34)),
    )
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {"uids": frozenset(), "pending_count": 0},
    )
    monkeypatch.setattr(
        runner,
        "_wait_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("not visible")),
    )

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {"redpacket_confirm_seconds": 1},
    ))

    assert result["result"] == "success"
    assert calls == [
        ("click", 395, "聊天"),
        ("shape", 333, "聊天", 332),
        ("shape", 332, "全部", 332),
    ]


@pytest.mark.parametrize(
    ("view_ids", "expected_clicks", "expected_opened"),
    [
        ([399, 30], [(399, "返回")], 0),
        (
            [397, 398, 397, 398, 397, 399, 30],
            [(397, "开"), (398, "下一个"), (397, "开"), (398, "下一个"), (397, "开"), (399, "返回")],
            3,
        ),
    ],
)
def test_daily_redpacket_claim_loop_is_idempotent_and_stops_at_399(view_ids, expected_clicks, expected_opened):
    runner = _Runner()
    runtime = _Runtime(view_ids)
    opened = _consume(runner._claim_daily_redpackets(runtime, transition_timeout=10, max_open_count=20))
    assert opened == expected_opened
    assert runtime.clicks == expected_clicks


def test_daily_redpacket_treats_claim_quota_toast_as_terminal():
    runner = _Runner()

    class Runtime(_Runtime):
        def __init__(self):
            super().__init__([397])
            self.attrs = {}

        def cur_frame(self, *, update):
            assert update is True
            return "quota-frame"

        def ocr_text(self, frame):
            assert frame == "quota-frame"
            return "领取次数不足，无法开启红包"

    runtime = Runtime()

    opened = _consume(
        runner._claim_daily_redpackets(
            runtime,
            transition_timeout=10,
            max_open_count=20,
        )
    )

    assert opened == 0
    assert runtime.attrs["daily_redpacket_quota_exhausted"] is True
    assert runtime.clicks == [(397, "开")]
    assert runner.logs[-1] == (
        "success",
        "日常_红包：今日领取次数不足，停止继续开启红包",
    )


def test_daily_redpacket_uses_bounded_scene30_locator_before_card_click(monkeypatch):
    runner = _Runner()
    calls = []

    class Runtime:
        def wait_view(self, *view_ids, **_kwargs):
            calls.append(("wait_view", view_ids))
            if False:
                yield None
            return SimpleNamespace(id=view_ids[0])

        def wait_shape(self, scene_id, shape, **_kwargs):
            calls.append(("wait_shape", scene_id, shape))
            if False:
                yield None

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))

        def click_frame_point(self, scene_id, x, y):
            pytest.fail(f"missing card OCR must not click a card hotspot: {(scene_id, x, y)}")

        def wait_action_settle(self, *_args):
            if False:
                yield None

    def no_targets(*_args, **_kwargs):
        if False:
            yield None
        raise TimeoutError("no target")

    monkeypatch.setattr(runner, "_wait_daily_redpacket_ocr_targets", no_targets)
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {"uids": frozenset({"uid-1"}), "pending_count": 1},
    )

    result = _consume(runner._process_current_daily_redpacket_group(
        Runtime(),
        {},
        transition_timeout=10,
        poll_seconds=1,
        max_open_count=20,
        max_locator_clicks=2,
    ))

    assert result == (0, False)
    assert calls == [
        ("wait_view", (30,)),
        ("wait_shape", 30, "红包"),
        ("click_shape_center", 30, "红包"),
        ("wait_shape", 30, "红包"),
        ("click_shape_center", 30, "红包"),
    ]


def test_daily_redpacket_scene30_locator_repeats_until_card_ocr_is_stable(monkeypatch):
    runner = _Runner()
    calls = []
    probes = iter([
        TimeoutError("current viewport has no card"),
        TimeoutError("first locator click has not reached the card"),
        ("card-frame", [{"matched_text": "首领累杀", "x": 500.0, "y": 600.0}]),
    ])

    class Runtime:
        def wait_view(self, *view_ids, **_kwargs):
            calls.append(("wait_view", view_ids))
            if False:
                yield None
            return SimpleNamespace(id=view_ids[0])

        def wait_shape(self, scene_id, shape, **_kwargs):
            calls.append(("wait_shape", scene_id, shape))
            if False:
                yield None

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))

        def click_frame_point(self, scene_id, x, y):
            calls.append(("click_frame_point", scene_id, x, y))

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    def probe(*_args, **_kwargs):
        result = next(probes)
        if isinstance(result, Exception):
            if False:
                yield None
            raise result
        if False:
            yield None
        return result

    monkeypatch.setattr(runner, "_wait_daily_redpacket_ocr_targets", probe)
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_card_click_points",
        lambda _image, _target: [(321.0, 654.0)],
    )
    monkeypatch.setattr(
        runner,
        "_claim_daily_redpackets",
        lambda *_args, **_kwargs: _yield_return(1),
    )
    snapshots = iter([
        {"uids": frozenset({"uid-1"}), "pending_count": 1},
        {"uids": frozenset(), "pending_count": 0},
    ])
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: next(snapshots),
    )

    result = _consume(runner._process_current_daily_redpacket_group(
        Runtime(),
        {"images": {30: {"id": 30}}},
        transition_timeout=10,
        poll_seconds=1,
        max_open_count=20,
        max_locator_clicks=5,
    ))

    assert result == (1, True)
    assert calls == [
        ("wait_view", (30,)),
        ("wait_shape", 30, "红包"),
        ("click_shape_center", 30, "红包"),
        ("wait_shape", 30, "红包"),
        ("click_shape_center", 30, "红包"),
        ("click_frame_point", 30, 321.0, 654.0),
        ("wait_view", (397, 399, 672)),
    ]


def test_daily_redpacket_sold_out_is_consumed_locally_and_returns_to_scene30(monkeypatch):
    runner = _Runner()
    calls = []

    class Runtime:
        def wait_view(self, *view_ids, **_kwargs):
            calls.append(("wait_view", view_ids))
            if False:
                yield None
            return SimpleNamespace(id=30 if view_ids == (30,) else 672)

        def click_frame_point(self, scene_id, x, y):
            calls.append(("click_frame_point", scene_id, x, y))

        def click_shape_center_then_view(self, scene_id, shape, target, **_kwargs):
            calls.append(("dismiss", scene_id, shape, target))
            if False:
                yield None
            return SimpleNamespace(id=target)

    monkeypatch.setattr(
        runner,
        "_wait_daily_redpacket_ocr_targets",
        lambda *_args, **_kwargs: _yield_return(
            ("fresh-card", [{"matched_text": "首领累杀", "x": 500.0, "y": 600.0}])
        ),
    )
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_card_click_points",
        lambda _image, _target: [(321.0, 654.0)],
    )
    snapshots = iter([
        {"uids": frozenset({"uid-1"}), "pending_count": 1},
        {"uids": frozenset({"uid-1"}), "pending_count": 1},
    ])
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: next(snapshots),
    )

    result = _consume(runner._process_current_daily_redpacket_group(
        Runtime(),
        {"images": {30: {"id": 30}}},
        transition_timeout=10,
        poll_seconds=1,
        max_open_count=20,
    ))

    assert result == (0, False)
    assert ("wait_view", (397, 399, 672)) in calls
    assert ("dismiss", 672, "弹窗外背景", 30) in calls
    assert any("当前传音群红包已抢光" in message for _kind, message in runner.logs)


def test_daily_redpacket_uid_postcondition_rejects_unchanged_claim_result(monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {"uids": frozenset({"uid-1"}), "pending_count": 1},
    )

    with pytest.raises(RuntimeError, match="UID 集合未减少"):
        runner._daily_redpacket_verify_uid_postcondition(
            {"uids": frozenset({"uid-1"}), "pending_count": 1},
            phase="领取",
            require_reduction=True,
        )


def test_daily_redpacket_fresh_uid_snapshot_fails_closed_when_incomplete(monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_runtime_candidates",
        lambda: {
            "evidence_levels": {"structural": False, "semantic": False},
            "reason": "stale",
        },
    )

    with pytest.raises(RuntimeError, match="结构或语义不完整"):
        runner._daily_redpacket_require_fresh_uid_snapshot(phase="before")


def test_scene672_is_a_redpacket_local_layer0_not_global_popup():
    from backend.core.fanxiu.data_annotation.storage import (
        data_annotation_asset_tree_path,
        read_data_annotation_asset_tree_snapshot,
    )
    from scripts.fanxiu_patch_redpacket_sold_out import validate_tree

    snapshot = read_data_annotation_asset_tree_snapshot(data_annotation_asset_tree_path())
    validate_tree(snapshot.tree)


def test_daily_redpacket_treats_missing_locator_count_after_click_as_group_terminal(monkeypatch):
    runner = _Runner()
    calls = []
    card_probes = iter([
        TimeoutError("current viewport has no card"),
        TimeoutError("locator reached no unclaimed card"),
    ])
    locator_available = iter([True, False])

    class Runtime:
        def wait_view(self, *view_ids, **_kwargs):
            calls.append(("wait_view", view_ids))
            if False:
                yield None
            return SimpleNamespace(id=view_ids[0])

        def wait_shape(self, scene_id, shape, **_kwargs):
            calls.append(("wait_shape", scene_id, shape))
            if not next(locator_available):
                if False:
                    yield None
                raise TimeoutError("OCR=福")
            if False:
                yield None

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))

        def click_frame_point(self, scene_id, x, y):
            pytest.fail(f"missing card OCR must not click a card hotspot: {(scene_id, x, y)}")

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    def no_targets(*_args, **_kwargs):
        error = next(card_probes)
        if False:
            yield None
        raise error

    monkeypatch.setattr(runner, "_wait_daily_redpacket_ocr_targets", no_targets)
    monkeypatch.setattr(
        runner,
        "_daily_redpacket_require_fresh_uid_snapshot",
        lambda **_kwargs: {"uids": frozenset({"uid-1"}), "pending_count": 1},
    )

    result = _consume(runner._process_current_daily_redpacket_group(
        Runtime(),
        {},
        transition_timeout=10,
        poll_seconds=1,
        max_open_count=20,
        max_locator_clicks=5,
    ))

    assert result == (0, False)
    assert calls == [
        ("wait_view", (30,)),
        ("wait_shape", 30, "红包"),
        ("click_shape_center", 30, "红包"),
        ("wait_shape", 30, "红包"),
    ]
    assert runner.logs[-1] == (
        "success",
        "日常_红包：#30 右上定位入口无数字角标，当前群没有更多待领红包",
    )


def test_daily_redpacket_exit_uses_verified_chat_return_chain():
    calls = []

    class Runtime:
        def click_shape_center_then_view(self, scene_id, shape, target, **kwargs):
            calls.append((scene_id, shape, target, kwargs))
            yield "clicked"
            return SimpleNamespace(id=target)

    _consume(_Runner()._exit_daily_redpacket_group_to_world(Runtime(), transition_timeout=15))

    assert calls == [
        (30, "返回", [332, 20, 34], {"timeout": 15}),
        (332, "返回", 34, {"timeout": 15}),
    ]


def test_daily_redpacket_group_return_falls_back_to_the_dimmed_outside_area():
    calls = []

    class Window:
        def box(self):
            return {"x": 90, "y": 490, "w": 760, "h": 745}

    class Runtime:
        def click_shape_center_then_view(self, *_args, **_kwargs):
            if False:
                yield None
            raise TimeoutError("back button intercepted")

        def current_scene(self, *_args, **_kwargs):
            return 30, 100.0, "frame"

        def shape(self, scene_id, shape):
            assert (scene_id, shape) == (30, "窗口")
            return Window()

        def click_frame_point(self, scene_id, x, y):
            calls.append(("outside", scene_id, x, y))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, *scene_ids, **kwargs):
            calls.append(("wait", scene_ids, kwargs))
            if False:
                yield None
            return SimpleNamespace(id=34)

    result = _consume(
        _Runner()._return_daily_redpacket_group_to_list(
            Runtime(),
            transition_timeout=15,
        )
    )

    assert result.id == 34
    assert calls == [
        ("outside", 30, 45.0, 862.5),
        ("settle", 1.0),
        (
            "wait",
            (332, 20, 34),
            {
                "timeout": 15,
                "label": "日常_红包：点击群聊弹层外部返回",
            },
        ),
    ]


def test_daily_redpacket_chat_close_falls_back_to_the_dimmed_outside_area():
    calls = []

    class Window:
        def box(self):
            return {"x": 90, "y": 600, "w": 760, "h": 700}

    class Runtime:
        def click_shape_center_then_view(self, *_args, **_kwargs):
            if False:
                yield None
            raise TimeoutError("back button intercepted")

        def current_scene(self, *_args, **_kwargs):
            return 332, 100.0, "frame"

        def shape(self, scene_id, shape):
            assert (scene_id, shape) == (332, "窗口")
            return Window()

        def click_frame_point(self, scene_id, x, y):
            calls.append(("outside", scene_id, x, y))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, *views, **kwargs):
            calls.append(("wait_view", views, kwargs))
            if False:
                yield None
            return SimpleNamespace(id=34)

    result = _consume(_Runner()._close_daily_redpacket_chat_to_world(Runtime(), transition_timeout=15))

    assert result.id == 34
    assert calls == [
        ("outside", 332, 45.0, 950.0),
        ("settle", 1.0),
        ("wait_view", (34,), {"timeout": 15, "label": "日常_红包：点击聊天弹层外部返回世界"}),
    ]


def test_daily_redpacket_processes_only_visually_matched_group_icons(tmp_path, monkeypatch):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    calls = []
    groups = iter([{"id": 1}, {"id": 2}, {"id": 3}])
    group_results = iter([(1, True), (2, True), (1, True)])

    class Runtime:
        attrs = {}

        def cur_frame(self, *, update):
            return "frame"

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))

        def wait_view(self, *views, **kwargs):
            calls.append(("wait_view", views, kwargs))
            yield "waiting"
            return SimpleNamespace(id=views[0])

        def wait_shape(self, scene_id, shape, **kwargs):
            calls.append(("wait_shape", scene_id, shape, kwargs))
            yield "waiting"

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            yield "settled"

        def shape(self, scene_id, shape):
            return (scene_id, shape)

        def scroll_shape_content(self, shape, *, direction):
            pytest.fail("all Runtime-named groups should be found without fallback scanning")

        def click_shape_center_then_view(self, scene_id, shape, target, **kwargs):
            calls.append(("return", scene_id, shape, target, kwargs))
            yield "clicked"
            return SimpleNamespace(id=target)

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime, raising=False)

    def find_group(*_args, **_kwargs):
        if False:
            yield None
        return next(groups)

    process_group_options = []

    def process_group(*_args, **kwargs):
        process_group_options.append(kwargs)
        if len(process_group_options) == 3:
            runtime.attrs["daily_redpacket_quota_exhausted"] = True
        if False:
            yield None
        return next(group_results)

    def return_to_list(*_args, **_kwargs):
        calls.append(("return_to_list",))
        if False:
            yield None
        return SimpleNamespace(id=332)

    monkeypatch.setattr(runner, "_find_and_click_daily_redpacket_group", find_group)
    monkeypatch.setattr(
        runner,
        "_wait_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: _yield_return(next(groups)),
    )
    monkeypatch.setattr(runner, "_process_current_daily_redpacket_group", process_group)
    monkeypatch.setattr(runner, "_return_daily_redpacket_group_to_list", return_to_list)
    monkeypatch.setattr(runner, "_daily_redpacket_quick_gate", lambda *_args: {"matched": True})

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {"max_group_scrolls": 8},
    ))

    assert result["result"] == "success"
    assert result["opened_count"] == 4
    assert "处理 3 个群" in result["message"]
    assert [item["max_open_count"] for item in process_group_options] == [100, 100, 100]
    assert calls.count(("return_to_list",)) == 3
    assert [call for call in calls if call[0] == "scroll"] == []
    assert calls[-1] == ("return", 332, "返回", 34, {"timeout": 15.0})


def test_daily_redpacket_scrolls_toward_later_visual_group_icon(
    tmp_path,
    monkeypatch,
):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    scroll_options = []
    find_results = iter([None] * 9 + [{"id": 1}])

    class Runtime:
        attrs = {}

        def cur_frame(self, *, update):
            assert update is True
            return "world-frame"

        def click_shape_center(self, *_args):
            return None

        def wait_view(self, *views, **_kwargs):
            if False:
                yield None
            return SimpleNamespace(id=views[0])

        def wait_action_settle(self, *_args):
            if False:
                yield None

        def wait_shape(self, *_args, **_kwargs):
            if False:
                yield None

        def shape(self, scene_id, title):
            return (scene_id, title)

        def scroll_shape_content(self, _shape, *, direction, unchanged_confirmations):
            scroll_options.append((direction, unchanged_confirmations))
            if False:
                yield None
            return True

        def click_shape_center_then_view(self, _scene_id, _shape, target, **_kwargs):
            if False:
                yield None
            return SimpleNamespace(id=target)

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime, raising=False)
    monkeypatch.setattr(
        runner,
        "_find_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: _yield_return(next(find_results)),
    )
    monkeypatch.setattr(
        runner,
        "_wait_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: _yield_return({"id": 0}),
    )

    process_count = 0

    def process_group(*_args, **_kwargs):
        nonlocal process_count
        process_count += 1
        if process_count == 2:
            runtime.attrs["daily_redpacket_quota_exhausted"] = True
        if False:
            yield None
        return 1, True

    monkeypatch.setattr(
        runner,
        "_process_current_daily_redpacket_group",
        process_group,
    )
    monkeypatch.setattr(
        runner,
        "_return_daily_redpacket_group_to_list",
        lambda *_args, **_kwargs: _yield_return(SimpleNamespace(id=332)),
    )
    monkeypatch.setattr(runner, "_daily_redpacket_quick_gate", lambda *_args: {"matched": True})

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {},
    ))

    assert result["result"] == "success"
    # ActionPlanner's direction describes content loading: "down" generates
    # an upward finger swipe and exposes later rows.
    assert scroll_options == [("down", 2)] * 9


def test_daily_redpacket_treats_group_return_to_green_bottle_as_chat_stack_exit(tmp_path, monkeypatch):
    runner = _Runner()
    path = tmp_path / "asset-tree.json"
    path.write_text("[]", encoding="utf-8")
    calls = []

    class Runtime:
        def cur_frame(self, *, update):
            return "frame"

        def click_shape_center(self, scene_id, shape):
            calls.append(("click_shape_center", scene_id, shape))

        def wait_view(self, *views, **kwargs):
            if False:
                yield None
            return SimpleNamespace(id=views[0])

        def wait_shape(self, *_args, **_kwargs):
            if False:
                yield None

        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield None

        def shape(self, scene_id, shape):
            return (scene_id, shape)

        def goto_view(self, scene_id):
            calls.append(("goto_view", scene_id))
            if False:
                yield None
            return SimpleNamespace(id=scene_id)

        def click_shape_center_then_view(self, scene_id, shape, target, **_kwargs):
            calls.append(("tab", scene_id, shape, target))
            if False:
                yield None
            return SimpleNamespace(id=target)

    runtime = Runtime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime, raising=False)
    monkeypatch.setattr(
        runner,
        "_find_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: _yield_return({"id": 1}),
    )
    monkeypatch.setattr(
        runner,
        "_wait_and_click_daily_redpacket_group",
        lambda *_args, **_kwargs: _yield_return({"id": 1}),
    )
    monkeypatch.setattr(
        runner,
        "_process_current_daily_redpacket_group",
        lambda *_args, **_kwargs: _yield_return((0, False)),
    )
    monkeypatch.setattr(
        runner,
        "_return_daily_redpacket_group_to_list",
        lambda *_args, **_kwargs: _yield_return(SimpleNamespace(id=20)),
    )
    monkeypatch.setattr(
        runner,
        "_close_daily_redpacket_chat_to_world",
        lambda *_args, **_kwargs: pytest.fail("chat sheet is already gone"),
    )
    monkeypatch.setattr(runner, "_daily_redpacket_quick_gate", lambda *_args: {"matched": True})

    result = _consume(runner._execute_daily_redpacket_task(
        {"asset_tree_path": path},
        None,
        {},
    ))

    assert result["result"] == "success"
    assert calls == [
        ("click_shape_center", 395, "聊天"),
        ("tab", 332, "全部", 332),
        ("goto_view", 34),
    ]
