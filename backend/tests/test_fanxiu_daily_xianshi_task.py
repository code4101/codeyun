from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


class _Runtime:
    def __init__(self, *, scene_id: int | None, text: str) -> None:
        self.scene_id = scene_id
        self.text = text
        self.actions: list[tuple] = []

    def click_shape_center(self, scene_id: int, shape: str) -> None:
        self.actions.append(("click_shape", scene_id, shape))

    def wait_action_settle(self, seconds: float = 1.0):
        self.actions.append(("settle", seconds))
        if False:
            yield None

    def current_scene(self, candidates, *, update: bool = False):
        self.actions.append(("current_scene", tuple(candidates), update))
        return self.scene_id, 100.0 if self.scene_id is not None else 0.0, "detail-frame"

    def ocr_text(self, frame=None, *, update: bool = False) -> str:
        self.actions.append(("ocr_text", frame, update))
        return self.text


def _missing_claim(*_args, **_kwargs):
    if False:
        yield None
    raise RuntimeError("#250 未匹配「领取」")


def test_xianshi_task_cell_leaves_scene_lifecycle_to_business_handler() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_xianshi")
    assert definition is not None
    calls: list[tuple[dict, dict]] = []

    class _Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("task wrapper must not perform an extra scene navigation")

        def _execute_daily_xianshi_task(self, ctx, _stop_event, payload):
            calls.append((ctx, payload))
            if False:
                yield None
            return "success"

    ctx = {"asset_tree_path": "asset-tree.json"}
    payload = {"coin_box_retry_seconds": 600}
    result = _finish(definition.handler(_Runner(), ctx, payload, threading.Event()))

    assert result == "success"
    assert calls == [(ctx, payload)]


def test_xianshi_missing_claim_accepts_only_proven_paid_detail(tmp_path, monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _Runtime(scene_id=None, text="秘藏阁 仙币 灵石仙币宝匣 打开可获得仙币 兑换所需 100 仙币")
    returned: list[str] = []

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", _missing_claim)

    def return_to_list(*_args, **_kwargs):
        returned.append("coin-list")
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_return_daily_xianshi_box_detail_to_coin_list", return_to_list)
    monkeypatch.setattr(runner, "_log", lambda *_args, **_kwargs: None)

    result = _finish(
        runner._click_daily_xianshi_free_coin_box(
            {"asset_tree_path": tmp_path / "asset-tree.json"},
            threading.Event(),
            {},
            {},
            {},
            task_label="仙市_秘藏阁",
        )
    )

    assert result == "not_free"
    assert returned == ["coin-list"]
    assert all(action[0] != "current_scene" for action in runtime.actions)
    assert ("ocr_text", None, True) in runtime.actions


def test_xianshi_shifted_first_paid_item_is_completed_without_exchange(
    tmp_path,
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _Runtime(
        scene_id=316,
        text="阵图自选宝匣 兑换",
    )
    returned: list[str] = []

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", _missing_claim)

    def return_to_list(*_args, **_kwargs):
        returned.append("coin-list")
        if False:
            yield None
        return "success"

    monkeypatch.setattr(
        runner,
        "_return_daily_xianshi_box_detail_to_coin_list",
        return_to_list,
    )
    monkeypatch.setattr(runner, "_log", lambda *_args, **_kwargs: None)

    result = _finish(
        runner._click_daily_xianshi_free_coin_box(
            {"asset_tree_path": tmp_path / "asset-tree.json"},
            threading.Event(),
            {},
            {},
            {},
            task_label="仙市_秘藏阁",
        )
    )

    assert result == "not_free"
    assert returned == ["coin-list"]
    assert ("click_shape", 249, "灵石仙币宝匣") in runtime.actions
    assert all(action != ("click_shape", 316, "兑换") for action in runtime.actions)


def test_xianshi_other_detail_fails_closed_without_paid_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _Runtime(scene_id=316, text="阵图自选宝匣")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", _missing_claim)

    with pytest.raises(RuntimeError, match="未同时证明商品详情与非免费状态"):
        _finish(
            runner._click_daily_xianshi_free_coin_box(
                {"asset_tree_path": tmp_path / "asset-tree.json"},
                threading.Event(),
                {},
                {},
                {},
                task_label="仙市_秘藏阁",
            )
        )

    assert all(action != ("click_shape", 316, "兑换") for action in runtime.actions)


def test_xianshi_paid_detail_return_uses_global_back_not_exchange(tmp_path, monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _Runtime(scene_id=316, text="阵图自选宝匣 兑换")
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    result = _finish(
        runner._return_daily_xianshi_box_detail_to_coin_list(
            {"asset_tree_path": tmp_path / "asset-tree.json"},
            threading.Event(),
            {},
            {},
            task_label="仙市_秘藏阁",
        )
    )

    assert result == "success"
    assert ("click_shape", 424, "返回") in runtime.actions
    assert all(action != ("click_shape", 316, "兑换") for action in runtime.actions)


@pytest.mark.parametrize(
    ("scene_id", "text"),
    [
        (250, "灵石仙币宝匣"),
        (250, "免费 灵石仙币宝匣 打开可获得仙币 兑换所需 0 仙币"),
    ],
)
def test_xianshi_missing_claim_fails_closed_without_paid_detail_proof(
    tmp_path,
    monkeypatch,
    scene_id,
    text,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    runtime = _Runtime(scene_id=scene_id, text=text)
    returned: list[str] = []

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", _missing_claim)
    monkeypatch.setattr(
        runner,
        "_return_daily_xianshi_box_detail_to_coin_list",
        lambda *_args, **_kwargs: returned.append("coin-list"),
    )

    with pytest.raises(RuntimeError, match="未同时证明商品详情与非免费状态"):
        _finish(
            runner._click_daily_xianshi_free_coin_box(
                {"asset_tree_path": tmp_path / "asset-tree.json"},
                threading.Event(),
                {},
                {},
                {},
                task_label="仙市_秘藏阁",
            )
        )

    assert returned == []
