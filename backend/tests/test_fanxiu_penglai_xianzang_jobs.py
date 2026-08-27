from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import penglai_xianzang_jobs as jobs
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XianzangPageResult,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    build_bothdraw_reward_items,
)


class _Runner:
    def __init__(self):
        self.runtime = object()
        self.next_times = []
        self.logs = []

    def _fanxiu_runtime(self, ctx, asset_tree_path, *, stop_event):
        assert ctx["asset_tree_path"] == asset_tree_path
        return self.runtime

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _log(self, kind, message):
        self.logs.append((kind, message))


def test_live_library_ids_are_resolved_without_fixed_item_assumptions():
    result = build_bothdraw_reward_items(
        [800101, 800102],
        optional_rows={
            800101: {"giftID": 11080004},
            800102: {"giftID": 11080005},
        },
        item_cards=[
            {"id": 11080004, "name": "普通道具"},
            {
                "id": 11080005,
                "name": "神炼材料",
                "linked_talisman_refine_target_id": 77,
            },
        ],
    )

    assert result == [
        {
            "library_id": 800101,
            "item_id": 11080004,
            "name": "普通道具",
            "target_talisman_id": None,
            "kind": "",
        },
        {
            "library_id": 800102,
            "item_id": 11080005,
            "name": "神炼材料",
            "target_talisman_id": 77,
            "kind": "talisman_refine_material",
        },
    ]


def test_non_four_shenlian_week_skips_optional_but_continues_store_and_tasks(monkeypatch):
    runner = _Runner()
    actions = []
    monkeypatch.setattr(jobs, "_record_availability", lambda **_kwargs: None)
    monkeypatch.setattr(jobs, "read_xianzang_page", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs, "enter_xianzang", lambda runtime: actions.append("enter"))
    monkeypatch.setattr(
        jobs,
        "read_bothdraw_optional_reward_runtime",
        lambda: {
            "complete": True,
            "reward_items": [
                {"item_id": index, "name": f"道具{index}", "kind": ""}
                for index in range(4)
            ],
        },
    )
    monkeypatch.setattr(
        jobs,
        "open_xianzang_optional_reward",
        lambda _runtime: actions.append("optional"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_store",
        lambda _runtime: actions.append("store") or SimpleNamespace(clicked_values=(488, 988)),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_tasks",
        lambda _runtime: actions.append("tasks") or SimpleNamespace(clicked_count=0, stop_reason="all_claimed"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_config_ten_draws",
        lambda _runtime: actions.append("lottery")
        or {
            "round_count": 2,
            "stop_reason": "draws_exhausted_and_rewards_claimed",
        },
    )
    monkeypatch.setattr(
        jobs,
        "leave_xianzang",
        lambda _runtime: actions.append("leave") or (34, 100.0),
    )

    result = jobs.execute_xianzang_config_job(
        runner,
        {"asset_tree_path": Path("asset-tree.json")},
        {},
        object(),
    )

    assert actions == ["enter", "store", "tasks", "lottery", "leave"]
    assert result["optional"]["outcome"] == "skipped"
    assert result["lottery_outcome"]["round_count"] == 2
    assert result["final_scene"] == 34
    assert result["final_scene_score"] == 100.0
    assert runner.next_times == [(jobs.XIANZANG_CONFIG_TASK_ID, None)]


def test_already_configured_penglai_never_reopens_optional(monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(
        jobs,
        "read_bothdraw_optional_reward_runtime",
        lambda: {
            "complete": True,
            "selected_big_reward": {
                "item_id": 4130017,
                "name": "已选奖励",
            },
            "reward_items": [],
        },
    )
    monkeypatch.setattr(
        jobs,
        "open_xianzang_optional_reward",
        lambda _runtime: pytest.fail("已配置后不得重复打开自选"),
    )

    result = jobs._optional_selection(object(), runner)

    assert result["outcome"] == "already_configured"
    assert result["confirmed"] is True


def test_four_shenlian_week_chooses_from_current_candidates(monkeypatch):
    runner = _Runner()
    reward_items = [
        {
            "item_id": 100 + index,
            "name": f"材料{index}",
            "target_talisman_id": 200 + index,
            "kind": "talisman_refine_material",
        }
        for index in range(1, 5)
    ]
    monkeypatch.setattr(
        jobs,
        "read_bothdraw_optional_reward_runtime",
        lambda: {"complete": True, "reward_items": reward_items},
    )
    monkeypatch.setattr(
        jobs,
        "read_magic_treasure_hall_runtime",
        lambda: {
            "complete": True,
            "items": [
                {
                    "talisman_id": 200 + index,
                    "name": f"法宝{index}",
                    "owned": True,
                    "rank": 1,
                    "wujing_level": level,
                }
                for index, level in enumerate((2, 8, 4, 5), start=1)
            ],
        },
    )
    monkeypatch.setattr(jobs, "open_xianzang_optional_reward", lambda _runtime: None)
    selected = []
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_optional_reward_selection",
        lambda _runtime, column, **kwargs: selected.append((column, kwargs))
        or SimpleNamespace(confirmed=True),
    )

    result = jobs._optional_selection(runner.runtime, runner)

    assert selected == [(2, {"allow_missing_fraction_ocr": True})]
    assert result["outcome"] == "configured"
    assert result["column"] == 2
    assert result["target_talisman_id"] == 202
    assert len(result["candidate_evidence"]) == 4
    assert result["candidate_evidence"][1]["selected"] is True
    assert "法宝2" not in "\n".join(message for _level, message in runner.logs)


def test_config_job_resumes_reliable_optional_page_before_entering_main(monkeypatch):
    runner = _Runner()
    actions = []
    monkeypatch.setattr(jobs, "_record_availability", lambda **_kwargs: None)
    monkeypatch.setattr(
        jobs,
        "read_xianzang_page",
        lambda _runtime, **_kwargs: XianzangPageResult("自选", 448, 100.0, "自选奖励"),
    )

    def resume_optional(_runtime, _runner, *, already_on_optional_page=False):
        assert already_on_optional_page is True
        actions.append("resume_optional")
        return {"outcome": "configured", "column": 3, "confirmed": True}

    monkeypatch.setattr(jobs, "_optional_selection", resume_optional)
    monkeypatch.setattr(jobs, "enter_xianzang", lambda _runtime: actions.append("enter"))
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_store",
        lambda _runtime: actions.append("store") or SimpleNamespace(clicked_values=()),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_tasks",
        lambda _runtime: actions.append("tasks")
        or SimpleNamespace(clicked_count=0, stop_reason="all_claimed"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_config_ten_draws",
        lambda _runtime: actions.append("lottery") or {"round_count": 0},
    )
    monkeypatch.setattr(
        jobs,
        "leave_xianzang",
        lambda _runtime: actions.append("leave") or (34, 100.0),
    )

    result = jobs.execute_xianzang_config_job(
        runner,
        {"asset_tree_path": Path("asset-tree.json")},
        {},
        object(),
    )

    assert actions == ["resume_optional", "enter", "store", "tasks", "lottery", "leave"]
    assert result["optional"] == {
        "outcome": "configured",
        "column": 3,
        "confirmed": True,
    }


def test_config_job_closes_reliable_draw_result_before_runtime_reconciliation(monkeypatch):
    class Runtime:
        def current_scene(self, scene_ids, *, update):
            assert scene_ids == [451]
            assert update is True
            return 451, 100.0, "fresh-result-frame"

    runner = _Runner()
    runner.runtime = Runtime()
    actions = []
    monkeypatch.setattr(jobs, "_record_availability", lambda **_kwargs: None)
    monkeypatch.setattr(
        jobs,
        "close_xianzang_draw_result",
        lambda _runtime: actions.append("close_existing_result")
        or {"result": "success", "clicked_count": 1},
    )
    monkeypatch.setattr(jobs, "read_xianzang_page", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs, "enter_xianzang", lambda _runtime: actions.append("enter"))
    monkeypatch.setattr(
        jobs,
        "_optional_selection",
        lambda _runtime, _runner: actions.append("already_configured")
        or {"outcome": "already_configured", "confirmed": True},
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_store",
        lambda _runtime: actions.append("store") or SimpleNamespace(clicked_values=()),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_tasks",
        lambda _runtime: actions.append("tasks")
        or SimpleNamespace(clicked_count=0, stop_reason="all_claimed"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_config_ten_draws",
        lambda _runtime: actions.append("reconcile_runtime_counter")
        or {"round_count": 0, "stop_reason": "fewer_than_ten"},
    )
    monkeypatch.setattr(
        jobs,
        "leave_xianzang",
        lambda _runtime: actions.append("leave") or (34, 100.0),
    )

    result = jobs.execute_xianzang_config_job(
        runner,
        {"asset_tree_path": Path("asset-tree.json")},
        {},
        object(),
    )

    assert actions == [
        "close_existing_result",
        "enter",
        "already_configured",
        "store",
        "tasks",
        "reconcile_runtime_counter",
        "leave",
    ]
    assert result["resumed_draw_result"]["result"] == "success"


def test_unresolved_talisman_name_skips_only_optional_selection(monkeypatch):
    runner = _Runner()
    reward_items = [
        {
            "item_id": 100 + index,
            "name": f"材料{index}",
            "target_talisman_id": 200 + index,
            "kind": "talisman_refine_material",
        }
        for index in range(1, 5)
    ]
    monkeypatch.setattr(
        jobs,
        "read_bothdraw_optional_reward_runtime",
        lambda: {"complete": True, "reward_items": reward_items},
    )
    monkeypatch.setattr(
        jobs,
        "read_magic_treasure_hall_runtime",
        lambda: {"complete": False, "reason": "法宝 2063 的名称无法解析"},
    )
    monkeypatch.setattr(
        jobs,
        "open_xianzang_optional_reward",
        lambda _runtime: pytest.fail("名称不完整时不得打开不可逆自选页"),
    )

    result = jobs._optional_selection(runner.runtime, runner)

    assert result["outcome"] == "skipped"
    assert "法宝 2063 的名称无法解析" in result["reason"]


def test_other_talisman_runtime_incompleteness_remains_an_error(monkeypatch):
    runner = _Runner()
    reward_items = [
        {
            "item_id": 100 + index,
            "name": f"材料{index}",
            "target_talisman_id": 200 + index,
            "kind": "talisman_refine_material",
        }
        for index in range(1, 5)
    ]
    monkeypatch.setattr(
        jobs,
        "read_bothdraw_optional_reward_runtime",
        lambda: {"complete": True, "reward_items": reward_items},
    )
    monkeypatch.setattr(
        jobs,
        "read_magic_treasure_hall_runtime",
        lambda: {"complete": False, "reason": "法宝清单尚未完整加载"},
    )

    with pytest.raises(RuntimeError, match="法宝清单尚未完整加载"):
        jobs._optional_selection(runner.runtime, runner)


def test_daily_sync_owned_jobs_have_no_static_bootstrap_times():
    now = datetime(2026, 8, 6, 19, 5, 0)  # Thursday
    tasks = {item["id"]: item for item in default_data_annotation_scheduler_tasks(now)}

    assert tasks[jobs.XIANZANG_CONFIG_TASK_ID]["next_time"] is None
    assert tasks[jobs.XIANZANG_LOTTERY_TASK_ID]["next_time"] is None
    assert tasks[jobs.XIANZANG_CONFIG_TASK_ID]["trigger_description"] == "动态"
    assert tasks[jobs.XIANZANG_LOTTERY_TASK_ID]["trigger_description"] == "动态"

    register_fanxiu_data_annotation_default_runtime_jobs()
    assert get_fanxiu_data_annotation_task_cell_definition(
        jobs.XIANZANG_CONFIG_TASK_TYPE
    ).scheduler_supported is True
    assert get_fanxiu_data_annotation_task_cell_definition(
        jobs.XIANZANG_LOTTERY_TASK_TYPE
    ).scheduler_supported is True


def test_registered_config_wrapper_delegates_without_generic_world_navigation(monkeypatch):
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        jobs.XIANZANG_CONFIG_TASK_TYPE
    )
    runner = _Runner()
    expected = {"result": "resumed"}
    calls = []
    monkeypatch.setattr(
        jobs,
        "execute_xianzang_config_job",
        lambda actual_runner, ctx, payload, stop_event: calls.append(
            (actual_runner, ctx, payload, stop_event)
        )
        or expected,
    )
    stop_event = object()
    ctx = {"asset_tree_path": Path("asset-tree.json")}

    result = definition.handler(runner, ctx, {"source": "test"}, stop_event)

    assert result is expected
    assert calls == [(runner, ctx, {"source": "test"}, stop_event)]


def test_lottery_job_runs_tasks_draw_claim_loop_and_returns_world(monkeypatch):
    runner = _Runner()
    runner.runtime = object()
    actions = []
    monkeypatch.setattr(jobs, "_record_availability", lambda **_kwargs: None)
    monkeypatch.setattr(jobs, "enter_xianzang", lambda _runtime: actions.append("enter"))
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_tasks",
        lambda _runtime: actions.append("tasks")
        or SimpleNamespace(clicked_count=2, stop_reason="all_claimed"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_lottery",
        lambda _runtime: actions.append("lottery")
        or {"round_count": 3, "stop_reason": "draws_exhausted_and_rewards_claimed"},
    )
    monkeypatch.setattr(
        jobs,
        "leave_xianzang",
        lambda _runtime: actions.append("leave") or (34, 100.0),
    )

    result = jobs.execute_xianzang_lottery_job(
        runner,
        {"asset_tree_path": Path("asset-tree.json")},
        {},
        object(),
    )

    assert actions == ["enter", "tasks", "lottery", "leave"]
    assert result["lottery_outcome"]["round_count"] == 3
    assert result["final_scene"] == 34
    assert runner.next_times == [(jobs.XIANZANG_LOTTERY_TASK_ID, None)]


def test_standard_job_does_not_advance_schedule_when_return_to_world_fails(monkeypatch):
    runner = _Runner()
    actions = []
    monkeypatch.setattr(jobs, "_record_availability", lambda **_kwargs: None)
    monkeypatch.setattr(jobs, "enter_xianzang", lambda _runtime: actions.append("enter"))
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_tasks",
        lambda _runtime: actions.append("tasks")
        or SimpleNamespace(clicked_count=0, stop_reason="all_claimed"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_xianzang_lottery",
        lambda _runtime: actions.append("lottery") or {"round_count": 0},
    )

    def fail_to_leave(_runtime):
        actions.append("leave")
        raise RuntimeError("未回到 #34")

    monkeypatch.setattr(jobs, "leave_xianzang", fail_to_leave)

    with pytest.raises(RuntimeError, match="#34"):
        jobs.execute_xianzang_lottery_job(
            runner,
            {"asset_tree_path": Path("asset-tree.json")},
            {},
            object(),
        )

    assert actions == ["enter", "tasks", "lottery", "leave"]
    assert runner.next_times == []
    assert not any(kind == "success" for kind, _message in runner.logs)


def test_weekly_activity_absence_is_idempotent_skip(monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(jobs, "_record_availability", lambda **_kwargs: None)
    monkeypatch.setattr(jobs, "read_xianzang_page", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jobs,
        "enter_xianzang",
        lambda _runtime: (_ for _ in ()).throw(
            jobs.XianzangActivityUnavailable("连续 60 秒未找到蓬莱仙藏入口")
        ),
    )
    monkeypatch.setattr(
        jobs,
        "_run_xianzang_config_workflow",
        lambda *_args: pytest.fail("活动缺席时不应执行配置流程"),
    )

    result = jobs.execute_xianzang_config_job(
        runner,
        {"asset_tree_path": Path("asset-tree.json")},
        {},
        object(),
    )

    assert result["result"] == "skipped"
    assert result["skip_reason"] == "activity_unavailable"
    assert result["final_scene"] == 34
    assert runner.next_times == [(jobs.XIANZANG_CONFIG_TASK_ID, None)]
