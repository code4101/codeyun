import json
from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.exchange_event import upsert_exchange_activity_snapshot
from backend.core.fanxiu.activity.yaochi_flower_resources import (
    YaochiFlowerResourceSnapshot,
    collect_and_store_yaochi_flower_resource_snapshot,
    load_yaochi_flower_resource_definitions,
    load_yaochi_flower_resource_snapshot,
    _snapshot_from_definitions,
    _normalize_snapshot_items,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_load_resource_definitions_keeps_universal_friendship_gifts(tmp_path) -> None:
    item_dir = tmp_path / "parsed_configs" / "Item"
    item_dir.mkdir(parents=True)
    rows = [
        {
            "id": 7020010,
            "name_plain": "浣星砂",
            "icon": "icon_item_1",
            "smallIcon": "small_item_1",
            "type": 20,
            "subType": 16,
            "quality": 3,
            "descript_plain": "赠送给仙缘人物可以提升10点友好度",
        },
        {
            "id": 7020045,
            "name_plain": "造化青莲",
            "type": 20,
            "subType": 16,
            "quality": 7,
            "descript_plain": "赠送给仙缘人物可以提升2000点友好度",
        },
        {
            "id": 7020038,
            "name_plain": "仙宝秘闻",
            "type": 20,
            "subType": 16,
            "quality": 6,
            "descript_plain": "赠送给轮回殿主可提升5000点友好度",
        },
    ]
    (item_dir / "rows.json").write_text(json.dumps(rows), encoding="utf-8")

    definitions = load_yaochi_flower_resource_definitions(export_root=tmp_path)

    assert [(item["item_id"], item["friendship"]) for item in definitions] == [
        (7020045, 2000),
        (7020010, 10),
    ]
    assert definitions[1]["icon"] == "icon_item_1"
    assert definitions[1]["small_icon"] == "small_item_1"


def test_collect_resource_snapshot_persists_runtime_counts() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "yaochi-flower-festival",
                "cross_count": 1,
                "start_date": "2026-08-05",
                "end_date": "2026-08-05",
                "game_rank_activity_id": 1042811,
                "currency_name": "仙花友好度",
                "source_kind": "worldline_activity",
            },
        )
        observed = YaochiFlowerResourceSnapshot(
            captured_at="2026-08-05T20:00:00+08:00",
            source_kind="read_only_runtime_memory",
            complete=True,
            total_count=1304,
            total_friendship=1_979_000,
            items=[
                {
                    "item_id": 7020045,
                    "name": "造化青莲",
                    "friendship": 2000,
                    "count": 964,
                    "total_friendship": 1_928_000,
                },
                {
                    "item_id": 7020015,
                    "name": "优昙婆罗花",
                    "friendship": 500,
                    "count": 102,
                    "total_friendship": 51_000,
                },
            ],
            evidence={"pid": 2805, "backpack_root_cache_hit": True},
        )

        saved = collect_and_store_yaochi_flower_resource_snapshot(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 5),
            observed_snapshot=observed,
        )
        loaded = load_yaochi_flower_resource_snapshot(session)

        assert saved.activity_id == activity_id
        assert loaded.activity_id == activity_id
        assert loaded.total_count == 1304
        assert loaded.total_friendship == 1_979_000
        assert [(item.name, item.count) for item in loaded.items] == [
            ("造化青莲", 964),
            ("优昙婆罗花", 102),
        ]
        assert loaded.evidence["backpack_root_cache_hit"] is True


def test_snapshot_merges_equivalent_items_but_keeps_source_ids() -> None:
    snapshot = _snapshot_from_definitions(
        [
            {
                "item_id": 7020045,
                "name": "造化青莲",
                "icon": "flower_qinglian",
                "quality": 7,
                "quality_color": "73123a",
                "friendship": 2000,
            },
            {
                "item_id": 394001018,
                "name": "造化青莲",
                "icon": "flower_qinglian",
                "quality": 7,
                "quality_color": "73123a",
                "friendship": 2000,
            },
        ],
        counts={7020045: 964, 394001018: 1},
    )

    assert len(snapshot.items) == 1
    assert snapshot.items[0].item_ids == [7020045, 394001018]
    assert snapshot.items[0].count == 965
    assert snapshot.items[0].total_friendship == 1_930_000
    assert snapshot.total_count == 965
    assert snapshot.total_friendship == 1_930_000


def test_normalize_snapshot_merges_legacy_duplicate_rows() -> None:
    snapshot = _normalize_snapshot_items(
        YaochiFlowerResourceSnapshot(
            complete=True,
            items=[
                {"item_id": 1, "name": "造化青莲", "icon": "same", "quality": 7, "friendship": 2000, "count": 964, "total_friendship": 1_928_000},
                {"item_id": 2, "name": "造化青莲", "icon": "same", "quality": 7, "friendship": 2000, "count": 1, "total_friendship": 2000},
            ],
        ),
        definitions=[
            {"item_id": 1, "quality_color": "73123a"},
            {"item_id": 2, "quality_color": "73123a"},
        ],
    )

    assert [(item.name, item.count) for item in snapshot.items] == [("造化青莲", 965)]
    assert snapshot.items[0].item_ids == [1, 2]
    assert snapshot.items[0].quality_color == "73123a"
