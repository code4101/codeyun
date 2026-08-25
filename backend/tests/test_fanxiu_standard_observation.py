from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.standard_observation import (
    ActivityObservationSpec,
    _full_observed_rank_rows,
    read_activity_rank_fact,
    read_currency_fact,
    store_runtime_activity_rank_fact,
    store_runtime_currency_fact,
)


def test_observation_spec_defaults_to_key_points_for_legacy_adapters() -> None:
    spec = ActivityObservationSpec(rank_activity_id=1, currency_type=2)

    assert spec.primary_scope == "personal"
    assert spec.row_mode == "key_points"


def test_full_observed_rows_keep_real_players_and_never_invent_last() -> None:
    fact = {
        "rank_activity_id": 70841,
        "rank": 2,
        "score": 200,
        "role_key": "role:2",
        "name": "自己",
        "server_id": 22077,
        "server_name": "",
        "club_name": "宗门",
        "rank_list_size": 92,
        "items": [
            {"rank": 1, "score": 300, "key": "role:1", "name": "第一"},
            {"rank": 2, "score": 200, "key": "role:2", "name": "自己", "server_id": 22077},
            {"rank": 3, "score": 100, "key": "role:3", "name": "第三"},
        ],
    }

    rows = _full_observed_rank_rows(fact, scope="personal")

    assert [row["rank"] for row in rows] == [1, 2, 3]
    assert sum(bool(row["is_self"]) for row in rows) == 1
    assert not any(row["is_last_player"] for row in rows)
    assert all(row["has_player"] for row in rows)
    assert rows[0]["raw_data"] == {
        "rank_activity_id": 70841,
        "reported_rank_list_size": 92,
        "loaded_player_count": 3,
        "scope_complete": False,
        "row_source": "rank_items",
    }


def test_full_observed_rows_add_only_a_real_self_fallback() -> None:
    fact = {
        "rank_activity_id": 70842,
        "rank": 5,
        "score": 99,
        "role_key": "22077",
        "name": "凌霄道宗",
        "server_id": 22077,
        "server_name": "凌霄道宗",
        "club_name": "",
        "rank_list_size": 8,
        "items": [],
    }

    rows = _full_observed_rank_rows(fact, scope="plane")

    assert len(rows) == 1
    assert rows[0]["role_key"] == "22077"
    assert rows[0]["raw_data"]["row_source"] == "personal_item_fallback"
    assert not rows[0]["is_last_player"]


def test_runtime_wallet_consumption_reduces_amount_but_preserves_history() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        store_runtime_currency_fact(session, {
            "currency_type": 17,
            "currency_amount": 200_000,
            "currency_borrow": 0,
            "cumulative_currency": 200_000,
            "captured_at": "2026-08-10 20:00:00",
        })
        store_runtime_currency_fact(session, {
            "currency_type": 17,
            "currency_amount": 145_000,
            "currency_borrow": 0,
            "cumulative_currency": 200_000,
            "captured_at": "2026-08-10T20:01:00",
        })

        fact = read_currency_fact(session, 17)

    assert fact["exchange_currency"] == 145_000
    assert fact["cumulative_currency"] == 200_000


def test_late_runtime_wallet_snapshot_cannot_roll_absolute_state_back() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        store_runtime_currency_fact(session, {
            "currency_type": 17,
            "currency_amount": 145_000,
            "currency_borrow": 0,
            "cumulative_currency": 200_000,
            "captured_at": "2026-08-10T20:01:00",
        })
        result = store_runtime_currency_fact(session, {
            "currency_type": 17,
            "currency_amount": 200_000,
            "currency_borrow": 0,
            "cumulative_currency": 200_000,
            "captured_at": "2026-08-10 20:00:00",
        })
        fact = read_currency_fact(session, 17)

    assert result["skipped_duplicate"] == 1
    assert fact["exchange_currency"] == 145_000
    assert fact["captured_at"] == "2026-08-10 20:01:00"


def test_runtime_rank_fact_keeps_exact_occurrence_binding() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        store_runtime_activity_rank_fact(
            session,
            {
                "ok": True,
                "complete": True,
                "rank_activity_id": 70111,
                "rank_list_size": 2,
                "self_ranking": {
                    "rank": 2,
                    "score": 520950,
                    "role_key": "self",
                    "name": "自己",
                },
                "rankings": [
                    {"rank": 1, "score": 600000, "role_key": "first"},
                    {"rank": 2, "score": 520950, "role_key": "self"},
                ],
                "captured_at": "2026-08-22T02:36:46+08:00",
                "evidence": {"process_start_ticks": 5379},
            },
            occurrence_runtime_id="1070011400004",
        )
        fact = read_activity_rank_fact(session, 70111)

    assert fact["rank"] == 2
    assert fact["score"] == 520950
    assert fact["evidence"]["occurrence_runtime_id"] == "1070011400004"
    assert fact["captured_at"] == "2026-08-22 02:36:46+08:00"
