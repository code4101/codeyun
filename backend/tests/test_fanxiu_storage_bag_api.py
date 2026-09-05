from __future__ import annotations

from backend.api import fanxiu


class _ReadOnlySession:
    def commit(self) -> None:
        raise AssertionError("cached GET must not commit")


class _SyncSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_storage_bag_get_reads_only_cached_projection(monkeypatch) -> None:
    cached = {"complete": True, "items": [{"base_id": 10}]}
    monkeypatch.setattr(fanxiu, "load_storage_bag_atlas", lambda **_kwargs: cached)
    monkeypatch.setattr(
        fanxiu,
        "apply_storage_bag_item_settings",
        lambda _session, atlas: {**atlas, "projected": True},
    )
    monkeypatch.setattr(
        fanxiu.fanxiu_instrumentation_service,
        "backpack_ui_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("cached GET must not read Runtime")),
    )
    monkeypatch.setattr(
        fanxiu,
        "sync_storage_bag_atlas",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached GET must not write the atlas")
        ),
    )

    result = fanxiu.get_fanxiu_business_storage_bag(
        current_user=object(),
        session=_ReadOnlySession(),
    )

    assert result == {
        "ok": True,
        "state": "cached",
        "reason": None,
        "bag": {**cached, "projected": True},
    }


def test_storage_bag_sync_explicitly_reads_runtime_and_commits(monkeypatch) -> None:
    runtime = {"complete": True, "source": "active_backpack_panel_item_info_list"}
    synced = {"complete": True, "items": [{"base_id": 10}]}
    calls: list[str] = []
    session = _SyncSession()

    monkeypatch.setattr(
        fanxiu,
        "ensure_fanxiu_write_permission",
        lambda _user, _session: calls.append("permission"),
    )
    monkeypatch.setattr(
        fanxiu.fanxiu_instrumentation_service,
        "backpack_ui_snapshot",
        lambda: calls.append("runtime") or runtime,
    )
    monkeypatch.setattr(
        fanxiu,
        "load_fanxiu_item_runtime_index",
        lambda **_kwargs: {"cards_by_id": {}},
    )
    monkeypatch.setattr(
        fanxiu,
        "sync_storage_bag_atlas",
        lambda observed, _cards, **_kwargs: (
            calls.append("atlas") or synced
            if observed is runtime
            else None
        ),
    )
    monkeypatch.setattr(
        fanxiu,
        "ensure_storage_bag_atlas_analysis",
        lambda _session, atlas: calls.append("analysis") if atlas is synced else None,
    )
    monkeypatch.setattr(
        fanxiu,
        "apply_storage_bag_item_settings",
        lambda _session, atlas: atlas,
    )

    result = fanxiu.sync_fanxiu_business_storage_bag(
        current_user=object(),
        session=session,
    )

    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["bag"] is synced
    assert calls == ["permission", "runtime", "atlas", "analysis"]
    assert session.commits == 1

