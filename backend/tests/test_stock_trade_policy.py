from backend.core.stock.trade_policy import (
    DEFAULT_TRADE_WORKBENCH_POLICY,
    get_trade_candidate_pool,
    serialize_trade_candidate_pool,
    serialize_trade_workbench_policy,
)


def test_trade_policy_serializes_user_facing_rules() -> None:
    payload = serialize_trade_workbench_policy(DEFAULT_TRADE_WORKBENCH_POLICY)

    assert payload["key"] == "standard_trade_maintenance"
    assert payload["max_single_position_percent"] == 25
    assert payload["score_threshold"] == 70
    assert any("候选首仓" in rule for rule in payload["rules"])


def test_watchlist_candidate_pool_is_policy_defined() -> None:
    pool = get_trade_candidate_pool("watchlist")
    payload = serialize_trade_candidate_pool(pool)

    assert payload["name"] == "自选候选"
    assert payload["source"] == "watchlist:default"
    assert [item["symbol"] for item in payload["targets"]][:2] == ["562500", "159278"]


def test_hk_pool_candidate_pool_is_named_as_buy_candidates() -> None:
    pool = get_trade_candidate_pool("hk_pool")
    payload = serialize_trade_candidate_pool(pool)

    assert payload["name"] == "可买候选"
    assert payload["source"] == "hk_pool:qlib_screen"
    assert "可买首仓候选" in payload["description"]


def test_unknown_candidate_pool_is_rejected() -> None:
    try:
        get_trade_candidate_pool("unknown")
    except ValueError as exc:
        assert "候选池" in str(exc)
    else:
        raise AssertionError("unknown pool should be rejected")
