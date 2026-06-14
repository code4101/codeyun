from backend.core.stock.strategy_research import (
    get_strategy_research_item,
    list_strategy_research_backlog,
    list_strategy_research_items,
    load_strategy_research_catalog,
)


def test_strategy_research_catalog_loads_and_has_unique_entries():
    catalog = load_strategy_research_catalog()

    assert catalog["schema_version"] == 1
    assert len(catalog["source_groups"]) >= 5
    assert len(catalog["strategies"]) >= 10

    strategy_ids = [item["id"] for item in catalog["strategies"]]
    assert len(strategy_ids) == len(set(strategy_ids))


def test_strategy_research_catalog_filters_by_family_status_and_market():
    result = list_strategy_research_items(
        family="etf_rotation",
        status="promising_candidate_sensitivity_supported",
        market="CN",
        min_priority=2,
    )

    assert result["count"] >= 1
    assert any(item["id"] == "cross_asset_etf_weekly_relative_momentum_abs_filter" for item in result["items"])


def test_strategy_research_catalog_can_read_hk_connect_candidate():
    item = get_strategy_research_item("hk_connect_hsi60_largecap_volume_momentum_top2")

    assert item is not None
    assert item["status"] == "implemented_candidate_needs_membership_retest"
    assert item["existing_mapping"]["api"] == "/api/eastmoney/qlib/hk-connect-momentum-review"


def test_strategy_research_backlog_is_execution_oriented_and_sorted():
    backlog = list_strategy_research_backlog(max_priority=3)

    assert backlog["count"] >= 5
    priorities = [item["priority"] for item in backlog["items"]]
    assert priorities == sorted(priorities)
    assert all("next_validation" in item for item in backlog["items"])
    assert any(item["id"] == "cross_asset_etf_weekly_relative_momentum_abs_filter" for item in backlog["items"])
