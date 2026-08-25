from __future__ import annotations

import pytest
import numpy as np

from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxBlocked,
    StorageBagChoiceBoxGuiAdapter,
    StorageBagChoiceBoxRequest,
    StorageBagChoiceReward,
    StorageBagPartnerOutcomeProof,
    choice_rewards_from_catalog,
    scan_selected_choice_detail,
    green_pixel_ratio,
    choose_reward_from_note,
    luminance_availability,
    unique_green_selection,
    verify_authoritative_partner_outcome,
)
from backend.core.fanxiu.runtime_gui import StorageBagItemClickPlan


def _snapshot(items, fingerprint):
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": 12, "process_start_ticks": 34},
        "items": [
            {
                "ui_index": index,
                "instance_id": instance_id,
                "base_id": base_id,
                "num": quantity,
                "is_padding": False,
            }
            for index, (instance_id, base_id, quantity) in enumerate(items)
        ],
    }


def _partner_snapshot(ids, fingerprint, conversion_events=(), unowned_ids=()):
    return {
        "complete": True,
        "source": "runtime_memory+version_pinned_localization",
        "fingerprint": fingerprint,
        "evidence": {"pid": 12, "process_start_ticks": 34},
        "partners": [
            *({"id": partner_id, "owned": True} for partner_id in ids),
            *({"id": partner_id, "owned": False} for partner_id in unowned_ids),
        ],
        "conversion_events": list(conversion_events),
    }


CATALOG = {
    "100": {
        "name": "三宝自选匣",
        "optional_gift_rewards": [
            {"id": 201, "name": "仙币", "count": 2},
            {"id": 202, "name": "灵石", "count": 5},
            {"id": 203, "name": "洗灵石", "count": 1},
        ],
    }
}


class _Runtime:
    def __init__(self) -> None:
        self.count = 1
        self.selected = 0
        self.box_title = "三宝自选匣"
        self.events = []

    def click_frame_point(self, scene, x, y):
        self.events.append(("point", scene, x, y))

    def wait_view(self, *scenes, **_options):
        self.events.append(("view", scenes))
        if False:
            yield None
        return scenes[0]

    def cur_frame(self, update=False):
        return "frame"

    def ocr_tokens_in_shapes(self, scene, shapes, **_options):
        if scene == 587:
            names = {1: "仙币", 2: "灵石", 3: "洗灵石"}
            return [{"text": names[self.detail_target], "x": 1, "y": 1}]
        if shapes == ("详情标题",):
            return [{"text": self.box_title, "x": 1, "y": 1}]
        return [{"text": str(self.count), "x": 1, "y": 1}]

    def wait_click(self, scene, shape, **_options):
        self.events.append(("click", scene, shape))
        if shape.endswith("/打开详情"):
            self.detail_target = int(shape[2])
        elif shape.endswith("/右上选择框"):
            self.selected = int(shape[2])
        elif shape == "增加数量":
            self.count += 1
        if False:
            yield None

    def wait_action_settle(self, seconds):
        self.events.append(("settle", seconds))
        if False:
            yield None

    def shape_matches(self, _scene, shape):
        return {"matched": shape.endswith("/可选")}


def _consume(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


def _ready(*_args):
    return StorageBagItemClickPlan(
        "ready",
        "unique",
        runtime_index=0,
        runtime_item={"instance_id": "box", "base_id": 100, "num": 3},
        point=(50.0, 60.0),
        viewport_runtime_start=0,
    )


def _target_detail(_runtime, reward):
    if False:
        yield None
    return reward.name


def _adapter(runtime, snapshots, **overrides):
    options = {
        "runtime": runtime,
        "snapshot_reader": lambda: next(snapshots),
        "catalog_cards_by_id": CATALOG,
        "click_planner": _ready,
        "asset_validator": lambda _runtime, _slots: None,
        "visible_slot_reader": lambda _runtime: (1, 2, 3),
        "target_detail_scanner": _target_detail,
        "availability_reader": lambda _runtime, slots: {
            slot: True for slot in slots
        },
        "selection_verifier": lambda rt, slot, _count: rt.selected == slot,
        "count_reader": lambda rt: rt.count,
    }
    options.update(overrides)
    return StorageBagChoiceBoxGuiAdapter(**options)


def test_named_choice_clicks_top_right_checkbox_and_increments_strictly_to_runtime_total() -> None:
    before = _snapshot([("box", 100, 3)], "before")
    after = _snapshot([("reward", 202, 15)], "after")
    runtime = _Runtime()
    adapter = _adapter(runtime, iter((before, after)))

    result = _consume(
        adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 3, "选择灵石"))
    )

    assert result.selected_reward.base_id == 202
    assert result.delta.reward_quantity == 15
    assert ("click", 586, "候选2/右上选择框") in runtime.events
    assert [event for event in runtime.events if event == ("click", 586, "增加数量")] == [
        ("click", 586, "增加数量"),
        ("click", 586, "增加数量"),
    ]
    assert runtime.events.count(("view", (586,))) == 3
    assert not any("record" in str(event) for event in runtime.events)


def test_first_available_uses_first_candidate_with_explicit_availability() -> None:
    before = _snapshot([("box", 100, 1)], "before")
    after = _snapshot([("reward", 202, 5)], "after")
    runtime = _Runtime()
    adapter = _adapter(
        runtime,
        iter((before, after)),
        availability_reader=lambda _runtime, _slots: {1: False, 2: True, 3: True},
    )

    result = _consume(
        adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 1, "选第1个可以选的"))
    )

    assert result.selected_reward.slot == 2


def test_first_available_partner_skips_available_non_partner_and_uses_catalog_metadata() -> None:
    box = {
        "optional_gift_rewards": [
            {"id": 301, "name": "专属法宝自选匣", "count": 1},
            {"id": 302, "name": "陈巧倩", "count": 1},
            {"id": 303, "name": "董萱儿", "count": 1},
            {"id": 304, "name": "银月", "count": 1},
            {"id": 305, "name": "小极宫主", "count": 1},
            {"id": 306, "name": "凌玉灵", "count": 1},
        ]
    }
    cards = {
        "305": {"linked_partner_id": 4005},
        "306": {"business_type": "partner"},
    }
    rewards = choice_rewards_from_catalog(box, cards)
    selected = choose_reward_from_note(
        "选第1个可以选的仙侣",
        rewards,
        {1: True, 2: False, 3: False, 4: False, 5: True, 6: True},
        (1, 2, 3, 4, 5, 6),
    )

    assert selected.slot == 5
    assert selected.name == "小极宫主"
    assert not rewards[0].is_partner


def test_first_available_partner_fails_without_authoritative_partner_metadata() -> None:
    rewards = choice_rewards_from_catalog(CATALOG["100"])
    with pytest.raises(StorageBagChoiceBoxBlocked, match="未权威证明"):
        choose_reward_from_note(
            "选第1个可以选的仙侣",
            rewards,
            {1: True, 2: True, 3: True},
            (1, 2, 3),
        )


def test_inherited_initial_count_adds_only_remaining_difference() -> None:
    before = _snapshot([("box", 100, 3)], "before")
    after = _snapshot([("reward", 202, 15)], "after")
    runtime = _Runtime()
    runtime.count = 2
    adapter = _adapter(runtime, iter((before, after)))

    result = _consume(
        adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 3, "选择灵石"))
    )

    assert result.delta.opened_count == 3
    assert [event for event in runtime.events if event == ("click", 586, "增加数量")] == [
        ("click", 586, "增加数量")
    ]


def test_partial_open_consumes_only_requested_minimum_quantity() -> None:
    before = _snapshot([("box", 100, 3)], "before")
    after = _snapshot([("box", 100, 1), ("reward", 202, 10)], "after")
    runtime = _Runtime()
    adapter = _adapter(runtime, iter((before, after)))

    result = _consume(
        adapter.execute(
            StorageBagChoiceBoxRequest(
                100,
                "box",
                "三宝自选匣",
                3,
                "选择灵石",
                open_quantity=2,
            )
        )
    )

    assert result.delta.opened_count == 2
    assert result.delta.reward_quantity == 10
    assert runtime.events.count(("click", 586, "增加数量")) == 1


def test_uncontrolled_note_fails_before_any_click() -> None:
    runtime = _Runtime()
    adapter = _adapter(runtime, iter(()))

    with pytest.raises(StorageBagChoiceBoxBlocked, match="只支持"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 3, "随便选")))

    assert runtime.events == []

    with pytest.raises(StorageBagChoiceBoxBlocked, match="附加文本"):
        _consume(
            adapter.execute(
                StorageBagChoiceBoxRequest(
                    100, "box", "三宝自选匣", 3, "随便写第1个可以选的再说"
                )
            )
        )


def test_missing_green_selection_evidence_fails_before_quantity_or_confirm() -> None:
    before = _snapshot([("box", 100, 3)], "before")
    runtime = _Runtime()
    adapter = _adapter(
        runtime,
        iter((before,)),
        selection_verifier=lambda *_args: False,
    )

    with pytest.raises(StorageBagChoiceBoxBlocked, match="绿色勾选"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 3, "选择灵石")))

    assert ("click", 586, "确定") not in runtime.events
    assert ("click", 586, "增加数量") not in runtime.events


def test_final_count_must_be_proven_even_when_intermediate_four_is_not_ocr_read() -> None:
    before = _snapshot([("box", 100, 3)], "before")
    runtime = _Runtime()
    reads = iter((1, 2))
    adapter = _adapter(runtime, iter((before,)), count_reader=lambda _rt: next(reads))

    with pytest.raises(StorageBagChoiceBoxBlocked, match="最终 OCR"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 3, "选择灵石")))

    assert ("click", 586, "确定") not in runtime.events


def test_wrong_selected_reward_delta_fails_after_confirm_without_yield_record() -> None:
    before = _snapshot([("box", 100, 1)], "before")
    after = _snapshot([("reward", 202, 4)], "after")
    runtime = _Runtime()
    adapter = _adapter(runtime, iter((before, after)))

    with pytest.raises(StorageBagChoiceBoxBlocked, match="Catalog 期望 5"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 1, "选择灵石")))


def test_current_asset_contract_missing_candidate_subshapes_fails_closed() -> None:
    class MissingAssetRuntime:
        def shape(self, _scene, title):
            if "/" in title:
                raise RuntimeError(f"missing {title}")
            return type("Shape", (), {"raw": {"x": 0, "y": 0, "w": 1, "h": 1}})()

    adapter = StorageBagChoiceBoxGuiAdapter(
        runtime=MissingAssetRuntime(),
        snapshot_reader=lambda: pytest.fail("must fail before Runtime read"),
        catalog_cards_by_id=CATALOG,
        visible_slot_reader=lambda _runtime: (1, 2, 3),
    )

    with pytest.raises(RuntimeError, match="候选1/打开详情"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "三宝自选匣", 1, "选择灵石")))


def test_green_pixel_ratio_and_unique_selection_use_generic_roi_evidence() -> None:
    roi = np.zeros((100, 100, 3), dtype=np.uint8)
    roi[:21, :, 1] = 255

    ratio = green_pixel_ratio(roi)

    assert ratio == pytest.approx(0.21, abs=0.001)
    assert unique_green_selection({1: 0.2114, 2: 0.0, 3: 0.0}, 1)
    assert not unique_green_selection({1: 0.07, 2: 0.0, 3: 0.0}, 1)
    assert not unique_green_selection({1: 0.2114, 2: 0.04, 3: 0.0}, 1)
    assert not unique_green_selection({1: 0.10, 2: 0.06, 3: 0.0}, 1)


def test_luminance_availability_matches_real_six_slot_boundaries() -> None:
    observations = {
        1: (136.0, 0.164),
        2: (66.9, 0.689),
        3: (67.2, 0.665),
        4: (80.1, 0.481),
        5: (188.1, 0.031),
        6: (177.4, 0.069),
    }

    assert {
        slot: luminance_availability(mean, dark)
        for slot, (mean, dark) in observations.items()
    } == {1: True, 2: False, 3: False, 4: False, 5: True, 6: True}
    assert luminance_availability(120.0, 0.25)
    assert not luminance_availability(119.99, 0.25)
    assert not luminance_availability(120.0, 0.251)


def test_target_only_flow_selects_slot5_without_scanning_catalog_tail() -> None:
    box = {
        "name": "仙侣自选匣",
        "optional_gift_rewards": [
            {"id": 300 + slot, "name": name, "count": 1}
            for slot, name in enumerate(
                ("专属法宝自选匣", "陈巧倩", "董萱儿", "银月", "小极宫主", "凌玉灵", "候选七", "候选八", "候选九"),
                start=1,
            )
        ],
    }
    catalog = {"100": box, "305": {"linked_partner_id": 4005}, "306": {"business_type": "partner"}}
    before = _snapshot([("box", 100, 1)], "before")
    after = _snapshot([], "after")
    seen = []

    def target_only(_runtime, reward):
        seen.append(reward.slot)
        if False:
            yield None
        return reward.name

    runtime = _Runtime()
    runtime.box_title = "仙侣自选匣"
    partner_snapshots = iter(
        (
            _partner_snapshot([], "partners-before", unowned_ids=[4005]),
            _partner_snapshot([4005], "partners-after"),
        )
    )
    adapter = _adapter(
        runtime,
        iter((before, after)),
        catalog_cards_by_id=catalog,
        visible_slot_reader=lambda _runtime: (1, 2, 3, 4, 5, 6),
        availability_reader=lambda _runtime, _slots: {1: True, 2: False, 3: False, 4: False, 5: True, 6: True},
        target_detail_scanner=target_only,
        partner_snapshot_reader=lambda: next(partner_snapshots),
        partner_outcome_verifier=verify_authoritative_partner_outcome,
    )

    result = _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "仙侣自选匣", 1, "选第1个可以选的仙侣")))

    assert result.selected_reward.slot == 5
    assert seen == [5]
    assert result.partner_outcome is not None
    assert result.partner_outcome.outcome == "activated"


def test_named_target_beyond_visible_slots_fails_before_detail_navigation() -> None:
    rewards = tuple(StorageBagChoiceReward(slot, 400 + slot, f"候选{slot}", 1) for slot in range(1, 10))

    with pytest.raises(StorageBagChoiceBoxBlocked, match="超出当前正式标注"):
        choose_reward_from_note("选择候选7", rewards, {slot: True for slot in range(1, 7)}, (1, 2, 3, 4, 5, 6))


def test_default_target_detail_scanner_opens_only_selected_and_returns_via_detail_scene() -> None:
    runtime = _Runtime()
    selected = StorageBagChoiceReward(2, 202, "灵石", 5)

    observed = _consume(scan_selected_choice_detail(runtime, selected))

    assert observed == "灵石"
    assert ("click", 586, "候选2/打开详情") in runtime.events
    assert ("view", (587,)) in runtime.events
    assert ("click", 587, "右侧暗幕返回") in runtime.events
    assert runtime.events[-1] == ("view", (586,))
    assert not any(event[0] == "click" and str(event[2]).startswith("候选1/") for event in runtime.events)


def test_partner_reward_without_explicit_outcome_verifier_fails_before_confirm() -> None:
    catalog = {
        "100": {
            "name": "仙侣自选匣",
            "optional_gift_rewards": [{"id": 305, "name": "小极宫主", "count": 1}],
        },
        "305": {"linked_partner_id": 4005},
    }
    before = _snapshot([("box", 100, 1)], "before")
    runtime = _Runtime()
    runtime.box_title = "仙侣自选匣"
    adapter = _adapter(
        runtime,
        iter((before,)),
        catalog_cards_by_id=catalog,
        visible_slot_reader=lambda _runtime: (1,),
        availability_reader=lambda _runtime, _slots: {1: True},
        partner_snapshot_reader=None,
        partner_outcome_verifier=None,
    )

    with pytest.raises(StorageBagChoiceBoxBlocked, match="outcome verifier"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "仙侣自选匣", 1, "选择小极宫主")))

    assert ("click", 586, "确定") not in runtime.events


def test_owned_partner_missing_unique_fragment_catalog_fails_before_confirm() -> None:
    catalog = {
        "100": {
            "name": "仙侣自选匣",
            "optional_gift_rewards": [{"id": 305, "name": "小极宫主", "count": 1}],
        },
        "305": {"linked_partner_id": 16},
    }
    before = _snapshot([("box", 100, 1)], "before")
    runtime = _Runtime()
    runtime.box_title = "仙侣自选匣"
    adapter = _adapter(
        runtime,
        iter((before,)),
        catalog_cards_by_id=catalog,
        visible_slot_reader=lambda _runtime: (1,),
        availability_reader=lambda _runtime, _slots: {1: True},
        partner_snapshot_reader=lambda: _partner_snapshot([16], "partners-before"),
        partner_outcome_verifier=verify_authoritative_partner_outcome,
    )

    with pytest.raises(StorageBagChoiceBoxBlocked, match="未唯一解析"):
        _consume(adapter.execute(StorageBagChoiceBoxRequest(100, "box", "仙侣自选匣", 1, "选择小极宫主")))

    assert ("click", 586, "确定") not in runtime.events


def test_authoritative_partner_outcome_accepts_one_explicit_duplicate_conversion() -> None:
    reward = StorageBagChoiceReward(1, 305, "小极宫主", 1, True, "Catalog", 4005)
    before = _partner_snapshot([4005], "same-partners")
    after = _partner_snapshot([4005], "same-partners")
    before_storage = _snapshot([("box", 100, 2)], "storage-before")
    after_storage = _snapshot(
        [("fragment", 19604005, 2)], "storage-after"
    )
    catalog = {"19604005": {"effect_value": "PartnerFragment|4005"}}

    proof = verify_authoritative_partner_outcome(
        before,
        after,
        before_storage,
        after_storage,
        StorageBagChoiceBoxRequest(100, "box", "仙侣自选匣", 2, "选择小极宫主"),
        reward,
        catalog,
    )

    assert proof.outcome == "duplicate_converted"
    assert proof.fragment_base_id == 19604005
    assert proof.fragment_quantity == 2


def test_partner_outcome_rejects_same_list_without_explainable_conversion() -> None:
    reward = StorageBagChoiceReward(1, 305, "小极宫主", 1, True, "Catalog", 4005)
    before = _partner_snapshot([4005], "before")
    after = _partner_snapshot([4005], "after")

    with pytest.raises(StorageBagChoiceBoxBlocked, match="未唯一解析"):
        verify_authoritative_partner_outcome(
            before,
            after,
            _snapshot([("box", 100, 1)], "storage-before"),
            _snapshot([], "storage-after"),
            StorageBagChoiceBoxRequest(100, "box", "仙侣自选匣", 1, "选择小极宫主"),
            reward,
            {},
        )


def test_partner_outcome_rejects_different_game_process_even_if_target_appears() -> None:
    reward = StorageBagChoiceReward(1, 305, "小极宫主", 1, True, "Catalog", 4005)
    before = _partner_snapshot([], "before")
    after = _partner_snapshot([4005], "after")
    after["evidence"]["process_start_ticks"] = 99

    with pytest.raises(StorageBagChoiceBoxBlocked, match="同一游戏进程"):
        verify_authoritative_partner_outcome(
            before,
            after,
            _snapshot([("box", 100, 1)], "storage-before"),
            _snapshot([], "storage-after"),
            StorageBagChoiceBoxRequest(100, "box", "仙侣自选匣", 1, "选择小极宫主"),
            reward,
            {},
        )


def test_duplicate_partner_rejects_unrelated_storage_change_instead_of_fragment() -> None:
    reward = StorageBagChoiceReward(1, 9016, "小极宫主", 1, True, "Catalog", 16)
    before_partner = _partner_snapshot([16], "same")
    after_partner = _partner_snapshot([16], "same")
    before_storage = _snapshot([("box", 19701102, 2)], "storage-before")
    after_storage = _snapshot([("wrong", 999, 2)], "storage-after")
    catalog = {"19600016": {"effect_value": "PartnerFragment|16"}}

    with pytest.raises(StorageBagChoiceBoxBlocked, match="19600016 增量 0"):
        verify_authoritative_partner_outcome(
            before_partner,
            after_partner,
            before_storage,
            after_storage,
            StorageBagChoiceBoxRequest(19701102, "box", "仙侣自选匣", 2, "选择小极宫主"),
            reward,
            catalog,
        )


def test_real_shape_duplicate_partner_box_accepts_only_catalog_fragment_delta() -> None:
    catalog = {
        "19701102": {
            "name": "仙侣自选匣",
            "optional_gift_rewards": [{"id": 9016, "name": "小极宫主", "count": 1}],
        },
        "9016": {"linked_partner_id": 16},
        "19600016": {"effect_value": "PartnerFragment|16"},
    }
    before = _snapshot([("box", 19701102, 2)], "storage-before")
    after = _snapshot([("fragment", 19600016, 2)], "storage-after")
    partner_snapshots = iter(
        (_partner_snapshot([16], "partners-same"), _partner_snapshot([16], "partners-same"))
    )
    runtime = _Runtime()
    runtime.box_title = "仙侣自选匣"
    runtime.count = 2
    adapter = _adapter(
        runtime,
        iter((before, after)),
        catalog_cards_by_id=catalog,
        visible_slot_reader=lambda _runtime: (1,),
        availability_reader=lambda _runtime, _slots: {1: True},
        partner_snapshot_reader=lambda: next(partner_snapshots),
    )

    result = _consume(
        adapter.execute(
            StorageBagChoiceBoxRequest(
                19701102, "box", "仙侣自选匣", 2, "选择小极宫主"
            )
        )
    )

    assert result.partner_outcome is not None
    assert result.partner_outcome.outcome == "duplicate_converted"
    assert result.partner_outcome.fragment_base_id == 19600016
    assert result.partner_outcome.fragment_quantity == 2
    assert ("click", 586, "确定") in runtime.events
