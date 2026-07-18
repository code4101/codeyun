from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.tasks.daofa import DaofaTaskMixin


class _StopEvent:
    def is_set(self) -> bool:
        return False


class _View:
    def __init__(self, scene_id: int) -> None:
        self.id = scene_id


class _Runtime:
    def __init__(self, scene_id: int, landings: list[int]) -> None:
        self.scene_id = scene_id
        self.landings = list(landings)
        self.actions: list[tuple[object, ...]] = []

    def current_scene(self, _scene_ids, *, update: bool = False):
        assert update is True
        return self.scene_id, 100.0, "frame"

    def click_frame_point(self, scene_id: int, x: float, y: float) -> None:
        self.actions.append(("click_point", scene_id, x, y))

    def wait_scene(self, *scene_ids: int, timeout: float, label: str):
        self.actions.append(("wait_scene", scene_ids, timeout, label))
        landed = self.landings.pop(0)
        assert landed in scene_ids
        self.scene_id = landed
        if False:
            yield None
        return _View(landed)

    def click_shape_center(self, scene_id: int, shape: str) -> None:
        self.actions.append(("click_shape", scene_id, shape))

    def ocr_text(self, *, update: bool = False) -> str:
        assert update is True
        return "挑战成功，排名上升"


class _Runner(DaofaTaskMixin):
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _frame_size(self, image):
        return float(image["width"]), float(image["height"])


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _ctx() -> dict[str, object]:
    return {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {376: {"width": 900, "height": 1600}},
    }


def test_daofa_round_handles_optional_confirmation() -> None:
    runtime = _Runtime(376, [377, 378, 376])
    result = _run(
        _Runner(runtime)._run_daofa_challenge_round(
            _ctx(),
            _StopEvent(),
            challenge_point=(231.0, 1024.0),
        )
    )

    assert result == {
        "status": "success",
        "prompt_seen": True,
        "result_text": "挑战成功，排名上升",
        "final_scene": 376,
    }
    assert runtime.actions[:3] == [
        ("click_point", 376, 231.0, 1024.0),
        ("wait_scene", (377, 378), 15.0, "道法争锋：等待挑战确认或挑战结果"),
        ("click_shape", 377, "确认"),
    ]
    assert runtime.actions[-2:] == [
        ("click_shape", 378, "继续"),
        ("wait_scene", (376,), 45.0, "道法争锋：结果页继续并返回挑战页"),
    ]


def test_daofa_round_accepts_direct_result_when_login_prompt_is_suppressed() -> None:
    runtime = _Runtime(376, [378, 376])
    result = _run(
        _Runner(runtime)._run_daofa_challenge_round(
            _ctx(),
            _StopEvent(),
            challenge_point=(231.0, 1024.0),
        )
    )

    assert result["prompt_seen"] is False
    assert ("click_shape", 377, "确认") not in runtime.actions
    assert runtime.actions[1][0:2] == ("wait_scene", (377, 378))


@pytest.mark.parametrize("scene_id", [377, 378])
def test_daofa_round_can_resume_inside_closure(scene_id: int) -> None:
    landings = [378, 376] if scene_id == 377 else [376]
    runtime = _Runtime(scene_id, landings)
    result = _run(_Runner(runtime)._run_daofa_challenge_round(_ctx(), _StopEvent()))

    assert result["final_scene"] == 376
    assert not any(action[0] == "click_point" for action in runtime.actions)


def test_daofa_round_rejects_missing_or_out_of_bounds_target() -> None:
    with pytest.raises(RuntimeError, match="必须提供目标挑战按钮落点"):
        _run(_Runner(_Runtime(376, []))._run_daofa_challenge_round(_ctx(), _StopEvent()))

    with pytest.raises(ValueError, match="挑战落点越界"):
        _run(
            _Runner(_Runtime(376, []))._run_daofa_challenge_round(
                _ctx(),
                _StopEvent(),
                challenge_point=(901.0, 1024.0),
            )
        )
