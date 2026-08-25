import base64
import io
import threading
from datetime import datetime, timedelta

import pytest
from PIL import Image, ImageDraw

from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
    BehaviorTreeRuntimeRunner,
)


class _XianmengHarness(BehaviorTreeRuntimeRunner):
    def __init__(self):
        self.next_times = []
        self.logs = []

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _log(self, kind, message):
        self.logs.append((kind, message))


class _ImmunityRuntime:
    def __init__(self):
        self.actions = []

    def goto_view(self, scene_id):
        self.actions.append(("goto", scene_id))
        if False:
            yield None
        return "success"

    def wait_view(self, scene_id, **options):
        self.actions.append(("wait", scene_id, options))
        if False:
            yield None
        return scene_id


class _OptionRuntime:
    def __init__(self):
        self.actions = []

    def click_shape_center(self, scene_id, shape):
        self.actions.append(("click", scene_id, shape))

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None
        return "success"


class _NavigationRuntime:
    stop_event = None

    def __init__(self):
        self.calls = []

    def cur_frame(self, *, update):
        assert update is True
        return "frame"

    def ocr_tokens(self, frame):
        assert frame == "frame"
        return [
            {"text": "仙盟争霸", "x": 96, "y": 959, "w": 234, "h": 54},
            {"text": "仙盟争霸", "x": 177, "y": 372, "w": 127, "h": 35},
        ]

    def click_frame_point(self, scene_id, x, y):
        self.calls.append(("point", scene_id, x, y))

    def click_ocr_text(self, scene_id, text, **options):
        self.calls.append((scene_id, text, options))
        return object()


class _MapReturnRuntime(_ImmunityRuntime):
    def click_frame_point(self, scene_id, x, y):
        self.actions.append(("point", scene_id, x, y))

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None
        return "success"


class _UnavailableTaskTabRuntime:
    def __init__(self):
        self.actions = []

    def click_shape_center(self, scene_id, shape):
        self.actions.append(("click", scene_id, shape))

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None
        return "success"


class _BattlefieldEntryRuntime(_MapReturnRuntime):
    def click_shape_center(self, scene_id, shape):
        self.actions.append(("click", scene_id, shape))


class _TransientPopupRuntime:
    def __init__(self, runner, frame):
        self.runner = runner
        self.frame = frame
        self.actions = []

    def cur_frame(self, *, update):
        assert update is True
        return self.frame

    def click_frame_point(self, scene_id, x, y):
        self.actions.append(("point", scene_id, x, y))

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None
        return "success"


class _FastAttackSceneRuntime:
    stop_event = threading.Event()

    def __init__(self, scores):
        self.scores = scores
        self.queries = []
        self.ctx = {}

    def cur_frame(self, *, update):
        assert update is True
        return "frame"

    def shape_score(self, scene_id, shape, *, frame_data_url):
        assert frame_data_url == "frame"
        self.queries.append((scene_id, shape))
        return self.scores.get((scene_id, shape), 0.0)

    def wait_action_settle(self, _seconds):
        if False:
            yield None
        return "success"


def _frame_data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _returning(value):
    if False:
        yield None
    return value


def test_daily_xianmeng_fast_report_uses_stable_title_not_variable_reward_rows():
    harness = _XianmengHarness()
    runtime = _FastAttackSceneRuntime(
        {
            (294, "确定"): 100.0,
            (294, "挑战成功"): 100.0,
            (295, "关闭"): 10.0,
        }
    )

    scene = _drain(harness._wait_daily_xianmeng_fast_attack_scene(runtime, {}))

    assert scene == 294
    assert (294, "挑战成功") in runtime.queries
    assert (294, "总共获得奖励") not in runtime.queries


def test_xianmeng_challenge_is_internal_under_gameplay_parent():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_xianmeng")
    assert definition is not None
    assert definition.scheduler_supported is False
    assert not any(
        item["task_type"] == "daily_xianmeng"
        for item in default_data_annotation_scheduler_tasks()
    )


def test_daily_xianmeng_admission_stops_late_cell_without_gameplay(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 22, 1),
    )
    harness = _XianmengHarness()

    decision = harness.daily_xianmeng_admission(
        {"__scheduler_task_id": "legacy-daily-xianmeng", "daily_end_time": "22:00"}
    )

    assert decision == {
        "result": "success",
        "message": "仙盟_挑战：当前已到或超过 22:00，活动窗口结束，未执行游戏操作",
        "current_scene": None,
    }
    assert harness.next_times == [("legacy-daily-xianmeng", None)]


def test_daily_xianmeng_admission_allows_cell_before_close(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 21, 59, 59),
    )
    harness = _XianmengHarness()

    assert harness.daily_xianmeng_admission({"daily_end_time": "22:00"}) is None
    assert harness.next_times == []


def test_daily_xianmeng_skips_optional_rewards_when_task_tab_stays_on_cover():
    harness = _XianmengHarness()
    runtime = _UnavailableTaskTabRuntime()
    seen = []

    harness._daily_xianmeng_claim_markers = lambda _runtime: [{"x": 700, "y": 1300}]

    def wait_exact(_runtime, *scene_ids, timeout):
        seen.append((scene_ids, timeout))
        if scene_ids == (474,):
            raise TimeoutError("task tab ignored")
        assert scene_ids == (473,)
        if False:
            yield None
        return 473

    harness._wait_daily_xianmeng_exact_view = wait_exact

    claimed = _drain(harness._claim_daily_xianmeng_task_rewards(runtime, {}))

    assert claimed == 0
    assert runtime.actions[0] == ("click", 473, "任务")
    assert seen == [((473,), 12.0), ((474,), 12.0), ((473,), 3.0)]
    assert harness.logs[-1] == (
        "skip",
        "日常_仙盟：任务入口未响应且仍在封面 #473，跳过可选领奖并继续挑战",
    )


def test_daily_xianmeng_closes_dark_transient_business_layer_and_saves_evidence(
    monkeypatch,
    tmp_path,
):
    image = Image.new("RGB", (900, 1600), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((752, 92, 828, 170), fill=(90, 72, 28))
    draw.line((768, 108, 812, 152), fill="white", width=9)
    draw.line((812, 108, 768, 152), fill="white", width=9)
    harness = _XianmengHarness()
    runtime = _TransientPopupRuntime(harness, _frame_data_url(image))
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.codeyun_temp_root",
        lambda *_parts: tmp_path,
    )

    def wait_exact(_runtime, *scene_ids, timeout):
        assert scene_ids == (473, 474)
        assert timeout == 8.0
        if False:
            yield None
        return 473

    harness._wait_daily_xianmeng_exact_view = wait_exact

    recovered = _drain(harness._close_daily_xianmeng_transient_popup(runtime))

    assert recovered == 473
    assert runtime.actions[0] == ("point", 473, 790.0, 130.0)
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert any("异常黑色业务层" in message for _kind, message in harness.logs)


def test_daily_xianmeng_does_not_close_a_normal_bright_frame():
    image = Image.new("RGB", (900, 1600), "white")
    harness = _XianmengHarness()
    runtime = _TransientPopupRuntime(harness, _frame_data_url(image))

    recovered = _drain(harness._close_daily_xianmeng_transient_popup(runtime))

    assert recovered == 0
    assert runtime.actions == []


def test_daily_xianmeng_does_not_force_world_navigation_from_scheduler_cell():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_xianmeng")

    assert definition is not None
    assert definition.scheduler_supported is False
    assert not hasattr(definition, "lifecycle")


def test_daily_xianmeng_navigation_selects_the_upper_activity_row_by_geometry():
    harness = _XianmengHarness()
    runtime = _NavigationRuntime()

    _drain(
        harness._click_daily_xianmeng_ocr(
            runtime,
            "仙盟争霸",
            timeout=1.0,
            max_center_y=800.0,
        )
    )

    assert runtime.calls == [
        ("point", 66, 240.5, 389.5),
    ]


def test_daily_xianmeng_retries_when_battlefield_entry_click_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 20, 0),
    )
    harness = _XianmengHarness()
    runtime = _BattlefieldEntryRuntime()
    scenes = iter((473, 473, 475, 471))
    harness._claim_daily_xianmeng_task_rewards = lambda *_args: iter(())
    harness._wait_daily_xianmeng_command_target = lambda *_args: iter((None,))

    def wait_exact(_runtime, *scene_ids, timeout):
        if False:
            yield None
        return next(scenes)

    harness._wait_daily_xianmeng_exact_view = wait_exact
    result = _drain(harness._enter_daily_xianmeng_attack_view(runtime, {}))

    assert result is None
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 473, "前往战场"),
        ("click", 473, "前往战场"),
        ("click", 475, "战场地图"),
    ]
    assert any("点击未生效" in message for _kind, message in harness.logs)
    assert harness.next_times == []


def test_daily_xianmeng_retries_when_battlefield_entry_transition_is_temporarily_unknown():
    harness = _XianmengHarness()
    runtime = _BattlefieldEntryRuntime()
    probes = {"count": 0}
    harness._claim_daily_xianmeng_task_rewards = lambda *_args: iter(())
    harness._wait_daily_xianmeng_command_target = lambda *_args: iter((None,))

    def wait_exact(_runtime, *scene_ids, timeout):
        probes["count"] += 1
        if probes["count"] == 2:
            raise TimeoutError("temporary transition unknown")
        if False:
            yield None
        return {1: 473, 3: 475, 4: 471}[probes["count"]]

    harness._wait_daily_xianmeng_exact_view = wait_exact
    result = _drain(harness._enter_daily_xianmeng_attack_view(runtime, {}))

    assert result is None
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 473, "前往战场"),
        ("click", 473, "前往战场"),
        ("click", 475, "战场地图"),
    ]


def test_daily_xianmeng_reenters_cover_when_battlefield_entry_falls_back_to_world():
    harness = _XianmengHarness()
    runtime = _BattlefieldEntryRuntime()
    scenes = iter((473, 34, 475, 471))
    cover_entries = []
    harness._claim_daily_xianmeng_task_rewards = lambda *_args: iter(())
    harness._wait_daily_xianmeng_command_target = lambda *_args: iter((None,))

    def wait_exact(_runtime, *scene_ids, timeout):
        if False:
            yield None
        return next(scenes)

    def enter_cover(_runtime):
        cover_entries.append("cover")
        if False:
            yield None
        return 473

    harness._wait_daily_xianmeng_exact_view = wait_exact
    harness._enter_daily_xianmeng_cover = enter_cover

    result = _drain(harness._enter_daily_xianmeng_attack_view(runtime, {}))

    assert result is None
    assert cover_entries == ["cover"]
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 473, "前往战场"),
        ("click", 473, "前往战场"),
        ("click", 475, "战场地图"),
    ]
    assert any("落回 #34" in message for _kind, message in harness.logs)


def test_daily_xianmeng_return_without_scene_probe_uses_shared_navigation():
    harness = _XianmengHarness()
    runtime = _MapReturnRuntime()

    _drain(harness._return_daily_xianmeng_to_world(runtime))

    assert runtime.actions[0][0:2] == ("goto", 34)
    assert not any(action[0] == "point" for action in runtime.actions)


def test_daily_xianmeng_destroyed_pillar_fallback_excludes_friends_and_sorts_damage(monkeypatch):
    def classify(*, is_npc, server_id, data_dir=None):
        relation = {1: "same_server", 2: "ally"}.get(server_id, "other_server")
        return {
            "camp": "friendly" if relation in {"same_server", "ally"} else "non_friendly",
            "relation": relation,
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.catalog.server_relations.classify_fanxiu_target_relation",
        classify,
    )
    snapshot = {
        "camps": [
            {"id": 10, "name": "我方", "server_id": 1, "pillar_cur_hp": 0, "pillar_max_hp": 100},
            {"id": 20, "name": "盟友", "server_id": 2, "pillar_cur_hp": 1, "pillar_max_hp": 100},
            {"id": 30, "name": "敌甲", "server_id": 3, "pillar_cur_hp": 40, "pillar_max_hp": 100},
            {"id": 40, "name": "敌乙", "server_id": 4, "pillar_cur_hp": 10, "pillar_max_hp": 200},
        ]
    }

    result = _XianmengHarness._daily_xianmeng_fallback_candidates(snapshot)

    assert result["own_pillar_destroyed"] is True
    assert [item["name"] for item in result["candidates"]] == ["敌乙", "敌甲"]
    assert result["candidates"][0]["pillar_ratio"] == pytest.approx(0.05)


def test_daily_xianmeng_destroyed_pillar_fallback_excludes_live_battlefield_ally(monkeypatch):
    def classify(*, is_npc, server_id, data_dir=None):
        relation = "same_server" if server_id == 1 else "other_server"
        return {
            "camp": "friendly" if relation == "same_server" else "non_friendly",
            "relation": relation,
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.catalog.server_relations.classify_fanxiu_target_relation",
        classify,
    )
    snapshot = {
        "camps": [
            {"id": 10, "name": "我方", "server_id": 1, "ally_camp_id": 40, "pillar_cur_hp": 0, "pillar_max_hp": 100},
            {"id": 30, "name": "敌方", "server_id": 3, "pillar_cur_hp": 30, "pillar_max_hp": 100},
            {"id": 40, "name": "战场盟友", "server_id": 4, "ally_camp_id": 10, "pillar_cur_hp": 1, "pillar_max_hp": 100},
        ]
    }

    result = _XianmengHarness._daily_xianmeng_fallback_candidates(snapshot)

    assert result["battlefield_ally_ids"] == [40]
    assert [item["name"] for item in result["candidates"]] == ["敌方"]


@pytest.mark.parametrize(
    ("business_time", "own_pillar_hp", "expected_target", "expected_log"),
    [
        (datetime(2026, 8, 16, 20, 59), 80, None, "21:10 前保持等待大师兄"),
        (datetime(2026, 8, 16, 21, 10), 80, "敌乙", "已进入 21:10 后体力清扫"),
        (datetime(2026, 8, 16, 21, 50), 80, "敌乙", "已进入 21:10 后体力清扫"),
        (datetime(2026, 8, 16, 20, 59), 0, "敌乙", "我方柱子已爆"),
    ],
)
def test_daily_xianmeng_without_command_only_falls_back_when_authorized(
    monkeypatch,
    business_time,
    own_pillar_hp,
    expected_target,
    expected_log,
):
    def classify(*, is_npc, server_id, data_dir=None):
        relation = "same_server" if server_id == 1 else "other_server"
        return {
            "camp": "friendly" if relation == "same_server" else "non_friendly",
            "relation": relation,
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.catalog.server_relations.classify_fanxiu_target_relation",
        classify,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: business_time,
    )
    harness = _XianmengHarness()
    runtime = type("Runtime", (), {"stop_event": None})()
    harness._read_daily_xianmeng_command_target_snapshot = lambda: {
        "ok": True,
        "complete": True,
        "command_count": 0,
        "target": None,
        "camps": [
            {
                "id": 10,
                "name": "我方",
                "server_id": 1,
                "pillar_cur_hp": own_pillar_hp,
                "pillar_max_hp": 100,
            },
            {
                "id": 20,
                "name": "敌甲",
                "server_id": 2,
                "pillar_cur_hp": 40,
                "pillar_max_hp": 100,
            },
            {
                "id": 30,
                "name": "敌乙",
                "server_id": 3,
                "pillar_cur_hp": 10,
                "pillar_max_hp": 100,
            },
        ],
    }
    monkeypatch.setattr(harness, "_raise_if_stopped", lambda _event: None)

    target = _drain(harness._wait_daily_xianmeng_command_target(runtime, {}))

    assert (target or {}).get("name") == expected_target
    if expected_target is not None:
        assert harness._daily_xianmeng_target_selection["mode"] == "non-friendly-fallback"
        assert [
            item["name"] for item in harness._daily_xianmeng_target_selection["candidates"]
        ] == ["敌乙", "敌甲"]
    else:
        assert not hasattr(harness, "_daily_xianmeng_target_selection")
    assert any(expected_log in message for _kind, message in harness.logs)


def test_daily_xianmeng_command_target_remains_first_priority_during_stamina_sweep(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 21, 50),
    )
    harness = _XianmengHarness()
    runtime = type("Runtime", (), {"stop_event": None})()
    harness._read_daily_xianmeng_command_target_snapshot = lambda: {
        "ok": True,
        "complete": True,
        "command_count": 1,
        "target": {
            "id": 20,
            "name": "大师兄目标",
            "slot": 2,
            "pillar_cur_hp": 90,
            "pillar_max_hp": 100,
        },
        "camps": [],
    }
    monkeypatch.setattr(harness, "_raise_if_stopped", lambda _event: None)

    target = _drain(harness._wait_daily_xianmeng_command_target(runtime, {}))

    assert target["name"] == "大师兄目标"
    assert harness._daily_xianmeng_target_selection["mode"] == "command"


def test_daily_xianmeng_immunity_schedules_dynamic_retry_and_returns_world(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 20, 0),
    )
    harness = _XianmengHarness()
    runtime = _ImmunityRuntime()
    harness._read_daily_xianmeng_immunity_snapshot = lambda: {
        "ok": True,
        "complete": True,
        "cooldown_seconds": 600,
    }

    result = _drain(
        harness._record_daily_xianmeng_immunity_cd(
            runtime,
            {"__scheduler_task_id": "legacy-daily-xianmeng"},
        )
    )

    assert result == "2026-08-16 20:10:00"
    assert harness.next_times == []
    assert runtime.actions[0] == ("goto", 34)
    assert runtime.actions[1][0:2] == ("wait", 34)
    assert any("动态免战 CD 剩余 600 秒" in message for _kind, message in harness.logs)
    assert any("免战分支已回到 #34" in message for _kind, message in harness.logs)


def test_daily_xianmeng_immunity_probe_failure_retries_in_five_minutes(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 20, 0),
    )
    harness = _XianmengHarness()
    runtime = _ImmunityRuntime()
    harness._read_daily_xianmeng_immunity_snapshot = lambda: {
        "ok": False,
        "complete": False,
    }

    _drain(
        harness._record_daily_xianmeng_immunity_cd(
            runtime,
            {"__scheduler_task_id": "legacy-daily-xianmeng"},
        )
    )

    assert harness.next_times == []
    assert any("按 5 分钟安全复查" in message for _kind, message in harness.logs)


def test_daily_xianmeng_personal_score_parser_reads_each_reward_row():
    text = """
    总共获得奖励：
    获得个人积分×100，火焰×5
    获得个人积分x100，火焰×5
    获得个人积分 300，火焰×5
    获得个人积分×100，火焰×5
    """

    assert _XianmengHarness._parse_daily_xianmeng_personal_scores(text) == [
        100,
        100,
        300,
        100,
    ]


def test_daily_xianmeng_retry_has_no_hidden_time_buffer(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 20, 0),
    )
    harness = _XianmengHarness()

    payload = {}
    result = harness._schedule_daily_xianmeng_retry(payload, seconds=0, message="立即复查")

    assert result == "2026-08-16 20:00:00"
    assert payload["_xianmeng_next_time"] == result
    assert harness.next_times == []


def test_daily_xianmeng_business_retry_stops_at_activity_close(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 21, 55),
    )
    harness = _XianmengHarness()

    next_time = harness._schedule_daily_xianmeng_retry(
        {"daily_end_time": "22:00"},
        seconds=600,
        message="异常后复查",
    )

    assert next_time is None
    assert harness.next_times == []
    assert any("活动结束，不再调度" in message for _kind, message in harness.logs)


def test_daily_xianmeng_destroyed_command_target_waits_for_new_command(monkeypatch):
    harness = _XianmengHarness()
    runtime = type("Runtime", (), {"stop_event": None})()
    reads = 0

    def read_snapshot():
        nonlocal reads
        reads += 1
        return {
            "ok": True,
            "complete": True,
            "command_count": 1,
            "target": {
                "id": 88,
                "name": "已打碎目标",
                "pillar_cur_hp": 0,
                "pillar_max_hp": 100,
            },
            "camps": [],
        }

    harness._read_daily_xianmeng_command_target_snapshot = read_snapshot
    monkeypatch.setattr(harness, "_raise_if_stopped", lambda _event: None)

    target = _drain(harness._wait_daily_xianmeng_command_target(runtime, {}))

    assert target is None
    assert reads == 1
    assert any("阵柱已被打碎" in message for _kind, message in harness.logs)


@pytest.mark.parametrize(
    ("now", "remaining"),
    [
        (datetime(2026, 8, 16, 12, 0), 196),
        (datetime(2026, 8, 16, 13, 0), 60),
        (datetime(2026, 8, 16, 20, 0), 12),
    ],
)
def test_daily_xianmeng_low_average_score_waits_twenty_minutes(
    monkeypatch,
    now,
    remaining,
):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: now,
    )
    harness = _XianmengHarness()
    runtime = _ImmunityRuntime()
    runtime.ocr_text_in_shapes = lambda *_args, **_kwargs: (
        "获得个人积分×100 获得个人积分×100 获得个人积分×300 获得个人积分×100"
    )
    harness._fanxiu_runtime = lambda *_args, **_kwargs: runtime
    harness._enter_daily_xianmeng_attack_view = lambda *_args: _returning(294)
    harness._read_daily_xianmeng_count_snapshot = lambda: {
        "ok": True,
        "complete": True,
        "attack_count": remaining,
    }

    result = _drain(
        harness._execute_daily_xianmeng_task(
            {},
            threading.Event(),
            {"__scheduler_task_id": "legacy-daily-xianmeng"},
        )
    )

    assert result == "skipped"
    assert harness.next_times == []
    assert any(
        (now + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S") in message
        for _kind, message in harness.logs
    )
    assert runtime.actions[0][0:2] == ("goto", 34)
    assert any("平均个人积分 150.0" in message for _kind, message in harness.logs)


@pytest.mark.parametrize(
    ("now", "snapshot", "expected"),
    [
        (
            datetime(2026, 8, 16, 12, 59),
            {"ok": True, "complete": True, "attack_count": 196},
            (False, 196),
        ),
        (
            datetime(2026, 8, 16, 13, 0),
            {"ok": True, "complete": True, "attack_count": 60},
            (False, 60),
        ),
        (
            datetime(2026, 8, 16, 13, 0),
            {"ok": True, "complete": True, "attack_count": 61},
            (True, 61),
        ),
        (
            datetime(2026, 8, 16, 20, 0),
            {"ok": False, "complete": False, "attack_count": 196},
            (False, None),
        ),
    ],
)
def test_daily_xianmeng_low_score_batch_requires_afternoon_stamina_overflow(
    now,
    snapshot,
    expected,
):
    assert _XianmengHarness._daily_xianmeng_should_continue_low_score_sweep(
        snapshot,
        {},
        now=now,
    ) == expected


def test_daily_xianmeng_high_stamina_low_score_continues_in_same_cell(monkeypatch):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: datetime(2026, 8, 16, 20, 0),
    )
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    runtime.ocr_text_in_shapes = lambda *_args, **_kwargs: (
        "获得个人积分×100 获得个人积分×100 获得个人积分×100"
    )
    harness._fanxiu_runtime = lambda *_args, **_kwargs: runtime
    harness._enter_daily_xianmeng_attack_view = lambda *_args: _returning(294)
    harness._read_daily_xianmeng_count_snapshot = lambda: {
        "ok": True,
        "complete": True,
        "attack_count": 196,
    }
    harness._ensure_daily_xianmeng_attack_options = lambda *_args: _returning(True)
    harness._read_daily_xianmeng_attack_count_once = lambda *_args: _returning(196)
    scenes = iter((293, 294))
    harness._wait_daily_xianmeng_fast_attack_scene = (
        lambda *_args, **_kwargs: _returning(next(scenes))
    )
    harness._wait_daily_xianmeng_attack_departure = lambda *_args: _returning(True)
    harness._return_daily_xianmeng_to_world = lambda *_args: _returning(None)

    result = _drain(
        harness._execute_daily_xianmeng_task(
            {},
            threading.Event(),
            {"rounds": 1, "__scheduler_task_id": "legacy-daily-xianmeng"},
        )
    )

    assert result == "success"
    assert ("click", 293, "攻击") in runtime.actions
    assert harness.next_times == []
    assert any("本 Cell 继续批量清扫" in message for _kind, message in harness.logs)


@pytest.mark.parametrize(
    ("now", "expected_next_time"),
    [
        (datetime(2026, 8, 16, 19, 42), "2026-08-16 21:10:00"),
        (datetime(2026, 8, 16, 21, 20), "2026-08-16 21:50:00"),
    ],
)
def test_daily_xianmeng_keeps_last_two_attempts_for_next_tail_sweep(
    monkeypatch,
    now,
    expected_next_time,
):
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_resources.job_now",
        lambda: now,
    )
    harness = _XianmengHarness()
    runtime = _ImmunityRuntime()
    harness._fanxiu_runtime = lambda *_args, **_kwargs: runtime
    harness._enter_daily_xianmeng_attack_view = lambda *_args: _returning(293)
    harness._ensure_daily_xianmeng_attack_options = lambda *_args: _returning(True)
    harness._read_daily_xianmeng_attack_count_once = lambda *_args: _returning(2)

    result = _drain(
        harness._execute_daily_xianmeng_task(
            {},
            threading.Event(),
            {
                "__scheduler_task_id": "legacy-daily-xianmeng",
                "event_tail_date": "2026-08-16",
                "event_tail_times": ["21:10", "21:50"],
            },
        )
    )

    assert result == "skipped"
    assert harness.next_times == []
    assert any(expected_next_time in message for _kind, message in harness.logs)
    assert runtime.actions[0][0:2] == ("goto", 34)
    assert not any(action[0] == "click" for action in runtime.actions)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 16, 21, 29), True),
        (datetime(2026, 8, 16, 21, 30), False),
        (datetime(2026, 8, 16, 21, 50), False),
    ],
)
def test_daily_xianmeng_tail_preservation_stops_at_triple_disable_cutoff(now, expected):
    assert (
        _XianmengHarness._daily_xianmeng_should_preserve_tail_before_triple_disable(now)
        is expected
    )


def test_daily_xianmeng_enables_triple_once_then_uses_daily_checkpoint():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    today = datetime.now().astimezone().isoformat()
    snapshots = iter(
        [
            {
                "ok": True,
                "complete": True,
                "score": 15_000,
                "stage": 1,
                "skip_checked": True,
                "triple_checked": False,
                "captured_at": today,
            },
            {
                "ok": True,
                "complete": True,
                "score": 15_000,
                "stage": 1,
                "skip_checked": True,
                "triple_checked": True,
                "captured_at": today,
            },
        ]
    )
    read_count = 0

    def read_snapshot():
        nonlocal read_count
        read_count += 1
        return next(snapshots)

    harness._read_daily_xianmeng_attack_options_snapshot = read_snapshot

    first = _drain(harness._ensure_daily_xianmeng_attack_options(runtime, {}))
    second = _drain(harness._ensure_daily_xianmeng_attack_options(runtime, {}))

    assert first is True
    assert second is True
    assert read_count == 2
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 293, "三连")
    ]


def test_daily_xianmeng_enables_skip_but_keeps_single_attack_before_triple_score():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    today = datetime.now().astimezone().isoformat()
    snapshots = iter(
        [
            {
                "ok": True,
                "complete": True,
                "score": 1_000,
                "stage": 1,
                "skip_checked": False,
                "triple_checked": False,
                "captured_at": today,
            },
            {
                "ok": True,
                "complete": True,
                "score": 1_000,
                "stage": 1,
                "skip_checked": True,
                "triple_checked": False,
                "captured_at": today,
            },
        ]
    )
    harness._read_daily_xianmeng_attack_options_snapshot = lambda: next(snapshots)

    triple_enabled = _drain(
        harness._ensure_daily_xianmeng_attack_options(runtime, {})
    )

    assert triple_enabled is False
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 293, "跳过")
    ]
    assert not hasattr(harness, "_daily_xianmeng_option_verification_cache")


def test_daily_xianmeng_false_triple_state_is_rechecked_until_runtime_threshold():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    today = datetime.now().astimezone().isoformat()
    snapshots = iter(
        [
            {
                "ok": True,
                "complete": True,
                "score": 14_900,
                "stage": 1,
                "skip_checked": True,
                "triple_checked": False,
                "captured_at": today,
            },
            {
                "ok": True,
                "complete": True,
                "score": 15_100,
                "stage": 1,
                "skip_checked": True,
                "triple_checked": False,
                "captured_at": today,
            },
            {
                "ok": True,
                "complete": True,
                "score": 15_100,
                "stage": 1,
                "skip_checked": True,
                "triple_checked": True,
                "captured_at": today,
            },
        ]
    )
    harness._read_daily_xianmeng_attack_options_snapshot = lambda: next(snapshots)

    before_unlock = _drain(harness._ensure_daily_xianmeng_attack_options(runtime, {}))
    after_unlock = _drain(harness._ensure_daily_xianmeng_attack_options(runtime, {}))

    assert before_unlock is False
    assert after_unlock is True
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 293, "三连")
    ]
    assert harness._daily_xianmeng_option_verification_cache["triple_checked"] is True


@pytest.mark.parametrize(
    ("score", "average", "expected"),
    [
        (8_466, None, 10),
        (13_927, 200.5, 4),
        (14_900, 200.5, 1),
        (15_000, 200.5, 1),
    ],
)
def test_daily_xianmeng_estimates_bounded_runtime_probe_interval(score, average, expected):
    assert _XianmengHarness._daily_xianmeng_next_triple_probe_after(
        {
            "score": score,
            "triple_score_threshold": 15_000,
            "triple_checked": False,
        },
        average_score=average,
    ) == expected


def test_daily_xianmeng_disables_triple_to_consume_final_two_attempts():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    harness._read_daily_xianmeng_attack_options_snapshot = lambda: {
        "ok": True,
        "complete": True,
        "stage": 2,
        "triple_checked": False,
        "captured_at": datetime.now().astimezone().isoformat(),
    }

    triple_enabled = _drain(
        harness._disable_daily_xianmeng_triple_for_tail(
            runtime,
            {},
            remaining_attempts=2,
        )
    )

    assert triple_enabled is False
    assert runtime.actions[0] == ("click", 293, "三连")
    assert harness._daily_xianmeng_option_verification_cache["triple_checked"] is False
    assert any("改用单攻清零" in message for _kind, message in harness.logs)


@pytest.mark.parametrize("numbers", [[], [0], [3, 0]])
def test_daily_xianmeng_empty_or_zero_attempt_ocr_fails_closed(numbers):
    assert _XianmengHarness._daily_xianmeng_attempts_exhausted(numbers) is True


def test_daily_xianmeng_positive_attempt_ocr_can_continue():
    assert _XianmengHarness._daily_xianmeng_attempts_exhausted([1]) is False


def test_daily_xianmeng_event_tail_schedule_is_opt_in_and_one_off():
    today = datetime.now().astimezone().date().isoformat()
    next_time = _XianmengHarness._daily_xianmeng_event_tail_next_time(
        {"event_tail_date": today, "event_tail_times": ["23:58", "23:59"]}
    )

    assert next_time is None
    assert _XianmengHarness._daily_xianmeng_event_tail_next_time({}) is None


def test_daily_xianmeng_turns_off_stale_options_after_score_reset():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    today = datetime.now().astimezone().isoformat()
    snapshots = iter(
        [
            {
                "ok": True,
                "complete": True,
                "score": 0,
                "stage": 2,
                "skip_checked": True,
                "triple_checked": True,
                "captured_at": today,
            },
            {
                "ok": True,
                "complete": True,
                "score": 0,
                "stage": 2,
                "skip_checked": False,
                "triple_checked": False,
                "captured_at": today,
            },
        ]
    )
    harness._read_daily_xianmeng_attack_options_snapshot = lambda: next(snapshots)

    triple_enabled = _drain(
        harness._ensure_daily_xianmeng_attack_options(runtime, {})
    )

    assert triple_enabled is False
    assert [action for action in runtime.actions if action[0] == "click"] == [
        ("click", 293, "跳过"),
        ("click", 293, "三连"),
    ]


def test_daily_xianmeng_refuses_attack_option_guess_when_runtime_is_incomplete():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    harness._read_daily_xianmeng_attack_options_snapshot = lambda: {
        "ok": False,
        "complete": False,
    }

    with pytest.raises(RuntimeError, match="动态插桩未返回完整攻击配置"):
        _drain(harness._ensure_daily_xianmeng_attack_options(runtime, {}))

    assert runtime.actions == []


def test_daily_xianmeng_remaining_attempt_guard_follows_verified_triple_state():
    assert _XianmengHarness._daily_xianmeng_required_attempts(False) == 1
    assert _XianmengHarness._daily_xianmeng_required_attempts(True) == 3


def test_daily_xianmeng_rechecks_after_calendar_day_changes():
    harness = _XianmengHarness()
    runtime = _OptionRuntime()
    harness._daily_xianmeng_option_verification_cache = {
        "day": (datetime.now().astimezone() - timedelta(days=1)).date().isoformat(),
        "cycle_key": "yesterday:stage-1",
        "triple_checked": True,
    }
    reads = []

    def read_snapshot():
        reads.append(True)
        return {
            "ok": True,
            "complete": True,
            "score": 0,
            "stage": 2,
            "skip_checked": False,
            "triple_checked": False,
            "captured_at": datetime.now().astimezone().isoformat(),
        }

    harness._read_daily_xianmeng_attack_options_snapshot = read_snapshot

    triple_enabled = _drain(
        harness._ensure_daily_xianmeng_attack_options(runtime, {})
    )

    assert triple_enabled is False
    assert reads == [True]
