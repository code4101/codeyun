from types import SimpleNamespace

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


def test_build_fanxiu_wiki_link_index_uses_disk_cache_after_process_cache_clear(tmp_path, monkeypatch):
    payload = {
        "total": 1,
        "items": [
            {"alias": "掌天瓶", "tab": "item", "id": "1050040", "title": "掌天瓶", "priority": 70},
        ],
    }
    calls = {"count": 0}

    def fake_build(export_root=None):
        calls["count"] += 1
        return payload

    monkeypatch.setattr(fanxiu_resources, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(fanxiu_resources, "_build_fanxiu_wiki_link_index_uncached", fake_build)
    fanxiu_resources._build_fanxiu_wiki_link_index_cached.cache_clear()

    first = fanxiu_resources._build_fanxiu_wiki_link_index_cached("D:/fanxiu-export", 11, 22, 33, 44)
    assert first == payload
    assert calls["count"] == 1

    fanxiu_resources._build_fanxiu_wiki_link_index_cached.cache_clear()
    monkeypatch.setattr(
        fanxiu_resources,
        "_build_fanxiu_wiki_link_index_uncached",
        lambda export_root=None: (_ for _ in ()).throw(AssertionError("disk cache should satisfy the second load")),
    )

    second = fanxiu_resources._build_fanxiu_wiki_link_index_cached("D:/fanxiu-export", 11, 22, 33, 44)
    assert second == payload
