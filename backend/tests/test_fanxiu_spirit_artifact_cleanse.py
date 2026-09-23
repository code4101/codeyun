from __future__ import annotations

import time
from dataclasses import replace

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
        "runtime_equipped_count": 1,
        "artifacts": [
            {
                "order": 1,
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
    """完整已加载集合：每个灵器一个 artifact（带 order），各六个部位。

    资产快照按灵器分组并带 order，行内 runtime_ware_id 必须与所在 artifact 一致；
    装配数量等于全部非空部位数，validator 才能核验集合完整性。
    """

    snapshot = _snapshot(pending=pending, value=value)
    artifacts = snapshot["artifacts"]
    for ware_id in range(1, 10):
        if ware_id == 1:
            rows = artifacts[0]["rows"]
        else:
            rows = []
            artifacts.append({"order": ware_id, "name": f"灵器{ware_id}", "rows": rows})
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
    snapshot["runtime_equipped_count"] = sum(len(artifact["rows"]) for artifact in artifacts)
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


def test_runtime_gui_assets_include_owned_overlays():
    assets = SpiritArtifactCleanseGuiAssets()
    assert {669, 670, 671, 712, 713}.issubset(assets.observation_scene_ids)
    assert len(set(assets.observation_scene_ids)) == len(assets.observation_scene_ids)


def test_material_identity_is_bound_to_plan_authorization():
    observed = observe_spirit_artifact(_snapshot(), TARGET)
    request = replace(_request(observed), budget=SpiritArtifactCleanseBudget(1, 1, 14000007))
    first = prepare_spirit_artifact_cleanse(observed, request)
    second = prepare_spirit_artifact_cleanse(observed, replace(
        request, budget=SpiritArtifactCleanseBudget(1, 1, 14000009)))
    authorization = SpiritArtifactIrreversibleAuthorization(first.plan_token, allow_material_consumption=True)
    with pytest.raises(SpiritArtifactCleanseBlocked, match='token'):
        require_irreversible_authorization(second, authorization, material=True)


class _CleanseNavigationContext:
    """Minimal Cell-context double: only the public navigation contract used here."""

    def __init__(self, scene: int, transitions: dict[tuple[int, str], int]) -> None:
        self.scene = scene
        self.transitions = transitions
        self.clicks: list[tuple[int, str]] = []

    def click_shape_center_then_scene(self, scene, shape, *targets, **options):
        self.clicks.append((int(scene), str(shape)))
        landed = self.transitions[(int(scene), str(shape))]
        self.scene = landed
        return type("_View", (), {"id": landed, "scene_id": landed})()

    def wait_scene(self, scenes, wait=5.0, **options):
        return type("_Match", (), {"scene_id": self.scene, "score": 100.0,
                                   "matched_layer": 0, "frame_data_url": ""})()


def _cleanse_navigation_adapter(scene: int, transitions: dict[tuple[int, str], int]):
    context = _CleanseNavigationContext(scene, transitions)
    adapter = SpiritArtifactCleanseRuntimeGuiAdapter(context, lambda value: value)
    adapter.current_scene_id = lambda: context.scene
    return adapter, context


def test_return_to_world_closes_world_menu_entry_35():
    """#35 是 open_overview() 自 #34 打开的合法入口，必须沿正式 shape 回 #34。"""

    adapter, context = _cleanse_navigation_adapter(35, {(35, "关闭下方菜单"): 34})

    adapter.return_to_world()

    assert context.clicks == [(35, "关闭下方菜单")]
    assert adapter.current_scene_id() == 34


def test_return_to_world_still_rejects_unmapped_pages():
    """未映射页面继续失败关闭，不新增猜测返回路径。"""

    adapter, _context = _cleanse_navigation_adapter(400, {})

    with pytest.raises(SpiritArtifactCleanseBlocked, match="未落到 #34"):
        adapter.return_to_world()
