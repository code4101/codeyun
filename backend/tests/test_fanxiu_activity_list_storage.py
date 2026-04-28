from backend.core import fanxiu_inventory


def test_activity_list_extracts_legacy_cross_suffix(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "fanxiu_inventory.json"
    monkeypatch.setattr(fanxiu_inventory, "get_inventory_storage_path", lambda: storage_path)

    saved = fanxiu_inventory.save_activity_list(
        {
            "items": [
                {
                    "id": "activity-8",
                    "name": "丹道问鼎8跨",
                    "start_date": "2026-04-27",
                    "end_date": "2026-04-28",
                },
                {
                    "id": "activity-invalid",
                    "name": "灵兽祈愿",
                    "cross_count": 3,
                    "start_date": "2026-04-26",
                    "end_date": "2026-04-27",
                },
            ]
        }
    )

    loaded = fanxiu_inventory.load_activity_list()

    assert saved == loaded
    assert loaded[0]["id"] == "activity-8"
    assert loaded[0]["name"] == "丹道问鼎"
    assert loaded[0]["cross_count"] == 8
    assert loaded[1]["id"] == "activity-invalid"
    assert loaded[1]["cross_count"] == 0
