from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import backend.core.fanxiu.activity.tiandi_yiju as tiandi_yiju
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        value = cls(
            2026,
            8,
            27,
            19,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        )
        return value if tz is None else value.astimezone(tz)


class _Session:
    def __init__(self, activity) -> None:  # noqa: ANN001
        self.activity = activity

    def get(self, model, activity_id):  # noqa: ANN001
        del model, activity_id
        return self.activity


def test_tiandi_yiju_collect_persists_independent_shop_capture_time(
    monkeypatch,
) -> None:
    activity = SimpleNamespace(
        id="tiandi-yiju-1-2026-08-27-2026-08-27",
        activity_type="tiandi-yiju",
        cross_count=1,
        start_date="2026-08-27",
        end_date="2026-08-27",
        game_rank_activity_id=90101,
        evidence={"game_activity_id": 8090001},
    )
    persisted_payload = {}

    monkeypatch.setattr(tiandi_yiju, "datetime", _FixedDateTime)
    monkeypatch.setattr(tiandi_yiju, "_item_names", lambda: {101: "测试道具"})
    monkeypatch.setattr(
        tiandi_yiju,
        "collect_activity_shop_runtime",
        lambda **kwargs: {
            "complete": True,
            "active_shop_item_count": 1,
            "items": [
                {
                    "goods_id": 1,
                    "item_id": 101,
                    "name": "测试道具",
                    "source_order": 1,
                    "goods_num": 1,
                    "token_cost": 100,
                    "purchase_limit": 1,
                    "purchased_count": 0,
                }
            ],
            "evidence": {"shop_base_id": kwargs["shop_base_id"]},
        },
    )
    monkeypatch.setattr(
        tiandi_yiju,
        "read_wallet_currency_snapshot",
        lambda currency_type: {
            "exchange_currency": 66,
            "cumulative_currency": 66,
            "captured_at": "2026-08-27T19:31:00+08:00",
            "currency_type": currency_type,
        },
    )

    def _ranking_not_loaded():
        raise FanxiuRuntimeMemoryError("榜单未加载")

    monkeypatch.setattr(
        tiandi_yiju,
        "read_tiandi_yiju_runtime_snapshot",
        _ranking_not_loaded,
    )

    def _upsert(session, payload):  # noqa: ANN001
        del session
        persisted_payload.update(payload)
        return activity.id

    monkeypatch.setattr(tiandi_yiju, "upsert_exchange_activity_snapshot", _upsert)
    monkeypatch.setattr(
        tiandi_yiju,
        "list_exchange_activity_snapshot",
        lambda *args, **kwargs: SimpleNamespace(selected_activity=activity),
    )

    result = tiandi_yiju.collect_and_store_tiandi_yiju_activity(
        _Session(activity),
        activity_id=activity.id,
    )

    assert result is activity
    assert persisted_payload["captured_at"] == "2026-08-27T19:31:00+08:00"
    evidence = persisted_payload["evidence"]
    assert evidence["shop_snapshot_captured_at"] == "2026-08-27T19:30:00+08:00"
    assert evidence["refresh_status"]["currency_captured_at"] == (
        "2026-08-27T19:31:00+08:00"
    )
    assert evidence["refresh_status"]["shop"] == "updated"
    assert evidence["refresh_status"]["rankings"] == "retained"
