from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_cleanse import (
    FreshSpiritArtifactSnapshot,
    SpiritArtifactAttemptContext,
    SpiritArtifactCleanseBlocked,
    SpiritArtifactCleanseBudget,
    SpiritArtifactCleanseGuiAssets,
    SpiritArtifactCleanseInterface,
    SpiritArtifactCleanseRequest,
    SpiritArtifactCleanseRuntimeGuiAdapter,
    SpiritArtifactEffect,
    SpiritArtifactIrreversibleAuthorization,
    SpiritArtifactObservation,
    SpiritArtifactPageEvidence,
    SpiritArtifactTarget,
    observe_spirit_artifact,
    prepare_spirit_artifact_cleanse,
    require_irreversible_authorization,
    spirit_artifact_effect_fingerprint,
    verify_spirit_artifact_commit_delta,
    verify_spirit_artifact_lock_delta,
    validate_spirit_artifact_target_universe,
)


TARGET = SpiritArtifactTarget("24000000000000001", 1, 1, 14_000_106)


def _snapshot(
    *, locked: bool = False, value: int = 6700, pending: bool = False
) -> dict:
    return {
        "runtime_complete": True,
        "runtime_updated_at": time.time(),
        "runtime_debug": {"pid": 123, "process_start_ticks": 456},
        "artifacts": [
            {
                "name": "血晶摩诃剑",
                "rows": [
                    {
                        "part_name": "柄",
                        "runtime_item_id": TARGET.item_id,
                        "runtime_ware_id": 1,
                        "runtime_part": 1,
                        "runtime_base_id": TARGET.base_id,
                        "runtime_refine_num": 3,
                        "runtime_effects": [
                            {
                                "cleanse_id": 112002,
                                "value": value,
                                "quality": 6,
                                "locked": locked,
                            },
                            {
                                "cleanse_id": 999999,
                                "value": 123,
                                "quality": 3,
                                "locked": False,
                            },
                        ],
                        "runtime_pending_effects": (
                            [
                                {
                                    "cleanse_id": 112006,
                                    "value": 7200,
                                    "quality": 7,
                                    "locked": False,
                                }
                            ]
                            if pending
                            else []
                        ),
                    }
                ],
            }
        ],
    }


def _complete_snapshot(*, pending: bool = False, value: int = 6700) -> dict:
    snapshot = _snapshot(pending=pending, value=value)
    rows = snapshot["artifacts"][0]["rows"]
    for ware_id in range(1, 9):
        for part in range(1, 7):
            if (ware_id, part) == (1, 1):
                continue
            rows.append(
                {
                    "part_name": f"部位{part}",
                    "runtime_item_id": str(24_000_000_000_000_000 + ware_id * 10 + part),
                    "runtime_ware_id": ware_id,
                    "runtime_part": part,
                    "runtime_base_id": 14_000_100 + (ware_id - 1) * 600 + part * 100,
                    "runtime_refine_num": 0,
                    "runtime_effects": [
                        {
                            "cleanse_id": 500_000 + ware_id * 10 + part,
                            "value": 1,
                            "quality": 1,
                            "locked": False,
                        }
                    ],
                    "runtime_pending_effects": [],
                }
            )
    return snapshot


class _Gui:
    def __init__(self) -> None:
        self.calls = []
        self.scene = 34

    def select(self, prepared): self.calls.append("select")
    def set_lock(self, cleanse_id, locked): self.calls.append("lock")
    def open_attribute_preview(self, prepared): self.calls.append("preview")
    def start_auto_cleanse(self, prepared): self.calls.append("consume")
    def accept_pending(self, candidate): self.calls.append("replace")
    def cancel(self): self.calls.append("cancel")
    def return_to_world(self): self.calls.append("return")
    def current_scene_id(self): return self.scene


def _request(observation, *, allow_replace: bool = False):
    return SpiritArtifactCleanseRequest(
        target=TARGET,
        expected_fingerprint=observation.fingerprint,
        required_cleanse_ids=(1_000_001,),
        preserve_cleanse_ids=(112002,),
        desired_locked_ids=(),
        budget=SpiritArtifactCleanseBudget(max_rolls=10, max_material_cost=100),
        allow_replace=allow_replace,
    )


def test_observe_projects_exact_target_and_fingerprint():
    observed = observe_spirit_artifact(_snapshot(), TARGET)

    assert observed.target == TARGET
    assert observed.artifact_name == "血晶摩诃剑"
    assert observed.part_name == "柄"
    assert observed.process_identity == (123, 456)
    assert [effect.cleanse_id for effect in observed.effects] == [112002, 999999]
    assert observed.pending_effects == ()
    assert len(observed.fingerprint) == 64


def test_observe_rejects_stale_or_drifted_runtime():
    stale = _snapshot()
    stale["runtime_updated_at"] = time.time() - 1000
    with pytest.raises(SpiritArtifactCleanseBlocked, match="不新鲜"):
        observe_spirit_artifact(stale, TARGET)

    with pytest.raises(SpiritArtifactCleanseBlocked, match="base_id 已漂移"):
        observe_spirit_artifact(
            _snapshot(), SpiritArtifactTarget(TARGET.item_id, 1, 1, 123)
        )


def test_prepare_is_pure_and_plan_token_is_not_authorization():
    observed = observe_spirit_artifact(_snapshot(), TARGET)
    prepared = prepare_spirit_artifact_cleanse(observed, _request(observed))

    assert prepared.ready is True
    assert "dry-run" in prepared.reason
    with pytest.raises(SpiritArtifactCleanseBlocked, match="缺少"):
        require_irreversible_authorization(prepared, None, material=True)
    with pytest.raises(SpiritArtifactCleanseBlocked, match="未授权消耗"):
        require_irreversible_authorization(
            prepared,
            SpiritArtifactIrreversibleAuthorization(prepared.plan_token),
            material=True,
        )


def test_prepare_rejects_existing_unsaved_candidate():
    observed = observe_spirit_artifact(_snapshot(pending=True), TARGET)

    with pytest.raises(SpiritArtifactCleanseBlocked, match="未保存洗灵候选"):
        prepare_spirit_artifact_cleanse(observed, _request(observed))


def test_attempt_scoped_interface_separates_preview_consume_and_one_shot_auth():
    gui = _Gui()
    interface = SpiritArtifactCleanseInterface(lambda: _complete_snapshot(), gui=gui)
    context = SpiritArtifactAttemptContext("attempt-a", 1, (123, 456))
    fresh = interface.begin_attempt(context, TARGET)
    request = _request(fresh.observation)
    prepared = interface.prepare(request)

    interface.preview_attributes(prepared)
    assert gui.calls == ["preview"]
    auth = SpiritArtifactIrreversibleAuthorization(
        prepared.plan_token,
        phase="consume",
        nonce="consume-once",
        allow_material_consumption=True,
    )
    interface.start_auto_cleanse(prepared, auth)
    assert gui.calls[-1] == "consume"
    with pytest.raises(SpiritArtifactCleanseBlocked) as reused:
        interface.start_auto_cleanse(prepared, auth)
    assert reused.value.code.value == "AUTH_REUSED"


def test_target_universe_requires_exact_8x6_and_rejects_duplicate_item():
    complete = _complete_snapshot()
    validate_spirit_artifact_target_universe(complete)
    complete["artifacts"][0]["rows"].pop()
    with pytest.raises(SpiritArtifactCleanseBlocked) as missing:
        validate_spirit_artifact_target_universe(complete)
    assert missing.value.code.value == "TARGET_UNIVERSE_INCOMPLETE"

    duplicate = _complete_snapshot()
    duplicate["artifacts"][0]["rows"][1]["runtime_item_id"] = TARGET.item_id
    with pytest.raises(SpiritArtifactCleanseBlocked) as ambiguous:
        validate_spirit_artifact_target_universe(duplicate)
    assert ambiguous.value.code.value == "TARGET_AMBIGUOUS"


def test_same_runtime_state_gets_distinct_attempt_tokens():
    interface = SpiritArtifactCleanseInterface(lambda: _complete_snapshot())
    first = interface.begin_attempt(
        SpiritArtifactAttemptContext("attempt-a", 1, (123, 456)), TARGET
    )
    second = interface.begin_attempt(
        SpiritArtifactAttemptContext("attempt-b", 1, (123, 456)), TARGET
    )
    assert first.snapshot_token != second.snapshot_token
    assert first.attempt.attempt_id != second.attempt.attempt_id


def test_existing_pending_candidate_is_not_owned_by_new_attempt():
    interface = SpiritArtifactCleanseInterface(
        lambda: _complete_snapshot(pending=True)
    )
    interface.begin_attempt(
        SpiritArtifactAttemptContext("attempt-a", 1, (123, 456)), TARGET
    )
    with pytest.raises(SpiritArtifactCleanseBlocked) as blocked:
        interface.observe_pending()
    assert blocked.value.code.value == "PENDING_FROM_PRIOR_ATTEMPT"


def _observation(*, locked: bool, value: int) -> SpiritArtifactObservation:
    return observe_spirit_artifact(_snapshot(locked=locked, value=value), TARGET)


def test_lock_verifier_allows_only_one_lock_flag_delta():
    verify_spirit_artifact_lock_delta(
        _observation(locked=False, value=6700),
        _observation(locked=True, value=6700),
        cleanse_id=112002,
        locked=True,
    )
    with pytest.raises(SpiritArtifactCleanseBlocked, match="数值或品质"):
        verify_spirit_artifact_lock_delta(
            _observation(locked=False, value=6700),
            _observation(locked=True, value=6800),
            cleanse_id=112002,
            locked=True,
        )


def test_commit_verifier_requires_exact_material_and_effect_delta():
    before = _observation(locked=False, value=6700)
    after = _observation(locked=False, value=6800)
    page = SpiritArtifactPageEvidence(
        scene="wash-result",
        target_item_id=TARGET.item_id,
        observed_effect_fingerprint=spirit_artifact_effect_fingerprint(after.effects),
        frame_sha256="a" * 64,
    )
    result = verify_spirit_artifact_commit_delta(
        before,
        after,
        material_before=200,
        material_after=190,
        expected_material_cost=10,
        page_evidence=page,
    )
    assert result.material_cost == 10

    with pytest.raises(SpiritArtifactCleanseBlocked, match="材料"):
        verify_spirit_artifact_commit_delta(
            _observation(locked=False, value=6700),
            _observation(locked=False, value=6800),
            material_before=200,
            material_after=191,
            expected_material_cost=10,
            page_evidence=page,
        )


def test_commit_verifier_rejects_runtime_without_matching_page_evidence():
    before = _observation(locked=False, value=6700)
    after = _observation(locked=False, value=6800)
    with pytest.raises(SpiritArtifactCleanseBlocked, match="页面证据"):
        verify_spirit_artifact_commit_delta(
            before,
            after,
            material_before=200,
            material_after=190,
            expected_material_cost=10,
            page_evidence=SpiritArtifactPageEvidence(
                scene="wash-result",
                target_item_id=TARGET.item_id,
                observed_effect_fingerprint="wrong",
                frame_sha256="a" * 64,
            ),
        )


class _RuntimeGui:
    def __init__(self, scene: int = 34) -> None:
        self.scene = scene
        self.calls: list[tuple] = []

    def current_scene(self, candidates, **kwargs):
        self.calls.append(("observe", tuple(candidates), kwargs))
        matched = self.scene if self.scene in set(candidates) else None
        return matched, 100.0 if matched is not None else 0.0, "frame"

    def click_shape_center_then_view(
        self, source, shape, *targets, timeout=None, label=None
    ):
        def transition():
            self.calls.append(("click", source, shape, tuple(targets), timeout, label))
            self.scene = int(targets[0])
            if False:
                yield None
            return SimpleNamespace(id=self.scene)

        return transition()


def _execute_generator(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def _fresh_target():
    interface = SpiritArtifactCleanseInterface(lambda: _complete_snapshot())
    return interface.begin_attempt(
        SpiritArtifactAttemptContext("gui-attempt", 1, (123, 456)), TARGET
    )


def test_runtime_gui_assets_keep_business_and_layer0_domains_exact():
    assets = SpiritArtifactCleanseGuiAssets()

    assert assets.business_scene_ids == (666, 667, 668)
    assert assets.layer0_candidate_ids == (669, 670, 671)
    assert assets.observation_scene_ids == (34, 35, 666, 667, 668, 669, 670, 671)
    assert len(set(assets.observation_scene_ids)) == len(assets.observation_scene_ids)


def test_runtime_gui_adapter_selects_only_verified_first_target_path():
    runtime = _RuntimeGui(scene=34)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )

    adapter.select(_fresh_target())

    assert runtime.scene == 668
    clicks = [call for call in runtime.calls if call[0] == "click"]
    assert [(call[1], call[2], call[3]) for call in clicks] == [
        (34, "打开下方菜单", (35,)),
        (35, "灵器", (666,)),
        (666, "首个灵器", (667,)),
        (667, "洗炼", (668,)),
    ]


def test_runtime_gui_adapter_explicitly_observes_and_cancels_layer0_warning():
    runtime = _RuntimeGui(scene=668)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )

    adapter.probe_auto_settings_warning()
    assert runtime.scene == 669
    adapter.cancel()

    assert runtime.scene == 668
    clicks = [call for call in runtime.calls if call[0] == "click"]
    assert [(call[1], call[2], call[3]) for call in clicks] == [
        (668, "自动洗炼设置", (669,)),
        (669, "取消", (668,)),
    ]
    assert all(call[2] != "确定（研发禁止）" for call in clicks)


def test_runtime_gui_adapter_opens_and_closes_read_only_attribute_preview():
    runtime = _RuntimeGui(scene=668)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )

    adapter.open_attribute_preview(None)
    assert runtime.scene == 670
    adapter.cancel()

    assert runtime.scene == 668
    clicks = [call for call in runtime.calls if call[0] == "click"]
    assert [(call[1], call[2], call[3]) for call in clicks] == [
        (668, "词条预览", (670,)),
        (670, "点击空白关闭", (668,)),
    ]


def test_runtime_gui_adapter_opens_auto_settings_but_never_keep():
    runtime = _RuntimeGui(scene=668)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )

    adapter.open_auto_settings()
    assert runtime.scene == 671
    adapter.cancel()

    assert runtime.scene == 668
    clicks = [call for call in runtime.calls if call[0] == "click"]
    assert [(call[1], call[2], call[3]) for call in clicks] == [
        (668, "自动洗炼设置", (669,)),
        (669, "确定进入自动设置", (671,)),
        (671, "点击空白关闭", (668,)),
    ]
    assert all(call[2] != "开启自动（研发禁止）" for call in clicks)


def test_runtime_gui_adapter_returns_from_layer0_to_world_without_shortcut():
    runtime = _RuntimeGui(scene=669)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )

    adapter.return_to_world()

    assert runtime.scene == 34
    clicks = [call for call in runtime.calls if call[0] == "click"]
    assert [(call[1], call[2], call[3]) for call in clicks] == [
        (669, "取消", (668,)),
        (668, "装配", (667,)),
        (667, "返回", (666,)),
        (666, "返回", (34,)),
    ]


@pytest.mark.parametrize("overlay_scene", [670, 671])
def test_runtime_gui_adapter_returns_from_read_only_overlay_to_world(overlay_scene):
    runtime = _RuntimeGui(scene=overlay_scene)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )

    adapter.return_to_world()

    assert runtime.scene == 34
    clicks = [call for call in runtime.calls if call[0] == "click"]
    assert [(call[1], call[2], call[3]) for call in clicks] == [
        (overlay_scene, "点击空白关闭", (668,)),
        (668, "装配", (667,)),
        (667, "返回", (666,)),
        (666, "返回", (34,)),
    ]


def test_runtime_gui_adapter_keeps_unproved_targets_and_writes_fail_closed():
    runtime = _RuntimeGui(scene=34)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(
        runtime, _execute_generator
    )
    fresh = _fresh_target()
    unknown = FreshSpiritArtifactSnapshot(
        fresh.attempt,
        replace(fresh.observation, part_name="刃"),
        fresh.snapshot_token,
    )

    with pytest.raises(SpiritArtifactCleanseBlocked) as missing:
        adapter.select(unknown)
    assert missing.value.code.value == "ASSET_MISSING"
    assert not [call for call in runtime.calls if call[0] == "click"]

    with pytest.raises(SpiritArtifactCleanseBlocked, match="默认禁用"):
        adapter.set_lock(112002, True)
