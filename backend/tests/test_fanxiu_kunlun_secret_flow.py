from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import kunlun_secret as selection
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_jobs as jobs
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_navigation as navigation
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_tasks as task_completion


class _KunlunTaskRuntime:
    def __init__(self, *, scene_id: int = 540, auto_return_after_claim: bool = False):
        self.scene_id = scene_id
        self.auto_return_after_claim = auto_return_after_claim
        self.clicks: list[tuple[int, str]] = []

    def current_scene(self, _scene_ids, *, update=True):
        del update
        return self.scene_id, 100.0, "frame"

    def ocr_text(self, _frame):
        return "昆仑秘藏 任务 商店 自选"

    def cur_frame(self, *, update=True):
        del update
        return "frame"

    def click_shape(self, scene_id, title, *, frame_data_url):
        del frame_data_url
        self.clicks.append((int(scene_id), str(title)))
        if title == "任务":
            self.scene_id = navigation.KUNLUN_TASK_SCENE_ID
        elif title == "进度" and self.auto_return_after_claim:
            self.scene_id = navigation.KUNLUN_MAIN_SCENE_ID
        elif title == "kunlun昆仑秘藏":
            self.scene_id = navigation.KUNLUN_MAIN_SCENE_ID


def _kunlun_task_snapshot(state: str) -> dict[str, object]:
    task = {"task_id": 1, "state": state}
    return {
        "complete": True,
        "tasks": [task],
        "claimable": [task] if state == "claimable" else [],
    }


def _reward_items() -> list[dict[str, object]]:
    return [
        {"item_id": 100 + column, "name": f"道具{column}", "target_id": column}
        for column in range(1, 5)
    ]


def _owned_items() -> list[dict[str, object]]:
    return [
        {"target_id": column, "name": f"本体{column}", "rank": column, "weight": 2}
        for column in range(1, 5)
    ]


def test_first_row_requires_an_injected_evidence_based_selector() -> None:
    with pytest.raises(selection.KunlunFirstRowUndecided, match="尚未注入"):
        selection.decide_kunlun_first_row(
            _reward_items(), _owned_items(), selector=None
        )


def test_first_row_selector_receives_four_candidates_and_owned_progress() -> None:
    observed: dict[str, object] = {}

    def selector(candidates, owned):
        observed["candidates"] = candidates
        observed["owned"] = owned
        return selection.KunlunFirstRowDecision(column=3, reason="真实阶数/重数排序结果")

    decision = selection.decide_kunlun_first_row(
        _reward_items(), _owned_items(), selector=selector
    )

    assert decision.column == 3
    assert len(observed["candidates"]) == 4
    assert [item.rank for item in observed["owned"]] == [1, 2, 3, 4]


def test_default_selector_prioritizes_shanhe_stage_39_special_effect() -> None:
    owned = _owned_items()
    owned[0]["rank"] = 20
    owned[1]["rank"] = 36
    owned[2]["rank"] = 0
    owned[3]["rank"] = 0

    rewards = _reward_items()
    rewards[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    owned[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    decision = selection.decide_kunlun_first_row(
        rewards, owned, selector=jobs.KUNLUN_FIRST_ROW_SELECTOR
    )

    assert decision.column == 2
    assert "39阶" in decision.reason
    assert "万国来朝" in decision.reason
    assert "持续道具资源" in decision.reason


def test_default_selector_continues_shanhe_after_special_effect_milestone() -> None:
    rewards = _reward_items()
    owned = _owned_items()
    rewards[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    owned[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    owned[1]["rank"] = 39

    decision = selection.decide_kunlun_first_row(
        rewards, owned, selector=jobs.KUNLUN_FIRST_ROW_SELECTOR
    )

    assert decision.column == 2
    assert "继续优先山河" in decision.reason


def test_default_selector_fails_closed_when_shanhe_is_absent() -> None:
    with pytest.raises(selection.KunlunFirstRowUndecided, match="山河无疆屏候选"):
        selection.decide_kunlun_first_row(
            _reward_items(), _owned_items(), selector=jobs.KUNLUN_FIRST_ROW_SELECTOR
        )


def test_runtime_reader_snapshot_is_required_complete(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "read_kunlun_first_row_runtime",
        lambda: {"complete": False, "reason": "FashionMgr 尚未加载"},
    )
    with pytest.raises(selection.KunlunFirstRowUndecided, match="FashionMgr 尚未加载"):
        jobs.read_kunlun_first_row_inputs()


def test_optional_reward_never_opens_form_when_reader_is_incomplete(monkeypatch) -> None:
    opened = False

    def open_form(_runtime):
        nonlocal opened
        opened = True

    monkeypatch.setattr(jobs, "open_kunlun_optional_reward", open_form)

    def incomplete():
        raise selection.KunlunFirstRowUndecided("reader incomplete")

    with pytest.raises(selection.KunlunFirstRowUndecided, match="reader incomplete"):
        jobs._select_optional_reward(object(), inputs_reader=incomplete)
    assert opened is False


def test_optional_reward_does_not_reopen_after_server_confirmed_shanhe(monkeypatch) -> None:
    opened = False

    def open_form(_runtime):
        nonlocal opened
        opened = True

    monkeypatch.setattr(jobs, "open_kunlun_optional_reward", open_form)
    rewards = _reward_items()
    rewards[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    owned = _owned_items()
    owned[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    inputs = jobs.KunlunFirstRowInputs(
        reward_items=tuple(rewards),
        owned_items=tuple(owned),
        selected_big_reward={"item_id": rewards[1]["item_id"]},
    )

    result = jobs._select_optional_reward(
        object(),
        inputs_reader=lambda: inputs,
        selector=jobs.KUNLUN_FIRST_ROW_SELECTOR,
    )

    assert result["outcome"] == "already_configured"
    assert result["column"] == 2
    assert result["confirmed"] is True
    assert opened is False


def test_optional_reward_never_reconfigures_an_existing_different_reward(monkeypatch) -> None:
    opened = False

    def open_form(_runtime):
        nonlocal opened
        opened = True

    monkeypatch.setattr(jobs, "open_kunlun_optional_reward", open_form)
    rewards = _reward_items()
    rewards[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    owned = _owned_items()
    owned[1]["target_id"] = jobs.SHANHE_WUJIANG_TARGET_ID
    inputs = jobs.KunlunFirstRowInputs(
        reward_items=tuple(rewards),
        owned_items=tuple(owned),
        selected_big_reward={"item_id": rewards[0]["item_id"]},
    )

    with pytest.raises(selection.KunlunFirstRowUndecided, match="已配置为其它大奖"):
        jobs._select_optional_reward(
            object(),
            inputs_reader=lambda: inputs,
            selector=jobs.KUNLUN_FIRST_ROW_SELECTOR,
        )
    assert opened is False


def test_config_workflow_reuses_rows_two_three_then_store_and_tasks(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        jobs,
        "_select_optional_reward",
        lambda *_args, **_kwargs: calls.append("optional") or {"outcome": "configured"},
    )
    monkeypatch.setattr(
        jobs, "open_kunlun_tab", lambda _runtime, tab: calls.append(f"tab:{tab}")
    )
    monkeypatch.setattr(
        jobs,
        "complete_kunlun_store",
        lambda _runtime: calls.append("store") or SimpleNamespace(clicked_values=(8, 18)),
    )
    monkeypatch.setattr(
        jobs,
        "complete_kunlun_tasks",
        lambda _runtime: calls.append("tasks")
        or SimpleNamespace(clicked_count=2, stop_reason="all_claimed"),
    )
    monkeypatch.setattr(
        jobs,
        "complete_kunlun_lottery",
        lambda _runtime, **kwargs: calls.append(f"lottery:{kwargs['allow_single_draws']}")
        or {"stop_reason": "stop_first_grand_prize"},
    )

    result = jobs._run_kunlun_config_workflow(object())

    assert calls == ["optional", "tab:商店", "store", "tasks", "lottery:False"]
    assert result["store_clicked_values"] == [8, 18]


def test_completed_kunlun_tasks_from_main_skip_task_page(monkeypatch) -> None:
    runtime = _KunlunTaskRuntime(scene_id=navigation.KUNLUN_MAIN_SCENE_ID)
    monkeypatch.setattr(
        task_completion,
        "read_bothdraw_task_runtime",
        lambda: _kunlun_task_snapshot("claimed"),
    )

    result = task_completion.complete_kunlun_tasks(runtime, retry_seconds=0)

    assert result.clicked_count == 0
    assert result.stop_reason == "all_claimed"
    assert result.final_page.scene_id == navigation.KUNLUN_MAIN_SCENE_ID
    assert runtime.clicks == []


def test_last_kunlun_task_auto_return_to_main_converges(monkeypatch) -> None:
    runtime = _KunlunTaskRuntime(
        scene_id=navigation.KUNLUN_MAIN_SCENE_ID,
        auto_return_after_claim=True,
    )
    snapshots = iter(
        [
            _kunlun_task_snapshot("claimable"),
            _kunlun_task_snapshot("claimed"),
        ]
    )
    monkeypatch.setattr(
        task_completion,
        "read_bothdraw_task_runtime",
        lambda: next(snapshots),
    )

    result = task_completion.complete_kunlun_tasks(runtime, retry_seconds=0)

    assert result.clicked_count == 1
    assert result.stop_reason == "all_claimed"
    assert result.final_page.scene_id == navigation.KUNLUN_MAIN_SCENE_ID
    assert runtime.clicks == [(540, "任务"), (543, "进度")]


def test_claimable_kunlun_task_enters_task_page_before_claim(monkeypatch) -> None:
    runtime = _KunlunTaskRuntime(scene_id=navigation.KUNLUN_MAIN_SCENE_ID)
    snapshots = iter(
        [
            _kunlun_task_snapshot("claimable"),
            _kunlun_task_snapshot("claimed"),
        ]
    )
    monkeypatch.setattr(
        task_completion,
        "read_bothdraw_task_runtime",
        lambda: next(snapshots),
    )

    result = task_completion.complete_kunlun_tasks(runtime, retry_seconds=0)

    assert result.clicked_count == 1
    assert result.stop_reason == "all_claimed"
    assert result.final_page.scene_id == navigation.KUNLUN_MAIN_SCENE_ID
    assert runtime.clicks == [
        (540, "任务"),
        (543, "进度"),
        (543, "kunlun昆仑秘藏"),
    ]


def test_kunlun_store_uses_real_scene_wait_budget(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_store

    observed: dict[str, object] = {}

    def operate(_runtime, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(clicked_values=(), remaining_targets=())

    monkeypatch.setattr(kunlun_secret_store, "operate_activity_store_region", operate)
    kunlun_secret_store.complete_kunlun_store(object())

    assert observed["stability_timeout_seconds"] == 30.0
    assert observed["purchase_timeout_seconds"] == 30.0


def test_scene_ids_are_independent_from_penglai_assets() -> None:
    assert navigation.KUNLUN_KNOWN_SCENE_IDS == (540, 541, 542, 543)


@pytest.mark.parametrize("scene_id,score", [(34, 100.0), (None, 0.0), (540, 79.9)])
def test_world_activity_menu_text_never_identifies_kunlun_main_page(
    scene_id: int | None, score: float
) -> None:
    result = navigation._page_from_observation(
        scene_id,
        score,
        "世界 日程 昆仑秘藏 任务 商店 自选",
    )

    assert result is None


def test_reliable_kunlun_scene_identifies_main_page() -> None:
    result = navigation._page_from_observation(
        navigation.KUNLUN_MAIN_SCENE_ID,
        80.0,
        "昆仑秘藏 任务 商店 自选",
    )

    assert result is not None
    assert result.page == "昆仑秘藏"


@pytest.mark.parametrize(
    "operation",
    [
        navigation.enter_kunlun,
        navigation.open_kunlun_tab,
        navigation.open_kunlun_optional_reward,
        navigation.leave_kunlun,
    ],
)
def test_navigation_default_wait_covers_real_scene_recognition(operation) -> None:
    timeout = inspect.signature(operation).parameters["timeout_seconds"].default

    assert timeout == navigation.KUNLUN_PAGE_WAIT_TIMEOUT_SECONDS
    assert timeout == 30.0


def test_undecided_first_row_does_not_advance_next_time(monkeypatch) -> None:
    class Runner:
        def __init__(self):
            self.next_times = []

        def _persist_scheduler_task_next_time(self, task_id, next_time):
            self.next_times.append((task_id, next_time))

    runner = Runner()
    runtime = object()
    monkeypatch.setattr(jobs, "_runtime", lambda *_args: runtime)
    monkeypatch.setattr(jobs, "enter_kunlun", lambda _runtime: None)
    monkeypatch.setattr(
        jobs,
        "_run_kunlun_config_workflow",
        lambda _runtime: (_ for _ in ()).throw(
            selection.KunlunFirstRowUndecided("没有安全决策")
        ),
    )

    with pytest.raises(selection.KunlunFirstRowUndecided, match="没有安全决策"):
        jobs.execute_kunlun_config_job(runner, {}, {}, object())
    assert runner.next_times == []
