from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from backend.core.fanxiu.data_annotation.equipment import (
    EquipmentStrengtheningObservation,
    EquipmentStrengtheningTarget,
    _predict_equipment_point_from_level_sequence,
    complete_equipment_strengthening_tasks,
    ensure_equipment_strengthening,
    resolve_equipment_strengthening_target,
    select_equipment_strengthening,
    verify_selected_equipment_strengthening,
)


def _drain(generator: Generator[Any, None, Any]) -> Any:
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Runtime:
    def __init__(
        self,
        scene_id: int,
        *,
        world_tokens: list[dict[str, Any]] | None = None,
        strengthening_tokens: list[dict[str, Any]] | None = None,
    ) -> None:
        self.scene_id = scene_id
        self.world_tokens = list(world_tokens or [])
        self.strengthening_tokens = list(strengthening_tokens or [])
        self.events: list[tuple[Any, ...]] = []

    def current_scene(self, views, *, update=False):
        self.events.append(("current_scene", tuple(views), update))
        return self.scene_id, 100.0, "frame"

    def goto_view(self, scene_id: int):
        self.events.append(("goto_view", scene_id))
        self.scene_id = scene_id
        if False:
            yield None

    def wait_view(self, scene_id: int, **options):
        self.events.append(("wait_view", scene_id, options))
        assert self.scene_id == scene_id
        if False:
            yield None
        return scene_id

    def cur_frame(self, update=False):
        self.events.append(("cur_frame", self.scene_id, update))
        return f"frame-{self.scene_id}"

    def ocr_tokens_in_shapes(self, view_id, shape_titles, **options):
        self.events.append(("ocr", view_id, tuple(shape_titles), options))
        if view_id == 34:
            return list(self.world_tokens)
        if view_id == 445:
            return list(self.strengthening_tokens)
        return []

    def click_frame_point(self, view_id: int, x: float, y: float):
        self.events.append(("click", view_id, x, y))
        if view_id == 34:
            self.scene_id = 445
        elif view_id == 445:
            self.scene_id = 446

    def wait_action_settle(self, seconds: float):
        self.events.append(("settle", seconds))
        if False:
            yield None


def _world_equipment_token() -> dict[str, Any]:
    return {"text": "装备", "x": 462, "y": 1533, "w": 64, "h": 35}


def _strengthening_tokens() -> list[dict[str, Any]]:
    return [
        {"text": "强", "x": 303, "y": 1401, "w": 41, "h": 35},
        {"text": "化", "x": 304, "y": 1445, "w": 39, "h": 35},
    ]


def test_ensure_equipment_strengthening_is_idempotent_on_446() -> None:
    runtime = _Runtime(446)

    result = _drain(ensure_equipment_strengthening(runtime))

    assert result == {"ok": True, "changed": False, "view_id": 446}
    assert runtime.events == [("current_scene", (446, 445, 34), True)]


def test_ensure_equipment_strengthening_navigates_34_to_445_to_446() -> None:
    runtime = _Runtime(
        34,
        world_tokens=[_world_equipment_token()],
        strengthening_tokens=_strengthening_tokens(),
    )

    result = _drain(ensure_equipment_strengthening(runtime))

    assert result["view_id"] == 446
    assert [event for event in runtime.events if event[0] == "goto_view"] == []
    assert ("click", 34, 494.0, 1498.0) in runtime.events
    strengthening_click = next(
        event for event in runtime.events if event[:2] == ("click", 445)
    )
    assert strengthening_click[2:] == pytest.approx((323.5, 1440.5))
    assert [event[1] for event in runtime.events if event[0] == "wait_view"] == [
        34,
        445,
        446,
    ]
    strengthening_ocr = next(
        event for event in runtime.events if event[:2] == ("ocr", 445)
    )
    assert strengthening_ocr[3]["crop"] is True
    assert strengthening_ocr[3]["options"] == {
        "text_det_thresh": 0.2,
        "text_det_box_thresh": 0.35,
        "text_det_unclip_ratio": 1.1,
    }


def test_ensure_equipment_strengthening_navigates_445_to_446_only() -> None:
    runtime = _Runtime(445, strengthening_tokens=_strengthening_tokens())

    result = _drain(ensure_equipment_strengthening(runtime))

    assert result["view_id"] == 446
    assert [event for event in runtime.events if event[0] == "goto_view"] == []
    assert [event[1] for event in runtime.events if event[0] == "ocr"] == [445]
    assert [event[1] for event in runtime.events if event[0] == "wait_view"] == [446]


def test_ensure_equipment_strengthening_preserves_scene_when_world_ocr_fails() -> None:
    runtime = _Runtime(999, world_tokens=[])

    with pytest.raises(RuntimeError, match=r"#34\[下方菜单\].*装备"):
        _drain(
            ensure_equipment_strengthening(
                runtime,
                world_ocr_attempts=2,
                retry_seconds=0,
            )
        )

    assert ("goto_view", 34) in runtime.events
    assert runtime.scene_id == 34
    assert not any(event[0] == "click" for event in runtime.events)
    assert len([event for event in runtime.events if event[:2] == ("ocr", 34)]) == 2
    assert not any(event[:2] == ("wait_view", 446) for event in runtime.events)


def test_complete_strengthening_tasks_is_idempotent_at_final_tier(monkeypatch) -> None:
    snapshot = {
        "complete": True,
        "equipment_current": 12_000,
        "equipment_tasks": [
            {
                "task_id": 1,
                "order": 14,
                "name": "装备强化十四",
                "progress": 12_000,
                "target": 12_000,
            }
        ],
        "rows": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.lingzhuang_strengthening.read_lingzhuang_strengthening_runtime_snapshot",
        lambda **_options: snapshot,
    )

    result = _drain(complete_equipment_strengthening_tasks(_Runtime(446), activity_id="activity"))

    assert result["equipment_progress"] == 12_000
    assert result["click_count"] == 0
    assert result["skipped"] == "already_complete"


def test_complete_strengthening_tasks_resolves_one_based_live_target_tier(
    monkeypatch,
) -> None:
    targets = (
        100,
        200,
        400,
        600,
        800,
        1000,
        1400,
        2000,
        3000,
        4000,
        6000,
        8000,
        10000,
        12000,
    )
    snapshot = {
        "complete": True,
        "equipment_current": 4000,
        "equipment_tasks": [
            {
                "task_id": order,
                "order": order,
                "name": f"装备强化{order}",
                "progress": 4000,
                "target": target,
            }
            for order, target in enumerate(targets, 1)
        ],
        "rows": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.lingzhuang_strengthening.read_lingzhuang_strengthening_runtime_snapshot",
        lambda **_options: snapshot,
    )

    result = _drain(
        complete_equipment_strengthening_tasks(
            _Runtime(446),
            activity_id="activity",
            target_tier=10,
        )
    )

    assert result["target_tier"] == 10
    assert result["target_progress"] == 4000
    assert result["equipment_progress"] == 4000
    assert result["click_count"] == 0


def _snapshot(*, equipped: bool = True) -> dict[str, Any]:
    return {
        "rows": [
            {
                "part": "羽巾",
                "initial": {
                    "equipment_level": 157,
                    "material_count": 6578,
                    "equipped": True,
                },
                "dongxuan": {
                    "equipment_level": 108 if equipped else 0,
                    "material_count": 6578,
                    "equipped": equipped,
                },
            }
        ]
    }


def _ordered_snapshot() -> dict[str, Any]:
    parts = (
        "灵环",
        "气铠",
        "宝冠",
        "羽巾",
        "华履",
        "锦带",
        "灵坠",
        "仙符",
        "灵镯",
        "宝戒",
    )
    dongxuan_levels = (None, 87, 228, 108, 333, 310, None, 327, None, None)
    return {
        "rows": [
            {
                "part": part,
                "initial": {
                    "equipment_level": None,
                    "material_count": 0,
                    "equipped": False,
                },
                "dongxuan": {
                    "equipment_level": level,
                    "material_count": 9970 if part == "仙符" else index * 100,
                    "equipped": level is not None,
                },
            }
            for index, (part, level) in enumerate(zip(parts, dongxuan_levels), 1)
        ]
    }


def _level_token(text: str, center_x: float) -> dict[str, Any]:
    return {"text": text, "x": center_x - 18, "y": 80, "w": 36, "h": 24}


def test_ordered_level_alignment_keeps_empty_slot_and_tolerates_target_ocr_error() -> None:
    snapshot = _ordered_snapshot()
    target = resolve_equipment_strengthening_target(snapshot, "洞玄", "仙符")
    tokens = [
        _level_token("333", 300),
        _level_token("310", 480),
        # The target's 327 is visually obscured and misread.  It is soft
        # evidence only; parts 5/6 plus the fixed pitch locate part 8.
        _level_token("32", 840),
    ]

    result = _predict_equipment_point_from_level_sequence(
        tokens,
        snapshot,
        target,
        slot_pitch=180,
        shape_left=0,
        shape_right=1000,
        click_y=120,
    )

    assert result is not None
    assert result["x"] == pytest.approx(840)
    assert [match["part_index"] for match in result["exact_matches"]] == [5, 6]
    assert any(match["observed"] == "32" for match in result["soft_matches"])


def test_ordered_level_alignment_rejects_reversed_ambiguous_row() -> None:
    snapshot = _ordered_snapshot()
    target = resolve_equipment_strengthening_target(snapshot, "洞玄", "仙符")

    result = _predict_equipment_point_from_level_sequence(
        [_level_token("310", 300), _level_token("333", 480)],
        snapshot,
        target,
        slot_pitch=180,
        shape_left=0,
        shape_right=1000,
        click_y=120,
    )

    assert result is None


def test_resolve_equipment_strengthening_target_rejects_unequipped_slot() -> None:
    with pytest.raises(RuntimeError, match="洞玄羽巾当前未装备"):
        resolve_equipment_strengthening_target(_snapshot(equipped=False), "洞玄", "羽巾")


def test_resolve_equipment_strengthening_target_accepts_amulet_alias() -> None:
    snapshot = _snapshot()
    snapshot["rows"][0]["part"] = "仙符"

    target = resolve_equipment_strengthening_target(snapshot, "洞玄", "护符")

    assert target.part == "仙符"


def test_initial_target_fingerprint_is_checked_against_dongxuan_too() -> None:
    snapshot = _snapshot()
    snapshot["rows"][0]["dongxuan"].update(
        {"equipment_level": 157, "material_count": 6578}
    )

    target = resolve_equipment_strengthening_target(snapshot, "初灵", "羽巾")

    assert target.fingerprint_unique is False


def test_verify_selected_equipment_requires_category_and_unique_numeric_fingerprint() -> None:
    target = EquipmentStrengtheningTarget("洞玄", "羽巾", 108, 6578, True)
    observation = EquipmentStrengtheningObservation(
        description_text="洞玄·流云羽巾\n强化等级：108",
        resource_text="6578/300",
        equipment_level=108,
        resource_current=6578,
        resource_required=300,
    )
    assert verify_selected_equipment_strengthening(observation, target) == (True, [])

    wrong_material = EquipmentStrengtheningObservation(
        **{**observation.__dict__, "resource_current": 999}
    )
    verified, failures = verify_selected_equipment_strengthening(wrong_material, target)
    assert verified is False
    assert any("玄铁不符" in failure for failure in failures)

    ambiguous = EquipmentStrengtheningTarget(
        "洞玄", "羽巾", 108, 6578, True, fingerprint_unique=False
    )
    no_part_name = EquipmentStrengtheningObservation(
        **{**observation.__dict__, "description_text": "洞玄·流云冠\n强化等级：108"}
    )
    verified, failures = verify_selected_equipment_strengthening(no_part_name, ambiguous)
    assert verified is False
    assert any("指纹在当前类别不唯一" in failure for failure in failures)


class _SelectionRuntime(_Runtime):
    def __init__(self) -> None:
        super().__init__(446)
        self.page = 0
        self.selected = {
            "description": "洞玄·别的装备\n强化等级：108",
            "resource": "999/300",
        }
        self.pages = [
            [
                {
                    "text": "108",
                    "x": 100,
                    "y": 100,
                    "w": 50,
                    "h": 30,
                    "description": "洞玄·别的装备\n强化等级：108",
                    "resource": "999/300",
                }
            ],
            [
                {
                    "text": "108",
                    "x": 300,
                    "y": 100,
                    "w": 50,
                    "h": 30,
                    "description": "洞玄·流云羽巾\n强化等级：108",
                    "resource": "6578/300",
                }
            ],
        ]

    def click_ocr_text(self, view_id, target, **options):
        self.events.append(("click_ocr_text", view_id, target, options))
        self.page = 0

    def view(self, view_id):
        return {"id": view_id}

    def resolve_shape_selector(self, view, selector):
        return (view, selector)

    def ocr_tokens_in_shapes(self, view_id, shape_titles, **options):
        if view_id == 446 and tuple(shape_titles) == ("装备",):
            return list(self.pages[self.page])
        return super().ocr_tokens_in_shapes(view_id, shape_titles, **options)

    def click_frame_point(self, view_id: int, x: float, y: float):
        if view_id == 446:
            card = min(self.pages[self.page], key=lambda item: abs(item["x"] + item["w"] / 2 - x))
            self.selected = {
                "description": card["description"],
                "resource": card["resource"],
            }
            self.events.append(("click_card", self.page, x, y))
            return
        super().click_frame_point(view_id, x, y)

    def ocr_text_in_shapes(self, view_id, shape_titles, **options):
        title = tuple(shape_titles)[0]
        return self.selected["description" if title == "描述" else "resource"]

    def click_shape_center(self, view_id, shape, *, x_ratio, y_ratio):
        self.events.append(("click_shape_center", self.page, x_ratio, y_ratio))
        card = self.pages[self.page][0]
        self.selected = {
            "description": card["description"],
            "resource": card["resource"],
        }

    def scroll_shape_content(self, shape, *, direction):
        self.events.append(("scroll", direction, self.page))
        next_page = self.page + (1 if direction == "right" else -1)
        if not 0 <= next_page < len(self.pages):
            if False:
                yield None
            return False
        self.page = next_page
        if False:
            yield None
        return True


class _EffectHiddenLevelRuntime(_SelectionRuntime):
    def ocr_tokens_in_shapes(self, view_id, shape_titles, **options):
        if view_id == 446 and tuple(shape_titles) == ("装备",):
            return []
        return super().ocr_tokens_in_shapes(view_id, shape_titles, **options)

    def click_shape_center(self, view_id, shape, *, x_ratio, y_ratio):
        self.events.append(("click_shape_center", self.page, x_ratio, y_ratio))
        if x_ratio == pytest.approx(0.72):
            self.selected = {
                "description": "洞玄·流云冠\n强化等级：108",
                "resource": "6578/300",
            }


def test_select_equipment_strengthening_selects_category_first_and_verifies_duplicate_level() -> None:
    runtime = _SelectionRuntime()

    result = _drain(
        select_equipment_strengthening(
            runtime,
            "洞玄",
            "羽巾",
            snapshot=_snapshot(),
            settle_seconds=0,
        )
    )

    assert result["ok"] is True
    assert result["target"]["part"] == "羽巾"
    assert result["observation"]["resource_current"] == 6578
    assert len(result["attempts"]) >= 2
    category_event = next(event for event in runtime.events if event[0] == "click_ocr_text")
    first_card_event = next(event for event in runtime.events if event[0] == "click_card")
    assert runtime.events.index(category_event) < runtime.events.index(first_card_event)
    assert any(event[:2] == ("scroll", "right") for event in runtime.events)


def test_select_equipment_strengthening_uses_verified_geometry_when_effect_hides_level() -> None:
    runtime = _EffectHiddenLevelRuntime()

    result = _drain(
        select_equipment_strengthening(
            runtime,
            "洞玄",
            "羽巾",
            snapshot=_snapshot(),
            settle_seconds=0,
        )
    )

    assert result["ok"] is True
    assert result["attempts"][-1]["method"] == "card_geometry"
    assert result["attempts"][-1]["x_ratio"] == pytest.approx(0.72)
