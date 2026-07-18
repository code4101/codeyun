import json

from backend.core.fanxiu.catalog.server_relations import (
    classify_fanxiu_target_relation,
    get_fanxiu_server_relation_config_path,
    load_fanxiu_server_relations,
    save_fanxiu_server_relations,
)


def _configured_tree() -> dict:
    return {
        "groups": [
            {
                "key": "friendly",
                "children": [
                    {
                        "key": "same_server",
                        "servers": [{"server_id": 22077, "server_order": 53, "server_name": "岁序更替"}],
                    },
                    {
                        "key": "alliance",
                        "servers": [
                            {"server_id": 22079, "server_order": 55, "server_name": "金相玉质"},
                            {"server_id": 22074, "server_order": 50, "server_name": "月白风清"},
                            {"server_id": 22073, "server_order": 49, "server_name": "快步流星"},
                        ],
                    },
                    {
                        "key": "ally",
                        "servers": [
                            {"server_id": 22088, "server_order": 64, "server_name": "喜笑颜开"},
                            {"server_id": 22082, "server_order": 58, "server_name": "风云人物"},
                            {"server_id": 22056, "server_order": 32, "server_name": "忠肝义胆"},
                        ],
                    },
                ],
            },
        ],
    }


def test_server_relation_tree_preserves_protection_order(tmp_path) -> None:
    tree = save_fanxiu_server_relations(_configured_tree(), tmp_path)

    assert tree["ordering"] == "protection_desc"
    friendly, non_friendly = tree["groups"]
    assert [item["label"] for item in friendly["children"]] == ["本服", "联盟", "盟友"]
    assert [item["server_name"] for item in friendly["children"][1]["servers"]] == [
        "金相玉质",
        "月白风清",
        "快步流星",
    ]
    assert [item["server_name"] for item in friendly["children"][2]["servers"]] == [
        "喜笑颜开",
        "风云人物",
        "忠肝义胆",
    ]
    assert [item["label"] for item in non_friendly["children"]] == ["其他区服", "NPC"]


def test_server_relation_loader_reads_latest_file_on_every_call(tmp_path) -> None:
    save_fanxiu_server_relations(_configured_tree(), tmp_path)
    path = get_fanxiu_server_relation_config_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["groups"][0]["children"][1]["servers"].reverse()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    reloaded = load_fanxiu_server_relations(tmp_path)

    assert [server["server_name"] for server in reloaded["groups"][0]["children"][1]["servers"]] == [
        "快步流星",
        "月白风清",
        "金相玉质",
    ]


def test_target_classification_uses_shared_dynamic_config(tmp_path) -> None:
    save_fanxiu_server_relations(_configured_tree(), tmp_path)

    alliance = classify_fanxiu_target_relation(is_npc=False, server_id=22073, data_dir=tmp_path)
    npc = classify_fanxiu_target_relation(is_npc=True, data_dir=tmp_path)
    other = classify_fanxiu_target_relation(is_npc=False, server_id=99999, data_dir=tmp_path)

    assert alliance["path"] == ["友军", "联盟"]
    assert alliance["server_priority"] == 2
    assert npc["path"] == ["非友军", "NPC"]
    assert other["path"] == ["非友军", "其他区服"]
