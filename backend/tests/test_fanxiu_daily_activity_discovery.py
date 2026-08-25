from __future__ import annotations

from backend.core.fanxiu.activity import daily_activity_discovery as discovery


def _runtime_item(
    *,
    activity_id: int = 4150001,
    runtime_id: int = 4150001400002,
    name: str = "兽渊探秘",
    server_count: int = 4,
    start: int = 1786413600000,
    end: int = 1786543200000,
    identity_complete: bool = True,
    schedule_id: int = 6_400_001,
) -> dict:
    return {
        "id": runtime_id,
        "activityId": activity_id,
        "activityType": 15,
        "state": 2,
        "scheduleId": schedule_id,
        "startTime": start,
        "endTime": end,
        "closePanelTime": end + 86_399_000,
        "serverCount": server_count,
        "name": name,
        "identityComplete": identity_complete,
    }


def _snapshot(*items: dict) -> dict:
    return {
        "complete": True,
        "source_kind": "worldline_activity_runtime_memory",
        "captured_at": "2026-08-14T00:05:00+08:00",
        "count": len(items),
        "declared_count": len(items),
        "resolved_identity_count": sum(
            bool(item.get("identityComplete")) for item in items
        ),
        "unresolved_identity_count": sum(
            not item.get("identityComplete") for item in items
        ),
        "evidence": {
            "pid": 4321,
            "process_start_ticks": 987654,
            "manager_resolver": "lua_global",
        },
        "items": list(items),
    }


def test_not_loaded_never_falls_back_to_old_occurrences() -> None:
    plan = discovery.build_daily_activity_sync_plan(
        {
            "complete": False,
            "reason": "ActivityData 尚未加载",
            "items": [_runtime_item()],
        },
        [
            {
                "id": "old",
                "name": "兽渊探秘",
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
            }
        ],
        known_catalog_ids={4150001},
        target_date="2026-08-14",
    )

    assert plan["status"] == "not_loaded"
    assert plan["requires_ui_preheat"] is True
    assert plan["operations"] == []
    assert plan["write_authorized"] is False


def test_new_and_continuing_occurrences_are_separate() -> None:
    continuing = _runtime_item(
        start=1786586400000,  # 2026-08-13 10:00 +08
        end=1786716000000,  # 2026-08-14 22:00 +08
    )
    starts_today = _runtime_item(
        activity_id=8210001,
        runtime_id=8210001400003,
        name="云梦试剑",
        server_count=8,
        start=1786672800000,  # 2026-08-14 10:00 +08
        end=1786716000000,
    )

    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(continuing, starts_today),
        [],
        known_catalog_ids={4150001, 8210001},
        target_date="2026-08-14",
    )

    assert plan["status"] == "ready"
    assert plan["summary"] == {
        "total": 2,
        "projected_total": 2,
        "starts_today": 1,
        "continues_today": 1,
        "claim_grace_today": 0,
        "outside_day": 0,
        "ends_today": 2,
        "propose_create": 2,
    }
    assert {row["action"] for row in plan["operations"]} == {"propose_create"}


def test_exact_existing_occurrence_is_idempotent() -> None:
    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(_runtime_item()),
        [
            {
                "id": "existing",
                "name": "兽渊探秘",
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
            }
        ],
        known_catalog_ids={4150001},
        target_date="2026-08-12",
    )

    operation = plan["operations"][0]
    assert operation["action"] == "noop"
    assert operation["existing"]["id"] == "existing"
    assert operation["proposed_occurrence"] is None


def test_unknown_activity_is_preserved_but_never_proposed() -> None:
    unknown = _runtime_item(
        activity_id=14_000_000,
        runtime_id=14_000_000_400_002,
        name="",
        identity_complete=False,
    )

    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(unknown),
        [],
        known_catalog_ids=set(),
        target_date="2026-08-12",
    )

    operation = plan["operations"][0]
    assert operation["action"] == "review_unknown_identity"
    assert operation["occurrence"]["activity_id"] == 14_000_000
    assert operation["occurrence"]["display_name"] == "未知活动 14000000"
    assert operation["occurrence"]["raw"]["id"] == 14_000_000_400_002
    assert operation["proposed_occurrence"] is None


def test_same_name_and_dates_with_different_cross_count_requires_review() -> None:
    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(_runtime_item()),
        [
            {
                "id": "wrong-scope",
                "name": "兽渊探秘",
                "cross_count": 8,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
            }
        ],
        known_catalog_ids={4150001},
        target_date="2026-08-12",
    )

    operation = plan["operations"][0]
    assert operation["action"] == "review_scope_conflict"
    assert operation["conflicting_existing"][0]["cross_count"] == 8


def test_duplicate_runtime_rows_are_coalesced_without_losing_ids() -> None:
    first = _runtime_item(runtime_id=101)
    second = _runtime_item(runtime_id=202)

    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(first, second),
        [],
        known_catalog_ids={4150001},
        target_date="2026-08-12",
    )

    assert len(plan["operations"]) == 1
    assert plan["operations"][0]["occurrence"]["runtime_ids"] == [101, 202]


def test_close_panel_grace_is_separate_from_active_day() -> None:
    ended = _runtime_item(
        start=1786413600000,
        end=1786543200000,  # 2026-08-12 22:00 +08
    )
    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(ended),
        [],
        known_catalog_ids={4150001},
        target_date="2026-08-13",
    )

    operation = plan["operations"][0]
    assert operation["occurrence"]["day_relation"] == "claim_grace_today"
    assert operation["occurrence"]["ends_today"] is False
    assert plan["summary"]["starts_today"] == 0
    assert plan["summary"]["continues_today"] == 0
    assert plan["summary"]["claim_grace_today"] == 1


def test_future_runtime_occurrence_is_preserved_for_downstream_projection() -> None:
    future = _runtime_item(
        activity_id=16420001,
        runtime_id=16420001400004,
        name="仙盟争霸",
        start=1786845600000,  # 2026-08-16 10:00 +08
        end=1786975200000,
    )
    future["prepareEndTime"] = 1786759200000
    future["baseId"] = 28000

    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(future),
        [],
        known_catalog_ids={16420001},
        target_date="2026-08-15",
    )

    assert plan["operations"] == []
    assert len(plan["occurrences"]) == 1
    occurrence = plan["occurrences"][0]
    assert occurrence["on_target_day"] is False
    assert occurrence["day_relation"] == "outside_day"
    assert occurrence["prepare_at"] == "2026-08-15T10:00:00+08:00"
    assert occurrence["start_at"] == "2026-08-16T10:00:00+08:00"
    assert occurrence["base_id"] == 28000
    assert plan["summary"]["outside_day"] == 1
    assert plan["summary"]["projected_total"] == 0


def test_worldline_item_without_schedule_id_is_observation_only() -> None:
    permanent = _runtime_item(schedule_id=0)

    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(permanent),
        [],
        known_catalog_ids={4150001},
        target_date="2026-08-12",
    )

    operation = plan["operations"][0]
    assert operation["action"] == "observe_only"
    assert operation["occurrence"]["schedule_id"] == 0
    assert operation["proposed_occurrence"] is None


def test_reader_wrapper_uses_only_current_runtime_and_static_inputs(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        discovery,
        "read_worldline_activity_runtime_snapshot",
        lambda **kwargs: calls.append(kwargs) or _snapshot(_runtime_item()),
    )
    monkeypatch.setattr(discovery, "_load_activity_definitions", lambda _root: {4150001: {}})
    monkeypatch.setattr(discovery, "load_activity_list", lambda: [])
    monkeypatch.setattr(
        discovery,
        "read_revenue_activity_observation_snapshot",
        lambda **_kwargs: {"complete": False, "items": []},
    )

    plan = discovery.read_daily_activity_discovery_plan(
        target_date="2026-08-12",
        allow_discovery=False,
    )

    assert plan["status"] == "ready"
    assert calls == [
        {
            "allow_discovery": False,
            "force_refresh": False,
            "export_root": None,
        }
    ]


def test_reader_wrapper_not_loaded_skips_catalog_and_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery,
        "read_worldline_activity_runtime_snapshot",
        lambda **_kwargs: {"complete": False, "reason": "尚未加载"},
    )
    monkeypatch.setattr(
        discovery,
        "_load_activity_definitions",
        lambda _root: (_ for _ in ()).throw(AssertionError("不应读取图鉴")),
    )
    monkeypatch.setattr(
        discovery,
        "load_activity_list",
        lambda: (_ for _ in ()).throw(AssertionError("不应读取历史清单")),
    )
    monkeypatch.setattr(
        discovery,
        "read_revenue_activity_observation_snapshot",
        lambda **_kwargs: {"complete": False, "items": []},
    )

    plan = discovery.read_daily_activity_discovery_plan(target_date="2026-08-14")

    assert plan["status"] == "not_loaded"
    assert plan["requires_ui_preheat"] is True


def test_revenue_observation_remains_separate_from_worldline_occurrences() -> None:
    observation = {
        "complete": True,
        "source_kind": "revenue_activity_observation_runtime_memory",
        "captured_at": "2026-08-19T09:53:42+08:00",
        "items": [
            {
                "observation_id": "revenue:712",
                "activity_id": 712,
                "template_id": 909,
                "name": "万宝臻宝",
                "is_schedule_occurrence": False,
                # A hostile adapter cannot smuggle schedule semantics in.
                "scheduleId": 123,
                "startTime": 456,
            }
        ],
        "evidence": {"join": "exact_activity_id_intersection"},
    }
    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(_runtime_item()),
        [],
        activity_observation_snapshot=observation,
        known_catalog_ids={4150001},
        target_date="2026-08-12",
    )

    assert len(plan["occurrences"]) == 1
    assert len(plan["operations"]) == 1
    assert plan["summary"]["activity_observation_total"] == 1
    assert plan["activity_observations"] == [
        {
            "observation_id": "revenue:712",
            "activity_id": 712,
            "template_id": 909,
            "name": "万宝臻宝",
            "is_schedule_occurrence": False,
        }
    ]


def test_plan_preserves_runtime_source_evidence() -> None:
    plan = discovery.build_daily_activity_sync_plan(
        _snapshot(_runtime_item()),
        [],
        known_catalog_ids={4150001},
        target_date="2026-08-12",
    )

    assert plan["source_evidence"] == {
        "count": 1,
        "declared_count": 1,
        "resolved_identity_count": 1,
        "unresolved_identity_count": 0,
        "runtime": {
            "pid": 4321,
            "process_start_ticks": 987654,
            "manager_resolver": "lua_global",
        },
    }
