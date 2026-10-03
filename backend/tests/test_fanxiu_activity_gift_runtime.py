from __future__ import annotations

import pytest
from backend.core.fanxiu.instrumentation import activity_gift

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


@pytest.mark.parametrize("second_ok", [True, False])
def test_mapping_recovery_rebuilds_once_and_preserves_first_error(monkeypatch, second_ok):
    calls = []

    def observe(ids, *, refresh_process=False):
        calls.append((ids, refresh_process))
        return {
            "ok": second_ok if refresh_process else False,
            "error_code": "memory_address_unmapped",
            "reason": "new mapping" if refresh_process else "original mapping",
            "evidence": {"pid": 1, "process_start_ticks": 2},
        }

    monkeypatch.setattr(activity_gift, "_read_activity_gift_runtime_snapshot", observe)
    result = activity_gift.read_activity_gift_runtime_snapshot(iter([1043111]))
    assert calls == [((1043111,), False), ((1043111,), True)]
    assert result["ok"] is second_ok
    assert result["evidence"]["mapping_recovery"]["first_error"] == "original mapping"


def test_missing_data_does_not_refresh_process(monkeypatch):
    calls = []

    def observe(ids, *, refresh_process=False):
        calls.append(refresh_process)
        return {"ok": False, "error_code": "runtime_unavailable"}

    monkeypatch.setattr(activity_gift, "_read_activity_gift_runtime_snapshot", observe)
    assert not activity_gift.read_activity_gift_runtime_snapshot([1043111])["ok"]
    assert calls == [False]
