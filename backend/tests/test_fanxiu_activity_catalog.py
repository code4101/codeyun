from backend.core.fanxiu_activity_catalog import _active_task_resource_icon, _compact_active_task_row


def test_active_task_background_resource_is_not_icon():
    card = _compact_active_task_row(
        {
            "_row_key": 200110,
            "id": 200110,
            "name": "血色禁地试炼一",
            "resource": "xiuxz_bg_0012",
        },
        item_by_id={},
    )

    assert card["icon"] == ""


def test_active_task_non_background_resource_is_icon():
    row = {
        "_row_key": 200111,
        "id": 200111,
        "name": "试炼任务",
        "resource": "mainui_icon_0737",
    }

    assert _active_task_resource_icon(row) == "mainui_icon_0737"
    assert _compact_active_task_row(row, item_by_id={})["icon"] == "mainui_icon_0737"
