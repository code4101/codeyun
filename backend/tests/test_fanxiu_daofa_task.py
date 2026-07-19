from __future__ import annotations

from pathlib import Path

import pytest

from datetime import datetime

from backend.core.fanxiu.data_annotation.tasks.daofa import (
    DaofaTaskMixin,
    normalize_daofa_packet_record,
    select_daofa_target,
    should_force_finish_daofa,
)


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
        ("wait_scene", (377, 378), 600.0, "道法争锋：等待挑战确认或挑战结果"),
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


def _packet_record(name: str, parsed: dict[str, object]) -> dict[str, object]:
    return {
        "name": name,
        "pro_id": 89202 if name.endswith("PlayInfo") else 89206,
        "packet_id": "packet-1",
        "captured_at": "2026-07-18 22:25:45",
        "payload": {"parsed": parsed},
    }


def test_normalize_daofa_initial_and_challenge_packets() -> None:
    target = {"id": 7, "rank": 21, "name": "target", "server": 22043, "power": 12.5, "player": True}
    initial = normalize_daofa_packet_record(
        _packet_record(
            "SM_LingArenaPlayInfo",
            {"joinerVO": {"rank": 26, "remainTimes": 1}, "targets": {"items": [target]}},
        )
    )
    challenge = normalize_daofa_packet_record(
        _packet_record(
            "SM_LingArenaChallenge",
            {"oldRank": 26, "newRank": 21, "remainTimes": 0, "targets": {"items": [target]}},
        )
    )

    assert (initial["rank"], initial["remain_times"]) == (26, 1)
    assert initial["targets"][0]["server_id"] == 22043
    assert (challenge["old_rank"], challenge["rank"], challenge["remain_times"]) == (26, 21, 0)


def test_select_daofa_target_uses_group_order_and_rank(tmp_path: Path) -> None:
    from backend.core.fanxiu.catalog.server_relations import save_fanxiu_server_relations

    save_fanxiu_server_relations(
        {
            "groups": [
                {
                    "key": "friendly",
                    "children": [
                        {"key": "same_server", "servers": [{"server_id": 22077, "server_order": 53, "server_name": "same"}]},
                        {"key": "alliance", "servers": [{"server_id": 22055, "server_order": 55, "server_name": "alliance"}]},
                        {"key": "ally", "servers": [{"server_id": 22064, "server_order": 64, "server_name": "ally"}]},
                    ],
                }
            ]
        },
        tmp_path,
    )
    facts = {
        "rank": 55,
        "targets": [
            {"rank": 40, "name": "same", "server_id": 22077, "power": 1, "is_npc": False},
            {"rank": 41, "name": "alliance", "server_id": 22055, "power": 1, "is_npc": False},
            {"rank": 42, "name": "ally", "server_id": 22064, "power": 1, "is_npc": False},
            {"rank": 48, "name": "npc", "server_id": 22077, "power": 0, "is_npc": True},
            {"rank": 49, "name": "other", "server_id": 22049, "power": 1, "is_npc": False},
        ],
    }

    selected = select_daofa_target(facts, battle_score=10, data_dir=tmp_path)
    assert selected is not None
    assert selected["name"] == "npc"

    facts["targets"] = facts["targets"][:3]
    assert select_daofa_target(facts, battle_score=10, data_dir=tmp_path)["name"] == "ally"


def test_force_finish_still_prefers_beatable_target_ahead() -> None:
    facts = {
        "rank": 26,
        "targets": [
            {"rank": 21, "name": "ahead", "server_id": 1, "power": 5, "is_npc": False},
            {"rank": 27, "name": "behind", "server_id": 2, "power": 2, "is_npc": False},
        ],
    }
    selected = select_daofa_target(facts, battle_score=10, force_finish=True)
    assert selected is not None and selected["name"] == "ahead"


def test_force_finish_uses_low_power_target_behind_only_when_no_ahead_target() -> None:
    facts = {
        "rank": 26,
        "targets": [
            {"rank": 21, "name": "too-strong", "server_id": 1, "power": 20, "is_npc": False},
            {"rank": 27, "name": "behind", "server_id": 2, "power": 2, "is_npc": False},
            {"rank": 28, "name": "behind-stronger", "server_id": 3, "power": 4, "is_npc": False},
        ],
    }
    selected = select_daofa_target(facts, battle_score=10, force_finish=True)
    assert selected is not None and selected["name"] == "behind"


def test_force_finish_window_uses_sunday_settlement() -> None:
    assert should_force_finish_daofa(datetime(2026, 7, 18, 23, 35)) is True
    assert should_force_finish_daofa(datetime(2026, 7, 19, 21, 35)) is True
    assert should_force_finish_daofa(datetime(2026, 7, 19, 21, 20)) is False
