from datetime import datetime
from threading import Event

from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
    LINGXIAO_ACTIVITY_ID,
    _activity_range_from_fragments,
    activity_end_from_text,
    decide_lingxiao_draw,
    decide_lingxiao_special_recharge,
    next_lingxiao_check_time,
    read_lingxiao_fuling_tasks_runtime,
    read_lingxiao_gui_ticket_draws,
    read_lingxiao_gui_ticket_draws_from_tokens,
    read_lingxiao_fuling_rewards_runtime,
    read_lingxiao_free_track_gui_state,
    read_lingxiao_runtime_state,
    choose_lingxiao_daily_claim,
    should_try_lingxiao_free_track_claim,
    lingxiao_free_track_claim_target_ids,
    build_lingxiao_ten_draw_observations,
    claim_lingxiao_special_recharge_first_free,
)
from backend.core.fanxiu.instrumentation.lingxiao_fuling import (
    _with_normal_track_state,
    build_lingxiao_fuling_panel_snapshot,
)
from backend.core.fanxiu.instrumentation.lingxiao_special_offer import (
    build_lingxiao_special_offer_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.bothdraw import (
    _basic_lottery_snapshot,
    derive_bothdraw_ordinary_draw_delta,
)


def test_lingxiao_ticket_runtime_sums_wallet_and_bound_item_pools(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import lingxiao_xianhui as target

    monkeypatch.setattr(
        target,
        "read_bothdraw_ticket_bindings_runtime",
        lambda **_kwargs: {
            "complete": True,
            "activity_id": LINGXIAO_ACTIVITY_ID,
            "primary_resource_id": 29726,
            "bound_replacement_item_id": 29727,
            "cost_per_draw": 1,
            "evidence": {"pid": 1},
        },
    )
    monkeypatch.setattr(
        target,
        "read_wallet_currency_snapshot",
        lambda *_args, **_kwargs: {"exchange_currency": 3, "evidence": {"pid": 1}},
    )
    monkeypatch.setattr(
        target,
        "read_backpack_item_counts",
        lambda *_args, **_kwargs: ({29727: 13}, {"pid": 1}),
    )

    snapshot = target.read_lingxiao_ticket_runtime()

    assert snapshot["bound_item_draws"] == 13
    assert snapshot["wallet_draws"] == 3
    assert snapshot["available_draws"] == 16


def test_lingxiao_ticket_runtime_propagates_incomplete_panel_identity(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import lingxiao_xianhui as target

    monkeypatch.setattr(
        target,
        "read_bothdraw_ticket_bindings_runtime",
        lambda **_kwargs: {"complete": False, "reason": "not loaded"},
    )

    snapshot = target.read_lingxiao_ticket_runtime()

    assert snapshot["complete"] is False
    assert snapshot["reason"] == "not loaded"


def test_lingxiao_draw_always_uses_ten_control_and_client_caps_remainder() -> None:
    pending = decide_lingxiao_draw(
        activity_id=LINGXIAO_ACTIVITY_ID,
        runtime_draws=0,
        ten_draw_enabled=True,
    )
    assert pending.action == "wait"
    enable = decide_lingxiao_draw(
        activity_id=LINGXIAO_ACTIVITY_ID,
        runtime_draws=10,
        ten_draw_enabled=False,
    )
    assert enable.action == "enable_ten"
    assert decide_lingxiao_draw(
        activity_id=LINGXIAO_ACTIVITY_ID,
        runtime_draws=10,
        ten_draw_enabled=True,
    ).draw_count == 10
    remainder = decide_lingxiao_draw(
        activity_id=LINGXIAO_ACTIVITY_ID,
        runtime_draws=3,
        ten_draw_enabled=True,
    )
    assert (remainder.action, remainder.draw_count) == ("draw", 10)


def test_lingxiao_ten_draw_records_runtime_capped_remainder_as_ten_draw() -> None:
    before = {
        "complete": True,
        "activity_id": LINGXIAO_ACTIVITY_ID,
        "x": 10,
        "y": 1,
        "big_prize_items": [{"id": 300100301, "hit_count": 1}],
        "evidence": {"pid": 1},
    }
    after = {
        "complete": True,
        "activity_id": LINGXIAO_ACTIVITY_ID,
        "x": 13,
        "y": 1,
        "big_prize_items": [{"id": 300100301, "hit_count": 1}],
        "evidence": {"pid": 1},
    }

    recorded_before, recorded_after = build_lingxiao_ten_draw_observations(
        before, after, action_id="runtime-capped-three"
    )

    assert recorded_before["draw_mode"] == "ten_draw"
    assert recorded_after["draw_mode"] == "ten_draw"
    assert recorded_after["requested_batch_size"] == 10
    assert recorded_after["batch_size"] == 3
    assert recorded_after["evidence"]["ordinary_big_prize_delta"] == {
        "draw_delta": 3,
        "big_delta": 0,
        "items": [],
    }


def test_lingxiao_ten_draw_records_all_runtime_big_prize_deltas() -> None:
    before = {
        "complete": True,
        "activity_id": LINGXIAO_ACTIVITY_ID,
        "x": 40,
        "y": 3,
        "big_prize_items": [
            {"id": 11, "reward": "A", "hit_count": 2},
            {"id": 12, "reward": "B", "hit_count": 1},
        ],
    }
    after = {
        "complete": True,
        "activity_id": LINGXIAO_ACTIVITY_ID,
        "x": 50,
        "y": 5,
        "big_prize_items": [
            {"id": 11, "reward": "A", "hit_count": 3},
            {"id": 12, "reward": "B", "hit_count": 2},
        ],
    }

    _recorded_before, recorded_after = build_lingxiao_ten_draw_observations(
        before, after, action_id="two-runtime-big-prizes"
    )

    assert recorded_after["batch_size"] == 10
    assert recorded_after["evidence"]["ordinary_big_prize_delta"]["big_delta"] == 2
    assert {
        (row["id"], row["hit_increment"])
        for row in recorded_after["hit_big_prize_items"]
    } == {(11, 1), (12, 1)}


def test_lingxiao_gui_ticket_count_observes_both_pools_without_becoming_truth() -> None:
    assert read_lingxiao_gui_ticket_draws("0/1 0/1", cost_per_draw=1) == 0
    assert read_lingxiao_gui_ticket_draws("10丨1 10｜1", cost_per_draw=1) == 20
    assert read_lingxiao_gui_ticket_draws("13/1 0/1", cost_per_draw=1) == 13
    assert read_lingxiao_gui_ticket_draws("10/1 9/1", cost_per_draw=1) == 19
    assert read_lingxiao_gui_ticket_draws("10/10 10/10", cost_per_draw=1) is None
    assert read_lingxiao_gui_ticket_draws("10/1", cost_per_draw=1) is None


def test_lingxiao_gui_ticket_count_uses_ocr_line_geometry_before_text_joining() -> None:
    tokens = [
        {"text": "1", "x": 217, "parent_line_id": "left"},
        {"text": "/", "x": 232, "parent_line_id": "left"},
        {"text": "1", "x": 248, "parent_line_id": "left"},
        {"text": "0", "x": 419, "parent_line_id": "right"},
        {"text": "/", "x": 431, "parent_line_id": "right"},
        {"text": "1", "x": 447, "parent_line_id": "right"},
    ]
    assert read_lingxiao_gui_ticket_draws_from_tokens(tokens, cost_per_draw=1) == 1
    matching = [
        {**token, "text": "0"}
        if token["parent_line_id"] == "left" and token["x"] == 217
        else token
        for token in tokens
    ]
    assert read_lingxiao_gui_ticket_draws_from_tokens(matching, cost_per_draw=1) == 0


def test_lingxiao_free_track_gui_state_uses_the_action_label_not_red_dot() -> None:
    assert read_lingxiao_free_track_gui_state("免费奖励") == "unactivated"
    assert read_lingxiao_free_track_gui_state("已 激活") == "activated"
    assert read_lingxiao_free_track_gui_state("领") is None


def test_lingxiao_free_track_claim_requires_complete_runtime_batch_targets() -> None:
    snapshot = {
        "complete": True,
        "normal_track_state": {"complete": True, "activated": True},
        "normal_items": [
            {
                "reward_id": 70002001, "is_box": False,
                "reached": True, "logical_left_mask_active": True,
            },
            {
                "reward_id": 70002002, "is_box": False,
                "reached": True, "logical_left_mask_active": True,
            },
        ],
    }
    assert should_try_lingxiao_free_track_claim(snapshot, free_track_gui_state="activated") is True
    assert lingxiao_free_track_claim_target_ids(
        snapshot, free_track_gui_state="activated"
    ) == (70002001, 70002002)
    assert should_try_lingxiao_free_track_claim(snapshot, free_track_gui_state="unactivated") is False
    assert should_try_lingxiao_free_track_claim(
        {**snapshot, "normal_track_state": {"complete": False}},
        free_track_gui_state="activated",
    ) is False


def test_lingxiao_daily_claim_uses_live_scrollview_order_not_config_position() -> None:
    task_snapshot = {
        "daily": {
            "tasks": [
                {"task_id": 300100301, "state": "claimed", "position": 3},
                {"task_id": 300100302, "state": "claimable", "position": 2},
                {"task_id": 300100303, "state": "claimable", "position": 1},
                {"task_id": 300100304, "state": "pending", "position": 4},
            ]
        }
    }
    ui_snapshot = {
        "rows": [
            {"ui_index": 1, "task_id": 300100302, "is_finished": False},
            {"ui_index": 2, "task_id": 300100303, "is_finished": False},
            {"ui_index": 3, "task_id": 300100304, "is_finished": False},
            {"ui_index": 4, "task_id": 300100301, "is_finished": True},
        ]
    }

    assert choose_lingxiao_daily_claim(task_snapshot, ui_snapshot) == {
        "task_id": 300100302,
        "ui_index": 1,
    }


def test_lingxiao_daily_claim_rejects_runtime_gui_set_mismatch() -> None:
    task_snapshot = {"daily": {"tasks": [{"task_id": i, "state": "claimable"} for i in range(1, 5)]}}
    ui_snapshot = {"rows": [{"ui_index": i, "task_id": i, "is_finished": False} for i in range(1, 4)]}

    try:
        choose_lingxiao_daily_claim(task_snapshot, ui_snapshot)
    except RuntimeError as exc:
        assert "集合不一致" in str(exc)
    else:
        raise AssertionError("mismatched Runtime/GUI task sets must fail closed")


def test_lingxiao_daily_claim_returns_none_when_no_runtime_task_is_claimable() -> None:
    task_snapshot = {"daily": {"tasks": [{"task_id": i, "state": "claimed"} for i in range(1, 5)]}}
    ui_snapshot = {"rows": [{"ui_index": i, "task_id": i, "is_finished": True} for i in range(1, 5)]}

    assert choose_lingxiao_daily_claim(task_snapshot, ui_snapshot) is None


def test_lingxiao_schedule_uses_live_end_boundary() -> None:
    end = activity_end_from_text("08/14 00:00:05-08/16 23:59:45")
    assert end is not None
    assert next_lingxiao_check_time(
        now=datetime(2026, 8, 15, 22, 0, 0), activity_end=end
    ) == "2026-08-16 00:05:00"
    assert next_lingxiao_check_time(
        now=datetime(2026, 8, 16, 22, 0, 0), activity_end=end
    ) == "2026-08-16 23:54:45"
    assert next_lingxiao_check_time(
        now=datetime(2026, 8, 16, 23, 59, 45), activity_end=end
    ) is None


def test_lingxiao_schedule_keeps_final_day_open_until_real_activity_end() -> None:
    end = activity_end_from_text("08/14 00:00:05-08/16 23:59:45")
    assert end is not None
    assert next_lingxiao_check_time(
        now=datetime(2026, 8, 16, 1, 27, 55), activity_end=end
    ) == "2026-08-16 03:27:55"


def test_lingxiao_activity_range_recovers_split_time_strip_tokens() -> None:
    fragments = [
        {"parent_line_id": "time", "x": x, "text": text}
        for x, text in enumerate(("08", "1400", "00", "05-08", "1623", "59", "45"))
    ]
    assert _activity_range_from_fragments(fragments) == "08/14 00:00:05-08/16 23:59:45"


def test_special_recharge_only_allows_the_true_first_round_free_reward() -> None:
    assert decide_lingxiao_special_recharge(
        runtime_round=1,
        runtime_first_free_claimed=False,
        gui_first_free_claimable=True,
    ).action == "claim_first_free"
    assert decide_lingxiao_special_recharge(
        runtime_round=2,
        runtime_first_free_claimed=False,
        gui_first_free_claimable=True,
    ).action == "stop"
    assert decide_lingxiao_special_recharge(
        runtime_round=1,
        runtime_first_free_claimed=None,
        gui_first_free_claimable=True,
    ).action == "wait"


def test_special_offer_uses_loaded_prerequisite_chain_not_free_copywriting() -> None:
    snapshot = build_lingxiao_special_offer_snapshot(
        special_activity_id=3001003,
        current_page_id=3001009,
        packages=[
            {"id": 1, "activityid": 3001009, "sort": 1, "payId": 0, "personlimit": 1, "purchaseEligibility": 0, "optPacCount": 0},
            {"id": 2, "activityid": 3001009, "sort": 2, "payId": 6, "personlimit": 1, "purchaseEligibility": 1, "optPacCount": 0},
            {"id": 3, "activityid": 3001009, "sort": 3, "payId": 0, "personlimit": 1, "purchaseEligibility": 2, "optPacCount": 0},
        ],
        bought_by_offer_id={},
    )
    assert snapshot["state"] == "free_claimable"
    assert snapshot["first_reachable"]["id"] == 1

    after_first = build_lingxiao_special_offer_snapshot(
        special_activity_id=3001003,
        current_page_id=3001009,
        packages=snapshot["packages"],
        bought_by_offer_id={1: 1},
    )
    assert after_first["state"] == "paid_gate"
    assert after_first["free_claimable"] is False
    assert after_first["first_reachable"]["id"] == 2


def test_special_offer_free_package_with_choice_is_not_auto_claimable() -> None:
    snapshot = build_lingxiao_special_offer_snapshot(
        special_activity_id=3001003,
        current_page_id=3001009,
        packages=[
            {"id": 1, "activityid": 3001009, "sort": 1, "payId": 0, "personlimit": 1, "purchaseEligibility": 0, "optPacCount": 1},
        ],
        bought_by_offer_id={},
    )
    assert snapshot["state"] == "free_choice_unmapped"
    assert snapshot["free_claimable"] is None


def test_special_recharge_claims_only_runtime_first_free_offer_and_reads_back(monkeypatch) -> None:
    before = build_lingxiao_special_offer_snapshot(
        special_activity_id=3001003,
        current_page_id=3001009,
        packages=[
            {"id": 1, "activityid": 3001009, "sort": 1, "payId": 0, "personlimit": 1, "purchaseEligibility": 0, "optPacCount": 0},
            {"id": 2, "activityid": 3001009, "sort": 2, "payId": 6, "personlimit": 1, "purchaseEligibility": 1, "optPacCount": 0},
        ],
        bought_by_offer_id={},
    )
    after = build_lingxiao_special_offer_snapshot(
        special_activity_id=3001003,
        current_page_id=3001009,
        packages=[
            {"id": 1, "activityid": 3001009, "sort": 1, "payId": 0, "personlimit": 1, "purchaseEligibility": 0, "optPacCount": 0},
            {"id": 2, "activityid": 3001009, "sort": 2, "payId": 6, "personlimit": 1, "purchaseEligibility": 1, "optPacCount": 0},
        ],
        bought_by_offer_id={1: 1},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_special_recharge_runtime",
        lambda: after,
    )

    class Runtime:
        def wait_click(self, scene, title, **_kwargs):
            yield ("click", scene, title)

        def wait_view(self, scene, **_kwargs):
            yield ("view", scene)
            return scene

    generator = claim_lingxiao_special_recharge_first_free(
        Runtime(), initial_snapshot=before
    )
    events: list[tuple] = []
    try:
        while True:
            events.append(next(generator))
    except StopIteration as done:
        claimed_offer_id = done.value

    assert claimed_offer_id == 1
    assert events == [
        ("click", 577, "第1轮免费领取"),
        ("view", 578),
        ("click", 578, "继续"),
        ("view", 577),
    ]


def test_special_recharge_never_clicks_paid_or_choice_package() -> None:
    choice = build_lingxiao_special_offer_snapshot(
        special_activity_id=3001003,
        current_page_id=3001009,
        packages=[
            {"id": 1, "activityid": 3001009, "sort": 1, "payId": 0, "personlimit": 1, "purchaseEligibility": 0, "optPacCount": 1},
        ],
        bought_by_offer_id={},
    )

    class Runtime:
        def wait_click(self, *_args, **_kwargs):
            raise AssertionError("choice package must not be clicked")

    generator = claim_lingxiao_special_recharge_first_free(
        Runtime(), initial_snapshot=choice
    )
    try:
        next(generator)
    except StopIteration as done:
        assert done.value is None
    else:
        raise AssertionError("unmapped choice package must remain a no-op")


def test_special_offer_rejects_a_package_with_an_unloaded_prerequisite() -> None:
    try:
        build_lingxiao_special_offer_snapshot(
            special_activity_id=3001003,
            current_page_id=3001009,
            packages=[
                {"id": 2, "activityid": 3001009, "sort": 2, "payId": 0, "personlimit": 1, "purchaseEligibility": 1, "optPacCount": 0},
            ],
            bought_by_offer_id={},
        )
    except FanxiuRuntimeMemoryError as exc:
        assert "前置" in str(exc)
    else:
        raise AssertionError("incomplete package chain must fail closed")


def test_lingxiao_executor_reenters_main_before_using_fuling_shape_after_special_return(monkeypatch) -> None:
    """#577 returns to #34, so a #575 action may not immediately follow it."""
    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import execute_lingxiao_xianhui_job

    class Runtime:
        def __init__(self): self._free_labels = iter(("免费奖励", "已激活"))
        def current_scene(self, *_args, **_kwargs): return (575, 100, "")
        def cur_frame(self, **_kwargs): return ""
        def full_frame_ocr_tokens(self, *_args): return []
        def ocr_tokens_in_shapes(self, *_args, **_kwargs): return []
        def ocr_text_in_shapes(self, *_args, **_kwargs): return next(self._free_labels)
        def wait_click(self, scene, title, **_kwargs):
            yield ("click", scene, title)
        def wait_view(self, *scenes, **_kwargs):
            yield ("view", *scenes)
            return scenes[0]
        def goto_view(self, scene):
            yield ("goto", scene)

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs): return Runtime()
        def _persist_scheduler_task_next_time(self, *_args): pass
        def _log(self, *_args): pass

    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_runtime_state", lambda: {"complete": True, "activity_id": 3001003, "x": 0, "cost_per_draw": 1, "available_draws": None, "cumulative": {"complete": True, "visible_claimable": []}})
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_ten_draw_runtime", lambda **_kwargs: {"complete": True, "ten_draw_enabled": True})
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_special_recharge_runtime", lambda: {"complete": True, "state": "paid_gate"})
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_fuling_rewards_runtime", lambda: {"complete": True, "free_reward_claimable": False})
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_fuling_tasks_runtime", lambda: {"complete": True, "daily": {"tasks": []}})

    events = list(execute_lingxiao_xianhui_job(Runner(), {}, {}, Event()))
    special_return = events.index(("click", 577, "返回"))
    fuling_click = events.index(("click", 575, "仙门福令"))
    assert ("click", 34, "灵霄仙会") in events[special_return:fuling_click]
    assert ("view", 575, 574) in events[special_return:fuling_click]
    assert ("view", 581) in events
    assert ("click", 581, "继续") in events
    assert events.index(("view", 581)) < events.index(("click", 581, "继续"))


def test_lingxiao_executor_resumes_cover_after_optional_popup_without_forcing_world(monkeypatch) -> None:
    """A popup may close back to #574; #574→#34 is not a required edge."""
    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import execute_lingxiao_xianhui_job

    class Runtime:
        def __init__(self): self._free_labels = iter(("免费奖励", "已激活"))
        def current_scene(self, *_args, **_kwargs): return (None, 0, "")
        def cur_frame(self, **_kwargs): return ""
        def full_frame_ocr_tokens(self, *_args): return []
        def ocr_tokens_in_shapes(self, *_args, **_kwargs): return []
        def ocr_text_in_shapes(self, *_args, **_kwargs): return next(self._free_labels)
        def wait_click(self, scene, title, **_kwargs): yield ("click", scene, title)
        def wait_view(self, *scenes, **_kwargs):
            yield ("view", *scenes)
            return 574 if 574 in scenes and 34 in scenes else scenes[0]
        def goto_view(self, scene): yield ("goto", scene)

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs): return Runtime()
        def _persist_scheduler_task_next_time(self, *_args): pass
        def _log(self, *_args): pass

    base = "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui."
    monkeypatch.setattr(base + "read_lingxiao_runtime_state", lambda: {"complete": True, "activity_id": 3001003, "x": 0, "cost_per_draw": 1, "available_draws": None, "cumulative": {"complete": True, "visible_claimable": []}})
    monkeypatch.setattr(base + "read_bothdraw_ten_draw_runtime", lambda **_kwargs: {"complete": True, "ten_draw_enabled": True})
    monkeypatch.setattr(base + "read_lingxiao_special_recharge_runtime", lambda: {"complete": True, "state": "paid_gate"})
    monkeypatch.setattr(base + "read_lingxiao_fuling_rewards_runtime", lambda: {"complete": True, "free_reward_claimable": False})
    monkeypatch.setattr(base + "read_lingxiao_fuling_tasks_runtime", lambda: {"complete": True, "daily": {"tasks": []}})

    events = list(execute_lingxiao_xianhui_job(Runner(), {}, {}, Event()))
    assert ("view", 34, 20, 574, 575, 570, 571) in events
    assert ("click", 574, "仙门寻宝") in events
    assert ("goto", 34) not in events


def test_lingxiao_executor_recovers_a_residual_fuling_page_via_its_own_return(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import execute_lingxiao_xianhui_job

    class Runtime:
        def __init__(self): self._free_labels = iter(("已激活",))
        def current_scene(self, *_args, **_kwargs): return (571, 100, "")
        def cur_frame(self, **_kwargs): return ""
        def full_frame_ocr_tokens(self, *_args): return []
        def ocr_tokens_in_shapes(self, *_args, **_kwargs): return []
        def ocr_text_in_shapes(self, *_args, **_kwargs): return next(self._free_labels)
        def wait_click(self, scene, title, **_kwargs): yield ("click", scene, title)
        def wait_view(self, *scenes, **_kwargs):
            yield ("view", *scenes)
            return scenes[0]

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs): return Runtime()
        def _persist_scheduler_task_next_time(self, *_args): pass
        def _log(self, *_args): pass

    base = "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui."
    monkeypatch.setattr(base + "read_lingxiao_runtime_state", lambda: {"complete": True, "activity_id": 3001003, "x": 0, "cost_per_draw": 1, "available_draws": None, "cumulative": {"complete": True, "visible_claimable": []}})
    monkeypatch.setattr(base + "read_bothdraw_ten_draw_runtime", lambda **_kwargs: {"complete": True, "ten_draw_enabled": True})
    monkeypatch.setattr(base + "read_lingxiao_special_recharge_runtime", lambda: {"complete": True, "state": "paid_gate"})
    monkeypatch.setattr(base + "read_lingxiao_fuling_rewards_runtime", lambda: {"complete": True, "free_reward_claimable": False})
    monkeypatch.setattr(base + "read_lingxiao_fuling_tasks_runtime", lambda: {"complete": True, "daily": {"tasks": []}})

    events = list(execute_lingxiao_xianhui_job(Runner(), {}, {}, Event()))

    assert events[:3] == [
        ("click", 571, "返回"),
        ("view", 34, 20),
        ("click", 34, "灵霄仙会"),
    ]


def test_lingxiao_world_normalization_keeps_green_bottle_as_a_return_branch() -> None:
    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
        _normalize_lingxiao_world,
    )

    class Runtime:
        def wait_view(self, *scenes, **_kwargs):
            yield ("view", *scenes)
            return 20 if 20 in scenes else 34

        def wait_click(self, scene, title, **_kwargs):
            yield ("click", scene, title)

    assert list(_normalize_lingxiao_world(Runtime(), label="test")) == [
        ("view", 34, 20),
        ("click", 20, "回到世界"),
        ("view", 34),
    ]


def test_lingxiao_executor_claims_the_complete_runtime_free_track_batch(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import execute_lingxiao_xianhui_job

    class Runtime:
        def __init__(self):
            self._free_labels = iter(("已激活",))
            self._mask_matches = iter(({"matched": True},))
        def current_scene(self, *_args, **_kwargs): return (575, 100, "")
        def cur_frame(self, **_kwargs): return ""
        def full_frame_ocr_tokens(self, *_args): return []
        def ocr_tokens_in_shapes(self, *_args, **_kwargs): return []
        def ocr_text_in_shapes(self, *_args, **_kwargs): return next(self._free_labels)
        def shape_matches(self, *_args, **_kwargs): return next(self._mask_matches)
        def wait_click(self, scene, title, **_kwargs): yield ("click", scene, title)
        def wait_action_settle(self, *_args, **_kwargs):
            if False:
                yield None
        def wait_view(self, *scenes, **_kwargs):
            yield ("view", *scenes)
            return scenes[0]

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs): return Runtime()
        def _persist_scheduler_task_next_time(self, *_args): pass
        def _log(self, *_args): pass

    base = "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui."
    monkeypatch.setattr(base + "read_lingxiao_runtime_state", lambda: {"complete": True, "activity_id": 3001003, "x": 0, "cost_per_draw": 1, "available_draws": None, "cumulative": {"complete": True, "visible_claimable": []}})
    monkeypatch.setattr(base + "read_bothdraw_ten_draw_runtime", lambda **_kwargs: {"complete": True, "ten_draw_enabled": True})
    monkeypatch.setattr(base + "read_lingxiao_special_recharge_runtime", lambda: {"complete": True, "state": "paid_gate"})
    free_snapshots = iter((
        {
            "complete": True,
            "normal_track_state": {"complete": True, "activated": True, "claimed_reward_ids": []},
            "normal_items": [
                {"reward_id": 70002001, "is_box": False, "logical_left_mask_active": True},
                {"reward_id": 70002002, "is_box": False, "logical_left_mask_active": True},
            ],
        },
        {
            "complete": True,
            "normal_track_state": {"complete": True, "activated": True, "claimed_reward_ids": [70002001, 70002002]},
            "normal_items": [
                {"reward_id": 70002001, "is_box": False, "logical_left_mask_active": False},
                {"reward_id": 70002002, "is_box": False, "logical_left_mask_active": False},
            ],
        },
    ))
    monkeypatch.setattr(base + "read_lingxiao_fuling_rewards_runtime", lambda: next(free_snapshots))
    monkeypatch.setattr(base + "read_lingxiao_fuling_tasks_runtime", lambda: {"complete": True, "daily": {"tasks": []}})

    events = list(execute_lingxiao_xianhui_job(Runner(), {}, {}, Event()))

    assert events.count(("click", 571, "当前免费奖励（领取门卫）")) == 1


def test_ordinary_pool_snapshot_keeps_cumulative_point_without_fake_selected_reward() -> None:
    point = _basic_lottery_snapshot(3001003, {"times": 40, "hitBig": 1, "hitBigTotal": 3})
    assert point == {
        "activity_id": 3001003,
        "x": 40,
        "y": 3,
        "hit_big_total": 3,
        "hit_big": 1,
        "selected_big_reward": None,
        "probability_pool_kind": "ordinary_pool",
    }


def test_lingxiao_scatter_uses_runtime_big_config_delta() -> None:
    before = {
        "complete": True,
        "activity_id": LINGXIAO_ACTIVITY_ID,
        "x": 10,
        "y": 1,
        "big_prize_items": [{"id": 300100301, "reward": "Item|1_1", "hit_count": 1}],
    }
    capped_remainder = {
        **before,
        "x": 13,
        "big_prize_items": [dict(before["big_prize_items"][0])],
    }
    assert derive_bothdraw_ordinary_draw_delta(before, capped_remainder) == {
        "activity_id": LINGXIAO_ACTIVITY_ID,
        "draw_delta": 3,
        "big_delta": 0,
        "hit_big_prize_items": [],
    }
    one_more_big = {
        **before,
        "x": 20,
        "y": 2,
        "big_prize_items": [{"id": 300100301, "reward": "Item|1_1", "hit_count": 2}],
    }
    assert derive_bothdraw_ordinary_draw_delta(before, one_more_big)["hit_big_prize_items"] == [
        {"id": 300100301, "reward": "Item|1_1", "hit_count": 2, "hit_increment": 1}
    ]


def test_lingxiao_runtime_state_refuses_misaligned_page_bound_cumulative(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_ticket_runtime",
        lambda: {"complete": True, "available_draws": 10},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_basic_runtime",
        lambda: {"complete": True, "activity_id": 3001003, "x": 10, "available_draws": 10},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_cumulative_rewards_runtime",
        lambda: {"complete": True, "activity_id": 3001003, "x": 9, "visible_claimable": []},
    )

    state = read_lingxiao_runtime_state()

    assert state["complete"] is False
    assert "抽数不一致" in state["reason"]


def test_lingxiao_runtime_state_keeps_the_page_bound_cumulative_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_ticket_runtime",
        lambda: {"complete": True, "available_draws": 10},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_basic_runtime",
        lambda: {"complete": True, "activity_id": 3001003, "x": 10, "available_draws": 10},
    )
    cumulative = {"complete": True, "activity_id": 3001003, "x": 10, "visible_claimable": [{"id": 7}]}
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_cumulative_rewards_runtime",
        lambda: cumulative,
    )

    state = read_lingxiao_runtime_state()

    assert state["complete"] is True
    assert state["cumulative"] == cumulative


def test_lingxiao_runtime_state_rejects_cross_process_join(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_ticket_runtime",
        lambda: {
            "complete": True,
            "available_draws": 3,
            "evidence": {"bindings": {"pid": 10, "process_start_ticks": 100}},
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_basic_runtime",
        lambda: {
            "complete": True, "activity_id": 3001003, "x": 10,
            "evidence": {"pid": 10, "process_start_ticks": 100},
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_cumulative_rewards_runtime",
        lambda: {
            "complete": True, "activity_id": 3001003, "x": 10,
            "evidence": {"pid": 11, "process_start_ticks": 101},
        },
    )

    state = read_lingxiao_runtime_state()

    assert state["complete"] is False
    assert "不同游戏进程" in state["reason"]


def test_lingxiao_cumulative_preserves_a_runtime_loading_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_cumulative_rewards_runtime",
        lambda **_kwargs: {"complete": False, "reason": "WalletMgr ticket item not loaded"},
    )

    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
        read_lingxiao_cumulative_rewards_runtime,
    )

    state = read_lingxiao_cumulative_rewards_runtime()

    assert state["complete"] is False
    assert state["reason"] == "WalletMgr ticket item not loaded"


def test_lingxiao_cumulative_uses_its_verified_eight_visible_slots(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_cumulative_rewards_runtime",
        lambda **kwargs: calls.append(kwargs) or {"complete": True, "activity_id": 3001003},
    )

    from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
        read_lingxiao_cumulative_rewards_runtime,
    )

    state = read_lingxiao_cumulative_rewards_runtime()

    assert state["complete"] is True
    assert calls == [{"include_selected_big_reward": False, "visible_slot_count": 4}]


def test_lingxiao_fuling_reader_requires_the_verified_daily_and_advanced_groups(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_revenue_task_runtime",
        lambda **_kwargs: {
            "complete": True,
            "activity_id": 3001003,
            "task_groups": [
                {"group_id": 4, "task_count": 4, "tasks": [{"state": "claimed"}] * 4},
                {"group_id": 5, "task_count": 60, "tasks": []},
            ],
        },
    )

    state = read_lingxiao_fuling_tasks_runtime()

    assert state["complete"] is True
    assert state["daily"]["group_id"] == 4
    assert state["advanced"]["group_id"] == 5


def test_lingxiao_fuling_reader_rejects_unmapped_group_layout(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_bothdraw_revenue_task_runtime",
        lambda **_kwargs: {
            "complete": True,
            "activity_id": 3001003,
            "task_groups": [{"group_id": 8, "task_count": 4, "tasks": []}],
        },
    )

    state = read_lingxiao_fuling_tasks_runtime()

    assert state["complete"] is False
    assert "布局不一致" in state["reason"]


def test_lingxiao_fuling_reward_panel_only_certifies_observed_no_reward_state() -> None:
    snapshot = build_lingxiao_fuling_panel_snapshot(
        expected_activity_id=3001003,
        panel_activity_id=3001003,
        fuling_activity_id=3001006,
        activity_type=1,
        has_any_reward=False,
        score_min=100,
        score_max=2100,
        current_score_index=7,
        show_index=-1,
    )

    assert snapshot["free_reward_claimable"] is None
    assert snapshot["free_reward_state"] == "unmapped_panel_items"
    assert snapshot["fuling_activity_id"] == 3001006
    assert snapshot["current_score_index"] == 7


def test_lingxiao_fuling_reward_panel_refuses_to_guess_free_track_when_red_state_is_true() -> None:
    snapshot = build_lingxiao_fuling_panel_snapshot(
        expected_activity_id=3001003,
        panel_activity_id=3001003,
        fuling_activity_id=3001006,
        activity_type=1,
        has_any_reward=True,
        score_min=100,
        score_max=2100,
        current_score_index=7,
        show_index=-1,
    )

    assert snapshot["free_reward_claimable"] is None
    assert snapshot["free_reward_state"] == "unmapped_panel_items"


def test_lingxiao_fuling_reward_panel_requires_a_real_child_activity_identity() -> None:
    try:
        build_lingxiao_fuling_panel_snapshot(
            expected_activity_id=3001003,
            panel_activity_id=3001003,
            fuling_activity_id=None,
            activity_type=1,
            has_any_reward=False,
            score_min=100,
            score_max=2100,
            current_score_index=7,
            show_index=-1,
        )
    except FanxiuRuntimeMemoryError:
        pass
    else:
        raise AssertionError("must reject a missing nested activity identity")


def test_lingxiao_fuling_rewards_wrapper_keeps_the_panel_runtime_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui.read_lingxiao_fuling_panel_runtime",
        lambda **kwargs: {"complete": True, "activity_id": kwargs["expected_activity_id"]},
    )

    assert read_lingxiao_fuling_rewards_runtime() == {"complete": True, "activity_id": 3001003}


def test_lingxiao_fuling_normal_mask_logic_keeps_repeatable_box_separate() -> None:
    rows = _with_normal_track_state(
        [
            {"reward_id": 11, "is_box": False, "reached": True, "claimed_normal": False, "logical_left_mask_active": None},
            {"reward_id": 12, "is_box": False, "reached": False, "claimed_normal": False, "logical_left_mask_active": None},
            {"reward_id": 13, "is_box": True, "reached": None, "claimed_normal": None, "logical_left_mask_active": True},
        ],
        activated=True,
        claimed_ids=set(),
    )
    assert [row["logical_left_mask_active"] for row in rows] == [True, False, True]


def test_lingxiao_fuling_rejects_panel_and_client_claimed_state_conflict() -> None:
    try:
        _with_normal_track_state(
            [{"reward_id": 11, "is_box": False, "reached": True, "claimed_normal": False, "logical_left_mask_active": None}],
            activated=True,
            claimed_ids={11},
        )
    except FanxiuRuntimeMemoryError as exc:
        assert "冲突" in str(exc)
    else:
        raise AssertionError("conflicting client and panel claimed state must fail closed")
