from backend.api import fanxiu_resources


def test_build_fanxiu_wiki_link_targets_filters_by_text(monkeypatch):
    monkeypatch.setattr(
        fanxiu_resources,
        "build_fanxiu_wiki_link_index",
        lambda export_root=None: {
            "total": 3,
            "items": [
                {"alias": "功法经验", "tab": "item", "id": "1004", "title": "功法经验", "priority": 70},
                {"alias": "掌天瓶", "tab": "item", "id": "1050040", "title": "掌天瓶", "priority": 70},
                {"alias": "无关道具", "tab": "item", "id": "999", "title": "无关道具", "priority": 70},
            ],
        },
    )

    result = fanxiu_resources.build_fanxiu_wiki_link_targets(
        texts=["提升功法经验", "点击使用前往掌天瓶"],
        limit=10,
    )

    assert result["source_total"] == 3
    assert result["total"] == 2
    assert [item["alias"] for item in result["items"]] == ["功法经验", "掌天瓶"]
