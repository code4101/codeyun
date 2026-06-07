from backend.core.fanxiu_activity_catalog import search_fanxiu_activity_cards


def test_current_server_scope_keeps_matching_cross_group_activity() -> None:
    response = search_fanxiu_activity_cards(query="问道巅峰", server_scope="current", limit=30)
    ids = {str(item["id"]) for item in response["items"]}

    assert "11690192" in ids
    assert "11690152" not in ids
    assert "11690162" not in ids
    assert "11690172" not in ids
    assert "11690182" not in ids


def test_current_server_scope_uses_parent_activity_for_child_rank_rows() -> None:
    response = search_fanxiu_activity_cards(query="个人榜单", server_scope="current", limit=40)
    ids = {str(item["id"]) for item in response["items"]}

    assert "11690193" in ids
    assert "11690153" not in ids
    assert "11690163" not in ids
    assert "11690173" not in ids
    assert "11690183" not in ids


def test_current_server_scope_updates_activity_facet_counts() -> None:
    response = search_fanxiu_activity_cards(server_scope="current", limit=10)
    facet_rows = response["facet_index"]["rows"]

    assert response["total"] == len(response["facet_index"]["object_ids"])
    assert len(facet_rows["kind_key"]["activity"]) < 4126
    assert len(facet_rows["time_kind"]["absolute"]) < 1605


def test_activity_cards_can_skip_facet_index_for_lightweight_list() -> None:
    response = search_fanxiu_activity_cards(server_scope="current", limit=10, include_facets=False)

    assert "facet_index" not in response
    assert response["items"]
