from backend.core.fanxiu.data_annotation import behavior_tree_executor  # noqa: F401
from datetime import datetime

from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin
import threading


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_xianshi_weekly_resource_restores_right_menu_before_clicking_entry():
    task = DailyResourceTaskMixin()
    task._log = lambda *_args, **_kwargs: None
    events: list[tuple] = []

    class FakeRuntime:
        matches = iter([False, False, True])

        def shape(self, view_id, title):
            events.append(("shape", view_id, title))
            return object()

        def match_shape(self, _shape):
            matched = next(self.matches)
            events.append(("match", matched))
            return matched

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return ""

        def scroll_shape_content(self, view_id, title, **options):
            events.append(("scroll", view_id, title, options["direction"]))
            if False:
                yield
            return True

        def wait_click_then_shape(self, *args, **_options):
            events.append(("click",) + args)
            if False:
                yield
            return True

    result = _drain(task._open_xianshi_weekly_resource_entry(FakeRuntime(), {}))

    assert result is True
    assert events == [
        ("shape", 34, "仙市"),
        ("match", False),
        ("scroll", 34, "右侧菜单", "up"),
        ("match", False),
        ("scroll", 34, "右侧菜单", "up"),
        ("match", True),
        ("click", 34, "仙市", 247, "秘藏阁"),
    ]


def test_xianshi_weekly_resource_leaves_world_like_internal_scene_first():
    task = DailyResourceTaskMixin()
    task._log = lambda *_args, **_kwargs: None
    events: list[tuple] = []

    class FakeRuntime:
        matches = iter([False, True])

        def shape(self, view_id, title):
            return object()

        def match_shape(self, _shape):
            return next(self.matches)

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "天机阁 储物袋 战斗"

        def click_shape_center(self, view_id, title):
            events.append(("leave", view_id, title))

        def wait_scene(self, layer0, **_options):
            view_id = layer0[0]
            events.append(("wait", view_id))
            if False:
                yield
            return view_id

        def wait_click_then_scene(self, *args, **_options):
            events.append(("confirm",) + args)
            if False:
                yield
            return True

        def wait_click_then_shape(self, *args, **_options):
            events.append(("entry",) + args)
            if False:
                yield
            return True

    result = _drain(task._open_xianshi_weekly_resource_entry(FakeRuntime(), {}))

    assert result is True
    assert events == [
        ("leave", 85, "离开"),
        ("wait", 86),
        ("confirm", 86, "确认", 34),
        ("entry", 34, "仙市", 247, "秘藏阁"),
    ]


def test_xianshi_weekly_resource_success_advances_to_same_monday_five():
    task = DailyResourceTaskMixin()
    writes: list[tuple[str, str]] = []
    task._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append(
        (task_id, next_time)
    )

    result = task._record_xianshi_weekly_resources_done(
        {"__scheduler_task_id": "weekly-instance"},
        now=datetime(2026, 8, 3, 1, 33),
    )

    assert result == "2026-08-03 05:00:00"
    assert writes == [("weekly-instance", "2026-08-03 05:00:00")]


def test_xianshi_weekly_resource_success_after_reset_advances_to_next_monday():
    task = DailyResourceTaskMixin()
    writes: list[tuple[str, str]] = []
    task._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append(
        (task_id, next_time)
    )

    result = task._record_xianshi_weekly_resources_done(
        {},
        now=datetime(2026, 8, 3, 5, 30),
    )

    assert result == "2026-08-10 00:00:00"
    assert writes == [("xianshi-weekly-resources", "2026-08-10 00:00:00")]


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_enough():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("800011400/8000") == (11400, 8000)


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_insufficient():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("80001400/8000") == (1400, 8000)


def test_daily_gongfeng_law_progress_keeps_plain_progress():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("11400/8000") == (11400, 8000)


def test_xianmeng_autonomous_sweep_starts_at_eleven_boundary():
    task = DailyResourceTaskMixin()

    assert task._daily_xianmeng_stamina_sweep_allowed(datetime(2026, 9, 20, 10, 59)) is False
    assert task._daily_xianmeng_stamina_sweep_allowed(datetime(2026, 9, 20, 11, 0)) is True
    assert task._daily_xianmeng_stamina_sweep_allowed(datetime(2026, 9, 20, 11, 1)) is True


def test_xianmeng_eleven_sweep_spends_even_sub_cap_low_score_stamina():
    task = DailyResourceTaskMixin()
    for remaining in (1, 60, 244):
        snapshot = {"ok": True, "complete": True, "attack_count": remaining}
        assert task._daily_xianmeng_should_continue_low_score_sweep(snapshot, {}, now=datetime(2026, 9, 20, 10, 59)) == (False, remaining)
        assert task._daily_xianmeng_should_continue_low_score_sweep(snapshot, {}, now=datetime(2026, 9, 20, 11)) == (True, remaining)
    assert task._daily_xianmeng_should_continue_low_score_sweep({"ok": False}, {}, now=datetime(2026, 9, 20, 11)) == (False, None)


def test_xianmeng_completed_batch_waits_for_evening_checkpoints():
    from backend.core.fanxiu.activity.daily_activity_job_registry import next_xianmeng_stamina_review
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 10)) == datetime(2026, 9, 20, 20, 45)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 12), tail_at=datetime(2026, 9, 20, 21, 10)) == datetime(2026, 9, 20, 20, 45)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 20, 45)) == datetime(2026, 9, 20, 21, 50)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 21, 50)) is None
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 21, 40), tail_at=datetime(2026, 9, 20, 21, 50)) == datetime(2026, 9, 20, 21, 50)
    assert next_xianmeng_stamina_review(datetime(2026, 9, 20, 22)) is None


def test_xianmeng_fallback_excludes_destroyed_own_and_ally_camps(monkeypatch):
    from backend.core.fanxiu.catalog import server_relations

    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": 1, "name": "own", "server_id": 1, "ally_camp_id": 2, "pillar_cur_hp": 200, "pillar_max_hp": 600},
        {"id": 2, "name": "battlefield ally", "server_id": 2, "ally_camp_id": 1, "pillar_cur_hp": 0, "pillar_max_hp": 600},
        {"id": 3, "name": "destroyed enemy", "server_id": 3, "ally_camp_id": 0, "pillar_cur_hp": 0, "pillar_max_hp": 600},
        {"id": 4, "name": "live enemy", "server_id": 4, "ally_camp_id": 0, "pillar_cur_hp": 10, "pillar_max_hp": 600},
    ]
    result = DailyResourceTaskMixin._daily_xianmeng_fallback_candidates({"camps": camps})
    assert [row["id"] for row in result["candidates"]] == [4]


def test_xianmeng_fallback_skips_immune_and_ranks_shielded_after_unshielded(monkeypatch):
    from backend.core.fanxiu.catalog import server_relations

    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": 1, "name": "own", "server_id": 1, "pillar_cur_hp": 600, "pillar_max_hp": 600},
        {"id": 2, "name": "immune", "server_id": 2, "pillar_cur_hp": 10, "pillar_max_hp": 600, "protect_end_time": 1001},
        {"id": 3, "name": "shielded", "server_id": 3, "pillar_cur_hp": 20, "pillar_max_hp": 600, "has_xiaoyan_mirror": True},
        {"id": 4, "name": "ready", "server_id": 4, "pillar_cur_hp": 100, "pillar_max_hp": 600, "protect_end_time": 0, "has_xiaoyan_mirror": False},
    ]
    result = DailyResourceTaskMixin._daily_xianmeng_fallback_candidates({"camps": camps}, now_ms=1000)
    assert [row["id"] for row in result["candidates"]] == [4, 3]


def test_xianmeng_tail_preserved_until_2150():
    task = DailyResourceTaskMixin()

    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 10, 59)
        )
        is True
    )
    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 11, 0)
        )
        is True
    )
    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 11, 1)
        )
        is True
    )
    assert (
        task._daily_xianmeng_should_preserve_tail_before_triple_disable(
            datetime(2026, 9, 20, 21, 50)
        )
        is False
    )


def test_xianmeng_sweep_log_clock_matches_single_constant():
    from backend.core.fanxiu.activity.daily_activity_job_registry import (
        XIANMENG_AUTONOMOUS_SWEEP_START,
    )

    assert XIANMENG_AUTONOMOUS_SWEEP_START == (11, 0)
    assert DailyResourceTaskMixin._daily_xianmeng_autonomous_sweep_start_text() == "11:00"


def test_xianmeng_tail_sweeps_stay_separate_from_eleven_sweep():
    from backend.core.fanxiu.activity.daily_activity_job_registry import (
        XIANMENG_AUTONOMOUS_SWEEP_START,
        XIANMENG_STAMINA_SWEEPS,
    )

    assert XIANMENG_STAMINA_SWEEPS == ((21, 10), (21, 50))
    assert XIANMENG_AUTONOMOUS_SWEEP_START not in XIANMENG_STAMINA_SWEEPS



def test_xianmeng_no_opponents_terminal_requires_complete_positive_evidence(monkeypatch):
    from backend.core.fanxiu.catalog import server_relations
    monkeypatch.setattr(server_relations, "classify_fanxiu_target_relation", lambda *, is_npc, server_id: {
        "camp": "friendly" if server_id == 1 else "non_friendly",
        "relation": "same_server" if server_id == 1 else "other_server",
    })
    camps = [
        {"id": 1, "server_id": 1, "ally_camp_id": 2, "pillar_cur_hp": 100, "pillar_max_hp": 600},
        {"id": 2, "server_id": 2, "ally_camp_id": 1, "pillar_cur_hp": 100, "pillar_max_hp": 600},
        {"id": 3, "server_id": 3, "pillar_cur_hp": 0, "pillar_max_hp": 600},
    ]
    classify = DailyResourceTaskMixin._daily_xianmeng_fallback_candidates
    assert classify({"camps": camps, "camp_count": 3})["all_opponents_defeated"]
    assert not classify({"camps": camps, "camp_count": 4})["all_opponents_defeated"]
    assert not classify({"camps": [], "camp_count": 0})["all_opponents_defeated"]
    for hp in (None, 1):
        changed = [*camps[:2], {**camps[2], "pillar_cur_hp": hp}]
        assert not classify({"camps": changed, "camp_count": 3})["all_opponents_defeated"]
