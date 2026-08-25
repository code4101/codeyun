from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.activity_gift import _activity_gift_rows
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


class _Reader:
    def fields(self, value):
        return dict(value)

    def list_items(self, value):
        values = list(value or [])
        return values, len(values)

    def dictionary_fields(self, value):
        return dict(value)


def _data(*, purchased: int = 0):
    return {
        "_CycleGiftDicFunc": {
            "ACTIVITY_GIFT": {
                "giftConfVOs": [
                    {
                        "id": 104311101,
                        "activityId": 1043111,
                        "title": "免费冲榜礼包",
                        "times": 1,
                        "giftType": "EVERY_DAY",
                    },
                    {
                        "id": 104311102,
                        "activityId": 1043111,
                        "title": "灵石礼包",
                        "costs": "Item|1_488",
                        "times": 1,
                    },
                    {
                        "id": 104311105,
                        "activityId": 1043111,
                        "title": "付费礼包",
                        "payId": 200001,
                        "times": 1,
                    },
                ],
                "giftUserVOs": (
                    [{"id": 104311101, "times": purchased}]
                    if purchased
                    else []
                ),
            },
            "ACTIVITY_FREE_GIFT": {
                "giftConfVOs": [],
                "giftUserVOs": [],
            },
        }
    }


def test_only_zero_cost_row_is_free_and_claimable() -> None:
    rows = _activity_gift_rows(
        _Reader(),
        _data(),
        expected_activity_ids={1043111},
    )

    assert [row["id"] for row in rows if row["is_free"]] == [104311101]
    assert [row["id"] for row in rows if row["claimable"]] == [104311101]
    assert rows[0]["gift_type"] == "EVERY_DAY"


def test_purchase_count_is_authoritative_idempotency_fact() -> None:
    rows = _activity_gift_rows(
        _Reader(),
        _data(purchased=1),
        expected_activity_ids={1043111},
    )

    free = next(row for row in rows if row["id"] == 104311101)
    assert free["remaining_times"] == 0
    assert free["claimable"] is False


def test_paid_and_item_cost_rows_never_become_free() -> None:
    rows = _activity_gift_rows(
        _Reader(),
        _data(),
        expected_activity_ids={1043111},
    )

    assert next(row for row in rows if row["id"] == 104311102)["is_free"] is False
    assert next(row for row in rows if row["id"] == 104311105)["is_free"] is False


def test_incoherent_purchase_count_fails_closed() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="已购次数超过配置"):
        _activity_gift_rows(
            _Reader(),
            _data(purchased=2),
            expected_activity_ids={1043111},
        )
