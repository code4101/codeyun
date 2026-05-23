import json
import struct
from pathlib import Path

import pytest

from backend.core.fanxiu_resources import (
    FANXIU_RESOURCE_EXPORT_ROOT_ENV,
    FANXIU_RESOURCE_ROOT_ENV,
    FanxiuResourceError,
    build_fanxiu_resource_summary,
    export_fanxiu_unity_text_assets,
    extract_fanxiu_wwise_wems,
    inspect_fanxiu_wwise_bank,
    list_fanxiu_unity_bundles,
    resolve_fanxiu_asset_path,
    resolve_fanxiu_sprite_icon_path,
)
from backend.core.fanxiu_apk_static import (
    build_fanxiu_apk_download_config_report,
    build_fanxiu_apk_runtime_entry_report,
    build_fanxiu_apk_static_index,
    build_fanxiu_resource_manifest_diff_report,
    build_fanxiu_resource_package_report,
)
from backend.core.fanxiu_game_luaconfig import (
    build_fanxiu_gongfa_feature_probe,
    build_fanxiu_lingjie_feature_catalog,
    build_fanxiu_special_gongfa_feature_probe,
    get_fanxiu_lingjie_feature_card,
    search_fanxiu_lingjie_feature_cards,
)
from backend.core.fanxiu_il2cpp_metadata import build_fanxiu_il2cpp_hot_update_report, build_fanxiu_il2cpp_metadata_probe
from backend.core.fanxiu_download_bridge import build_fanxiu_il2cpp_download_inventory, build_fanxiu_lua_download_bridge_report
from backend.core.fanxiu_item_catalog import build_fanxiu_item_catalog, get_fanxiu_item_card, search_fanxiu_item_cards
from backend.core.fanxiu_hot_update import (
    build_fanxiu_bluestarsea_catalog_probe,
    build_fanxiu_bluestarsea_model_state_probe,
    build_fanxiu_bluestarsea_open_red_dot_probe,
    build_fanxiu_bluestarsea_purify_energy_probe,
    build_fanxiu_bluestarsea_runtime_probe,
    build_fanxiu_bluestarsea_support_config_probe,
    build_fanxiu_blld_combat_mechanics_probe,
    build_fanxiu_blld_finish_flow_probe,
    build_fanxiu_blld_level_catalog_probe,
    build_fanxiu_blld_reward_catalog_probe,
    build_fanxiu_blld_runtime_probe,
    build_fanxiu_hot_update_feature_probe,
    build_fanxiu_hot_update_lscripts_report,
)
from backend.core.fanxiu_gongfa_catalog import build_fanxiu_gongfa_catalog, get_fanxiu_gongfa_card, search_fanxiu_gongfa_cards
from backend.core.fanxiu_lua_config import (
    build_fanxiu_lua_config_batch_report,
    build_fanxiu_lua_config_report,
    parse_fanxiu_generated_lua_config,
)
from backend.core.fanxiu_lua_logic_index import (
    build_fanxiu_lingjie_gongfa_runtime_report,
    build_fanxiu_lua_logic_index,
)
from backend.core.fanxiu_lua_packet_index import build_fanxiu_lua_packet_index
from backend.core.fanxiu_wiki import (
    build_fanxiu_wiki_catalog,
    get_fanxiu_wiki_text_entry,
    resolve_fanxiu_wiki_media_path,
    search_fanxiu_wiki_texts,
)


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload


def _write_minimal_bnk(path: Path, wem_payload: bytes = b"RIFF\x10\x00\x00\x00WAVEfmt ") -> None:
    didx_payload = struct.pack("<III", 12345, 0, len(wem_payload))
    path.write_bytes(
        b"".join(
            [
                _chunk(b"BKHD", b"\x00\x00\x00\x00"),
                _chunk(b"DIDX", didx_payload),
                _chunk(b"DATA", wem_payload),
                _chunk(b"HIRC", b""),
            ]
        )
    )


def _uleb128(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _write_minimal_dex(path: Path) -> None:
    strings = [b"Lcom/example/Main;", b"downloadFile", b"Ljava/lang/Object;"]
    header_size = 112
    string_ids_off = header_size
    type_ids_off = string_ids_off + len(strings) * 4
    method_ids_off = type_ids_off + 4
    class_defs_off = method_ids_off + 8
    data_off = class_defs_off + 32

    string_items = bytearray()
    string_offsets: list[int] = []
    for item in strings:
        string_offsets.append(data_off + len(string_items))
        string_items.extend(_uleb128(len(item)))
        string_items.extend(item)
        string_items.append(0)

    file_size = data_off + len(string_items)
    header = bytearray(header_size)
    header[:8] = b"dex\n035\0"
    struct.pack_into("<I", header, 32, file_size)
    struct.pack_into("<I", header, 36, header_size)
    struct.pack_into("<I", header, 40, 0x12345678)
    struct.pack_into("<I", header, 56, len(strings))
    struct.pack_into("<I", header, 60, string_ids_off)
    struct.pack_into("<I", header, 64, 1)
    struct.pack_into("<I", header, 68, type_ids_off)
    struct.pack_into("<I", header, 88, 1)
    struct.pack_into("<I", header, 92, method_ids_off)
    struct.pack_into("<I", header, 96, 1)
    struct.pack_into("<I", header, 100, class_defs_off)
    struct.pack_into("<I", header, 104, len(string_items))
    struct.pack_into("<I", header, 108, data_off)

    string_ids = b"".join(struct.pack("<I", offset) for offset in string_offsets)
    type_ids = struct.pack("<I", 0)
    method_ids = struct.pack("<HHI", 0, 0, 1)
    class_defs = struct.pack("<IIIIIIII", 0, 1, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    path.write_bytes(bytes(header) + string_ids + type_ids + method_ids + class_defs + bytes(string_items))


def _write_minimal_il2cpp_metadata(path: Path) -> None:
    table_names_count = 32
    header_size = 8 + table_names_count * 8
    sections: dict[str, bytes] = {}

    string_blob = bytearray()

    def add_string(value: str) -> int:
        offset = len(string_blob)
        string_blob.extend(value.encode("utf-8"))
        string_blob.append(0)
        return offset

    game_index = add_string("Game")
    player_index = add_string("Player")
    move_index = add_string("Move")
    health_index = add_string("health")

    sections["string_literal"] = struct.pack("<II", 5, 0)
    sections["string_literal_data"] = b"Hello"
    sections["string"] = bytes(string_blob)
    sections["methods"] = struct.pack("<iiiiiIHHHH", move_index, 0, -1, -1, -1, 0x06000001, 0, 0, 0, 0)
    sections["fields"] = struct.pack("<iiI", health_index, -1, 0x04000001)
    sections["type_definitions"] = struct.pack(
        "<iiiiiiiiIiiiiiiiiHHHHHHHHII",
        player_index,
        game_index,
        0,
        0,
        -1,
        -1,
        -1,
        -1,
        0,
        0,
        0,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        1,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0x02000001,
    )

    table_names = [
        "string_literal",
        "string_literal_data",
        "string",
        "events",
        "properties",
        "methods",
        "parameter_default_values",
        "field_default_values",
        "field_and_parameter_default_value_data",
        "field_marshaled_sizes",
        "parameters",
        "fields",
        "generic_parameters",
        "generic_parameter_constraints",
        "generic_containers",
        "nested_types",
        "interfaces",
        "vtable_methods",
        "interface_offsets",
        "type_definitions",
        "rgctx_entries",
        "images",
        "assemblies",
        "metadata_usage_lists",
        "metadata_usage_pairs",
        "field_refs",
        "referenced_assemblies",
        "attributes_info",
        "attribute_types",
        "unresolved_virtual_call_parameter_types",
        "unresolved_virtual_call_parameter_ranges",
        "windows_runtime_type_names",
    ]
    cursor = header_size
    table_pairs: list[tuple[int, int]] = []
    payload = bytearray()
    for name in table_names:
        content = sections.get(name, b"")
        table_pairs.append((cursor, len(content)))
        payload.extend(content)
        cursor += len(content)

    header = bytearray(header_size)
    struct.pack_into("<II", header, 0, 0xFAB11BAF, 24)
    for index, (offset, size) in enumerate(table_pairs):
        struct.pack_into("<II", header, 8 + index * 8, offset, size)
    path.write_bytes(bytes(header) + bytes(payload))


def test_fanxiu_resource_summary_and_unity_candidate_scan(tmp_path, monkeypatch):
    root = tmp_path / "frxx_game_files"
    asset_dir = root / "texturenew" / "login"
    asset_dir.mkdir(parents=True)
    asset = asset_dir / "login_bg.bytes"
    asset.write_bytes(b"\x00" * 99 + b"UnityFS\x00fake")
    (root / "readme.txt").write_text("ok", encoding="utf-8")
    monkeypatch.setenv(FANXIU_RESOURCE_ROOT_ENV, str(root))

    summary = build_fanxiu_resource_summary()
    assert summary["exists"] is True
    assert summary["file_count"] == 2
    assert summary["suffix_counts"][".bytes"] == 1

    bundles = list_fanxiu_unity_bundles(subdir="texturenew", limit=10, scan_limit=20)
    assert [item["relative_path"] for item in bundles["items"]] == ["texturenew/login/login_bg.bytes"]
    assert bundles["items"][0]["offset"] == 99


def test_fanxiu_resource_path_must_stay_under_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bytes"
    outside.write_bytes(b"UnityFS")
    monkeypatch.setenv(FANXIU_RESOURCE_ROOT_ENV, str(root))

    with pytest.raises(FanxiuResourceError):
        resolve_fanxiu_asset_path(outside)


def test_fanxiu_wwise_inspect_and_extract(tmp_path, monkeypatch):
    root = tmp_path / "frxx_game_files"
    audio_dir = root / "Audio"
    export_root = tmp_path / "exports"
    audio_dir.mkdir(parents=True)
    bank = audio_dir / "sample.bnk"
    _write_minimal_bnk(bank)
    monkeypatch.setenv(FANXIU_RESOURCE_ROOT_ENV, str(root))
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    inspected = inspect_fanxiu_wwise_bank("Audio/sample.bnk")
    assert [chunk["fourcc"] for chunk in inspected["chunks"]] == ["BKHD", "DIDX", "DATA", "HIRC"]
    assert inspected["wem_entries"][0]["wem_id"] == 12345

    extracted = extract_fanxiu_wwise_wems("Audio/sample.bnk")
    assert len(extracted["items"]) == 1
    wem_path = Path(extracted["items"][0]["output_path"])
    assert wem_path.read_bytes().startswith(b"RIFF")


def test_fanxiu_unity_text_asset_export_uses_source_scoped_dir(tmp_path, monkeypatch):
    root = tmp_path / "frxx_game_files"
    export_root = tmp_path / "exports"
    asset = root / "lscripts" / "generate" / "cfg" / "gongfa.bytes"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"\x00" * 99 + b"UnityFS\x00fake")
    monkeypatch.setenv(FANXIU_RESOURCE_ROOT_ENV, str(root))
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    class FakeTextAssetExport:
        def __init__(self, output_path: Path):
            self.output_path = output_path

        def to_dict(self):
            return {
                "source_path": str(asset),
                "output_path": str(self.output_path),
                "path_id": 1,
                "name": "Gongfa.lua",
                "byte_size": 9,
            }

    def fake_export_unity_text_assets(source, output_dir, max_assets=None):
        assert Path(source) == asset
        assert max_assets == 5
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True)
        output_path = out_dir / "Gongfa.lua"
        output_path.write_text("return {}", encoding="utf-8")
        return [FakeTextAssetExport(output_path)]

    monkeypatch.setattr("backend.core.fanxiu_resources.export_unity_text_assets", fake_export_unity_text_assets)

    result = export_fanxiu_unity_text_assets("lscripts/generate/cfg/gongfa.bytes", max_assets=5)

    output_path = Path(result["items"][0]["output_path"])
    assert output_path.read_text(encoding="utf-8") == "return {}"
    assert "by_source" in result["output_dir"]
    assert result["items"][0]["name"] == "Gongfa.lua"


def test_fanxiu_wiki_text_search_and_detail(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    text_dir = export_root / "by_source" / "lscripts" / "text"
    indexes_dir = export_root / "indexes"
    text_dir.mkdir(parents=True)
    indexes_dir.mkdir(parents=True)
    lang_path = text_dir / "lang.lua"
    lang_path.write_text(
        "local _M={\n"
        "[204189]='十星效果：<color=#864c00>【玄天冥宝】</color>\\n星海之力+12%',\n"
        "[204598]='玄魔大法1重',\n"
        "}\n",
        encoding="utf-8",
    )
    (indexes_dir / "text_assets.tsv").write_text(
        "source\tasset\tentries\tpath\n"
        f"source.bytes\tlang.lua\t2\t{lang_path}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    catalog = build_fanxiu_wiki_catalog()
    assert catalog["text_count"] == 2

    result = search_fanxiu_wiki_texts(query="玄天冥宝")
    assert result["total"] == 1
    assert result["items"][0]["key"] == "204189"

    detail = get_fanxiu_wiki_text_entry(asset="lang.lua", key="204189")
    assert detail["plain_text"].startswith("十星效果：【玄天冥宝】")
    assert "<color=#864c00>" in detail["rich_text"]


def test_fanxiu_wiki_media_path_must_stay_under_export_root(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    export_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    with pytest.raises(FanxiuResourceError):
        resolve_fanxiu_wiki_media_path(str(outside))


def test_fanxiu_wiki_user_fields_persist_in_data_dir(tmp_path, monkeypatch):
    from backend.core.fanxiu_wiki_user_fields import (
        get_fanxiu_wiki_user_fields,
        get_fanxiu_wiki_user_fields_storage_path,
        save_fanxiu_wiki_user_fields,
    )
    from backend.core.settings import get_settings

    data_dir = tmp_path / "data"
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    try:
        saved = save_fanxiu_wiki_user_fields("gongfa", "476701", note="备注", source="来源")

        assert saved["note"] == "备注"
        assert saved["source"] == "来源"
        assert get_fanxiu_wiki_user_fields_storage_path().is_file()
        assert get_fanxiu_wiki_user_fields("gongfa", "476701")["source"] == "来源"
        assert save_fanxiu_wiki_user_fields("item", "19030146", note="道具备注")["note"] == "道具备注"
        assert get_fanxiu_wiki_user_fields("item", "19030146")["note"] == "道具备注"
        assert save_fanxiu_wiki_user_fields("lingjie", "306101", source="灵界来源")["source"] == "灵界来源"
        assert get_fanxiu_wiki_user_fields("lingjie", "306101")["source"] == "灵界来源"

        with pytest.raises(FanxiuResourceError):
            save_fanxiu_wiki_user_fields("../bad", "1")

        cleared = save_fanxiu_wiki_user_fields("gongfa", "476701", note="", source="")
        assert cleared["note"] == ""
        assert get_fanxiu_wiki_user_fields("gongfa", "476701")["source"] == ""
    finally:
        get_settings.cache_clear()


def test_fanxiu_sprite_icon_resolves_cached_export(tmp_path, monkeypatch):
    resource_root = tmp_path / "frxx_game_files"
    export_root = tmp_path / "exports"
    icon_dir = export_root / "icons"
    resource_root.mkdir()
    icon_dir.mkdir(parents=True)
    icon_path = icon_dir / "icon9_item_0713.png"
    icon_path.write_bytes(b"png")
    monkeypatch.setenv(FANXIU_RESOURCE_ROOT_ENV, str(resource_root))
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    assert resolve_fanxiu_sprite_icon_path("icon9_item_0713") == icon_path.resolve()
    with pytest.raises(FanxiuResourceError):
        resolve_fanxiu_sprite_icon_path("../icon9_item_0713")


def test_fanxiu_item_catalog_links_quality_and_searches(tmp_path):
    export_root = tmp_path / "exports"
    item_dir = export_root / "parsed_configs" / "Item"
    quality_dir = export_root / "parsed_configs" / "Quality"
    lingjie_dir = export_root / "parsed_configs" / "Lingjie-GongfaJie"
    feature_probe_dir = export_root / "parsed_configs" / "gongfa_feature_probe"
    activity_dir = export_root / "parsed_configs" / "Activity"
    activity_gift_dir = export_root / "parsed_configs" / "ActivityGift"
    item_dir.mkdir(parents=True)
    quality_dir.mkdir(parents=True)
    lingjie_dir.mkdir(parents=True)
    feature_probe_dir.mkdir(parents=True)
    activity_dir.mkdir(parents=True)
    activity_gift_dir.mkdir(parents=True)
    (item_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 3020501,
                    "id": 3020501,
                    "name_plain": "赤书玄鸟卷",
                    "descript_plain": "仙书材料",
                    "effDescript_plain": "用于玄鸟进阶",
                    "icon": "icon2_skill_ljst_7601",
                    "type": 5,
                    "subType": 2,
                    "quality": 7,
                    "effectValue": 316501,
                    "overlay": 999,
                },
                {
                    "_row_key": 3000101,
                    "id": 3000101,
                    "name_plain": "优惠券（6元）",
                    "descript_plain": "购买灵石时可抵扣",
                    "effDescript_plain": "每张优惠券仅可使用一次",
                    "icon": "icon_coupon_6",
                    "type": 9,
                    "subType": 1,
                    "quality": 6,
                    "effectValue": 6,
                    "overlay": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (quality_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 7,
                    "id": 7,
                    "name_plain": "红色品质",
                    "color": "9e1e09",
                    "tab_plain": "红",
                    "squareBg": "common_iconframe_red",
                },
                {
                    "_row_key": 6,
                    "id": 6,
                    "name_plain": "紫色品质",
                    "color": "73123a",
                    "tab_plain": "紫",
                    "squareBg": "common_iconframe_purple",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lingjie_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 31650101,
                    "id": 31650101,
                    "gid": 316501,
                    "name_plain": "1重",
                    "jie": 1,
                    "attr": [],
                },
                {
                    "_row_key": 31650102,
                    "id": 31650102,
                    "gid": 316501,
                    "name_plain": "2重",
                    "jie": 2,
                    "consume": ["Item|3020501_1"],
                    "feature": "35760101",
                    "attr": {"ENDURANCE": 38000},
                    "describe_plain": "二重：仙书玄鸟造成225%攻击力的仙灵伤害",
                },
                {
                    "_row_key": 31650103,
                    "id": 31650103,
                    "gid": 316501,
                    "name_plain": "3重",
                    "jie": 3,
                    "consume": ["Item|3020501_1"],
                    "feature": "99990101",
                    "attr": {"ENDURANCE": 76000},
                    "describe_plain": "三重：玄鸟暴击后触发额外伤害",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (feature_probe_dir / "feature_links.tsv").write_text(
        "feature\tsource_gid\tsource_jie\tsource_name\tsource_describe\tdirect_match_count\tfamily_match_count\tmatch_kind\tconfig_ids\tconfig_descriptions\ttimelines\teffect_paths\tsound_ids\thit_frames\n"
        "35760101\t316501\t2\t2重\t二重：仙书玄鸟造成225%攻击力的仙灵伤害\t1\t2\tdirect_prefix8\t357601014\t仙书-彩-01-二级仙鹤\tTimeLine357601014\tskill/eff_skill_fen_06_01_buff_attack\t201010001\t33\n"
        "99990101\t316501\t3\t3重\t三重：玄鸟暴击后触发额外伤害\t0\t0\t\t\t\t\t\t\t\n",
        encoding="utf-8",
    )
    (activity_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 990001,
                    "id": 990001,
                    "name_plain": "玄鸟试炼",
                    "startTime": "ABS|2025_2_16_0_00_05",
                },
                {
                    "_row_key": 990002,
                    "id": 990002,
                    "name_plain": "玄鸟复刻",
                    "startTime": "ABS|2025_3_1_0_00_05",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (activity_gift_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 880001,
                    "id": 880001,
                    "activityId": 990001,
                    "reward": "Item|3020501_1",
                },
                {
                    "_row_key": 880002,
                    "id": 880002,
                    "activityId": 990002,
                    "reward": "Item|3020501_1",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_fanxiu_item_catalog(export_root=export_root)
    catalog = json.loads(Path(result["files"]["catalog"]).read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 9
    assert catalog["cards"][0]["quality_name"] == "红色品质"
    assert catalog["cards"][0]["type_name"] == "材料"
    assert catalog["cards"][0]["sub_type_key"] == "5:2"
    assert catalog["cards"][0]["progression_counts"] == {"lingjie_jie": 3}
    assert catalog["cards"][0]["first_time_hint"]["date"] == "2025-02-16"
    assert catalog["cards"][0]["first_time_hint"]["activity_name"] == "玄鸟试炼"
    assert [hint["date"] for hint in catalog["cards"][0]["time_hints"]] == ["2025-02-16", "2025-03-01"]
    assert result["stats"]["progression_linked_item_count"] == 1
    assert result["stats"]["item_with_time_hint_count"] == 1

    searched = search_fanxiu_item_cards(query="玄鸟", export_root=export_root)
    assert searched["total"] == 1
    assert searched["items"][0]["id"] == 3020501
    assert searched["items"][0]["quality_name"] == "红色品质"
    assert searched["items"][0]["progression_counts"] == {"lingjie_jie": 3}
    assert searched["items"][0]["first_time_hint"]["date"] == "2025-02-16"
    assert {item["label"]: item["count"] for item in searched["quality_options"]} == {
        "紫色品质": 1,
        "红色品质": 1,
    }
    assert {item["value"]: item["label"] for item in searched["type_options"]}["5"] == "材料"
    assert searched["facet_index"]["rows"]["type_key"]["5"] == ["3020501"]

    filtered = search_fanxiu_item_cards(quality_name="紫色品质", export_root=export_root)
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == 3000101
    assert search_fanxiu_item_cards(quality_name="上品功法", export_root=export_root)["total"] == 0
    assert search_fanxiu_item_cards(type_key="5", export_root=export_root)["items"][0]["id"] == 3020501
    assert search_fanxiu_item_cards(sub_type_key="5:2", export_root=export_root)["items"][0]["id"] == 3020501

    sorted_asc = search_fanxiu_item_cards(sort_by="time", sort_order="asc", export_root=export_root)
    assert sorted_asc["sort_by"] == "time"
    assert sorted_asc["sort_order"] == "asc"
    assert sorted_asc["items"][0]["id"] == 3000101

    sorted_desc = search_fanxiu_item_cards(sort_by="time", sort_order="desc", export_root=export_root)
    assert sorted_desc["sort_by"] == "time"
    assert sorted_desc["sort_order"] == "desc"
    assert sorted_desc["items"][0]["id"] == 3020501

    detail = get_fanxiu_item_card(3020501, export_root=export_root)
    assert detail["card"]["effect_description"] == "用于玄鸟进阶"
    assert detail["card"]["time_hints"][0]["source"] == "ActivityGift.reward"
    assert detail["card"]["progression"]["lingjie_jie"][1]["consume_items"][0]["name"] == "赤书玄鸟卷"
    assert detail["card"]["progression"]["lingjie_jie"][1]["feature_link"]["timelines"] == "TimeLine357601014"
    assert detail["card"]["progression"]["lingjie_jie"][2]["feature_link"]["source_describe"] == "三重：玄鸟暴击后触发额外伤害"


def test_fanxiu_item_catalog_links_optional_gift_rewards(tmp_path):
    export_root = tmp_path / "exports"
    item_dir = export_root / "parsed_configs" / "Item"
    quality_dir = export_root / "parsed_configs" / "Quality"
    optional_gift_dir = (
        export_root
        / "by_source"
        / "lscripts"
        / "generate"
        / "cfg"
        / "item_demo"
        / "text_assets"
    )
    item_dir.mkdir(parents=True)
    quality_dir.mkdir(parents=True)
    optional_gift_dir.mkdir(parents=True)
    (item_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 400003001,
                    "id": 400003001,
                    "name_plain": "星海宝匣",
                    "descript_plain": "蕴藏诸天星域奇珍",
                    "effDescript_plain": "打开后会随机掉落以下道具",
                    "icon": "icon9_item_0709",
                    "type": 2,
                    "subType": 1,
                    "quality": 7,
                    "effectValue": "98041001_25039",
                    "overlay": 999999,
                },
                {
                    "_row_key": 29806,
                    "id": 29806,
                    "name_plain": "废料·魔修残魂",
                    "descript_plain": "可转化为其中之一",
                    "icon": "icon_waste_moxiu",
                    "type": 5,
                    "subType": 1,
                    "quality": 4,
                },
                {
                    "_row_key": 29810,
                    "id": 29810,
                    "name_plain": "废料·荒道残卷",
                    "descript_plain": "荒古大道残页",
                    "icon": "icon_waste_huangdao",
                    "type": 5,
                    "subType": 1,
                    "quality": 5,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (quality_dir / "rows.json").write_text(
        json.dumps(
            [
                {"_row_key": 7, "id": 7, "name_plain": "彩色品质", "color": "c28a00"},
                {"_row_key": 5, "id": 5, "name_plain": "黄色品质", "color": "864c00"},
                {"_row_key": 4, "id": 4, "name_plain": "紫色品质", "color": "73123a"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (optional_gift_dir / "OptionalGift.lua").write_text(
        "local c=require('Generate.Cfg.bean')\n"
        "local _key2index={id=1,groupID=2,giftID=3,number=4,limitNumber=5,career=6,limitCondition=7,limitConditionTip=8,showCondition=9,limitType=10,showSort=11}\n"
        "local _key2null={[1]=0,[2]='',[3]=0,[4]=0,[5]=0,[6]=nil,[7]='',[8]='',[9]='',[10]=0,[11]=0}\n"
        "local _key2type={[1]=0,[2]=0,[3]=0,[4]=0,[5]=0,[6]=0,[7]=0,[8]=1,[9]=0,[10]=0,[11]=0}\n"
        "local _P=c.Init(_key2index,_key2null,_key2type)\n"
        "local _A={\n[1]='25039',\n}\n"
        "local _B={\n}\n"
        "local _C={\n}\n"
        "local _M={\n"
        "[55450]=setmetatable({[1]=55450,[2]=_A[1],[3]=29806,[4]=5},_P),\n"
        "[55454]=setmetatable({[1]=55454,[2]=_A[1],[3]=29810,[4]=2},_P),\n"
        "}\n"
        "return _M\n",
        encoding="utf-8",
    )

    result = build_fanxiu_item_catalog(export_root=export_root)
    assert result["stats"]["optional_gift_group_count"] == 1
    assert result["stats"]["item_with_optional_gift_count"] == 1

    detail = get_fanxiu_item_card(400003001, export_root=export_root)
    card = detail["card"]
    assert card["optional_gift_group_id"] == "25039"
    assert [(item["id"], item["name"], item["count"]) for item in card["optional_gift_rewards"]] == [
        (29810, "废料·荒道残卷", 2),
        (29806, "废料·魔修残魂", 5),
    ]
    searched = search_fanxiu_item_cards(query="魔修残魂", export_root=export_root)
    assert any(item["id"] == 400003001 for item in searched["items"])


def test_fanxiu_lua_config_report_resolves_language_fields(tmp_path):
    export_root = tmp_path / "exports"
    config_path = export_root / "by_source" / "cfg" / "text_assets" / "Gongfa.lua"
    lang_path = export_root / "by_source" / "lang" / "text_assets" / "lang.lua"
    config_path.parent.mkdir(parents=True)
    lang_path.parent.mkdir(parents=True)
    config_path.write_text(
        "local c=require('Generate.Cfg.bean')\n"
        "local _key2index={id=1,name=2,quality=3,icon=4,descript=5,consume=6}\n"
        "local _key2null={[1]=0,[2]='',[3]=0,[4]='',[5]='',[6]=nil}\n"
        "local _key2type={[1]=0,[2]=1,[3]=0,[4]=0,[5]=1,[6]=0}\n"
        "local _P=c.Init(_key2index,_key2null,_key2type)\n"
        "local _A={\n[1]='icon_xuanmo',\n[2]='_I(103)道具',\n}\n"
        "local _B={\n[1]={_A[2],2},\n}\n"
        "local _C={\n[1]=100,\n[2]=101,\n[3]={102,3},\n[4]={104,_A[2],_A[2]},\n}\n"
        "local _M={\n"
        "[303101]=setmetatable({[1]=303101,[2]=_C[1],[3]=409,[4]=_A[1],[5]=_C[2],[6]=_B[1]},_P),\n"
        "['303101_1']=setmetatable({[1]=303101,[2]=_C[1],[3]=410,[4]=_A[1],[5]=_C[2]},_P),\n"
        "['303101_3']=setmetatable({[1]=303101,[2]=_C[1],[3]=410,[4]=_A[1],[5]=_C[3]},_P),\n"
        "['303101_4']=setmetatable({[1]=303101,[2]=_C[1],[3]=410,[4]=_A[1],[5]=_C[4]},_P),\n"
        "}\n"
        "return _M\n",
        encoding="utf-8",
    )
    lang_path.write_text(
        "local _M={\n"
        "[100]='玄魔大法',\n"
        "[101]='描述<color=#2a4b10>+1</color>',\n"
        "[102]='%s阶效果',\n"
        "[103]='蓝色',\n"
        "[104]='%s/%s',\n"
        "}\n",
        encoding="utf-8",
    )

    parsed = parse_fanxiu_generated_lua_config(config_path, lang_path=lang_path)
    assert parsed["rows"][0]["name_plain"] == "玄魔大法"
    assert parsed["rows"][0]["descript_plain"] == "描述+1"
    assert parsed["rows"][0]["consume"] == ["蓝色道具", 2]
    assert parsed["rows"][1]["_row_key"] == "303101_1"
    assert parsed["rows"][2]["descript_plain"] == "3阶效果"
    assert parsed["rows"][3]["descript_plain"] == "蓝色道具/蓝色道具"

    result = build_fanxiu_lua_config_report(config_path, lang_path=lang_path, export_root=export_root)
    output_dir = Path(result["output_dir"])
    assert "玄魔大法" in (output_dir / "rows.json").read_text(encoding="utf-8")
    assert "name_plain" in (output_dir / "preview.tsv").read_text(encoding="utf-8-sig")


def test_fanxiu_lua_config_batch_report_indexes_tables(tmp_path):
    export_root = tmp_path / "exports"
    config_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "gongfa_hash" / "text_assets"
    lang_dir = export_root / "by_source" / "lscripts" / "generate" / "localization" / "chinese" / "lang_hash" / "text_assets"
    config_dir.mkdir(parents=True)
    lang_dir.mkdir(parents=True)
    (lang_dir / "lang.lua").write_text("local _M={\n[100]='玄魔大法',\n}\n", encoding="utf-8")
    (config_dir / "Gongfa.lua").write_text(
        "local c=require('Generate.Cfg.bean')\n"
        "local _key2index={id=1,name=2}\n"
        "local _key2null={[1]=0,[2]=''}\n"
        "local _key2type={[1]=0,[2]=1}\n"
        "local _P=c.Init(_key2index,_key2null,_key2type)\n"
        "local _C={\n[1]=100,\n}\n"
        "local _M={\n[476701]=setmetatable({[1]=476701,[2]=_C[1]},_P),\n}\n"
        "return _M\n",
        encoding="utf-8",
    )

    result = build_fanxiu_lua_config_batch_report(export_root=export_root)
    tables_text = Path(result["files"]["tables_tsv"]).read_text(encoding="utf-8-sig")

    assert result["table_count"] == 1
    assert result["parsed_count"] == 1
    assert "Gongfa.lua" in tables_text
    assert "玄魔大法" in (export_root / "parsed_configs" / "Gongfa" / "rows.json").read_text(encoding="utf-8")


def test_fanxiu_lingjie_feature_catalog_links_feature_groups(tmp_path):
    export_root = tmp_path / "exports"
    feature_base_dir = export_root / "parsed_configs" / "FeatureBase"
    main_feature_dir = export_root / "parsed_configs" / "MainFeature"
    main_pin_dir = export_root / "parsed_configs" / "MainFeaturePin"
    side_jie_dir = export_root / "parsed_configs" / "SideFeatureJie"
    side_pin_dir = export_root / "parsed_configs" / "SideFeaturePin"
    lingjie_jie_dir = export_root / "parsed_configs" / "LingjieGongfaJie"
    lingjie_star_dir = export_root / "parsed_configs" / "LingjieGongfaStar"
    gongfa_dir = export_root / "parsed_configs" / "Gongfa"
    item_dir = export_root / "parsed_configs" / "Item"
    for path in [
        feature_base_dir,
        main_feature_dir,
        main_pin_dir,
        side_jie_dir,
        side_pin_dir,
        lingjie_jie_dir,
        lingjie_star_dir,
        gongfa_dir,
        item_dir,
    ]:
        path.mkdir(parents=True)

    (feature_base_dir / "rows.json").write_text(
        json.dumps(
            [
                {"_row_key": 1, "id": 1, "group": 101, "featureGroup": 201, "keyFeature": 1, "weighted": 100, "quality": 6},
                {"_row_key": 2, "id": 2, "group": 1001, "featureGroup": 3001, "keyFeature": 0, "weighted": 50, "quality": 5},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (main_feature_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 101,
                    "id": 101,
                    "gongfaId": 306101,
                    "groups": [101],
                    "featureType": 1,
                    "condition": "8|2",
                    "describe_plain": "(悟境后可生效)",
                },
                {"_row_key": 1001, "id": 1001, "gongfaId": 306101, "groups": [1001], "featureType": 2},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (main_pin_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 9001,
                    "id": 9001,
                    "gongfaId": 306101,
                    "pin": 1,
                    "quality": 6,
                    "featureGroup": 201,
                    "feature": "35810160",
                    "name_plain": "惊神剑光",
                    "describe_plain": "主词条效果",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (side_jie_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 9101,
                    "id": 9101,
                    "featureGroup": 3001,
                    "jie": 1,
                    "feature": "35820101",
                    "param": [1, 2, 3],
                    "name_plain": "侧词条一",
                    "describe_plain": "侧词条进阶效果",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (side_pin_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 9201,
                    "id": 9201,
                    "featureGroup": 3001,
                    "pin": 1,
                    "quality": 6,
                    "feature": "35830101",
                    "name_plain": "侧品阶词条",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lingjie_jie_dir / "rows.json").write_text(
        json.dumps(
            [{"_row_key": 9301, "id": 9301, "gongfaId": 306101, "jie": 1, "feature": "35840101"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lingjie_star_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 9401,
                    "id": 9401,
                    "gongfaId": 306101,
                    "star": 1,
                    "skill": "358999",
                    "describe_plain": "升星技能",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (gongfa_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 306101,
                    "id": 306101,
                    "name_plain": "千锋聚灵剑",
                    "descript_plain": "剑修功法说明",
                    "icon": "gongfa_icon",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (item_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 3010158,
                    "id": 3010158,
                    "name_plain": "千锋聚灵剑残页",
                    "icon": "icon_skill_zw_0021",
                    "quality": 6,
                    "effectValue": "306101",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_fanxiu_lingjie_feature_catalog(export_root=export_root)
    catalog = json.loads(Path(result["files"]["catalog"]).read_text(encoding="utf-8"))
    group_links = Path(result["files"]["group_links_tsv"]).read_text(encoding="utf-8-sig")
    cards_tsv = Path(result["files"]["cards_tsv"]).read_text(encoding="utf-8-sig")

    assert result["stats"]["gongfa_count"] == 1
    assert result["stats"]["used_feature_base_group_count"] == 2
    assert result["stats"]["linked_main_pin_group_count"] == 1
    assert result["stats"]["linked_side_jie_group_count"] == 1
    assert result["stats"]["linked_side_pin_group_count"] == 1
    assert result["stats"]["unmatched_group_link_count"] == 0
    assert catalog["cards"][0]["gongfa_id"] == "306101"
    assert catalog["cards"][0]["name"] == "千锋聚灵剑"
    assert catalog["cards"][0]["icon"] == "icon_skill_zw_0021"
    assert catalog["cards"][0]["items"][0]["name"] == "千锋聚灵剑残页"
    assert catalog["cards"][0]["main_feature_count"] == 2
    assert catalog["cards"][0]["jie_rows"][0]["feature"] == "35840101"
    assert catalog["cards"][0]["star_rows"][0]["skill"] == "358999"
    assert catalog["cards"][0]["main_features"][1]["expanded_groups"][0]["target_kinds"] == ["side_jie", "side_pin"]
    assert "惊神剑光" in group_links
    assert "侧词条一" in group_links
    assert "side_jie,side_pin" in group_links
    assert "千锋聚灵剑残页" in cards_tsv
    assert "358999" in cards_tsv

    searched = search_fanxiu_lingjie_feature_cards(query="侧词条一", export_root=export_root)
    assert searched["total"] == 1
    assert searched["items"][0]["gongfa_id"] == "306101"
    assert searched["items"][0]["name"] == "千锋聚灵剑"

    card = get_fanxiu_lingjie_feature_card("306101", export_root=export_root)
    assert card["main_features"][0]["expanded_groups"][0]["target_kinds"] == ["main_pin"]


def test_fanxiu_gongfa_feature_probe_links_lingjie_luaconfig(tmp_path):
    export_root = tmp_path / "exports"
    lingjie_dir = export_root / "parsed_configs" / "Lingjie-GongfaJie"
    item_dir = export_root / "parsed_configs" / "Item"
    config_dir = (
        export_root
        / "by_source"
        / "lscripts"
        / "gamesystem"
        / "game"
        / "luaconfig_demo"
        / "text_assets"
    )
    lingjie_dir.mkdir(parents=True)
    item_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (lingjie_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 31650102,
                    "id": 31650102,
                    "gid": 316501,
                    "jie": 2,
                    "name": "2重",
                    "feature": "35760101",
                    "consume": "Item|3020501_1",
                    "describe": "二重：仙书玄鸟<color=#864c00>【玄鸟】</color>",
                },
                {
                    "_row_key": 31650103,
                    "id": 31650103,
                    "gid": 316501,
                    "jie": 3,
                    "name": "3重",
                    "feature": "99999999",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (item_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 3020501,
                    "id": 3020501,
                    "name": "赤书玄鸟卷",
                    "name_plain": "赤书玄鸟卷",
                    "icon": "icon2_skill_ljst_7601",
                    "quality": 7,
                    "effectValue": "316501",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    track_value = json.dumps(
        {
            "ParentName": "AttackTrack",
            "Name": "特效通道",
            "ClipDataList": [
                {
                    "ClipType": 2,
                    "Start_Frame": 15,
                    "args": {"res_Name": "skill/eff_test", "Sound_Id": 201010001},
                }
            ],
            "FrameCount": 60,
            "TotalTime": 2.0,
            "TracType": 2,
        },
        ensure_ascii=False,
    )
    attack_track = json.dumps(
        [json.dumps({"TrackName": "特效通道", "TrackValue": track_value}, ensure_ascii=False)],
        ensure_ascii=False,
    )
    (config_dir / "357601014.lua").write_text(
        "local _key2index={q_skillID=1,q_type=2,q_desc=3,q_hurt_events=4,q_keyframe_events=5,"
        "q_track_time=6,q_timeline_displayName=7,q_timeline_attacktrack=8,q_timeline_suffertrack=9,}\n"
        "local _M={\n"
        "[357601014]=setmetatable({"
        '357601014,"主角剑气技能","仙书-彩-01-二级仙鹤",{[1]={1299,100,0,1}},{0,2000,0},'
        f'2299,"TimeLine357601014",{json.dumps(attack_track, ensure_ascii=False)},"[]"'
        "},_o),\n"
        "}\nreturn _M\n",
        encoding="utf-8",
    )

    parsed = parse_fanxiu_generated_lua_config(config_dir / "357601014.lua")
    assert parsed["rows"][0]["q_hurt_events"] == [[1299, 100, 0, 1]]
    assert parsed["rows"][0]["q_timeline_displayName"] == "TimeLine357601014"

    result = build_fanxiu_gongfa_feature_probe(export_root=export_root)
    feature_links = Path(result["files"]["feature_links_tsv"]).read_text(encoding="utf-8-sig")
    families = Path(result["files"]["feature_families_tsv"]).read_text(encoding="utf-8-sig")
    candidates = Path(result["files"]["luaconfig_candidates_tsv"]).read_text(encoding="utf-8-sig")

    assert result["stats"]["feature_count"] == 2
    assert result["stats"]["feature_family_count"] == 1
    assert result["stats"]["direct_match_feature_count"] == 1
    assert result["stats"]["no_luaconfig_feature_count"] == 1
    assert result["stats"]["item_row_count"] == 1
    assert result["stats"]["linked_item_family_count"] == 1
    assert "357601014" in feature_links
    assert "TimeLine357601014" in feature_links
    assert "direct_luaconfig" in families
    assert "99999999" in families
    assert "3020501" in families
    assert "赤书玄鸟卷" in families
    assert "icon2_skill_ljst_7601" in families
    assert "skill/eff_test" in candidates
    assert "201010001" in candidates


def test_fanxiu_special_gongfa_feature_probe_links_runtime_tables(tmp_path):
    export_root = tmp_path / "exports"
    special_dir = export_root / "parsed_configs" / "Special-GongfaJie"
    gongfa_dir = export_root / "parsed_configs" / "Gongfa"
    skill_dir = export_root / "parsed_configs" / "GongfaSkill"
    star_dir = export_root / "parsed_configs" / "GongfaStar"
    upgrade_dir = export_root / "parsed_configs" / "GongfaUpgrade"
    faze_dir = export_root / "parsed_configs" / "FazeEffectResource"
    faze_resource_dir = export_root / "parsed_configs" / "FazeResource"
    item_dir = export_root / "parsed_configs" / "Item"
    config_dir = (
        export_root
        / "by_source"
        / "lscripts"
        / "gamesystem"
        / "game"
        / "luaconfig_demo"
        / "text_assets"
    )
    for path in [
        special_dir,
        gongfa_dir,
        skill_dir,
        star_dir,
        upgrade_dir,
        faze_dir,
        faze_resource_dir,
        item_dir,
        config_dir,
    ]:
        path.mkdir(parents=True)
    (special_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 32461302,
                    "id": 32461302,
                    "gid": 324613,
                    "jie": 2,
                    "name_plain": "2重",
                    "feature": "327805000",
                    "consume": "Item|3060031_1",
                    "describe_plain": "二重：【识海万象】增强",
                },
                {
                    "_row_key": 41080101,
                    "id": 41080101,
                    "gid": 410801,
                    "jie": 1,
                    "feature": "763016010",
                    "describe_plain": "一重：法则效果",
                },
                {
                    "_row_key": 38180101,
                    "id": 38180101,
                    "gid": 381801,
                    "jie": 1,
                    "feature": "49000101",
                    "describe_plain": "一重：月桂幽云诀",
                },
                {
                    "_row_key": 45280101,
                    "id": 45280101,
                    "gid": 452801,
                    "jie": 1,
                    "feature": "490014010",
                    "describe_plain": "一重：幻喙织霄",
                },
                {
                    "_row_key": 45980102,
                    "id": 45980102,
                    "gid": 459801,
                    "jie": 2,
                    "feature": "498016011",
                    "describe_plain": "二重：抓土成兵只在文案中提升",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (gongfa_dir / "rows.json").write_text(
        json.dumps(
            [
                {"id": 324613, "name_plain": "识海万象"},
                {"id": 410801, "name_plain": "法则功法"},
                {"id": 381801, "name_plain": "月桂幽云诀"},
                {"id": 452801, "name_plain": "幻喙织霄"},
                {"id": 459801, "name_plain": "抓土成兵"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (skill_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": "327805000_1",
                    "id": "327805000_1",
                    "group": 327805000,
                    "originId": 324613,
                    "skillName_plain": "识海万象",
                },
                {
                    "_row_key": "881006010_1",
                    "id": "881006010_1",
                    "group": 881006010,
                    "originId": 390007,
                    "skillName_plain": "雁天行",
                },
                {
                    "_row_key": "490001600_1",
                    "id": "490001600_1",
                    "group": 490001600,
                    "originId": 459801,
                    "skillName_plain": "抓土成兵",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (star_dir / "rows.json").write_text(
        json.dumps([{"_row_key": 32461301, "id": 32461301, "gid": 324613, "skill": "327805000"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (upgrade_dir / "rows.json").write_text(
        json.dumps([{"_row_key": 990001, "id": 990001, "gid": 324613, "showSkillGet": 327805000}], ensure_ascii=False),
        encoding="utf-8",
    )
    (faze_dir / "rows.json").write_text(
        json.dumps([{"_row_key": 763016, "id": 763016, "type": 8, "params": "763016010"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (faze_resource_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 490014,
                    "id": 490014,
                    "effects": 49014,
                    "tipStr_plain": "追月灵兔：获得额外属性加成和珍稀丹药",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (item_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 3060031,
                    "id": 3060031,
                    "name_plain": "识海万象残页",
                    "icon": "icon2_skill_ljst_7805",
                    "effectValue": "324613",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (config_dir / "490001011.lua").write_text(
        "local _key2index={q_skillID=1,q_type=2,q_desc=3}\n"
        "local _M={\n"
        '[490001011]=setmetatable({490001011,"技能","月桂幽云诀表现"},_o),\n'
        "}\nreturn _M\n",
        encoding="utf-8",
    )

    result = build_fanxiu_special_gongfa_feature_probe(export_root=export_root)
    feature_links = Path(result["files"]["feature_links_tsv"]).read_text(encoding="utf-8-sig")
    families = Path(result["files"]["feature_families_tsv"]).read_text(encoding="utf-8-sig")
    candidates = Path(result["files"]["luaconfig_candidates_tsv"]).read_text(encoding="utf-8-sig")

    assert result["stats"]["feature_count"] == 5
    assert result["stats"]["skill_exact_feature_count"] == 1
    assert result["stats"]["star_exact_feature_count"] == 1
    assert result["stats"]["upgrade_exact_feature_count"] == 1
    assert result["stats"]["same_gongfa_skill_feature_count"] == 1
    assert result["stats"]["faze_feature_count"] == 1
    assert result["stats"]["faze_resource_feature_count"] == 1
    assert result["stats"]["linked_item_family_count"] == 1
    assert "gongfa_skill_exact" in feature_links
    assert "FazeEffectResource:763016" in feature_links
    assert "FazeResource:490014" in feature_links
    assert "same_gongfa_skill" in feature_links
    assert "GongfaSkill:490001600_1:抓土成兵:gid=459801" in feature_links
    assert "490001011" in feature_links
    assert "识海万象残页" in families
    assert "月桂幽云诀表现" in candidates


def test_fanxiu_gongfa_catalog_links_skills_by_origin_id(tmp_path):
    export_root = tmp_path / "exports"
    gongfa_dir = export_root / "parsed_configs" / "Gongfa"
    skill_dir = export_root / "parsed_configs" / "GongfaSkill"
    pin_dir = export_root / "parsed_configs" / "GongfaPin"
    quality_dir = export_root / "parsed_configs" / "Quality"
    item_dir = export_root / "parsed_configs" / "Item"
    faze_resource_dir = export_root / "parsed_configs" / "FazeResource"
    faze_effect_dir = export_root / "parsed_configs" / "FazeEffectResource"
    ani_effect_dir = export_root / "parsed_configs" / "AniEffect"
    gongfa_jie_dir = export_root / "parsed_configs" / "GongfaJie"
    special_jie_dir = export_root / "parsed_configs" / "Special-GongfaJie"
    activity_dir = export_root / "parsed_configs" / "Activity"
    activity_gift_dir = export_root / "parsed_configs" / "ActivityGift"
    gongfa_dir.mkdir(parents=True)
    skill_dir.mkdir(parents=True)
    pin_dir.mkdir(parents=True)
    quality_dir.mkdir(parents=True)
    item_dir.mkdir(parents=True)
    faze_resource_dir.mkdir(parents=True)
    faze_effect_dir.mkdir(parents=True)
    ani_effect_dir.mkdir(parents=True)
    gongfa_jie_dir.mkdir(parents=True)
    special_jie_dir.mkdir(parents=True)
    activity_dir.mkdir(parents=True)
    activity_gift_dir.mkdir(parents=True)
    (gongfa_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 476701,
                    "id": 476701,
                    "name": "玄魔大法",
                    "name_plain": "玄魔大法",
                    "quality": 416,
                    "skillType": 2,
                    "icon": "icon9_item_0713",
                    "consume": ["Item|3110210_1"],
                    "showCondition": "GongfaJie|476701_1;Item|3110210_1;ActivityPassed|12651300_1;ActivityPassed|12651301_1",
                    "descript_plain": "人界顶级魔道功法",
                    "sort": 10,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (item_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 3110210,
                    "id": 3110210,
                    "name_plain": "玄魔大法",
                    "icon": "icon9_item_0713",
                    "quality": 8,
                    "effectValue": "476701",
                },
                {
                    "_row_key": 3000777,
                    "id": 3000777,
                    "name_plain": "独立进阶卷",
                    "icon": "icon_test",
                    "quality": 6,
                    "effectValue": "777",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pin_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 416,
                    "id": 416,
                    "name_plain": "圣品秘传",
                    "name": "<color=#017077>圣品秘传</color>",
                    "quality": 8,
                    "typeId": 6,
                    "typeName_plain": "通用",
                    "qualityIcon": "mainui_bg_zw_0365",
                    "sort": 7,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (quality_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 8,
                    "id": 8,
                    "name_plain": "彩色品质",
                    "color": "017077",
                    "tab_plain": "彩",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (faze_resource_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 10130001,
                    "id": 10130001,
                    "name_plain": "秘术·玄魔大法",
                    "headName_plain": "秘术·玄魔大法",
                    "effects": 1013001,
                    "lastGrade": 10130000,
                    "showCondition": "CL|999",
                    "sort": 10130001,
                    "tipStr_plain": "1265|玄魔大法：真元自然恢复速度提升;1254|玄魔大法：恢复自身命魂",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (faze_effect_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 1013001,
                    "id": 1013001,
                    "type": 804,
                    "params": "demo_param",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (ani_effect_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 1265,
                    "reasonId": 1265,
                    "baseId": [70000, 70001],
                    "effect": "demo_top_effect",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (skill_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": "327501300_1",
                    "id": "327501300_1",
                    "originId": 476701,
                    "name_plain": "玄魔大法",
                    "skillName_plain": "玄魔大法",
                    "quality": 8,
                    "pin": 1,
                    "group": 327501300,
                    "type": 24,
                    "subType": 1,
                    "icon": "skill2_yrzx_1015",
                    "describe_plain": "十阶效果：【先天魔功】\n星海之力+12%\n\n【血魂大法】\n恢复自身2%命魂",
                },
                {
                    "_row_key": "orphan",
                    "id": "orphan",
                    "originId": 999999,
                    "name_plain": "孤立技能",
                },
                {
                    "_row_key": "326402000_1",
                    "id": "326402000_1",
                    "originId": 326402,
                    "name_plain": "天阳之怒",
                    "skillName_plain": "天阳之怒",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (gongfa_jie_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 30531602,
                    "id": 30531602,
                    "gid": 305316,
                    "jie": 2,
                    "feature": "32640202",
                    "describe_plain": "旧表二重效果",
                },
                {
                    "_row_key": 77702,
                    "id": 77702,
                    "gid": 777,
                    "jie": 2,
                    "feature": "77702",
                    "describe_plain": "独立进阶效果",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (special_jie_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 47670101,
                    "id": 47670101,
                    "gid": 476701,
                    "pin": 1,
                    "jie": 1,
                    "fazeId": 10130001,
                    "consume": ["Item|3110210_1"],
                    "describe": "一阶效果：<color=#864c00>【先天魔功】</color>\n<color=#9e1e09>星海之力</color>+<color=#2a4b10>3%</color>\n\n【血魂大法】\n恢复自身<color=#2a4b10>2%</color>命魂",
                    "describe_plain": "一阶效果：【先天魔功】\n星海之力+3%\n\n【血魂大法】\n恢复自身2%命魂",
                },
                {
                    "_row_key": 32640202,
                    "id": 32640202,
                    "gid": 326402,
                    "pin": 1,
                    "jie": 2,
                    "feature": "32640202",
                    "describe_plain": "二重：引动天阳之气",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (activity_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 12651300,
                    "id": 12651300,
                    "name_plain": "镇妖臻宝",
                    "startTime": "ABS|2025_2_16_0_00_05",
                },
                {
                    "_row_key": 12651301,
                    "id": 12651301,
                    "name_plain": "镇妖臻宝",
                    "startTime": "ABS|2025_2_16_0_00_05",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (activity_gift_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "_row_key": 1265130001,
                    "id": 1265130001,
                    "activityId": 12651300,
                    "reward": "Item|3110210_1",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_fanxiu_gongfa_catalog(export_root=export_root)
    catalog = json.loads(Path(result["files"]["catalog"]).read_text(encoding="utf-8"))

    assert catalog["schema_version"] == 5
    assert result["stats"]["gongfa_count"] == 1
    assert result["stats"]["item_count"] == 2
    assert result["stats"]["linked_skill_count"] == 1
    assert result["stats"]["unmatched_skill_count"] == 2
    assert catalog["cards"][0]["name"] == "玄魔大法"
    assert catalog["cards"][0]["quality_name"] == "圣品秘传"
    assert catalog["cards"][0]["quality_type_name"] == "通用"
    assert catalog["cards"][0]["skill_type_name"] == "神通"
    assert catalog["cards"][0]["consume_items"][0]["name"] == "玄魔大法"
    assert catalog["cards"][0]["show_condition_items"][0]["icon"] == "icon9_item_0713"
    assert catalog["cards"][0]["skills"][0]["row_key"] == "327501300_1"
    assert catalog["cards"][0]["skills"][0]["quality_name"] == "彩色品质"
    assert catalog["cards"][0]["skills"][0]["type_name"] == "秘传"
    assert catalog["cards"][0]["skills"][0]["sub_type_name"] == "剑修"
    assert "星海之力" in catalog["cards"][0]["skills"][0]["describe"]
    assert catalog["cards"][0]["skills"][0]["describe_sections"][0]["title"] == "十阶效果：【先天魔功】"
    assert catalog["cards"][0]["skills"][0]["describe_sections"][1]["title"] == "【血魂大法】"
    assert catalog["cards"][0]["progression_counts"]["special_jie"] == 1
    assert catalog["cards"][0]["progression"]["special_jie"][0]["jie"] == 1
    assert "<color=#2a4b10>3%</color>" in catalog["cards"][0]["progression"]["special_jie"][0]["describe_rich"]
    assert catalog["cards"][0]["progression"]["special_jie"][0]["describe_sections"][0]["title"] == "一阶效果：【先天魔功】"
    assert catalog["cards"][0]["progression"]["special_jie"][0]["describe_sections"][1]["title"] == "【血魂大法】"
    assert catalog["cards"][0]["progression"]["special_jie"][0]["consume_items"][0]["id"] == 3110210
    assert catalog["cards"][0]["progression"]["special_jie"][0]["faze_resource"]["name"] == "秘术·玄魔大法"
    assert catalog["cards"][0]["progression"]["special_jie"][0]["faze_resource"]["last_grade"] == 10130000
    assert catalog["cards"][0]["progression"]["special_jie"][0]["faze_resource"]["show_condition"] == "CL|999"
    assert catalog["cards"][0]["progression"]["special_jie"][0]["faze_resource"]["tips"][0]["code"] == "1265"
    assert catalog["cards"][0]["progression"]["special_jie"][0]["faze_resource"]["effect_resource"]["type"] == 804
    assert catalog["cards"][0]["first_time_hint"]["date"] == "2025-02-16"
    assert catalog["cards"][0]["first_time_hint"]["activity_name"] == "镇妖臻宝"
    assert catalog["cards"][0]["first_time_hint"]["merged_count"] == 2
    assert catalog["cards"][0]["first_time_hint"]["activity_ids"] == ["12651300", "12651301"]
    assert "327501300_1" in Path(result["files"]["skills_tsv"]).read_text(encoding="utf-8-sig")
    assert "special_jie" in Path(result["files"]["progression_tsv"]).read_text(encoding="utf-8-sig")
    assert "玄魔大法：恢复自身命魂" in Path(result["files"]["progression_tsv"]).read_text(encoding="utf-8-sig")
    assert "<color=#2a4b10>3%</color>" in Path(result["files"]["progression_tsv"]).read_text(encoding="utf-8-sig")
    assert "10130000" in Path(result["files"]["progression_tsv"]).read_text(encoding="utf-8-sig")
    assert "demo_param" in Path(result["files"]["progression_tsv"]).read_text(encoding="utf-8-sig")
    assert result["stats"]["faze_effect_resource_count"] == 1
    assert result["stats"]["ani_effect_count"] == 1
    assert result["stats"]["faze_effect_type_count"] == 1
    assert result["stats"]["faze_tip_code_count"] == 2
    assert result["stats"]["faze_tip_code_with_ani_effect_count"] == 1
    assert result["stats"]["linked_faze_effect_resource_count"] == 1
    assert result["stats"]["linked_faze_effect_progression_count"] == 1
    assert "秘术·玄魔大法" in Path(result["files"]["faze_effect_type_summary_tsv"]).read_text(encoding="utf-8-sig")
    assert "1265" in Path(result["files"]["faze_tip_code_summary_tsv"]).read_text(encoding="utf-8-sig")
    assert "demo_top_effect" in Path(result["files"]["faze_tip_code_summary_tsv"]).read_text(encoding="utf-8-sig")
    assert result["stats"]["progression_alias_count"] == 1
    assert result["stats"]["source_only_progression_count"] == 1
    assert result["stats"]["unresolved_progression_alias_count"] == 0
    target_alias = next(row for row in catalog["progression_aliases"] if row["target_gid"])
    assert target_alias["source_gid"] == 305316
    assert target_alias["target_gid"] == 326402
    progression_aliases = Path(result["files"]["progression_aliases_tsv"]).read_text(encoding="utf-8-sig")
    assert "天阳之怒" in progression_aliases
    assert "source_only" in progression_aliases
    assert "独立进阶卷" in progression_aliases

    searched = search_fanxiu_gongfa_cards(query="先天魔功", export_root=export_root)
    assert searched["total"] == 1
    assert searched["items"][0]["id"] == 476701
    assert searched["items"][0]["quality_name"] == "圣品秘传"
    assert searched["items"][0]["skill_type_name"] == "神通"
    assert searched["items"][0]["skill_type_names"] == ["秘传"]
    assert searched["items"][0]["terms"] == ["先天魔功", "血魂大法"]
    assert searched["items"][0]["first_time_hint"]["date"] == "2025-02-16"
    assert searched["quality_options"][0]["label"] == "圣品秘传"
    assert searched["quality_options"][0]["count"] == 1
    assert searched["quality_grade_options"] == [
        {"value": "圣品", "label": "圣品", "rich_label": "<color=#017077>圣品</color>", "color": "#017077", "count": 1}
    ]
    assert searched["quality_family_options"] == [
        {"value": "秘传", "label": "秘传", "rich_label": "<color=#017077>秘传</color>", "color": "#017077", "count": 1}
    ]
    assert {item["label"]: item["count"] for item in searched["skill_type_options"]} == {"神通": 1, "秘传": 1}

    filtered = search_fanxiu_gongfa_cards(quality_name="圣品秘传", export_root=export_root)
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == 476701
    assert search_fanxiu_gongfa_cards(quality_name="上品功法", export_root=export_root)["total"] == 0
    assert search_fanxiu_gongfa_cards(quality_grade_name="圣品", export_root=export_root)["total"] == 1
    assert search_fanxiu_gongfa_cards(quality_family_name="秘传", export_root=export_root)["total"] == 1
    assert search_fanxiu_gongfa_cards(quality_family_name="异能", export_root=export_root)["total"] == 0
    assert search_fanxiu_gongfa_cards(skill_type_name="秘传", export_root=export_root)["total"] == 1
    assert search_fanxiu_gongfa_cards(skill_type_name="心法", export_root=export_root)["total"] == 0

    detail = get_fanxiu_gongfa_card(476701, export_root=export_root)
    assert detail["card"]["name"] == "玄魔大法"
    assert detail["card"]["quality_name"] == "圣品秘传"
    assert detail["card"]["skills"][0]["origin_id"] == 476701
    assert detail["card"]["time_hints"][0]["source"] == "Gongfa.showCondition"


def test_fanxiu_lua_logic_index_extracts_config_refs_and_functions(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    text_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "gongfanew_demo" / "text_assets"
    text_dir.mkdir(parents=True)
    (text_dir / "GongFaNewData.lua").write_text(
        'package.loaded["GameSystem.Game.GongFaNew.Model.GongFaNewData"]=_M\n'
        "function _M.GetItemTime(self)\n"
        "local cfg=DBMgr.Inst_get():GetConfigTableById(ConfigName.Item_Item,3110210)\n"
        "local quality=DBMgr.Inst_get():GetConfigTableById(ConfigName.Quality_Quality,cfg.quality)\n"
        'local IconItem=require"GameSystem.Game.Item.IconItem"\n'
        "return cfg,quality,IconItem\n"
        "end\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    result = build_fanxiu_lua_logic_index()
    files_text = Path(result["files"]["lua_files_tsv"]).read_text(encoding="utf-8-sig")
    refs_text = Path(result["files"]["config_refs_tsv"]).read_text(encoding="utf-8-sig")
    functions_text = Path(result["files"]["functions_tsv"]).read_text(encoding="utf-8-sig")

    assert result["stats"]["lua_file_count"] == 1
    assert result["stats"]["config_name_count"] == 2
    assert "GongFaNewData.lua" in files_text
    assert "GameSystem.Game.Item.IconItem" in files_text
    assert "Item_Item" in refs_text
    assert "Quality_Quality" in refs_text
    assert "GetItemTime" in functions_text


def test_fanxiu_lua_packet_index_extracts_message_ids_and_fields(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    text_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "message_demo" / "text_assets"
    text_dir.mkdir(parents=True)
    (text_dir / "SM_FazeEffect.lua").write_text(
        'local ClientResult=require"GameSystem.Game.Message.core.model.ClientResult"\n'
        'package.loaded["GameSystem.Game.Message.module.player.faze.packet.SM_FazeEffect"]=_M\n'
        "_M=class(ClientResult,_M)\n"
        "function _M.reading(self)\n"
        "self.fazeId=self:readInt()\n"
        "self.effectType=self:readInt()\n"
        "self.num=self:readInt()\n"
        "self.reason=self:readInt()\n"
        "return true\n"
        "end\n"
        "function _M.getId(self)\n"
        "return 34034\n"
        "end\n"
        "function _M.getName(self)\n"
        'return"SM_FazeEffect"\n'
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SM_UpFazeLevel.lua").write_text(
        'package.loaded["GameSystem.Game.Message.module.player.faze.packet.SM_UpFazeLevel"]=_M\n'
        "_M=class(ClientResult,_M)\n"
        "function _M.reading(self)\n"
        'local FazeInfoVO=require"GameSystem.Game.Message.module.player.faze.packet.FazeInfoVO"\n'
        "self.fazeInfoVO=_AS_(self:readBean(typeof(FazeInfoVO)),FazeInfoVO)\n"
        "return true\n"
        "end\n"
        "function _M.getId(self)\n"
        "return 34036\n"
        "end\n"
        "function _M.getName(self)\n"
        'return"SM_UpFazeLevel"\n'
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "CM_FazePutUp.lua").write_text(
        'package.loaded["GameSystem.Game.Message.module.player.faze.packet.CM_FazePutUp"]=_M\n'
        "_M=class(BaseMessage,_M)\n"
        "function _M.reading(self)\n"
        "self:readMessageList2List(self.putUpList)\n"
        "return true\n"
        "end\n"
        "function _M.getId(self)\n"
        "return 34006\n"
        "end\n"
        "function _M.getName(self)\n"
        'return"CM_FazePutUp"\n'
        "end\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))

    result = build_fanxiu_lua_packet_index()
    packets_text = Path(result["files"]["packets_tsv"]).read_text(encoding="utf-8-sig")
    fields_text = Path(result["files"]["packet_fields_tsv"]).read_text(encoding="utf-8-sig")

    assert result["stats"]["packet_count"] == 3
    assert result["stats"]["message_id_count"] == 3
    assert result["stats"]["faze_packet_count"] == 3
    assert result["stats"]["direction_counts"]["server_to_client"] == 2
    assert "34034\tSM_FazeEffect" in packets_text
    assert "34034\tSM_FazeEffect\t1\tfazeId\tInt" in fields_text
    assert "34034\tSM_FazeEffect\t4\treason\tInt" in fields_text
    assert "34036\tSM_UpFazeLevel\t1\tfazeInfoVO\tBean\tFazeInfoVO" in fields_text
    assert "34006\tCM_FazePutUp\t1\tputUpList\tMessageList2List" in fields_text
    assert "fazeInfoVO:Bean<FazeInfoVO>" in Path(result["files"]["faze_packets_tsv"]).read_text(
        encoding="utf-8-sig"
    )


def test_fanxiu_lingjie_gongfa_runtime_report_links_configs_packets_and_callsites(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    text_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "gongfahomemake_demo" / "text_assets"
    packet_dir = export_root / "parsed_configs" / "lua_packet_index"
    text_dir.mkdir(parents=True)
    packet_dir.mkdir(parents=True)
    (text_dir / "localization.lua").write_text(
        "local _M={\n"
        "['GongFa_Tip_19']='<color=#2a4b10>(新)%s </color>',\n"
        "['GongFa_Tip_20']='<color=#322722>%s: </color>',\n"
        "['GongFa_Tip_21']='<color=#FFFFFF>%s </color>',\n"
        "['GongFa_Tip_22']='<color=#8de349>+%s</color>',\n"
        "['GongFa_LingJie_100']='<color=#%s>%s:</color><color=#%s>%s</color>',\n"
        "['GongFa_LingJie_101']='<href=67|%s_%s><color=#9e1e09>(未激活)</color></href><color=#%s>%s:</color><color=#%s>%s</color>',\n"
        "['GongFa_LingJie_102']='<href=67|%s_%s><color=#9e1e09>(未激活)</color></href><color=#%s>%s</color>',\n"
        "['GongFa_LingJie_106']='<color=#%s>(悟境):</color><color=#%s>%s</color>',\n"
        "['GongFa_LingJie_131']='<color=#%s></color><color=#%s>%s</color>',\n"
        "}\n",
        encoding="utf-8",
    )
    (text_dir / "GongfahomemakeData.lua").write_text(
        "function _M.GetLingjieGongfaJieCfgEx(self)\n"
        "local cfg=DBMgr.Inst_get():GetConfigTable(ConfigName.LingjieGongfa_LingjieGongfaJie)\n"
        "return cfg\n"
        "end\n"
        "function _M.GetMainFeatureCfgById(self,id)\n"
        "return DBMgr.Inst_get():GetConfigTableById(ConfigName.LingjieGongfa_FeatureBase,id)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongfahomemakeNetLogic.lua").write_text(
        "function _M.CM_GongFaHomeMakeCombineFun(self,mainId,assist1)\n"
        "local CM_GongFaHomeMakeCombine=SocketManager.Inst_get():GetMessageFromPools(_CM_GongFaHomeMakeCombine)\n"
        "CM_GongFaHomeMakeCombine.mainId=mainId\n"
        "CM_GongFaHomeMakeCombine.assist1=assist1\n"
        "SocketManager.Inst_get():F_SendMsg(CM_GongFaHomeMakeCombine)\n"
        "end\n"
        "function _M.SM_GongFaHomeMakeCombineFun(msg)\n"
        "GongfahomemakeMgr.Inst_get().Model:GongFaHomeMakeCombine(msg)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "DemoView.lua").write_text(
        "function _M.OnClick(self)\n"
        "GongfahomemakeMgr.Inst_get().NetLogic:CM_GongFaHomeMakeCombineFun(1,2)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "CreateSkillCommonVO.lua").write_text(
        "function _M.reading(self)\n"
        "self.id=self:readLong()\n"
        "self:readMessageMap2Dic(self.effectMap)\n"
        "end\n"
        "function _M.writing(self)\n"
        "self:writeLong(self.id)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaBattleCustomView.lua").write_text(
        "function _M.EquipSkill(self,id,gongFaHomeMakeVO)\n"
        "local makeId=gongFaHomeMakeVO.skillCommonVO.id\n"
        "return makeId\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SkillConfig.lua").write_text(
        "function _M.GetSkillExParams(skillId)\n"
        "local exParamsCfg=DBMgr.Inst_get():GetConfigTable(ConfigName.Skill_SkillExParams)\n"
        "return exParamsCfg[skillId]\n"
        "end\n"
        "function _M.GetTimelineIdBySkillId(skillId,sex,stage,star)\n"
        "local key=\"timelineId\"\n"
        "if star==1 then key=\"jian_timelineId\" end\n"
        "return key\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SkillBase.lua").write_text(
        "function _M._init_(self,skillId,entityView)\n"
        "self.hurtFrameVo=HurtPool.Inst_get():CreateHurtFrameVO(skillId)\n"
        "self.trajectoryCachedHurtVo=HurtPool.Inst_get():CreateHurtFrameVO()\n"
        "end\n"
        "function _M.UpdateTimelineData(self)\n"
        "self.Cfg_Hurts=baseConfig.q_hurt_events\n"
        "local exParamCfg=SkillConfig.GetSkillExParams(self.timeline_id)\n"
        "if exParamCfg and exParamCfg.channel and exParamCfg.channel~=\"\"then\n"
        "self.real_section_dmg=true\n"
        "end\n"
        "end\n"
        "function _M.SetSM_FightResult(self,msg)\n"
        "if self.real_section_dmg then self.hurt_index=1 end\n"
        "for _,hurt_event in ipairs(self.Cfg_Hurts)do\n"
        "local percent=hurt_event[2]or 100\n"
        "for _,v in Cipairs(msg.results)do\n"
        "local resultVo=v\n"
        "local hurtData=HurtPool.Inst_get():CreateHurtData()\n"
        "local damage_num=Mathf.Floor(resultVo.damage)*percent*0.01\n"
        "local damage_view=Mathf.Floor(resultVo.damageView)*percent*0.01\n"
        "local recover_num=Mathf.Floor(resultVo.recoverHp)*percent*0.01\n"
        "local damage_reflect=Mathf.Floor(resultVo.damageReflect)*percent*0.01\n"
        "local mpDamage_num=Mathf.Floor(resultVo.mpAddDamage)*percent*0.01\n"
        "local mpDamage_view=Mathf.Floor(resultVo.mpAddDamageView)*percent*0.01\n"
        "local mpDamageAbsorb_num=Mathf.Floor(resultVo.mpDamageAbsorb)*percent*0.01\n"
        "local key=resultVo.targetId:ToString()\n"
        "self.temp_cur_damage[key]=damage_num+mpDamage_num\n"
        "self.temp_cur_recover[key]=recover_num\n"
        "hurtData:SetData(entityId,resultVo.targetId,resultVo.fightEffect:ToNum(),damage_view,damage_reflect,mpDamage_view,recover_num,0,0,\n"
        "self.temp_cur_damage[key],self.temp_cur_recover[key],mpDamageAbsorb_num,0,false,self.entityView.Entity.V_EntityType,self.skillId)\n"
        "end\n"
        "self.hurtFrameVo:Add4HurtDataListDic(time_ms,list)\n"
        "end\n"
        "end\n"
        "function _M.Start(self,targetId,tParam,fun_skillStart,fun_castFinish,fun_skillFinish,isPassiveSkill)\n"
        "if tParam and tParam.stage then self:UpdateTimelineData(tParam.stage) end\n"
        "if not self.timeline_id then self:Stop() return end\n"
        "self:SetStageState(SkillDefine.Stage.Before)\n"
        "if fun_skillStart then fun_skillStart(self.skillId) end\n"
        "self.isRunning=true\n"
        "self.tParam=tParam\n"
        "self:PlaySkillTimeline(targetId,self.Cfg_TotalTime)\n"
        "self.updateStart=true\n"
        "end\n"
        "function _M.PlaySkillTimeline(self,targetId,totalTime)\n"
        "self.Cfg_CastTotal=totalTime*0.001\n"
        "local skillCombineData=SkillConfigMgr.GetInstance():GetTimeLineAllData(self.timeline_id)\n"
        "if self.entityView.Entity:IsUser()and not self.real_section_dmg then\n"
        "PresentationMgr.Inst_get():PlayBattleSkillSuffer(self.skillId,0,self.tParam,self,skillCombineData)\n"
        "end\n"
        "PresentationMgr.Inst_get():PlayBattleSkill(self.skillId,0,self.tParam,self,skillCombineData)\n"
        "end\n"
        "function _M.Update4Hurt(self,timer,hurtCount,hurtDuration)\n"
        "if hurtCount and hurtDuration then self.hurtFrameVo:CheckMultiHurt(timer,hurtCount,hurtDuration) else self.hurtFrameVo:CheckHurt(timer) end\n"
        "end\n"
        "function _M.Stop(self)\n"
        "self:StopSkillTimeline()\n"
        "self:ClearHurtFrameVO()\n"
        "end\n"
        "function _M.StopSkillTimeline(self)\n"
        "PresentationMgr.Inst_get():AbortSkillByUseSkillPlayerId(self.entityView.Entity.V_ID,self.entityView.Entity.V_ID,self.skillId)\n"
        "end\n"
        "function _M.ClearHurtFrameVO(self)\n"
        "self.hurtFrameVo:Clear4HurtDataListDic()\n"
        "end\n"
        "function _M.IsInSkillCastArea(self,targetId,center_type,center_offset_x,center_offset_y,center_offset_z,scope_type,scope_param1,scope_param2)\n"
        "if center_type==self.DamageCenterType.TARGET then return true end\n"
        "if scope_type==SkillDefine.ScopeType.Circle then return true end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "FightResultVO.lua").write_text(
        "function _M.reading(self)\n"
        "self.targetId=self:readLong()\n"
        "self.fightEffect=self:readLong()\n"
        "self.damageView=self:readDouble()\n"
        "self.recoverHp=self:readDouble()\n"
        "return true\n"
        "end\n"
        "function _M.writing(self)\n"
        "self:writeDouble(self.damageView)\n"
        "self:writeDouble(self.recoverHp)\n"
        "return true\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SM_FightResult.lua").write_text(
        "function _M.reading(self)\n"
        "self.casterId=self:readLong()\n"
        "self:readMessageList2List(self.results)\n"
        "return true\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "FightNetLogic.lua").write_text(
        "function _M.LuaFightNetLogic(self)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightResult:getId(),typeof(_SM_FightResult),self.SM_FightResultFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_UnitHpUpdate:getId(),typeof(_SM_UnitHpUpdate),self.SM_UnitHpUpdateFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_UnitMpUpdate:getId(),typeof(_SM_UnitMpUpdate),self.SM_UnitMpUpdateFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_HpChange:getId(),typeof(_SM_HpChange),self.SM_HpChangeFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_MpChange:getId(),typeof(_SM_MpChange),self.SM_MpChangeFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FixDamage:getId(),typeof(_SM_FixDamage),self.SM_FixDamageFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_ShadowHpChange:getId(),typeof(_SM_ShadowHpChange),self.SM_ShadowHpChangeFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_ShadowInfo:getId(),typeof(_SM_ShadowInfo),self.SM_ShadowInfoFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_UnitMaxHpUpdate:getId(),typeof(_SM_UnitMaxHpUpdate),self.SM_UnitMaxHpUpdateFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_Revive:getId(),typeof(_SM_Revive),self.SM_ReviveFun)\n"
        "_MessagePool.Inst_get():F_Register(_CM_FightByTarget:getId(),typeof(_CM_FightByTarget))\n"
        "_MessagePool.Inst_get():F_Register(_CM_FightByTargets:getId(),typeof(_CM_FightByTargets))\n"
        "_MessagePool.Inst_get():F_Register(_CM_FightByDir:getId(),typeof(_CM_FightByDir))\n"
        "_MessagePool.Inst_get():F_Register(_CM_FightByPosition:getId(),typeof(_CM_FightByPosition))\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightCast:getId(),typeof(_SM_FightCast),self.SM_FightCastFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_QiChange:getId(),typeof(_SM_QiChange),self.SM_QiChangeFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightFail:getId(),typeof(_SM_FightFail),self.SM_FightFailFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightInterrupt:getId(),typeof(_SM_FightInterrupt),self.SM_FightInterruptFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_RestrictStatus:getId(),typeof(_SM_RestrictStatus),self.SM_RestrictStatusFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightTimeLine:getId(),typeof(_SM_FightTimeLine),self.SM_FightTimeLineFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_SkillEffect:getId(),typeof(_SM_SkillEffect),self.SM_SkillEffectFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_UpdateCd:getId(),typeof(_SM_UpdateCd),self.SM_UpdateCdFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_UpdateSelect:getId(),typeof(_SM_UpdateSelect),self.SM_UpdateSelect)\n"
        "_MessagePool.Inst_get():F_Register(_SM_SyncUnit:getId(),typeof(_SM_SyncUnit),self.SM_SyncUnitFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_TestAdjustDirect:getId(),typeof(_SM_TestAdjustDirect),self.SM_TestAdjustDirectFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_TestShape:getId(),typeof(_SM_TestShape),self.SM_TestShapeFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightChannel:getId(),typeof(_SM_FightChannel),self.SM_FightChannelFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_UnitState:getId(),typeof(_SM_UnitState),self.SM_UnitStateFun)\n"
        "end\n"
        "function _M.CM_FightBySkill(skillInfo,casterId,targetId,dir_euler,pos,movePos,target_move_dis,break_skill_move)\n"
        "if FightMgr.Inst_get():ReleaseSkillExecute(skillInfo.skillId,casterId,targetId,dir_euler,pos,movePos)then\n"
        "return _M.SendFightMessage(skillInfo,casterView,targetId,dir_euler,pos,movePos,break_skill_move)\n"
        "end\n"
        "end\n"
        "function _M.SendFightMessage(skillInfo,casterView,targetId,dir_euler,pos,movePos,break_skill_move)\n"
        "_M.CM_FightByTarget(skillInfo.skillId,casterView.Entity.V_ID,targetId,movePos,break_skill_move)\n"
        "_M.CM_FightByDir(skillInfo.skillId,casterView.Entity.V_ID,dir_euler,movePos,break_skill_move)\n"
        "_M.CM_FightByPosition(skillInfo.skillId,casterView.Entity.V_ID,pos,movePos,break_skill_move)\n"
        "end\n"
        "function _M.CM_FightByTarget(skillId,casterId,targetId,movePos,bBreakSkillMove)\n"
        "local cm_FightByTarget=SocketManager.Inst_get():GetMessageFromPools(_CM_FightByTarget)\n"
        "cm_FightByTarget.skillId=skillId\n"
        "cm_FightByTarget.targetId=targetId or 0\n"
        "cm_FightByTarget.casterId=casterId\n"
        "cm_FightByTarget.movePos=movePos\n"
        "cm_FightByTarget.currPos=entityView.Entity.V_GridPosition\n"
        "SocketManager.Inst_get():F_SendMsg(cm_FightByTarget)\n"
        "end\n"
        "function _M.CM_FightByTargets(skillId,casterId,targetIds,dir_euler,pos,movePos,bBreakSkillMove)\n"
        "local _CM_FightByTargets=SocketManager.Inst_get():GetMessageFromPools(_CM_FightByTargets)\n"
        "_CM_FightByTargets.skillId=skillId\n"
        "_CM_FightByTargets.casterId=casterId\n"
        "_CM_FightByTargets.selectTargetIds:Clear()\n"
        "_CM_FightByTargets.selectTargetIds:AddRange(targetIds)\n"
        "_CM_FightByTargets.selectDir=dir_euler\n"
        "_CM_FightByTargets.selectPos=pos\n"
        "_CM_FightByTargets.movePos=movePos\n"
        "_CM_FightByTargets.currPos=entityView.Entity.V_GridPosition\n"
        "SocketManager.Inst_get():F_SendMsg(_CM_FightByTargets)\n"
        "end\n"
        "function _M.CM_FightByDir(skillId,casterId,dir_euler,movePos,bBreakSkillMove)\n"
        "local _CM_FightByDir=SocketManager.Inst_get():GetMessageFromPools(_CM_FightByDir)\n"
        "_CM_FightByDir.skillId=skillId\n"
        "_CM_FightByDir.casterId=casterId\n"
        "_CM_FightByDir.selectDir=dir_euler\n"
        "_CM_FightByDir.movePos=movePos\n"
        "_CM_FightByDir.currPos=entityView.Entity.V_GridPosition\n"
        "SocketManager.Inst_get():F_SendMsg(_CM_FightByDir)\n"
        "end\n"
        "function _M.CM_FightByPosition(skillId,casterId,pos,movePos,bBreakSkillMove)\n"
        "local _CM_FightByPosition=SocketManager.Inst_get():GetMessageFromPools(_CM_FightByPosition)\n"
        "_CM_FightByPosition.skillId=skillId\n"
        "_CM_FightByPosition.casterId=casterId\n"
        "_CM_FightByPosition.selectPos=pos\n"
        "_CM_FightByPosition.movePos=movePos\n"
        "_CM_FightByPosition.currPos=entityView.Entity.V_GridPosition\n"
        "SocketManager.Inst_get():F_SendMsg(_CM_FightByPosition)\n"
        "end\n"
        "function _M.SM_FightCastFun(msg)\n"
        "local fightCastVO=msg.fightCastVO\n"
        "local targetId=fightCastVO.selectTargetId\n"
        "FightMgr.Inst_get():EntityFightCast(msg)\n"
        "end\n"
        "function _M.SM_QiChangeFun(msg)\n"
        "for k,v in Kpairs(msg.changeQiMap)do entityView.Entity:SetProperty(LuaEntityPropertyType.QI,v) end\n"
        "end\n"
        "function _M.SM_FightFailFun(msg)\n"
        "userView.SkillActor:OnSkillFailed(msg.skillId)\n"
        "self:MapPositionReset(msg.currPos)\n"
        "LuaEventMgr.Inst_get():RaiseEvent(FightEventType.SKILL_FAILED,msg.casterId,msg.skillId)\n"
        "end\n"
        "function _M.SM_FightInterruptFun(msg)\n"
        "local runtimeSkill=casterView.SkillActor:GetRuntimeSkill()\n"
        "if runtimeSkill then casterView.SkillActor:StopSkill(true,msg.skillId) end\n"
        "SkillEndActionMgr.Inst_get():RemoveSkillEndAction(msg.casterId)\n"
        "FightMgr.Inst_get():CheckAutoFightReleaseQueue()\n"
        "casterView:SetEntityPosition(targetPos)\n"
        "end\n"
        "function _M.SM_RestrictStatusFun(msg)\n"
        "entityView:AddRestrictCode(msg.restrictCode)\n"
        "end\n"
        "function _M.SM_FightTimeLineFun(msg)\n"
        "if msg.timeLineType==1 then PresentationMgr.Inst_get():PlayElement(buff,msg.timeLine) end\n"
        "local skillCombineData=SkillConfigMgr.GetInstance():GetTimeLineAllData(msg.timeLine)\n"
        "PresentationMgr.Inst_get():PlayBattleSkill(msg.skillId,msg.casterId,nil,nil,skillCombineData)\n"
        "PresentationMgr.Inst_get():PlayBattleSkillSuffer(msg.skillId,msg.targetId,nil,nil,skillCombineData)\n"
        "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.FIGHT_TIMELINE,msg.skillId,msg.buffId)\n"
        "end\n"
        "function _M.SM_SkillEffectFun(msg)\n"
        "local skillEffectVo=msg.skillEffectVO\n"
        "for _,moveSkillVo in Cipairs(skillEffectVo.forceMoveVOs)do\n"
        "local targetId=moveSkillVo.unitId\n"
        "local finalPos=moveSkillVo.finalGrid\n"
        "targetView.in_special_hit=true\n"
        "PresentationMgr.Inst_get():ResetSufferPlayableData(targetView.Entity.V_ID,msg.skillId,TrackType.MoveClipTrack,TimeLineClipType.MoveClip,{move_pos=finalPos})\n"
        "end\n"
        "end\n"
        "function _M.SM_UpdateCdFun(msg)\n"
        "for skillId,cdTime in Kpairs(msg.skill2cd)do userView.SkillActor:RefreshCDTime(skillId,cdTime); userView.SkillActor:ToCDStart(skillId,cdTime) end\n"
        "end\n"
        "function _M.SM_UpdateSelect(msg)\n"
        "entityView.Entity.V_CanSelect=msg.canSelect\n"
        "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.CLICK_ENTITYVIEW,nil)\n"
        "end\n"
        "function _M.SM_SyncUnitFun(msg)\n"
        "RoleMgr.Inst_get():ReviveInfo(msg)\n"
        "SkillMgr.Inst_get():RefreshUserSkillCD(msg)\n"
        "end\n"
        "function _M.SM_TestAdjustDirectFun(msg)\n"
        "PresentationBridge.TestAdjustDirect4Server(msg.pos.x,msg.pos.y,msg.pos.z)\n"
        "end\n"
        "function _M.SM_TestShapeFun(msg)\n"
        "SkillCastBridge.ShowSkillDamageRange(msg.casterId,msg.shapeType,msg.center,msg.dir,msg.width,msg.height,msg.angle,msg.toCheck)\n"
        "end\n"
        "function _M.SM_FightChannelFun(msg)\n"
        "local fightCastVO=msg.fightCastVO\n"
        "local movePos=fightCastVO.movePos\n"
        "casterView:SetEntityPosition(pos)\n"
        "PresentationMgr.Inst_get():ResetAttackPlayableData(casterView.Entity.V_ID,msg.skillId,TrackType.MoveClipTrack,TimeLineClipType.MoveClip,{move_pos=movePos},msg.channellingCount)\n"
        "PresentationMgr.Inst_get():ResetAttackPlayableData(casterView.Entity.V_ID,msg.skillId,TrackType.MoveToTargetTrack,TimeLineClipType.MoveClip,{move_pos=movePos},msg.channellingCount)\n"
        "end\n"
        "function _M.SM_UnitStateFun(msg)\n"
        "playerView:SetServerUnitState(msg.state)\n"
        "end\n"
        "function _M.SM_FightResultFun(msg)\n"
        "fightView.SkillActor:SetSM_FightResult4RunTimeSkill(msg)\n"
        "end\n"
        "function _M.SM_UnitHpUpdateFun(msg)\n"
        "local entityView=EntityMgr.Inst_get():GetEntityFightInBattleView(msg.casterId)\n"
        "if entityView then\n"
        "entityView:UpdateHpChange(msg.id,msg.damage,msg.recoverHp,msg.fightEffect:ToNum(),msg.mpDamageAbsorb,msg.shieldAbsorb)\n"
        "end\n"
        "end\n"
        "function _M.SM_UnitMpUpdateFun(msg)\n"
        "userView:UpdateMpChange(msg.id,msg.recoverMp,msg.changeMp)\n"
        "end\n"
        "function _M.SM_HpChangeFun(msg)\n"
        "for k,v in Kpairs(msg.changeHpMap)do fightView.Entity:SetProperty(LuaEntityPropertyType.HP,v) end\n"
        "for k,v in Kpairs(msg.changeVirtualHpMap)do fightView.Entity:SetProperty(LuaEntityPropertyType.VIRTUAL,v) end\n"
        "end\n"
        "function _M.SM_MpChangeFun(msg)\n"
        "for k,v in Kpairs(msg.changeMpMap)do fightView.Entity:SetProperty(LuaEntityPropertyType.MP,v) end\n"
        "end\n"
        "function _M.SM_FixDamageFun(msg)\n"
        "LastRealHp[msg.unitId]=msg.hp\n"
        "bossView.Entity:RaiseEvent(CommonEventType.HURT_HP_CHANGE,msg.hp,msg.totalDamage,msg.attackTime)\n"
        "bossView.Entity:SetProperty(LuaEntityPropertyType.HP,msg.hp)\n"
        "end\n"
        "function _M.SM_ReviveFun(msg)\n"
        "for k,v in Kpairs(msg.maxHp)do\n"
        "entityView.Entity:SetProperty(LuaEntityPropertyType.HP,v)\n"
        "entityView:Revive()\n"
        "end\n"
        "for k,v in Kpairs(msg.maxMp)do\n"
        "entityView.Entity:SetProperty(LuaEntityPropertyType.MP,v)\n"
        "end\n"
        "if msg.reviveType==PlayerType.ReviveType.BornRevive then\n"
        "RoleMgr.Inst_get():RaiseEvent(PlayerType.UpdateAutoFightBtnStatus,true,nil,true)\n"
        "CameraMgr.Inst_get():ResetUserSceneCamera(false,bornId)\n"
        "end\n"
        "end\n"
        "function _M.SM_ShadowHpChangeFun(msg)\n"
        "FightMgr.Inst_get().Model:SetReplayRecoverHpLock(msg)\n"
        "for k,v in Kpairs(msg.changeHpMap)do fightView.Entity:SetSepcialProperty(GameDefine.Dic_SepcialPropertyKey.SHADOWHP,v) end\n"
        "end\n"
        "function _M.SM_ShadowInfoFun(msg)\n"
        "userView.Entity:SetSepcialProperty(GameDefine.Dic_SepcialPropertyKey.SHADOWHP,msg.currHp)\n"
        "userView.Entity:SetSepcialProperty(GameDefine.Dic_SepcialPropertyKey.SHADOWMAXHP,msg.maxHp)\n"
        "end\n"
        "function _M.SM_UnitMaxHpUpdateFun(msg)\n"
        "entityView.Entity:SetProperty(LuaEntityPropertyType.MAXHP,msg.maxHp)\n"
        "entityView.Entity:SetProperty(LuaEntityPropertyType.HP,msg.currHp)\n"
        "end\n",
        encoding="utf-8",
    )
    role_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "role_demo" / "text_assets"
    role_dir.mkdir(parents=True)
    (role_dir / "RoleNetLogic.lua").write_text(
        "function _M.LuaRoleNetLogic(self)\n"
        "_MessagePool.Inst_get():F_Register(_SM_ChangedPlayerAttribute:getId(),typeof(_SM_ChangedPlayerAttribute),self.SM_ChangedPlayerAttributeFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_FightScore:getId(),typeof(_SM_FightScore),self.SM_FightScoreFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_ModuleFightScore:getId(),typeof(_SM_ModuleFightScore),self.SM_ModuleFightScoreFun)\n"
        "_MessagePool.Inst_get():F_Register(_SM_RealmUpRewardAttr:getId(),typeof(_SM_RealmUpRewardAttr),self.SM_RealmUpRewardAttr)\n"
        "_MessagePool.Inst_get():F_Register(_SM_RoleChangedAttrs:getId(),typeof(_SM_RoleChangedAttrs),self.SM_ChangeAttrsVoFun)\n"
        "end\n"
        "function _M.SM_ChangeAttrsVoFun(msg)\n"
        "if msg.code==0 then\n"
        "if msg.attrs then\n"
        "GameUtil.DealAttrChangeByModule(msg.attrs,nil,false)\n"
        "end\n"
        "end\n"
        "end\n"
        "function _M.SM_RealmUpRewardAttr(msg)\n"
        "GameUtil.DealAttrChangeByModule(msg.attrs,nil,false,realmCfg.realmConfirm==0)\n"
        "end\n"
        "function _M.SM_ChangedPlayerAttributeFun(msg)\n"
        "local entityView=EntityMgr.Inst_get():GetEntityFightInBattleView(msg.unitId)\n"
        "if entityView then\n"
        "for k,v in Kpairs(msg.attributes)do\n"
        "entityView.Entity:SetProperty(k,v)\n"
        "end\n"
        "else\n"
        "if LuaGlobal.IsDebugBuild then Debuger.Log(\"没有协议需要的EntityView \",msg.unitId:ToString()) end\n"
        "end\n"
        "end\n"
        "function _M.SM_FightScoreFun(msg)\n"
        "RoleMgr.Inst_get():UpdateFightScore(msg.score)\n"
        "end\n"
        "function _M.SM_ModuleFightScoreFun(msg)\n"
        "if msg.code==0 then\n"
        "GmMgr.Inst_get():ShowGmPowerView(msg)\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (role_dir / "RoleMgr.lua").write_text(
        "function _M.UpdateFightScore(self,score)\n"
        "if not score then return end\n"
        "if type(score)==\"table\"and score._IS_LUSUOLONG then\n"
        "score=score:ToNum()\n"
        "end\n"
        "local curNum=self.Model:GetProperty(LuaEntityPropertyType.FIGHT_POWER)\n"
        "local newValue=score\n"
        "local changeValue=newValue-curNum\n"
        "self.Model:SetProperty(LuaEntityPropertyType.FIGHT_POWER,newValue)\n"
        "RoleMgr.Inst_get():RaiseEvent(PlayerType.REFRESH_FIGHT_SCORE,curNum,newValue,changeValue)\n"
        "end\n"
        "function _M.ReviveInfo(self,msg)\n"
        "local userView=EntityMgr.Inst_get().UserView\n"
        "if userView==nil or msg==nil then return end\n"
        "if userView:IsInState(StateType.Dead)and msg.currHp>0 then\n"
        "userView:Revive()\n"
        "end\n"
        "userView.Entity:SetChargeLv(msg.chargeLv)\n"
        "userView.Entity:SetProperty(LuaEntityPropertyType.HP,msg.currHp)\n"
        "userView.Entity:SetProperty(LuaEntityPropertyType.MAXHP,msg.maxHp)\n"
        "userView.Entity:SetProperty(LuaEntityPropertyType.MP,msg.currMp)\n"
        "userView.Entity:SetProperty(LuaEntityPropertyType.MAXMP,msg.maxMp)\n"
        "userView.Entity:SetProperty(LuaEntityPropertyType.RUNSPEED,msg.runSpeed)\n"
        "end\n",
        encoding="utf-8",
    )
    common_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "common_demo" / "text_assets"
    common_dir.mkdir(parents=True)
    (common_dir / "GameUtil.lua").write_text(
        "function _M.DealAttrChangeByModule(attributes,exp,showTips,needShowTips)\n"
        "if not attributes then return end\n"
        "local addAttrs=attributes.addAttrs\n"
        "local subAttrs=attributes.subAttrs\n"
        "local finalAttrs=attributes.finalAttrs\n"
        "local userView=EntityMgr.Inst_get().UserView\n"
        "for key,v in Kpairs(finalAttrs)do\n"
        "local finalValue=v\n"
        "if finalValue>0 or finalValue==0 then\n"
        "userView.Entity:SetProperty(key,finalValue)\n"
        "end\n"
        "end\n"
        "for key,v in Kpairs(addAttrs)do\n"
        "local propCfg=DBMgr.Inst_get():GetConfigTableById(ConfigName.Attribute_Attribute,key)\n"
        "if addValue>0 and propCfg and propCfg.showTips==1 then\n"
        "end\n"
        "end\n"
        "TipsMgr.Inst_get():ShowAttrSystemTips2(list2,exp)\n"
        "TipsMgr.Inst_get():ShowAttrSystemTips(list1)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaNewNetLogic.lua").write_text(
        "function _M.LuaGongFaNewNetLogic(self)\n"
        "_MessagePool.Inst_get():F_Register(_CM_GongFaView:getId(),typeof(_CM_GongFaView))\n"
        "_MessagePool.Inst_get():F_Register(_SM_GongFaView:getId(),typeof(_SM_GongFaView),function(msg)\n"
        "self.SM_GongFaViewFun(msg)\n"
        "end)\n"
        "_MessagePool.Inst_get():F_Register(_CM_GongFaUpgrade:getId(),typeof(_CM_GongFaUpgrade))\n"
        "_MessagePool.Inst_get():F_Register(_SM_GongFaUpgrade:getId(),typeof(_SM_GongFaUpgrade),function(msg)\n"
        "self.SM_GongFaUpgradeFun(msg)\n"
        "end)\n"
        "_MessagePool.Inst_get():F_Register(_CM_GongFaLearn:getId(),typeof(_CM_GongFaLearn))\n"
        "_MessagePool.Inst_get():F_Register(_SM_GongFaLearn:getId(),typeof(_SM_GongFaLearn),function(msg)\n"
        "self.SM_GongFaLearnFun(msg)\n"
        "end)\n"
        "_MessagePool.Inst_get():F_Register(_CM_GongFaUpgradeTimes:getId(),typeof(_CM_GongFaUpgradeTimes))\n"
        "_MessagePool.Inst_get():F_Register(_SM_GongFaUpgradeTimes:getId(),typeof(_SM_GongFaUpgradeTimes),function(msg)\n"
        "self.SM_GongFaUpgradeTimesFun(msg)\n"
        "end)\n"
        "end\n"
        "function _M.CM_GongFaView()\n"
        "local CM_GongFaView=SocketManager.Inst_get():GetMessageFromPools(_CM_GongFaView)\n"
        "SocketManager.Inst_get():F_SendMsg(CM_GongFaView)\n"
        "end\n"
        "function _M.SM_GongFaViewFun(msg)\n"
        "if msg.code==0 then\n"
        "GongFaNewMgr.Inst_get().Model:SetGongFaInfo(msg)\n"
        "GongFaNewMgr.Inst_get():AddEventListeners()\n"
        "end\n"
        "end\n"
        "function _M.CM_GongFaUpgrade(type,baseId,times,gids)\n"
        "local CM_GongFaUpgrade=SocketManager.Inst_get():GetMessageFromPools(_CM_GongFaUpgrade)\n"
        "CM_GongFaUpgrade.type=type\n"
        "CM_GongFaUpgrade.baseId=baseId\n"
        "CM_GongFaUpgrade.times=times\n"
        "SocketManager.Inst_get():F_SendMsg(CM_GongFaUpgrade,type)\n"
        "end\n"
        "function _M.SM_GongFaUpgradeFun(msg)\n"
        "GongFaNewMgr.Inst_get().Model:GongFaUpgrade(msg)\n"
        "end\n"
        "function _M.CM_GongFaLearnFun(baseId)\n"
        "local CM_GongFaLearn=SocketManager.Inst_get():GetMessageFromPools(_CM_GongFaLearn)\n"
        "CM_GongFaLearn.baseId=baseId\n"
        "SocketManager.Inst_get():F_SendMsg(CM_GongFaLearn,true)\n"
        "end\n"
        "function _M.SM_GongFaLearnFun(msg)\n"
        "GongFaNewMgr.Inst_get().Model:GongFaLearn(msg)\n"
        "end\n"
        "function _M.CM_GongFaUpgradeTimesFun(self,upgradeList)\n"
        "local CM_GongFaUpgradeTimes=SocketManager.Inst_get():GetMessageFromPools(_CM_GongFaUpgradeTimes)\n"
        "CM_GongFaUpgradeTimes.upgradeList=upgradeList\n"
        "SocketManager.Inst_get():F_SendMsg(CM_GongFaUpgradeTimes)\n"
        "end\n"
        "function _M.SM_GongFaUpgradeTimesFun(msg)\n"
        "GongFaNewMgr.Inst_get().Model:GongFaUpgradeTimes(msg)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaNewModel.lua").write_text(
        "function _M.SetGongFaInfo(self,info)\n"
        "self.GongFaNewData:SetGongFaInfo(info.actives)\n"
        "self.GongFaNewData:SetXinFaInfo(info.xinFaPutUpList)\n"
        "self.GongFaNewData:SetLearnedSkillList(info.skillList,false)\n"
        "self.GongFaNewData:SetGongFaProgram(info.programVOList)\n"
        "FazeMgr.Inst_get().Model:SaveFazePutUpPanelData(info.fazePutUpList)\n"
        "self:RefreshAllRed()\n"
        "self:RefreshNewRed()\n"
        "self:RaiseEvent(GongFaNewType.RefreshGongFaDataEx)\n"
        "end\n"
        "function _M.SetGongFaVo(self,infoList)\n"
        "self.GongFaNewData:SetGongFaVo(infoList)\n"
        "end\n"
        "function _M.GetAllAddAttrTb(self,attr,notIgnoreIndirect)\n"
        "local addAttr={}\n"
        "for k,v in Kpairs(attr)do\n"
        "local attrCfg=DBMgr.Inst_get():GetConfigTableByKeyAndIdWithLog(ConfigName.Attribute_Attribute,\"id\",k)\n"
        "if attrCfg and(not notIgnoreIndirect and attrCfg.group==GameDefine.AttrType.Indirect)then\n"
        "local param={}\n"
        "param.cfg=attrCfg\n"
        "param.num=v\n"
        "table.insert(addAttr,param)\n"
        "end\n"
        "end\n"
        "table.sort(addAttr,rankAttr)\n"
        "return addAttr\n"
        "end\n"
        "function _M.GetLevelAndStarAttr(self,levelAttr,starAttr)\n"
        "local retTb={}\n"
        "for k,v in Kpairs(levelAttr)do retTb[k]=v end\n"
        "for k,v in Kpairs(starAttr)do if retTb[k]then retTb[k]=retTb[k]+v else retTb[k]=v end end\n"
        "return retTb\n"
        "end\n"
        "function _M.GetIngoreSpecialAttrNextAdd(self,attr,nextAttr,isLearn,notCheckCur)\n"
        "local ingoreAttr=self:GetSpecialAttrTypeEx()\n"
        "return self:GetAllAttrNextAdd(attr,nextAttr,isLearn,ingoreAttr,true,notCheckCur)\n"
        "end\n"
        "function _M.GetAllAttrNextAttr(self,attr,nextAttr,isLearn,ingoreAttr)\n"
        "local newIgnore={}\n"
        "for k,v in Kpairs(nextAttr)do if attr[k]>=v then newIgnore[k]=true end end\n"
        "return self:GetAllAttrNextAdd(attr,nextAttr,isLearn,newIgnore,true)\n"
        "end\n"
        "function _M.GetAllAttrNextAdd(self,attr,nextAttr,isLearn,ingoreAttr,notIgnoreIndirect,notCheckCur)\n"
        "local addAttr={}\n"
        "if nextAttr==nil or not isLearn then return self:GetAllAddAttrTb(attr,notIgnoreIndirect) end\n"
        "for k,v in Kpairs(nextAttr)do\n"
        "local attrCfg=DBMgr.Inst_get():GetConfigTableByKeyAndIdWithLog(ConfigName.Attribute_Attribute,\"id\",k)\n"
        "local param={}\n"
        "param.cfg=attrCfg\n"
        "param.num=attr[k]or 0\n"
        "local addNum=v-param.num\n"
        "param.addNum=addNum\n"
        "if param.num==0 then param.isNew=true end\n"
        "table.insert(addAttr,param)\n"
        "end\n"
        "table.sort(addAttr,rankAttr)\n"
        "return addAttr\n"
        "end\n"
        "function _M.GongFaUpgrade(self,msg)\n"
        "if msg.upgradeQuality then\n"
        "self.GongFaNewData:SaveUpgradeQualityData(msg)\n"
        "return\n"
        "end\n"
        "self:UpgradeRefresh(msg)\n"
        "end\n"
        "function _M.UpgradeRefresh(self,msg)\n"
        "self.GongFaNewData:UpdateGongFaVo(msg.gongfa)\n"
        "local exp=nil\n"
        "for _,v in Cipairs(msg.rewardResults)do\n"
        "if v.type==RewardType.EXP then exp=v.amount end\n"
        "end\n"
        "GameUtil.DealAttrChangeByModule(msg.attrs,exp)\n"
        "self:RaiseEvent(GongFaNewType.ChangeGongFa,msg.gongfa,msg.ClientData)\n"
        "end\n"
        "function _M.GongFaLearn(self,msg)\n"
        "self.GongFaNewData:UpdateGongFaVo(msg.gongfa)\n"
        "local exp=nil\n"
        "for _,v in Cipairs(msg.rewardResults)do\n"
        "if v.type==RewardType.EXP then exp=v.amount end\n"
        "end\n"
        "GameUtil.DealAttrChangeByModule(msg.attrs,exp)\n"
        "self:RaiseEvent(GongFaNewType.ChangeGongFa,msg.gongfa,GongFaNewType.UpType.Learn)\n"
        "end\n"
        "function _M.GongFaUpgradeTimes(self,msg)\n"
        "local ChangedAttrsVo=require\"GameSystem.Game.Message.module.common.attribute.packet.ChangedAttrsVo\"\n"
        "local allAttrs=ChangedAttrsVo.new()\n"
        "for k,v in Cipairs(msg.upgradeList)do\n"
        "self.GongFaNewData:UpdateGongFaVo(v.gongfa)\n"
        "for _,rewards in Cipairs(v.rewardResults)do end\n"
        "for key,value in Kpairs(v.attrs.addAttrs)do\n"
        "allAttrs.addAttrs:LuaDic_AddOrSetItem(key,value)\n"
        "end\n"
        "allAttrs.finalAttrs=v.attrs.finalAttrs\n"
        "end\n"
        "GameUtil.DealAttrChangeByModule(allAttrs,LusuoLong.FromNumber(exp))\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaNewData.lua").write_text(
        "function _M.LuaGongFaNewData(self)\n"
        "self.gongFaDic=Dictionary.new()\n"
        "self.gongFaCfg=DBMgr.Inst_get():GetConfigTable(ConfigName.Gongfa_Gongfa)\n"
        "self.gongFaPingCfg=DBMgr.Inst_get():GetConfigTable(ConfigName.Gongfa_GongfaPin)\n"
        "self:SetGongFaDic()\n"
        "end\n"
        "function _M.SetGongFaInfo(self,actives)\n"
        "self.actives=actives\n"
        "end\n"
        "function _M.SetGongFaProgram(self,programVOList)\n"
        "self.programDic:LuaDic_Clear()\n"
        "for k,v in Cipairs(programVOList)do\n"
        "self.programDic:LuaDic_AddOrSetItem(v.id,v)\n"
        "end\n"
        "end\n"
        "function _M.SetXinFaInfo(self,xinFaPutUpList)\n"
        "if self.xinFaPutUpList==nil then\n"
        "local GongFaItemDataList=GongFaNewMgr.Inst_get():GetGongFaBattleItemListByType(GongFaNewType.BattleType.XinFa)\n"
        "end\n"
        "for _,v in Cipairs(xinFaPutUpList)do\n"
        "for _,value in Cipairs(self.xinFaPutUpList)do\n"
        "value.xinFaId=v.xinFaId\n"
        "end\n"
        "end\n"
        "end\n"
        "function _M.SetLearnedSkillList(self,skillList,isUpdate)\n"
        "self.NewLearnedSkill:Add(cfg.id)\n"
        "self.OldLearnedSkill:Add(cfg.id)\n"
        "end\n"
        "function _M.SetGongFaDic(self)\n"
        "for k,v in pairs(self.gongFaCfg)do\n"
        "local gongFaVo=GongFaVo.new(v)\n"
        "self.gongFaDic:LuaDic_AddOrSetItem(v.id,gongFaVo)\n"
        "table.insert(self.tbTypeGongFa[pingCfg.typeId][v.quality],v.id)\n"
        "end\n"
        "end\n"
        "function _M.SetGongFaVo(self,infoList)\n"
        "for k,v in Kpairs(self.gongFaDic)do\n"
        "v:SetVo(nil)\n"
        "end\n"
        "for k,v in Cipairs(infoList)do\n"
        "self:UpdateGongFaVo(v)\n"
        "end\n"
        "end\n"
        "function _M.UpdateGongFaVo(self,data)\n"
        "local gongFaVo=self.gongFaDic:LuaDic_GetItem(data.baseId)\n"
        "if gongFaVo then\n"
        "gongFaVo:SetVo(data)\n"
        "else\n"
        "local gongFaVo=GongFaVo.new(cfg)\n"
        "gongFaVo:SetVo(data)\n"
        "end\n"
        "end\n"
        "function _M.GetGongFaById(self,id,reset)\n"
        "return self.gongFaDic:LuaDic_GetItem(id)\n"
        "end\n"
        "function _M.GetAllGongFa(self)\n"
        "return self.gongFaDic\n"
        "end\n"
        "function _M.GetTypeGongFa(self)\n"
        "return self.tbTypeGongFa\n"
        "end\n"
        "function _M.GetViewAttrListShow(self,curAttr,nextAttr)\n"
        "local showAttrList={}\n"
        "for attr,num in Kpairs(curAttr)do showAttrList[attr]=true end\n"
        "for attr,nextValue in Kpairs(nextAttr)do showAttrList[attr]=true end\n"
        "local viewAttrList={}\n"
        "for attr,_ in Kpairs(showAttrList)do\n"
        "local data={}\n"
        "data.cfg=DBMgr.Inst_get():GetConfigTableByKeyAndIdWithLog(ConfigName.Attribute_Attribute,\"id\",attr)\n"
        "data.num=curAttr[attr]or 0\n"
        "data.nextValue=nextAttr[attr]\n"
        "data.addNum=data.nextValue-data.num\n"
        "data.isNew=data.num==0\n"
        "table.insert(viewAttrList,data)\n"
        "end\n"
        "return viewAttrList\n"
        "end\n"
        "function _M.GetTongXuanDesInfo(self,id,pin)\n"
        "local tbGongFa=self:GetGongfaTongXuanCfgEx()\n"
        "local tbBeforeJie={}\n"
        "local tbNextJie={}\n"
        "for _,v in pairs(tbGongFa[id])do\n"
        "local cfg=v\n"
        "if cfg then\n"
        "if not StringProxy.IsNullOrEmpty(v.mainDescribe)then\n"
        "if cfg.pin>pin then\n"
        "table.insert(tbNextJie,cfg)\n"
        "else\n"
        "table.insert(tbBeforeJie,cfg)\n"
        "end\n"
        "end\n"
        "end\n"
        "end\n"
        "return tbBeforeJie,tbNextJie\n"
        "end\n"
        "function _M.UpdateActiveId(self,msg)\n"
        "if not self.actives then self.actives=Dictionary.new() end\n"
        "self.actives:LuaDic_AddOrSetItem(msg.starId,msg.jie)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaVo.lua").write_text(
        "function _M._init_(self,cfg)\n"
        "self.cfg=cfg\n"
        "self.vo=nil\n"
        "end\n"
        "function _M.SetVo(self,vo)\n"
        "self.vo=vo\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaItemVO.lua").write_text(
        "local SimpleItemVO=require\"GameSystem.Game.Message.module.player.backpack.packet.vo.SimpleItemVO\"\n"
        "_M=class(SimpleItemVO,_M)\n"
        "function _M._init_(self)\n"
        "self.grade=0\n"
        "self.jie=0\n"
        "self.star=0\n"
        "self.pin=0\n"
        "self.tongxuan=0\n"
        "self.quality=0\n"
        "self.totalExp=0\n"
        "self.qualityNum=Dictionary.new()\n"
        "end\n"
        "function _M.reading(self)\n"
        "self.grade=self:readInt()\n"
        "self.jie=self:readInt()\n"
        "self.star=self:readInt()\n"
        "self.pin=self:readInt()\n"
        "self.tongxuan=self:readInt()\n"
        "self.quality=self:readInt()\n"
        "self.totalExp=self:readLong()\n"
        "self:readMessageMap2Dic(self.qualityNum)\n"
        "_M._super_.reading(self)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SimpleItemVO.lua").write_text(
        "function _M._init_(self)\n"
        "self.baseId=0\n"
        "self.id=0\n"
        "self.num=0\n"
        "end\n"
        "function _M.reading(self)\n"
        "self.baseId=self:readInt()\n"
        "self.id=self:readLong()\n"
        "self.num=self:readInt()\n"
        "end\n"
        "function _M.writing(self)\n"
        "self:writeInt(self.baseId)\n"
        "self:writeLong(self.id)\n"
        "self:writeInt(self.num)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaNewMgr.lua").write_text(
        "function _M.FormatAttrNum(self,attrCfg,value)\n"
        "if attrCfg.group==GameDefine.AttrType.Value or attrCfg.group==GameDefine.AttrType.Indirect or attrCfg.group==GameDefine.AttrType.IndirectFormula then\n"
        "return GameUtil.ConvertBigDouble(value)\n"
        "elseif attrCfg.group==GameDefine.AttrType.Ratio or attrCfg.group==GameDefine.AttrType.RatioAttribute then\n"
        "return value*0.01 .. \"%\"\n"
        "end\n"
        "end\n"
        "function _M.GetGongFaAttrListByAttr(self,attr)\n"
        "local ShowAttrList={}\n"
        "for k,v in Kpairs(attr)do table.insert(ShowAttrList,{str=k,value=v}) end\n"
        "table.sort(ShowAttrList,function(a,b)return LuaEntityPropertyType[a.str]<LuaEntityPropertyType[b.str] end)\n"
        "local cfg=DBMgr.Inst_get():GetConfigTable(ConfigName.Attribute_Attribute)\n"
        "return ShowAttrList,cfg\n"
        "end\n"
        "function _M.GetGongFaAttrShow(self,attr,nextAttr)\n"
        "local ShowAttrList={}\n"
        "for k,v in Kpairs(nextAttr)do local addValue=v-attr[k] table.insert(ShowAttrList,{str=k,value=attr[k],addValue=addValue}) end\n"
        "return ShowAttrList\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongfahomemakeMgr.lua").write_text(
        "function _M.GetMainDes(self,pinCfg,starCfg,jieCfg,gongFaId,addActive,notClick,colorType,quality,tongxuan,checkActiveTongXuan)\n"
        "local qualityCfg=DBMgr.Inst_get():GetConfigTableByIdWithLog(ConfigName.Quality_Quality,quality)\n"
        "local starLoParam=starCfg and starCfg.param\n"
        "local jieLoParam=jieCfg and jieCfg.param\n"
        "local array=_M.TabelAddTabel({},starLoParam)\n"
        "array=_M.TabelAddTabel(array,jieLoParam)\n"
        "local desStr=string.format(starCfg.describe,unpack(array))\n"
        "local tongxuanStr=self:GetOneTongXuanMainDesc(tongxuan,gongFaId,colorType)\n"
        "local tongxuanQualityCfg=DBMgr.Inst_get():GetConfigTableByIdWithLog(ConfigName.Quality_Quality,GongFaNewType.TongXuanQuality)\n"
        "local showColor=(tongxuan and tongxuan>0)and tongxuanQualityCfg.color or qualityCfg.color\n"
        "if addActive then\n"
        "local str=\"\"\n"
        "if notClick then\n"
        "str=LuaLocalization.Format(\"GongFa_LingJie_60\",showColor,gongFaVo.cfg.name)\n"
        "else\n"
        "str=LuaLocalization.Format(\"GongFa_LingJie_58\",gongFaId,showColor,gongFaVo.cfg.name)\n"
        "end\n"
        "desStr=desStr..\"\\n\"..str\n"
        "if checkActiveTongXuan then\n"
        "tongxuanStr=LuaLocalization.Format(\"GongFa_LingJie_132\",tongxuanStr)\n"
        "end\n"
        "str=LuaLocalization.Format(\"GongFa_LingJie_128\",gongFaId,tongxuanQualityCfg.color,gongFaVo.cfg.name)\n"
        "str=LuaLocalization.Format(\"GongFa_LingJie_129\",tongxuanQualityCfg.color,gongFaVo.cfg.name)\n"
        "tongxuanStr=string.format(\"\\n\\n%s\\n%s\",tongxuanStr,str)\n"
        "desStr=desStr..tongxuanStr\n"
        "end\n"
        "return PostMgr.Inst_get():StringFormatColorType(desStr,colorType)\n"
        "end\n"
        "function _M.GetOneTongXuanMainDesc(self,tongxuan,originId,colorType)\n"
        "local isShowTongXuan=GongFaNewMgr.Inst_get().Model:CheckGongFaBookTongXuanIsShow(originId,nil,true)\n"
        "local tongxuanCfg=GongFaNewMgr.Inst_get().Model:GetGongfaTongXuanCfgByIdTongXuan(originId,tongxuan)\n"
        "if tongxuanCfg and not StringProxy.IsNullOrEmpty(tongxuanCfg.mainDescribe)then\n"
        "return PostMgr.Inst_get():StringFormatColorType(tongxuanCfg.mainDescribe,colorType)\n"
        "end\n"
        "end\n"
        "function _M.GetOneTongXuanSecDesc(self,tongxuan,originId,colorType)\n"
        "local tongxuanCfg=GongFaNewMgr.Inst_get().Model:GetGongfaTongXuanCfgByIdTongXuan(originId,tongxuan)\n"
        "if tongxuanCfg and not StringProxy.IsNullOrEmpty(tongxuanCfg.secDescribe)then\n"
        "return PostMgr.Inst_get():StringFormatColorType(tongxuanCfg.secDescribe,colorType)\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "DetailPanel.lua").write_text(
        "function _M.InitView(self)\n"
        "self.DescTxt=self:SetComponent(LuaTextGamma,1)\n"
        "end\n"
        "function _M.RefreshData(self,gongFaVo)\n"
        "local addAttr=GongFaNewMgr.Inst_get().Model:GetAllAddAttrTb(gongFaVo.cfg.attr)\n"
        "self.DescTxt:SetText(gongFaVo.cfg.descript)\n"
        "local desH=self.DescTxt:preferredHeight()\n"
        "return addAttr\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaUpLevelView.lua").write_text(
        "function _M.UpdateLevelAttr(self,curLevelCfg,nextLevelCfg,curStarCfg,nextStarCfg)\n"
        "local curLevelStarAttr=GongFaNewMgr.Inst_get().Model:GetLevelAndStarAttr(curLevelCfg.attr,curStarCfg.attr)\n"
        "local nextLevelStarAttr=GongFaNewMgr.Inst_get().Model:GetLevelAndStarAttr(nextLevelCfg.attr,nextStarCfg.attr)\n"
        "local addAttr=GongFaNewMgr.Inst_get().Model:GetIngoreSpecialAttrNextAdd(curLevelStarAttr,nextLevelStarAttr,true,true)\n"
        "return addAttr\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "GongFaAttrItem.lua").write_text(
        "function _M.Refresh(self,data)\n"
        "self.t1=LuaLocalization.Get(\"GongFa_Tip_22\")\n"
        "self.t2=LuaLocalization.Format(\"GongFa_Tip_21\",data.cfg.name)\n"
        "self.t3=LuaLocalization.Format(\"GongFa_Tip_19\",data.cfg.name)\n"
        "self.t4=LuaLocalization.Format(\"GongFa_Tip_20\",data.cfg.name)\n"
        "self.num=GongFaNewMgr.Inst_get():FormatAttrNum(data.cfg,data.num)\n"
        "self.add=GongFaNewMgr.Inst_get():FormatAttrNum(data.cfg,data.addNum)\n"
        "self.addNumTxt:SetText(\"<color=#2A4B10>+1</color>\")\n"
        "self.numTxt:SetColor3(\"322722\")\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "XianShuCreateSkillDetailView.lua").write_text(
        "function _M.InitView(self)\n"
        "self.desTxt=self:SetComponent(LuaTextEx,6)\n"
        "self.mainDesTxt=self:SetComponent(LuaTextGamma,7)\n"
        "end\n"
        "function _M.Refresh(self,gongFaVo,pinCfg,starCfg,jieCfg)\n"
        "local qualityCfg={color=\"9e1e09\"}\n"
        "local desStr=GongfahomemakeMgr.Inst_get():GetMainDes(pinCfg,starCfg,jieCfg,gongFaVo.cfg.id,false,nil,GameDefine.QualityColorType.Bright)\n"
        "self.mainDesTxt:SetText(desStr)\n"
        "local describe=LuaLocalization.Format(\"GongFa_LingJie_100\",qualityCfg.color,pinCfg.name,\"322722\",pinCfg.describe)\n"
        "describe=LuaLocalization.Format(\"GongFa_LingJie_101\",gongFaVo.cfg.id,1,qualityCfg.color,pinCfg.name,\"74746c\",pinCfg.describe)\n"
        "describe=describe..\"\\n\"..LuaLocalization.Format(\"GongFa_LingJie_106\",qualityCfg.color,\"322722\",pinCfg.describe)\n"
        "describe=describe..\"\\n\"..LuaLocalization.Format(\"GongFa_LingJie_102\",gongFaVo.cfg.id,1,\"74746c\",pinCfg.describe)\n"
        "describe=describe..\"\\n\"..LuaLocalization.Format(\"GongFa_LingJie_131\",qualityCfg.color,\"322722\",secDescribe)\n"
        "self.showList:Add({itemType=2,describe=describe,isActive=true})\n"
        "self.desTxt:SetText(v.describe)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "XianShuCreateItem.lua").write_text(
        "function _M.InitView(self)\n"
        "self.desTxt=self:SetComponent(LuaTextEx,1)\n"
        "self.desTipTxt=self:SetComponent(LuaTextGamma,2)\n"
        "end\n"
        "function _M.UpdateItem(self,data)\n"
        "self.desTxt:SetText(data.describe)\n"
        "if data.isActive then\n"
        "self.desTxt:SetColor3(\"322722\")\n"
        "else\n"
        "self.desTxt:SetColor3(\"74746c\")\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "DesItem.lua").write_text(
        "function _M.InitView(self)\n"
        "self.desTxt=self:SetComponent(LuaTextGamma,1)\n"
        "end\n"
        "function _M.UpdateItem(self,data)\n"
        "self.desTxt:SetText(data.desStr)\n"
        "self.desTxt:SetColor3(data.color)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SkillActor.lua").write_text(
        "function _M.ReleaseSkill(self,skillId,targetId,tParam)\n"
        "local runtimeSkill=self:GetRuntimeSkill()\n"
        "if runtimeSkill then self:StopSkill(false,skillId) end\n"
        "self:SetRuntimeSkillId(0)\n"
        "runtimeSkill=self:GetSkill(skillId)\n"
        "if runtimeSkill then\n"
        "self:SetRuntimeSkillId(skillId)\n"
        "runtimeSkill:Start(targetId,tParam,function(id) self:OnStartSkill(id) end,function(id) self:OnStopCast(id) end,function(id) self:OnStopSkill(id) end)\n"
        "end\n"
        "end\n"
        "function _M.ReleasePassiveSkill(self,skillId,fightCastVO)\n"
        "local tParam={cast_dir=fightCastVO.selectDir,target_pos=fightCastVO.selectPos,move_pos=fightCastVO.movePos}\n"
        "skillInfo:Start(fightCastVO.selectTargetId,tParam,nil,function() skillInfo:Stop() end)\n"
        "self.passiveSkills[skillId]=skillInfo\n"
        "end\n"
        "function _M.ReleaseMagicSkill(self,skillId,selectDir,selectPos,movePos,selectTargetId,jie,star)\n"
        "local tParam={cast_dir=selectDir,target_pos=selectPos,move_pos=movePos,stage=jie,star=star}\n"
        "skillInfo:Start(selectTargetId,tParam,nil,function() skillInfo:Stop() end)\n"
        "self.magicSkills[skillId]=skillInfo\n"
        "end\n"
        "function _M.SetSM_FightResult4RunTimeSkill(self,msg)\n"
        "skillInfo:SetSM_FightResult(msg)\n"
        "end\n"
        "function _M.StopSkill(self,isBreak,nextSkillId)\n"
        "local runtimeSkill=self:GetRuntimeSkill()\n"
        "if runtimeSkill then runtimeSkill:Stop(false,nextSkillId) end\n"
        "end\n"
        "function _M.OnStartSkill(self,skillId)\n"
        "SkillEndActionMgr.Inst_get():RemoveSkillEndAction(self.entityFightView.Entity.V_ID)\n"
        "end\n"
        "function _M.OnStopSkill(self,skillId,isNatureToFightIdle,nextSkillId)\n"
        "self:SetRuntimeSkillId(0)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "HurtEvent.lua").write_text(
        "function _M.OnStart(self,currentTime,isTrajectoryHit,hurtFrameVo)\n"
        "local isMiss,hasDamage,hasHealing=hurtFrameVo:GetHurtDataDetail(currentTime,targetView.Entity.V_ID,isTrajectoryHit)\n"
        "if not isMiss then self.skillObj:Update4Hurt(currentTime,args.hurt_multi_count,args.hurt_multi_duration) end\n"
        "isMiss=not self.skillObj:IsInSkillCastArea(targetView.Entity.V_ID,args.damage_center_type,args.damage_center_offset_x,args.damage_center_offset_y,args.damage_center_offset_z,args.damage_scope_type,args.scope_param1,args.scope_param2)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "HurtFrameVo.lua").write_text(
        "function _M.Add4HurtDataListDic(self,time,hurtDataList)\n"
        "self.hurtDataListDic:LuaDic_AddOrSetItem(time,hurtDataList)\n"
        "end\n"
        "function _M.CheckHurt(self,elapseTime,isTrajectoryHurt)\n"
        "self:ExecuteHurtDataList(time,hurtDataList)\n"
        "end\n"
        "function _M.ExecuteHurtDataList(self,time,hurtDataList)\n"
        "for i,v in Cipairs(hurtDataList)do local hurtData=v; hurtData:Execute() end\n"
        "end\n"
        "function _M.CheckMultiHurt(self,elapseTime,hurtCount,hurtDuration)\n"
        "self:SeparateHurtData(time,hurtDataList,hurtCount,hurtDuration)\n"
        "end\n"
        "function _M.SeparateHurtData(self,time,hurtDataList,hurtCount,duration)\n"
        "local separateHurtData=HurtPool.Inst_get():CreateHurtData()\n"
        "separateHurtData:SetData(hurtData.casterId,hurtData.targetId,hurtData.fightEffect,damage_num,reflect_num,mp_damage_num,recover_num,0,(damage_num+mp_damage_num)*j,recover_num*j,0,false,rangeLimit)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "BulletMgr.lua").write_text(
        "function _M.AddBulletToEntity(self,entityId,targetId,targetPosition,bulletId,resName,skillId,timelineId,elementId,clipId,bulletArgs,hurtEvent)\n"
        "local hurtVo=entityView.SkillActor:GetBulletHurtVo(skillId,bulletArgs.hurt_index)\n"
        "if hurtVo then bullet:AddHurtData(bulletArgs.hurt_index,hurtVo) end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "Bullet.lua").write_text(
        "function _M.AddHurtData(self,hurtIndex,list)\n"
        "self.hurtFrameVo:Add4HurtDataListDic(hurtIndex,list)\n"
        "end\n"
        "function _M.CheckBulletHit(self,targetId,targetX,targetY,targetZ)\n"
        "self:DoHurtEvent()\n"
        "end\n"
        "function _M.DoHurtEvent(self)\n"
        "self.hurt_event:OnStart(nil,true,self.hurtFrameVo)\n"
        "self.hurtFrameVo:CheckHurt(self.trajectory_hurt_index,true)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "FightMgr.lua").write_text(
        "function _M.ReleaseSkillExecute(self,skillId,casterId,targetId,dir_euler,pos,movePos,target_move_dis,attackSpeed,skillMoveSpeed,location,slotResult,jie,star,makeId)\n"
        "_TempSkillParam.SkillId=skillId\n"
        "_TempSkillParam.TargetId=targetId\n"
        "_TempSkillParam.Dir=dir_euler\n"
        "_TempSkillParam.Pos=pos\n"
        "_TempSkillParam.MovePos=movePos\n"
        "_TempSkillParam.AttackSpeed=attackSpeed\n"
        "_TempSkillParam.SkillMoveSpeed=skillMoveSpeed\n"
        "_TempSkillParam.Stage=jie\n"
        "_TempSkillParam.Star=star\n"
        "casterView:SetState(StateType.Skill,_TempSkillParam)\n"
        "casterView.Entity:SetTargetId(targetId)\n"
        "end\n"
        "function _M.EntityFightCast(self,msg)\n"
        "local fightCastVO=msg.fightCastVO\n"
        "local attackSpeed=msg.attackPerSecond*0.0001\n"
        "local casterView=EntityMgr.Inst_get():GetEntityFightInBattleView(msg.casterId)\n"
        "local targetView=EntityMgr.Inst_get():GetEntityFightInBattleView(fightCastVO.selectTargetId)\n"
        "self:OnUserCast(msg.skillId,msg.cdTime,attackSpeed,fightCastVO.movePos,msg.castingSpeed,fightCastVO.selectDir,fightCastVO.selectPos,fightCastVO.selectTargetId,msg.jie,msg.star)\n"
        "self:OnEntityCast(casterView,targetView,msg.skillId,fightCastVO.selectDir,fightCastVO.selectPos,msg.currPos,fightCastVO.movePos,attackSpeed,msg.castingSpeed,msg.jie,msg.star,msg)\n"
        "end\n"
        "function _M.OnUserCast(self,skillId,cdTime,attackSpeed,movePos,skillMoveSpeed,dir,selectPos,targetId,jie,star)\n"
        "userView.SkillActor:ToCDStart(skillId,cdTime)\n"
        "PresentationMgr.Inst_get():ResetAttackPlayableData(userView.Entity.V_ID,skillId,TrackType.MoveClipTrack,TimeLineClipType.MoveClip,{move_pos=movePos})\n"
        "self:ReleaseMagicSkill(userView,skillId,dir,selectPos,movePos,targetId,jie,star)\n"
        "end\n"
        "function _M.OnEntityCast(self,casterView,targetView,skillId,dir,pos,curPos,movePos,attackSpeed,skillMoveSpeed,jie,star)\n"
        "self:EntityReleaseSkill(casterView,targetView,skillId,dir,pos,curPos,movePos,attackSpeed,skillMoveSpeed,jie,star,true)\n"
        "end\n"
        "function _M.EntityReleaseSkill(self,casterView,targetView,skillId,dir,pos,curPos,movePos,attackSpeed,skillMoveSpeed,jie,star,immediateCast)\n"
        "self:ReleaseMagicSkill(casterView,skillId,dir,pos,movePos,targetView.Entity.V_ID,jie,star)\n"
        "self:ReleaseSkillExecute(skillId,casterView.Entity.V_ID,targetView.Entity.V_ID,dir,pos,movePos,nil,attackSpeed,skillMoveSpeed,nil,nil,jie,star)\n"
        "end\n"
        "function _M.ReleaseMagicSkill(self,casterView,skillId,selectDir,selectPos,movePos,selectTargetId,jie,star)\n"
        "casterView.SkillActor:ReleaseMagicSkill(skillId,selectDir,selectPos,movePos,selectTargetId,jie,star)\n"
        "return true\n"
        "end\n",
        encoding="utf-8",
    )
    core_state_dir = export_root / "by_source" / "lscripts" / "core_demo" / "text_assets"
    core_state_dir.mkdir(parents=True, exist_ok=True)
    (core_state_dir / "EntityView.lua").write_text(
        "function _M.SetState(self,state,tParam)\n"
        "local curState=self.StateMachine:CurrentState_get()\n"
        "if curState~=StateType.Skill and curState==state then return false end\n"
        "return self.StateMachine:ChangeState(state,false,tParam)\n"
        "end\n"
        "function _M.ForceSetState(self,state,tParam)\n"
        "return self.StateMachine:ChangeState(state,true,tParam)\n"
        "end\n"
        "function _M.IsInSkillState(self)\n"
        "local curState=self:GetCurrentState()\n"
        "return curState==StateType.Skill or curState==StateType.SkillMove or curState==StateType.SkillMoveStop\n"
        "end\n",
        encoding="utf-8",
    )
    (core_state_dir / "StateMachine.lua").write_text(
        "function _M.StateMachine(self,EntityView)\n"
        "self.m_dicState[StateType.Skill]=StateSkill.new()\n"
        "end\n"
        "function _M.ChangeState(self,nextState,bForce,tParam)\n"
        "local bCanChange=self.m_dicState[self._currentState]:CanChangeTo(nextState)\n"
        "self.m_dicState[self._currentState]:Exit(tParam,nextState)\n"
        "self:CurrentState_set(nextState)\n"
        "self.m_dicState[self._currentState]:Enter(tParam)\n"
        "end\n",
        encoding="utf-8",
    )
    (core_state_dir / "StateSkill.lua").write_text(
        "function _M.Enter(self,tParam)\n"
        "local skillId=tParam.SkillId\n"
        "local targetId=tParam.TargetId\n"
        "local pos=tParam.Pos\n"
        "local dir=tParam.Dir\n"
        "local movePos=tParam.MovePos\n"
        "local attack_speed=tParam.AttackSpeed\n"
        "local skill_move_speed=tParam.SkillMoveSpeed\n"
        "local stage=tParam.Stage\n"
        "local star=tParam.Star\n"
        "local makeId=tParam.MakeId\n"
        "local tParam={cast_dir=dir,target_pos=pos,move_pos=movePos,attack_speed=attack_speed,skill_move_speed=skill_move_speed,stage=stage,star=star,makeId=makeId}\n"
        "local skillInfo=skillActor:GetSkill(skillId)\n"
        "local targetView=EntityMgr.Inst_get():GetEntityFightInBattleView(targetId)\n"
        "self.character:LookAt(targetId)\n"
        "skillActor:ReleaseSkill(skillId,targetId,tParam)\n"
        "LuaEventMgr.Inst_get():RaiseEvent(FightEventType.ENTER_STATESKILL_USER)\n"
        "end\n"
        "function _M.Exit(self,tParam,nextState)\n"
        "skillActor:StopSkill()\n"
        "end\n"
        "function _M.CanChangeTo(self,state)\n"
        "return skillActor:IsCurSkillCanMove() or skillActor:IsInAfterCastingState()\n"
        "end\n",
        encoding="utf-8",
    )
    (core_state_dir / "StateBase.lua").write_text(
        "function _M.CanChangeTo(self,state)\n"
        "if(state==StateType.Skill or state==StateType.SkillNavigation)and self.m_state~=StateType.Dead then return true end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "BuffNetLogic.lua").write_text(
        "function _M.BuffNetLogic(self)\n"
        "_MessagePool.Inst_get():F_Register(_SM_BuffChangeHpAndMp:getId(),typeof(_SM_BuffChangeHpAndMp),self.SM_BuffChangeHpAndMpFunc)\n"
        "end\n"
        "function _M.SM_BuffChangeHpAndMpFunc(msg)\n"
        "BuffMgr.Inst_get():UpdateBuffResult(msg.resultVOs)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "BuffMgr.lua").write_text(
        "function _M.UpdateBuffResult(self,resultVOs)\n"
        "for _,buffResultVO in Kpairs(resultVOs)do\n"
        "local entityView=EntityMgr.Inst_get():GetEntityFightInBattleView(buffResultVO.ownerId)\n"
        "if entityView then entityView:AddBuffResult(buffResultVO) end\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "EntityFightView.lua").write_text(
        "function _M.AddBuffResult(self,buffResultVO)\n"
        "local hurtData=HurtPool.Inst_get():CreateHurtData()\n"
        "local damage_num=Mathf.Floor(buffResultVO.damage)\n"
        "local damage_view=Mathf.Floor(buffResultVO.damageView)\n"
        "local recoverHp_num=Mathf.Floor(buffResultVO.recoverHp)\n"
        "local recoverMp_num=Mathf.Floor(buffResultVO.recoverMp)\n"
        "local fightEffect=buffResultVO.fightEffect:ToNum()\n"
        "hurtData:SetData(buffResultVO.casterId,buffResultVO.targetId,fightEffect,damage_view,0,0,recoverHp_num,recoverMp_num,0,damage_num,recoverHp_num,0,0,false)\n"
        "end\n"
        "function _M.AddRestrictCode(self,code)\n"
        "local lastCode=self.restrictCode\n"
        "self.restrictCode=code\n"
        "if self:IsInRestrictStatus(SkillDefine.RestrictStatus.FORBID_MOVE)and self:IsInMoveState()then self:StopMove() end\n"
        "LuaEventMgr.Inst_get():RaiseEvent(FightEventType.RESTRICT_STATUS_CHANGED,self,lastCode,self.restrictCode)\n"
        "end\n"
        "function _M.IsInRestrictStatus(self,status)\n"
        "return bit.band(self.restrictCode,status)>0\n"
        "end\n"
        "function _M.IsCanSelectAsTarget(self)\n"
        "if self:IsInRestrictStatus(SkillDefine.RestrictStatus.CANNOT_SELECT_AS_TARGET)then return false end\n"
        "return true\n"
        "end\n"
        "function _M.IsCanMove(self)\n"
        "if self:IsInRestrictStatus(SkillDefine.RestrictStatus.FORBID_MOVE)then return false end\n"
        "return true\n"
        "end\n"
        "function _M.IsCanCastSkill(self)\n"
        "if self:IsInRestrictStatus(SkillDefine.RestrictStatus.FORBID_USE_SKILL)then return false end\n"
        "return true\n"
        "end\n"
        "function _M.UpdateHpChange(self,targetId,damage,recoverHp,fightEffect,mpDamageAbsorb,shieldAbsorb)\n"
        "local hurtData=HurtPool.Inst_get():CreateHurtData()\n"
        "local damage_num=damage\n"
        "local damage_view=damage\n"
        "local recoverHp_num=recoverHp\n"
        "local mpDamageAbsorb_num=mpDamageAbsorb or 0\n"
        "local shieldAbsorb_num=shieldAbsorb or 0\n"
        "hurtData:SetData(self.Entity.V_ID,targetId,fightEffect,damage_view,0,0,recoverHp_num,0,0,damage_num,recoverHp_num,mpDamageAbsorb_num,shieldAbsorb_num,false)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "PlayerType.lua").write_text(
        "local _M={}\n"
        "_M.UnitState=\n"
        "{\n"
        "idle=0,\n"
        "fight=1,\n"
        "fight_pvp=2,\n"
        "horse=4,\n"
        "}\n"
        "return _M\n",
        encoding="utf-8",
    )
    (text_dir / "Player.lua").write_text(
        "function _M.SetServerUnitState(self,value)\n"
        "self.serverUnitState=value\n"
        "end\n"
        "function _M.IsInServerFightState(self)\n"
        "return IsHasState(self.serverUnitState,PlayerType.UnitState.fight_pvp)\n"
        "end\n"
        "function _M.IsInServerHorseState(self)\n"
        "return IsHasState(self.serverUnitState,PlayerType.UnitState.horse)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "PlayerView.lua").write_text(
        "function _M.SetServerUnitState(self,value)\n"
        "local lastState=self.Entity.serverUnitState\n"
        "_M._super_.SetServerUnitState(self,value)\n"
        "local val=bit.band(lastState,PlayerType.UnitState.fight)\n"
        "local isInFightState=bit.band(self.Entity.serverUnitState,PlayerType.UnitState.fight)>0\n"
        "if val==0 and isInFightState then self:UpdateSurroundPartShow(true) end\n"
        "local horseVal=bit.band(lastState,PlayerType.UnitState.horse)\n"
        "if horseVal==0 and self.Entity:IsInServerHorseState()then self:SetState(StateType.EasyFly) end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "UserView.lua").write_text(
        "function _M.UpdateMpChange(self,targetId,recoverMp,reducedMp)\n"
        "local hurtData=HurtPool.Inst_get():CreateHurtData()\n"
        "local recoverMp_num=recoverMp\n"
        "local fightEffect=SkillDefine.FightCastEffect.NORMAL\n"
        "hurtData:SetData(self.Entity.V_ID,targetId,fightEffect,0,0,0,0,recoverMp_num,reducedMp)\n"
        "end\n"
        "function _M.UserNormalAttackOnly(self)\n"
        "return self:IsInRestrictStatus(SkillDefine.RestrictStatus.USE_DEFAULT_SKILL_ONLY)\n"
        "end\n"
        "function _M.ForbidUseGongFa(self)\n"
        "return self:IsInRestrictStatus(SkillDefine.RestrictStatus.FORBID_USE_SKILL_GONGFA)\n"
        "end\n"
        "function _M.ForbidUseDodge(self)\n"
        "return self:IsInRestrictStatus(SkillDefine.RestrictStatus.FORBID_USE_SKILL_DODGE)\n"
        "end\n"
        "function _M.ForbidUseNormalAttack(self)\n"
        "return self:IsInRestrictStatus(SkillDefine.RestrictStatus.FORBID_USE_SKILL_NORMAL)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SkillDefine.lua").write_text(
        "_M.FightCastEffect=\n"
        "{\n"
        "NORMAL=0,\n"
        "SKILL_MAIN_TARGET=2,\n"
        "DODGE=4,\n"
        "CRIT=8,\n"
        "IMMUNITY=512,\n"
        "BLOCK=1024,\n"
        "SPELL_CRIT=8192,\n"
        "DODGE_DAMAGE=16777216,\n"
        "}\n"
        "_M.RestrictStatus=\n"
        "{\n"
        "FORBID_MOVE=2,\n"
        "CANNOT_SELECT_AS_TARGET=4,\n"
        "USE_DEFAULT_SKILL_ONLY=8,\n"
        "FORBID_USE_SKILL=16,\n"
        "FORBID_USE_SKILL_GONGFA=32768,\n"
        "FORBID_USE_SKILL_DODGE=65536,\n"
        "FORBID_USE_SKILL_NORMAL=262144,\n"
        "}\n"
        "_M.HurtTipsType=\n"
        "{\n"
        "NormalDamage=1,\n"
        "HpRecover=7,\n"
        "}\n",
        encoding="utf-8",
    )
    (text_dir / "SkillData.lua").write_text(
        "function _M.UpdateGroupSkills(self,groupId,skills)\n"
        "if groupId and skills then self.groups[groupId].skills=skills end\n"
        "end\n"
        "function _M.SetChangeGroupData(self,data)\n"
        "self.currentGroupId=data.groupId\n"
        "self:UpdateGroupSkills(self.currentGroupId,data.skills)\n"
        "self:SetSkillCD(data.groupId,self.groups[self.currentGroupId].skills,data.cds,data.systemTime:ToNum())\n"
        "end\n"
        "function _M.SetSkillCD(self,groupId,skillList,cdList,systemTime)\n"
        "if self.cdDic:LuaDic_ContainsKey(groupId)then self.cdDic[groupId]:LuaDic_Clear() else self.cdDic:LuaDic_AddOrSetItem(groupId,Dictionary.new()) end\n"
        "local groupCDDic=self.cdDic[groupId]\n"
        "for index,skillVo in Kpairs(skillList)do\n"
        "if skillVo.skillId then\n"
        "if cdList and cdList[index-1]and systemTime then\n"
        "groupCDDic:LuaDic_AddOrSetItem(skillVo.skillId,math.max(cdList[index-1]:ToNum()-systemTime,0))\n"
        "else\n"
        "groupCDDic:LuaDic_AddOrSetItem(skillVo.skillId,0)\n"
        "end\n"
        "end\n"
        "end\n"
        "end\n"
        "function _M.SetChangeNoUpGroupData(self,data)\n"
        "self.currentGroupId=data.groupId\n"
        "self:SetSkillCD(data.groupId,self.groups[data.groupId].skills,data.cds,data.systemTime:ToNum())\n"
        "end\n"
        "function _M.SetChangeSkillGroupData(self,data)\n"
        "if not self.groups[data.groupId]then self.groups[data.groupId]={systemTime=0,groupId=data.groupId,skills=nil,cds=CList.new()} end\n"
        "self:UpdateGroupSkills(data.groupId,data.skills)\n"
        "self:SetSkillCD(data.groupId,self.groups[data.groupId].skills,data.cds,data.systemTime:ToNum())\n"
        "end\n"
        "function _M.GetCDBySkillId(self,skillId)\n"
        "local currentCDGroup=self.cdDic[self.currentGroupId]\n"
        "if currentCDGroup then return currentCDGroup[skillId] end\n"
        "return 0\n"
        "end\n"
        "function _M.GetShowSkillGroupData(self,key)\n"
        "return self.groups:LuaDic_GetItem(key)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SkillMgr.lua").write_text(
        "function _M.ChangeBattleGroupSkills(self,currentGroupId)\n"
        "local groupId=currentGroupId\n"
        "local skillBattleGroupDic=SkillMgr.Inst_get().Model.SkillData:GetShowSkillGroupData(groupId)\n"
        "local allSkillList=skillBattleGroupDic.skills\n"
        "for k,v in Cipairs(allSkillList)do self:UpdateBattleGroupSkill(v,k) end\n"
        "EntityMgr.Inst_get().UserView.SkillActor:LoadSkills()\n"
        "end\n"
        "function _M.RefreshUserSkillCD(self,msg)\n"
        "self.Model.SkillData:SetSkillCD(msg.groupId,msg.skills,msg.cds,msg.systemTime:ToNum())\n"
        "local userView=EntityMgr.Inst_get().UserView\n"
        "if userView and userView.SkillActor then\n"
        "for _,skillVo in Kpairs(msg.skills)do\n"
        "userView.SkillActor:RefreshSkillCD(skillVo.skillId)\n"
        "end\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "SkillNetLogic.lua").write_text(
        "function _M.SM_ReplaceSkillFun(msg)\n"
        "if msg.code==0 then\n"
        "SkillMgr.Inst_get():SetChangeSkillGroupData(msg)\n"
        "SettingMgr.Inst_get().Model:RaiseEvent(SettingType.ReFreshSkillGroupData)\n"
        "SkillMgr.Inst_get():ChangeBattleGroupSkills(msg.groupId)\n"
        "GongFaNewMgr.Inst_get().Model:RaiseEvent(GongFaNewType.CHANGE_BATTLE_SKILL)\n"
        "end\n"
        "end\n"
        "function _M.SM_AutoReplaceFun(msg)\n"
        "if msg.code==0 then\n"
        "SkillMgr.Inst_get():SetChangeNoUpGroupData(msg)\n"
        "SettingMgr.Inst_get().Model:RaiseEvent(SettingType.ReFreshSkillGroupData,msg.groupId)\n"
        "SkillMgr.Inst_get():ChangeBattleGroupSkills(msg.groupId)\n"
        "end\n"
        "end\n"
        "function _M.SM_ChangeGroupFun(msg)\n"
        "if msg.code==0 then\n"
        "SkillMgr.Inst_get():SetChangeGroupData(msg)\n"
        "SettingMgr.Inst_get().Model:RaiseEvent(SettingType.ReFreshSkillGroupData,msg.groupId)\n"
        "SkillMgr.Inst_get():ChangeBattleGroupSkills(msg.groupId)\n"
        "GongFaNewMgr.Inst_get().Model:RaiseEvent(GongFaNewType.ChangeGongFa)\n"
        "end\n"
        "GongFaNewMgr.Inst_get().Model:RaiseEvent(GongFaNewType.CHANGE_BATTLE_SKILL)\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "HurtData.lua").write_text(
        "function _M.SetData(self,casterId,targetId,fightEffect,damage_num,reflect_damage,mp_damage,recoverHp_num,recoverMp_num,reducedMp_num,total_damage,total_recover,mpDamageAbsorb_num,shieldAbsorb_num)\n"
        "self.casterId=casterId\n"
        "self.targetId=targetId\n"
        "self.fightEffect=fightEffect\n"
        "self.damage_num=damage_num\n"
        "self.reflect_num=reflect_damage\n"
        "self.mp_damage_num=mp_damage\n"
        "self.recoverHp_num=recoverHp_num\n"
        "self.total_damage=total_damage\n"
        "self.total_recover=total_recover\n"
        "self.mpDamageAbsorb_num=mpDamageAbsorb_num\n"
        "self.shieldAbsorb_num=shieldAbsorb_num\n"
        "end\n"
        "function _M.Execute(self)\n"
        "self:NormalExecute()\n"
        "end\n"
        "function _M.NormalExecute(self)\n"
        "local fightView=EntityMgr.Inst_get():GetEntityView(self.targetId)\n"
        "if self.recoverHp_num~=0 then\n"
        "local recoverHp_num=self.recoverHp_num\n"
        "HurtTipsMgr.Inst_get():AddTipsNum(self.targetId,SkillDefine.HurtTipsType.HpRecover,recoverHp_num)\n"
        "TipsMgr.Inst_get():ShowBloodTips(fightView,BloodType.CURE,self.hurt_tips:ToString(),0)\n"
        "end\n"
        "if self.damage_num~=0 then\n"
        "local damage_num=self.damage_num\n"
        "local tip,bloodType,isSpecialCast,fightEffect=self:FormatHurtTipsAndType(false,self.entityType,damage_num,fightView)\n"
        "TipsMgr.Inst_get():ShowBloodTips(fightView,bloodType,tip,0)\n"
        "end\n"
        "end\n"
        "function _M.FormatHurtTipsAndType(self,selfHurt,casterEntityType,damage_num,fightView)\n"
        "local bloodType=BloodType.NORMAL\n"
        "local ignoreDmg=false\n"
        "local isSpecialCast=false\n"
        "local fightEffect=SkillDefine.FightCastEffect.NORMAL\n"
        "if HasEffect(self,SkillDefine.FightCastEffect.IMMUNITY)then\n"
        "self.hurt_tips:Append(\"i \")\n"
        "ignoreDmg=true\n"
        "isSpecialCast=true\n"
        "elseif HasEffect(self,SkillDefine.FightCastEffect.BLOCK)then\n"
        "self.hurt_tips:Append(\"b \")\n"
        "fightEffect=SkillDefine.FightCastEffect.BLOCK\n"
        "elseif HasEffect(self,SkillDefine.FightCastEffect.CRIT)or HasEffect(self,SkillDefine.FightCastEffect.SPELL_CRIT)then\n"
        "self.hurt_tips:Append(\"b \")\n"
        "bloodType=BloodType.CRIT_HURT\n"
        "fightEffect=SkillDefine.FightCastEffect.CRIT\n"
        "end\n"
        "return self.hurt_tips:ToString(),bloodType,isSpecialCast,fightEffect\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "HurtTipsMgr.lua").write_text(
        "function _M.ShowHurtTipsByType(self,tipsType,targetId,fightEffect,totalNum)\n"
        "local recoverHp_num=0\n"
        "if tipsType==SkillDefine.HurtTipsType.HpRecover then\n"
        "recoverHp_num=totalNum\n"
        "end\n"
        "self:ShowHurtTips(targetId,LuaEntityType.User,false,fightEffect,0,0,0,recoverHp_num)\n"
        "end\n",
        encoding="utf-8",
    )
    blood_type_dir = export_root / "by_source" / "lscripts" / "core_demo" / "text_assets"
    blood_type_dir.mkdir(parents=True, exist_ok=True)
    (blood_type_dir / "BloodType.lua").write_text(
        "local _M={}\n"
        "package.loaded[\"Core.Battle.Entity.Const.BloodType\"]=_M\n"
        "_M.NORMAL=0\n"
        "_M.CURE=4\n"
        "_M.MP=5\n"
        "_M.BURNING=6\n"
        "_M.CRIT_HURT=14\n"
        "return _M\n",
        encoding="utf-8",
    )
    (text_dir / "PanelBloodTips.lua").write_text(
        "function _M.OnShow(self)\n"
        "local Tips3DUIShowComponent=require\"Core.UIManager.UIBridge.Tips3DUIShowComponent\"\n"
        "Tips3DUIShowComponent.new(\"UI/FightMainUI/PanelBloodTips_1\",function(findId)return self end,true)\n"
        "end\n"
        "function _M.InitView(self)\n"
        "local normalGO=self:SetComponent(LuaGameObject,4)\n"
        "local baojiGo=self:SetComponent(LuaGameObject,15)\n"
        "self.ItemWithTypes={\n"
        "[BloodType.NORMAL]=normalGO,\n"
        "[BloodType.CRIT_HURT]=baojiGo,\n"
        "}\n"
        "end\n",
        encoding="utf-8",
    )
    (text_dir / "BloodTipItem.lua").write_text(
        "function _M.Show(self,targetId,skillId,posX,posY,posZ,bloodType,tip,dirX,dirY,dirZ,scale)\n"
        "if bloodType==BloodType.NORMAL then\n"
        "self:PlayAnim(\"ani_jianxuego\",self.go,nil,nil,nil,dirX,dirY,dirZ)\n"
        "elseif bloodType==BloodType.CRIT_HURT then\n"
        "self:PlayAnim(\"ani_baojigo\",self.go,nil,nil,nil,dirX,dirY,dirZ)\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    fight_cfg_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "fight_demo" / "text_assets"
    fight_cfg_dir.mkdir(parents=True)
    (fight_cfg_dir / "ConfigValue.lua").write_text(
        "local c=require('Generate.Cfg.bean')\n"
        "local _key2index={id=1,value=2}\n"
        "local _key2null={[1]='',[2]=''}\n"
        "local _key2type={[1]=0,[2]=0}\n"
        "local _P=c.Init(_key2index,_key2null,_key2type)\n"
        "local _A={\n"
        "[1]='font_NormalDamage',\n"
        "[2]='0_1000,3_500,9_250',\n"
        "[3]='font_OtherNormalFont',\n"
        "[4]='7_1000,11_750',\n"
        "[5]='font_special_rose',\n"
        "[6]='200',\n"
        "}\n"
        "local _M={\n"
        "['font_NormalDamage']=setmetatable({[1]=_A[1],[2]=_A[2]},_P),\n"
        "['font_OtherNormalFont']=setmetatable({[1]=_A[3],[2]=_A[4]},_P),\n"
        "['font_special_rose']=setmetatable({[1]=_A[5],[2]=_A[6]},_P),\n"
        "}\n"
        "return _M\n",
        encoding="utf-8",
    )
    (packet_dir / "packets.tsv").write_text(
        "id\tname\tdirection\tmodule\tfield_count\tbase_class\tbundle\tfile\trelative_path\tpackage\n"
        "35713\tCM_GongFaHomeMakeCombine\tclient_to_server\tplayer.gongfahomemake\t2\tBaseMessage\tmessage_demo\tCM_GongFaHomeMakeCombine.lua\tpacket/CM_GongFaHomeMakeCombine.lua\tpkg\n"
        "35714\tSM_GongFaHomeMakeCombine\tserver_to_client\tplayer.gongfahomemake\t1\tClientResult\tmessage_demo\tSM_GongFaHomeMakeCombine.lua\tpacket/SM_GongFaHomeMakeCombine.lua\tpkg\n"
        "35710\tGongFaHomeMakeVO\tvalue_object\tplayer.gongfahomemake\t1\tBaseMessage\tmessage_demo\tGongFaHomeMakeVO.lua\tpacket/GongFaHomeMakeVO.lua\tpkg\n"
        "35740\tCreateSkillCommonVO\tvalue_object\tplayer.gongfahomemake\t2\tBaseMessage\tmessage_demo\tCreateSkillCommonVO.lua\tby_source/lscripts/gamesystem/game/gongfahomemake_demo/text_assets/CreateSkillCommonVO.lua\tpkg\n"
        "60005\tSM_FightResult\tserver_to_client\tscene.fight\t5\tBaseMessage\tmessage_demo\tSM_FightResult.lua\tpacket/SM_FightResult.lua\tpkg\n"
        "60004\tFightResultVO\tvalue_object\tscene.fight\t10\tBaseMessage\tmessage_demo\tFightResultVO.lua\tpacket/FightResultVO.lua\tpkg\n"
        "60039\tSM_UnitHpUpdate\tserver_to_client\tscene.fight\t7\tBaseMessage\tmessage_demo\tSM_UnitHpUpdate.lua\tpacket/SM_UnitHpUpdate.lua\tpkg\n"
        "60040\tSM_UnitMpUpdate\tserver_to_client\tscene.fight\t3\tBaseMessage\tmessage_demo\tSM_UnitMpUpdate.lua\tpacket/SM_UnitMpUpdate.lua\tpkg\n"
        "60034\tSM_BuffChangeHpAndMp\tserver_to_client\tscene.fight\t1\tBaseMessage\tmessage_demo\tSM_BuffChangeHpAndMp.lua\tpacket/SM_BuffChangeHpAndMp.lua\tpkg\n"
        "60033\tBuffResultVO\tvalue_object\tscene.fight\t10\tBaseMessage\tmessage_demo\tBuffResultVO.lua\tpacket/BuffResultVO.lua\tpkg\n"
        "60008\tSM_HpChange\tserver_to_client\tscene.fight\t2\tBaseMessage\tmessage_demo\tSM_HpChange.lua\tpacket/SM_HpChange.lua\tpkg\n"
        "60009\tSM_MpChange\tserver_to_client\tscene.fight\t1\tBaseMessage\tmessage_demo\tSM_MpChange.lua\tpacket/SM_MpChange.lua\tpkg\n"
        "60037\tSM_FixDamage\tserver_to_client\tscene.fight\t5\tBaseMessage\tmessage_demo\tSM_FixDamage.lua\tpacket/SM_FixDamage.lua\tpkg\n"
        "60058\tSM_ShadowHpChange\tserver_to_client\tscene.fight\t2\tBaseMessage\tmessage_demo\tSM_ShadowHpChange.lua\tpacket/SM_ShadowHpChange.lua\tpkg\n"
        "60059\tSM_ShadowInfo\tserver_to_client\tscene.fight\t2\tBaseMessage\tmessage_demo\tSM_ShadowInfo.lua\tpacket/SM_ShadowInfo.lua\tpkg\n"
        "60067\tSM_UnitMaxHpUpdate\tserver_to_client\tscene.fight\t3\tBaseMessage\tmessage_demo\tSM_UnitMaxHpUpdate.lua\tpacket/SM_UnitMaxHpUpdate.lua\tpkg\n"
        "60045\tSM_Revive\tserver_to_client\tscene.fight\t4\tBaseMessage\tmessage_demo\tSM_Revive.lua\tpacket/SM_Revive.lua\tpkg\n"
        "60001\tCM_FightByTarget\tclient_to_server\tscene.fight\t5\tBaseMessage\tmessage_demo\tCM_FightByTarget.lua\tpacket/CM_FightByTarget.lua\tpkg\n"
        "60018\tCM_FightByTargets\tclient_to_server\tscene.fight\t7\tBaseMessage\tmessage_demo\tCM_FightByTargets.lua\tpacket/CM_FightByTargets.lua\tpkg\n"
        "60023\tCM_FightByDir\tclient_to_server\tscene.fight\t5\tBaseMessage\tmessage_demo\tCM_FightByDir.lua\tpacket/CM_FightByDir.lua\tpkg\n"
        "60022\tCM_FightByPosition\tclient_to_server\tscene.fight\t5\tBaseMessage\tmessage_demo\tCM_FightByPosition.lua\tpacket/CM_FightByPosition.lua\tpkg\n"
        "60019\tCM_FightFinishCharge\tclient_to_server\tscene.fight\t2\tBaseMessage\tmessage_demo\tCM_FightFinishCharge.lua\tpacket/CM_FightFinishCharge.lua\tpkg\n"
        "60011\tCM_FightInterrupt\tclient_to_server\tscene.fight\t0\tBaseMessage\tmessage_demo\tCM_FightInterrupt.lua\tpacket/CM_FightInterrupt.lua\tpkg\n"
        "60006\tSM_FightCast\tserver_to_client\tscene.fight\t10\tBaseMessage\tmessage_demo\tSM_FightCast.lua\tpacket/SM_FightCast.lua\tpkg\n"
        "60003\tFightCastVO\tvalue_object\tscene.fight\t4\tSkillEffectVO\tmessage_demo\tFightCastVO.lua\tpacket/FightCastVO.lua\tpkg\n"
        "60025\tFightCastMultiVO\tvalue_object\tscene.fight\t4\tSkillEffectVO\tmessage_demo\tFightCastMultiVO.lua\tpacket/FightCastMultiVO.lua\tpkg\n"
        "60036\tSM_FightCastTalisman\tserver_to_client\tscene.fight\t1\tSM_FightCast\tmessage_demo\tSM_FightCastTalisman.lua\tpacket/SM_FightCastTalisman.lua\tpkg\n"
        "60049\tSM_FightCastPassive\tserver_to_client\tscene.fight\t3\tBaseMessage\tmessage_demo\tSM_FightCastPassive.lua\tpacket/SM_FightCastPassive.lua\tpkg\n"
        "60050\tSM_FightCastPet\tserver_to_client\tscene.fight\t1\tSM_FightCast\tmessage_demo\tSM_FightCastPet.lua\tpacket/SM_FightCastPet.lua\tpkg\n"
        "60053\tSM_FightCastFunnel\tserver_to_client\tscene.fight\t1\tSM_FightCast\tmessage_demo\tSM_FightCastFunnel.lua\tpacket/SM_FightCastFunnel.lua\tpkg\n"
        "60068\tSM_QiChange\tserver_to_client\tscene.fight\t1\tBaseMessage\tmessage_demo\tSM_QiChange.lua\tpacket/SM_QiChange.lua\tpkg\n"
        "60010\tSM_FightFail\tserver_to_client\tscene.fight\t5\tBaseMessage\tmessage_demo\tSM_FightFail.lua\tpacket/SM_FightFail.lua\tpkg\n"
        "60013\tSM_FightInterrupt\tserver_to_client\tscene.fight\t3\tBaseMessage\tmessage_demo\tSM_FightInterrupt.lua\tpacket/SM_FightInterrupt.lua\tpkg\n"
        "60012\tSM_RestrictStatus\tserver_to_client\tscene.fight\t2\tBaseMessage\tmessage_demo\tSM_RestrictStatus.lua\tpacket/SM_RestrictStatus.lua\tpkg\n"
        "60070\tSM_FightTimeLine\tserver_to_client\tscene.fight\t6\tBaseMessage\tmessage_demo\tSM_FightTimeLine.lua\tpacket/SM_FightTimeLine.lua\tpkg\n"
        "60024\tSkillEffectVO\tvalue_object\tscene.fight\t0\tBaseMessage\tmessage_demo\tSkillEffectVO.lua\tpacket/SkillEffectVO.lua\tpkg\n"
        "60026\tMoveSkillEffectVO\tvalue_object\tscene.fight\t1\tSkillEffectVO\tmessage_demo\tMoveSkillEffectVO.lua\tpacket/MoveSkillEffectVO.lua\tpkg\n"
        "60029\tSM_SkillEffect\tserver_to_client\tscene.fight\t3\tBaseMessage\tmessage_demo\tSM_SkillEffect.lua\tpacket/SM_SkillEffect.lua\tpkg\n"
        "60038\tSM_UpdateCd\tserver_to_client\tscene.fight\t1\tBaseMessage\tmessage_demo\tSM_UpdateCd.lua\tpacket/SM_UpdateCd.lua\tpkg\n"
        "60041\tSM_UpdateSelect\tserver_to_client\tscene.fight\t2\tBaseMessage\tmessage_demo\tSM_UpdateSelect.lua\tpacket/SM_UpdateSelect.lua\tpkg\n"
        "60044\tSM_SyncUnit\tserver_to_client\tscene.fight\t10\tBaseMessage\tmessage_demo\tSM_SyncUnit.lua\tpacket/SM_SyncUnit.lua\tpkg\n"
        "60043\tSM_TestAdjustDirect\tserver_to_client\tscene.fight\t1\tBaseMessage\tmessage_demo\tSM_TestAdjustDirect.lua\tpacket/SM_TestAdjustDirect.lua\tpkg\n"
        "60044\tSM_TestShape\tserver_to_client\tscene.fight\t8\tBaseMessage\tmessage_demo\tSM_TestShape.lua\tpacket/SM_TestShape.lua\tpkg\n"
        "60047\tSM_FightChannel\tserver_to_client\tscene.fight\t4\tBaseMessage\tmessage_demo\tSM_FightChannel.lua\tpacket/SM_FightChannel.lua\tpkg\n"
        "60066\tSM_UnitState\tserver_to_client\tscene.fight\t2\tBaseMessage\tmessage_demo\tSM_UnitState.lua\tpacket/SM_UnitState.lua\tpkg\n"
        "30013\tSM_ChangedPlayerAttribute\tserver_to_client\tplayer.role\t2\tBaseMessage\tmessage_demo\tSM_ChangedPlayerAttribute.lua\tpacket/SM_ChangedPlayerAttribute.lua\tpkg\n"
        "30017\tSM_FightScore\tserver_to_client\tplayer.role\t1\tBaseMessage\tmessage_demo\tSM_FightScore.lua\tpacket/SM_FightScore.lua\tpkg\n"
        "30018\tSM_ModuleFightScore\tserver_to_client\tplayer.role\t1\tClientResult\tmessage_demo\tSM_ModuleFightScore.lua\tpacket/SM_ModuleFightScore.lua\tpkg\n"
        "30019\tChangedAttrsVo\tvalue_object\tcommon.attribute\t3\tBaseMessage\tmessage_demo\tChangedAttrsVo.lua\tpacket/ChangedAttrsVo.lua\tpkg\n"
        "30021\tSM_RoleChangedAttrs\tserver_to_client\tcommon.attribute\t1\tClientResult\tmessage_demo\tSM_RoleChangedAttrs.lua\tpacket/SM_RoleChangedAttrs.lua\tpkg\n"
        "30046\tSM_RealmUpRewardAttr\tserver_to_client\tplayer.role\t1\tClientResult\tmessage_demo\tSM_RealmUpRewardAttr.lua\tpacket/SM_RealmUpRewardAttr.lua\tpkg\n"
        "42206\tSM_TakeMedicineAttributeSync\tserver_to_client\tplayer.medicine\t2\tClientResult\tmessage_demo\tSM_TakeMedicineAttributeSync.lua\tpacket/SM_TakeMedicineAttributeSync.lua\tpkg\n"
        "30740\tCM_GongFaView\tclient_to_server\tplayer.gongfa\t0\tBaseMessage\tmessage_demo\tCM_GongFaView.lua\tpacket/CM_GongFaView.lua\tpkg\n"
        "30741\tSM_GongFaView\tserver_to_client\tplayer.gongfa\t5\tClientResult\tmessage_demo\tSM_GongFaView.lua\tpacket/SM_GongFaView.lua\tpkg\n"
        "30251\tSimpleItemVO\tvalue_object\tplayer.backpack\t3\tBaseMessage\tmessage_demo\tSimpleItemVO.lua\tpacket/SimpleItemVO.lua\tpkg\n"
        "30742\tGongFaItemVO\tvalue_object\tplayer.gongfa\t8\tSimpleItemVO\tmessage_demo\tGongFaItemVO.lua\tpacket/GongFaItemVO.lua\tpkg\n"
        "30744\tCM_GongFaUpgrade\tclient_to_server\tplayer.gongfa\t3\tBaseMessage\tmessage_demo\tCM_GongFaUpgrade.lua\tpacket/CM_GongFaUpgrade.lua\tpkg\n"
        "30745\tSM_GongFaUpgrade\tserver_to_client\tplayer.gongfa\t5\tClientResult\tmessage_demo\tSM_GongFaUpgrade.lua\tpacket/SM_GongFaUpgrade.lua\tpkg\n"
        "30748\tCM_GongFaLearn\tclient_to_server\tplayer.gongfa\t1\tBaseMessage\tmessage_demo\tCM_GongFaLearn.lua\tpacket/CM_GongFaLearn.lua\tpkg\n"
        "30749\tSM_GongFaLearn\tserver_to_client\tplayer.gongfa\t4\tClientResult\tmessage_demo\tSM_GongFaLearn.lua\tpacket/SM_GongFaLearn.lua\tpkg\n"
        "35770\tCM_GongFaUpgradeTimes\tclient_to_server\tplayer.gongfa\t1\tBaseMessage\tmessage_demo\tCM_GongFaUpgradeTimes.lua\tpacket/CM_GongFaUpgradeTimes.lua\tpkg\n"
        "35771\tSM_GongFaUpgradeTimes\tserver_to_client\tplayer.gongfa\t1\tClientResult\tmessage_demo\tSM_GongFaUpgradeTimes.lua\tpacket/SM_GongFaUpgradeTimes.lua\tpkg\n"
        "32212\tSkillInfoVO\tvalue_object\tplayer.skill\t5\tBaseMessage\tmessage_demo\tSkillInfoVO.lua\tpacket/SkillInfoVO.lua\tpkg\n"
        "32202\tSM_ReplaceSkill\tserver_to_client\tplayer.skill\t4\tClientResult\tmessage_demo\tSM_ReplaceSkill.lua\tpacket/SM_ReplaceSkill.lua\tpkg\n"
        "32208\tSM_ChangeGroup\tserver_to_client\tplayer.skill\t4\tClientResult\tmessage_demo\tSM_ChangeGroup.lua\tpacket/SM_ChangeGroup.lua\tpkg\n"
        "32211\tSM_AutoReplace\tserver_to_client\tplayer.skill\t4\tClientResult\tmessage_demo\tSM_AutoReplace.lua\tpacket/SM_AutoReplace.lua\tpkg\n",
        encoding="utf-8",
    )
    (packet_dir / "packet_fields.tsv").write_text(
        "packet_id\tpacket_name\tfield_index\tfield_name\tread_method\ttype_hint\tdirection\tmodule\tbundle\tfile\tline\n"
        "35713\tCM_GongFaHomeMakeCombine\t1\tmainId\tInt\t\tclient_to_server\tplayer.gongfahomemake\tmessage_demo\tCM_GongFaHomeMakeCombine.lua\t18\n"
        "35713\tCM_GongFaHomeMakeCombine\t2\tassist1\tInt\t\tclient_to_server\tplayer.gongfahomemake\tmessage_demo\tCM_GongFaHomeMakeCombine.lua\t19\n"
        "35714\tSM_GongFaHomeMakeCombine\t1\thomeMakeVO\tBean\tGongFaHomeMakeVO\tserver_to_client\tplayer.gongfahomemake\tmessage_demo\tSM_GongFaHomeMakeCombine.lua\t22\n"
        "35710\tGongFaHomeMakeVO\t1\tskillCommonVO\tBean\tCreateSkillCommonVO\tvalue_object\tplayer.gongfahomemake\tmessage_demo\tGongFaHomeMakeVO.lua\t31\n"
        "35740\tCreateSkillCommonVO\t1\tid\tLong\t\tvalue_object\tplayer.gongfahomemake\tmessage_demo\tCreateSkillCommonVO.lua\t2\n"
        "35740\tCreateSkillCommonVO\t2\teffectMap\tMessageMap2Dic\t\tvalue_object\tplayer.gongfahomemake\tmessage_demo\tCreateSkillCommonVO.lua\t3\n"
        "60005\tSM_FightResult\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightResult.lua\t11\n"
        "60005\tSM_FightResult\t2\tlockId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightResult.lua\t12\n"
        "60005\tSM_FightResult\t3\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightResult.lua\t13\n"
        "60005\tSM_FightResult\t4\tresults\tMessageList2List\tFightResultVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightResult.lua\t15\n"
        "60005\tSM_FightResult\t5\tdelayTime\tShort\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightResult.lua\t16\n"
        "60004\tFightResultVO\t1\ttargetId\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t11\n"
        "60004\tFightResultVO\t2\tfightEffect\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t12\n"
        "60004\tFightResultVO\t3\tdamage\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t13\n"
        "60004\tFightResultVO\t4\tdamageView\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t14\n"
        "60004\tFightResultVO\t5\tmpAddDamage\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t15\n"
        "60004\tFightResultVO\t6\tmpAddDamageView\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t16\n"
        "60004\tFightResultVO\t7\tdamageTimes\tByte\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t17\n"
        "60004\tFightResultVO\t8\trecoverHp\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t18\n"
        "60004\tFightResultVO\t9\tdamageReflect\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t19\n"
        "60004\tFightResultVO\t10\tmpDamageAbsorb\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tFightResultVO.lua\t20\n"
        "60039\tSM_UnitHpUpdate\t1\tid\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t11\n"
        "60039\tSM_UnitHpUpdate\t2\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t12\n"
        "60039\tSM_UnitHpUpdate\t3\tdamage\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t13\n"
        "60039\tSM_UnitHpUpdate\t4\trecoverHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t14\n"
        "60039\tSM_UnitHpUpdate\t5\tfightEffect\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t15\n"
        "60039\tSM_UnitHpUpdate\t6\tmpDamageAbsorb\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t16\n"
        "60039\tSM_UnitHpUpdate\t7\tshieldAbsorb\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitHpUpdate.lua\t17\n"
        "60040\tSM_UnitMpUpdate\t1\tid\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitMpUpdate.lua\t11\n"
        "60040\tSM_UnitMpUpdate\t2\trecoverMp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitMpUpdate.lua\t12\n"
        "60040\tSM_UnitMpUpdate\t3\tchangeMp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitMpUpdate.lua\t13\n"
        "60034\tSM_BuffChangeHpAndMp\t1\tresultVOs\tMessageList2List\tBuffResultVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_BuffChangeHpAndMp.lua\t15\n"
        "60033\tBuffResultVO\t1\tid\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t12\n"
        "60033\tBuffResultVO\t2\townerId\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t13\n"
        "60033\tBuffResultVO\t3\tcasterId\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t14\n"
        "60033\tBuffResultVO\t4\ttargetId\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t15\n"
        "60033\tBuffResultVO\t5\tmodelId\tInt\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t16\n"
        "60033\tBuffResultVO\t6\tdamage\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t17\n"
        "60033\tBuffResultVO\t7\tdamageView\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t18\n"
        "60033\tBuffResultVO\t8\trecoverHp\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t19\n"
        "60033\tBuffResultVO\t9\trecoverMp\tDouble\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t20\n"
        "60033\tBuffResultVO\t10\tfightEffect\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tBuffResultVO.lua\t21\n"
        "60008\tSM_HpChange\t1\tchangeHpMap\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_HpChange.lua\t15\n"
        "60008\tSM_HpChange\t2\tchangeVirtualHpMap\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_HpChange.lua\t16\n"
        "60009\tSM_MpChange\t1\tchangeMpMap\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_MpChange.lua\t13\n"
        "60037\tSM_FixDamage\t1\tunitId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FixDamage.lua\t16\n"
        "60037\tSM_FixDamage\t2\thp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FixDamage.lua\t17\n"
        "60037\tSM_FixDamage\t3\ttotalDamage\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FixDamage.lua\t18\n"
        "60037\tSM_FixDamage\t4\tmaxDamage\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FixDamage.lua\t19\n"
        "60037\tSM_FixDamage\t5\tattackTime\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FixDamage.lua\t20\n"
        "60058\tSM_ShadowHpChange\t1\tchangeHpMap\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_ShadowHpChange.lua\t15\n"
        "60058\tSM_ShadowHpChange\t2\trecoverHpLock\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_ShadowHpChange.lua\t16\n"
        "60059\tSM_ShadowInfo\t1\tmaxHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_ShadowInfo.lua\t15\n"
        "60059\tSM_ShadowInfo\t2\tcurrHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_ShadowInfo.lua\t16\n"
        "60067\tSM_UnitMaxHpUpdate\t1\tid\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitMaxHpUpdate.lua\t14\n"
        "60067\tSM_UnitMaxHpUpdate\t2\tmaxHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitMaxHpUpdate.lua\t15\n"
        "60067\tSM_UnitMaxHpUpdate\t3\tcurrHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitMaxHpUpdate.lua\t16\n"
        "60045\tSM_Revive\t1\tmaxHp\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_Revive.lua\t15\n"
        "60045\tSM_Revive\t2\tcostResults\tMessageList2List\tRewardAndCostVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_Revive.lua\t16\n"
        "60045\tSM_Revive\t3\tmaxMp\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_Revive.lua\t17\n"
        "60045\tSM_Revive\t4\treviveType\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_Revive.lua\t18\n"
        "60001\tCM_FightByTarget\t1\tcasterId\tLong\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTarget.lua\t11\n"
        "60001\tCM_FightByTarget\t2\tskillId\tInt\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTarget.lua\t12\n"
        "60001\tCM_FightByTarget\t3\ttargetId\tLong\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTarget.lua\t13\n"
        "60001\tCM_FightByTarget\t4\tmovePos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTarget.lua\t14\n"
        "60001\tCM_FightByTarget\t5\tcurrPos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTarget.lua\t15\n"
        "60018\tCM_FightByTargets\t1\tcasterId\tLong\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t11\n"
        "60018\tCM_FightByTargets\t2\tskillId\tInt\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t12\n"
        "60018\tCM_FightByTargets\t3\tselectDir\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t13\n"
        "60018\tCM_FightByTargets\t4\tselectPos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t14\n"
        "60018\tCM_FightByTargets\t5\tselectTargetIds\tMessageList2List\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t15\n"
        "60018\tCM_FightByTargets\t6\tmovePos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t16\n"
        "60018\tCM_FightByTargets\t7\tcurrPos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByTargets.lua\t17\n"
        "60023\tCM_FightByDir\t1\tcasterId\tLong\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByDir.lua\t11\n"
        "60023\tCM_FightByDir\t2\tskillId\tInt\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByDir.lua\t12\n"
        "60023\tCM_FightByDir\t3\tselectDir\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByDir.lua\t13\n"
        "60023\tCM_FightByDir\t4\tmovePos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByDir.lua\t14\n"
        "60023\tCM_FightByDir\t5\tcurrPos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByDir.lua\t15\n"
        "60022\tCM_FightByPosition\t1\tcasterId\tLong\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByPosition.lua\t11\n"
        "60022\tCM_FightByPosition\t2\tskillId\tInt\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByPosition.lua\t12\n"
        "60022\tCM_FightByPosition\t3\tselectPos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByPosition.lua\t13\n"
        "60022\tCM_FightByPosition\t4\tcurrPos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByPosition.lua\t14\n"
        "60022\tCM_FightByPosition\t5\tmovePos\tBean\tGrid3DVO\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightByPosition.lua\t15\n"
        "60019\tCM_FightFinishCharge\t1\tcasterId\tLong\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightFinishCharge.lua\t11\n"
        "60019\tCM_FightFinishCharge\t2\tskillId\tInt\t\tclient_to_server\tscene.fight\tmessage_demo\tCM_FightFinishCharge.lua\t12\n"
        "60006\tSM_FightCast\t1\tid\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t11\n"
        "60006\tSM_FightCast\t2\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t12\n"
        "60006\tSM_FightCast\t3\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t13\n"
        "60006\tSM_FightCast\t4\tjie\tShort\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t14\n"
        "60006\tSM_FightCast\t5\tstar\tShort\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t15\n"
        "60006\tSM_FightCast\t6\tcdTime\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t16\n"
        "60006\tSM_FightCast\t7\tattackPerSecond\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t17\n"
        "60006\tSM_FightCast\t8\tfightCastVO\tBean\tSkillEffectVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t18\n"
        "60006\tSM_FightCast\t9\tcurrPos\tBean\tGrid3DVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t19\n"
        "60006\tSM_FightCast\t10\tcastingSpeed\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCast.lua\t20\n"
        "60003\tFightCastVO\t1\tselectTargetId\tLong\t\tvalue_object\tscene.fight\tmessage_demo\tFightCastVO.lua\t11\n"
        "60003\tFightCastVO\t2\tselectPos\tBean\tGrid3DVO\tvalue_object\tscene.fight\tmessage_demo\tFightCastVO.lua\t12\n"
        "60003\tFightCastVO\t3\tselectDir\tBean\tGrid3DVO\tvalue_object\tscene.fight\tmessage_demo\tFightCastVO.lua\t13\n"
        "60003\tFightCastVO\t4\tcastType\tByte\t\tvalue_object\tscene.fight\tmessage_demo\tFightCastVO.lua\t14\n"
        "60025\tFightCastMultiVO\t1\tselectTargetIds\tMessageList2List\t\tvalue_object\tscene.fight\tmessage_demo\tFightCastMultiVO.lua\t11\n"
        "60025\tFightCastMultiVO\t2\tselectPoses\tMessageList2List\t\tvalue_object\tscene.fight\tmessage_demo\tFightCastMultiVO.lua\t12\n"
        "60025\tFightCastMultiVO\t3\tselectDir\tBean\tGrid3DVO\tvalue_object\tscene.fight\tmessage_demo\tFightCastMultiVO.lua\t13\n"
        "60025\tFightCastMultiVO\t4\tcastType\tByte\t\tvalue_object\tscene.fight\tmessage_demo\tFightCastMultiVO.lua\t14\n"
        "60036\tSM_FightCastTalisman\t1\ttalismanId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCastTalisman.lua\t11\n"
        "60049\tSM_FightCastPassive\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCastPassive.lua\t11\n"
        "60049\tSM_FightCastPassive\t2\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCastPassive.lua\t12\n"
        "60049\tSM_FightCastPassive\t3\tfightCastVO\tBean\tSkillEffectVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCastPassive.lua\t13\n"
        "60050\tSM_FightCastPet\t1\tlocation\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCastPet.lua\t11\n"
        "60053\tSM_FightCastFunnel\t1\tbuffId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightCastFunnel.lua\t11\n"
        "60068\tSM_QiChange\t1\tchangeQiMap\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_QiChange.lua\t15\n"
        "60010\tSM_FightFail\t1\tcode\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightFail.lua\t11\n"
        "60010\tSM_FightFail\t2\terrorDesc\tString\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightFail.lua\t12\n"
        "60010\tSM_FightFail\t3\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightFail.lua\t13\n"
        "60010\tSM_FightFail\t4\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightFail.lua\t14\n"
        "60010\tSM_FightFail\t5\tcurrPos\tBean\tGrid3DVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightFail.lua\t15\n"
        "60013\tSM_FightInterrupt\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightInterrupt.lua\t11\n"
        "60013\tSM_FightInterrupt\t2\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightInterrupt.lua\t12\n"
        "60013\tSM_FightInterrupt\t3\ttargetPos\tBean\tGrid3DVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightInterrupt.lua\t13\n"
        "60012\tSM_RestrictStatus\t1\tunitId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_RestrictStatus.lua\t11\n"
        "60012\tSM_RestrictStatus\t2\trestrictCode\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_RestrictStatus.lua\t12\n"
        "60070\tSM_FightTimeLine\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightTimeLine.lua\t11\n"
        "60070\tSM_FightTimeLine\t2\ttargetId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightTimeLine.lua\t12\n"
        "60070\tSM_FightTimeLine\t3\ttimeLine\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightTimeLine.lua\t13\n"
        "60070\tSM_FightTimeLine\t4\ttimeLineType\tByte\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightTimeLine.lua\t14\n"
        "60070\tSM_FightTimeLine\t5\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightTimeLine.lua\t15\n"
        "60070\tSM_FightTimeLine\t6\tbuffId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightTimeLine.lua\t16\n"
        "60026\tMoveSkillEffectVO\t1\tforceMoveVOs\tMessageList2List\tForceMoveVO\tvalue_object\tscene.fight\tmessage_demo\tMoveSkillEffectVO.lua\t11\n"
        "60029\tSM_SkillEffect\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SkillEffect.lua\t11\n"
        "60029\tSM_SkillEffect\t2\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SkillEffect.lua\t12\n"
        "60029\tSM_SkillEffect\t3\tskillEffectVO\tBean\tSkillEffectVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_SkillEffect.lua\t13\n"
        "60038\tSM_UpdateCd\t1\tskill2cd\tMessageMap2Dic\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UpdateCd.lua\t11\n"
        "60041\tSM_UpdateSelect\t1\tid\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UpdateSelect.lua\t11\n"
        "60041\tSM_UpdateSelect\t2\tcanSelect\tBoolean\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UpdateSelect.lua\t12\n"
        "60044\tSM_SyncUnit\t1\tcurrHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t11\n"
        "60044\tSM_SyncUnit\t2\tmaxHp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t12\n"
        "60044\tSM_SyncUnit\t3\tcurrMp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t13\n"
        "60044\tSM_SyncUnit\t4\tmaxMp\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t14\n"
        "60044\tSM_SyncUnit\t5\trunSpeed\tDouble\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t15\n"
        "60044\tSM_SyncUnit\t6\tsystemTime\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t16\n"
        "60044\tSM_SyncUnit\t7\tgroupId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t17\n"
        "60044\tSM_SyncUnit\t8\tskills\tMessageList2List\tSkillInfoVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t18\n"
        "60044\tSM_SyncUnit\t9\tcds\tMessageList2List\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t19\n"
        "60044\tSM_SyncUnit\t10\tchargeLv\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_SyncUnit.lua\t20\n"
        "60043\tSM_TestAdjustDirect\t1\tpos\tBean\tGrid3DVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestAdjustDirect.lua\t11\n"
        "60044\tSM_TestShape\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t11\n"
        "60044\tSM_TestShape\t2\tcenter\tBean\tGrid3DVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t12\n"
        "60044\tSM_TestShape\t3\tdir\tBean\tGrid3DVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t13\n"
        "60044\tSM_TestShape\t4\tshapeType\tByte\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t14\n"
        "60044\tSM_TestShape\t5\twidth\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t15\n"
        "60044\tSM_TestShape\t6\theight\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t16\n"
        "60044\tSM_TestShape\t7\tangle\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t17\n"
        "60044\tSM_TestShape\t8\ttoCheck\tMessageList2List\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_TestShape.lua\t18\n"
        "60047\tSM_FightChannel\t1\tcasterId\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightChannel.lua\t11\n"
        "60047\tSM_FightChannel\t2\tskillId\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightChannel.lua\t12\n"
        "60047\tSM_FightChannel\t3\tchannellingCount\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightChannel.lua\t13\n"
        "60047\tSM_FightChannel\t4\tfightCastVO\tBean\tFightCastVO\tserver_to_client\tscene.fight\tmessage_demo\tSM_FightChannel.lua\t14\n"
        "60066\tSM_UnitState\t1\tid\tLong\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitState.lua\t11\n"
        "60066\tSM_UnitState\t2\tstate\tInt\t\tserver_to_client\tscene.fight\tmessage_demo\tSM_UnitState.lua\t12\n"
        "30013\tSM_ChangedPlayerAttribute\t1\tattributes\tMessageMap2Dic\t\tserver_to_client\tplayer.role\tmessage_demo\tSM_ChangedPlayerAttribute.lua\t16\n"
        "30013\tSM_ChangedPlayerAttribute\t2\tunitId\tLong\t\tserver_to_client\tplayer.role\tmessage_demo\tSM_ChangedPlayerAttribute.lua\t17\n"
        "30017\tSM_FightScore\t1\tscore\tDouble\t\tserver_to_client\tplayer.role\tmessage_demo\tSM_FightScore.lua\t14\n"
        "30018\tSM_ModuleFightScore\t1\tmoduleName2FightScore\tMessageMap2Dic\t\tserver_to_client\tplayer.role\tmessage_demo\tSM_ModuleFightScore.lua\t15\n"
        "30019\tChangedAttrsVo\t1\taddAttrs\tMessageMap2Dic\t\tvalue_object\tcommon.attribute\tmessage_demo\tChangedAttrsVo.lua\t19\n"
        "30019\tChangedAttrsVo\t2\tsubAttrs\tMessageMap2Dic\t\tvalue_object\tcommon.attribute\tmessage_demo\tChangedAttrsVo.lua\t20\n"
        "30019\tChangedAttrsVo\t3\tfinalAttrs\tMessageMap2Dic\t\tvalue_object\tcommon.attribute\tmessage_demo\tChangedAttrsVo.lua\t21\n"
        "30021\tSM_RoleChangedAttrs\t1\tattrs\tBean\tChangedAttrsVo\tserver_to_client\tcommon.attribute\tmessage_demo\tSM_RoleChangedAttrs.lua\t22\n"
        "30046\tSM_RealmUpRewardAttr\t1\tattrs\tBean\tChangedAttrsVo\tserver_to_client\tplayer.role\tmessage_demo\tSM_RealmUpRewardAttr.lua\t22\n"
        "42206\tSM_TakeMedicineAttributeSync\t1\tattrs\tBean\tChangedAttrsVo\tserver_to_client\tplayer.medicine\tmessage_demo\tSM_TakeMedicineAttributeSync.lua\t23\n"
        "42206\tSM_TakeMedicineAttributeSync\t2\texp\tLong\t\tserver_to_client\tplayer.medicine\tmessage_demo\tSM_TakeMedicineAttributeSync.lua\t24\n"
        "30741\tSM_GongFaView\t1\tactives\tMessageMap2Dic\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaView.lua\t23\n"
        "30741\tSM_GongFaView\t2\txinFaPutUpList\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaView.lua\t24\n"
        "30741\tSM_GongFaView\t3\tfazePutUpList\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaView.lua\t25\n"
        "30741\tSM_GongFaView\t4\tskillList\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaView.lua\t26\n"
        "30741\tSM_GongFaView\t5\tprogramVOList\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaView.lua\t27\n"
        "30251\tSimpleItemVO\t1\tbaseId\tInt\t\tvalue_object\tplayer.backpack\tmessage_demo\tSimpleItemVO.lua\t16\n"
        "30251\tSimpleItemVO\t2\tid\tLong\t\tvalue_object\tplayer.backpack\tmessage_demo\tSimpleItemVO.lua\t17\n"
        "30251\tSimpleItemVO\t3\tnum\tInt\t\tvalue_object\tplayer.backpack\tmessage_demo\tSimpleItemVO.lua\t18\n"
        "30742\tGongFaItemVO\t1\tgrade\tInt\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t22\n"
        "30742\tGongFaItemVO\t2\tjie\tInt\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t23\n"
        "30742\tGongFaItemVO\t3\tstar\tInt\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t24\n"
        "30742\tGongFaItemVO\t4\tpin\tInt\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t25\n"
        "30742\tGongFaItemVO\t5\ttongxuan\tInt\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t26\n"
        "30742\tGongFaItemVO\t6\tquality\tInt\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t27\n"
        "30742\tGongFaItemVO\t7\ttotalExp\tLong\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t28\n"
        "30742\tGongFaItemVO\t8\tqualityNum\tMessageMap2Dic\t\tvalue_object\tplayer.gongfa\tmessage_demo\tGongFaItemVO.lua\t29\n"
        "30744\tCM_GongFaUpgrade\t1\ttype\tInt\t\tclient_to_server\tplayer.gongfa\tmessage_demo\tCM_GongFaUpgrade.lua\t16\n"
        "30744\tCM_GongFaUpgrade\t2\tbaseId\tInt\t\tclient_to_server\tplayer.gongfa\tmessage_demo\tCM_GongFaUpgrade.lua\t17\n"
        "30744\tCM_GongFaUpgrade\t3\ttimes\tInt\t\tclient_to_server\tplayer.gongfa\tmessage_demo\tCM_GongFaUpgrade.lua\t18\n"
        "30745\tSM_GongFaUpgrade\t1\tgongfa\tBean\tGongFaItemVO\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaUpgrade.lua\t35\n"
        "30745\tSM_GongFaUpgrade\t2\tresults\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaUpgrade.lua\t36\n"
        "30745\tSM_GongFaUpgrade\t3\trewardResults\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaUpgrade.lua\t37\n"
        "30745\tSM_GongFaUpgrade\t4\tattrs\tBean\tChangedAttrsVo\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaUpgrade.lua\t39\n"
        "30745\tSM_GongFaUpgrade\t5\tupgradeQuality\tBool\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaUpgrade.lua\t40\n"
        "30748\tCM_GongFaLearn\t1\tbaseId\tInt\t\tclient_to_server\tplayer.gongfa\tmessage_demo\tCM_GongFaLearn.lua\t14\n"
        "30749\tSM_GongFaLearn\t1\tgongfa\tBean\tGongFaItemVO\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaLearn.lua\t34\n"
        "30749\tSM_GongFaLearn\t2\tresults\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaLearn.lua\t35\n"
        "30749\tSM_GongFaLearn\t3\trewardResults\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaLearn.lua\t36\n"
        "30749\tSM_GongFaLearn\t4\tattrs\tBean\tChangedAttrsVo\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaLearn.lua\t38\n"
        "35770\tCM_GongFaUpgradeTimes\t1\tupgradeList\tMessageList2List\t\tclient_to_server\tplayer.gongfa\tmessage_demo\tCM_GongFaUpgradeTimes.lua\t15\n"
        "35771\tSM_GongFaUpgradeTimes\t1\tupgradeList\tMessageList2List\t\tserver_to_client\tplayer.gongfa\tmessage_demo\tSM_GongFaUpgradeTimes.lua\t15\n"
        "32212\tSkillInfoVO\t1\tskillId\tInt\t\tvalue_object\tplayer.skill\tmessage_demo\tSkillInfoVO.lua\t11\n"
        "32212\tSkillInfoVO\t2\tjie\tShort\t\tvalue_object\tplayer.skill\tmessage_demo\tSkillInfoVO.lua\t12\n"
        "32212\tSkillInfoVO\t3\tstar\tShort\t\tvalue_object\tplayer.skill\tmessage_demo\tSkillInfoVO.lua\t13\n"
        "32212\tSkillInfoVO\t4\ttype\tInt\t\tvalue_object\tplayer.skill\tmessage_demo\tSkillInfoVO.lua\t14\n"
        "32212\tSkillInfoVO\t5\tmakeId\tLong\t\tvalue_object\tplayer.skill\tmessage_demo\tSkillInfoVO.lua\t15\n"
        "32202\tSM_ReplaceSkill\t1\tsystemTime\tLong\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ReplaceSkill.lua\t11\n"
        "32202\tSM_ReplaceSkill\t2\tgroupId\tInt\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ReplaceSkill.lua\t12\n"
        "32202\tSM_ReplaceSkill\t3\tskills\tMessageList2List\tSkillInfoVO\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ReplaceSkill.lua\t13\n"
        "32202\tSM_ReplaceSkill\t4\tcds\tMessageList2List\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ReplaceSkill.lua\t14\n"
        "32208\tSM_ChangeGroup\t1\tsystemTime\tLong\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ChangeGroup.lua\t11\n"
        "32208\tSM_ChangeGroup\t2\tgroupId\tInt\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ChangeGroup.lua\t12\n"
        "32208\tSM_ChangeGroup\t3\tskills\tMessageList2List\tSkillInfoVO\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ChangeGroup.lua\t13\n"
        "32208\tSM_ChangeGroup\t4\tcds\tMessageList2List\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_ChangeGroup.lua\t14\n"
        "32211\tSM_AutoReplace\t1\tsystemTime\tLong\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_AutoReplace.lua\t11\n"
        "32211\tSM_AutoReplace\t2\tgroupId\tInt\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_AutoReplace.lua\t12\n"
        "32211\tSM_AutoReplace\t3\tskills\tMessageList2List\tSkillInfoVO\tserver_to_client\tplayer.skill\tmessage_demo\tSM_AutoReplace.lua\t13\n"
        "32211\tSM_AutoReplace\t4\tcds\tMessageList2List\t\tserver_to_client\tplayer.skill\tmessage_demo\tSM_AutoReplace.lua\t14\n",
        encoding="utf-8",
    )
    lingjie_star_dir = export_root / "parsed_configs" / "LingjieGongfaStar"
    skill_config_dir = export_root / "parsed_configs" / "Skill"
    lingjie_star_dir.mkdir(parents=True)
    skill_config_dir.mkdir(parents=True)
    (lingjie_star_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 10001,
                    "gongfaId": 316101,
                    "star": 1,
                    "skill": 378101000,
                    "cd_plain": "25秒",
                    "describe": "进入战斗后触发",
                    "param": [0, 0, 5],
                },
                {
                    "id": 10002,
                    "gongfaId": 316101,
                    "star": 2,
                    "skill": 378101010,
                    "cd_plain": "25秒",
                    "describe": "二星效果",
                    "param": [0, 0, 6],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (skill_config_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 378101000,
                    "name_plain": "剑01",
                    "skillType": 5,
                    "targetType": 1,
                    "targetMax": 1,
                    "fightScore": 16000,
                    "jian_timelineId": ["357101000"],
                    "mo_timelineId": ["357101100"],
                },
                {
                    "id": 378101010,
                    "name_plain": "剑01二星",
                    "skillType": 5,
                    "targetType": 1,
                    "targetMax": 1,
                    "fightScore": 16060,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    skill_ex_dir = export_root / "parsed_configs" / "SkillExParams"
    lingjie_jie_config_dir = export_root / "parsed_configs" / "LingjieGongfaJie"
    main_feature_pin_config_dir = export_root / "parsed_configs" / "MainFeaturePin"
    sound_config_dir = export_root / "parsed_configs" / "Sound"
    attribute_source_dir = (
        export_root / "by_source" / "lscripts" / "generate" / "cfg" / "attribute_demo" / "text_assets"
    )
    skill_ex_dir.mkdir(parents=True)
    lingjie_jie_config_dir.mkdir(parents=True)
    main_feature_pin_config_dir.mkdir(parents=True)
    sound_config_dir.mkdir(parents=True)
    attribute_source_dir.mkdir(parents=True)
    (skill_ex_dir / "rows.json").write_text(
        json.dumps(
            [
                {"id": 357101000, "channel": "BYPERIOD|1400,467"},
                {"id": 357101100, "channel": "BYPERIOD|1500,500"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lingjie_jie_config_dir / "rows.json").write_text(
        json.dumps(
            [{"id": 1000011, "gongfaId": 316101, "jie": 11, "feature": 378101010, "param": [9, 11, 0]}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (main_feature_pin_config_dir / "rows.json").write_text(
        json.dumps(
            [{"id": 31610111, "gongfaId": 316101, "pin": 11, "quality": 7, "feature": 378101010, "name": "【剑影如风】"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sound_config_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 12345,
                    "type": 5,
                    "loop": 0,
                    "soundEventName": "Play_test_skill",
                    "soundEventId": 67890,
                    "soundLength": 1000,
                    "soundBank": "skill_bank",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (attribute_source_dir / "Attribute.lua").write_text(
        "local c=require('Generate.Cfg.bean')\n"
        "local _key2index={code=1,id=2,name=3,expShow=4,group=5,display_roles=6,descript=7,sort=8,showTips=9,iconPatch=10,icon=11}\n"
        "local _key2null={[1]=0,[2]='',[3]='',[4]=0,[5]='',[6]=0,[7]='',[8]=0,[9]=0,[10]='',[11]=''}\n"
        "local _key2type={[1]=0,[2]=0,[3]=1,[4]=0,[5]=0,[6]=0,[7]=1,[8]=0,[9]=0,[10]=0,[11]=0}\n"
        "local _P=c.Init(_key2index,_key2null,_key2type)\n"
        "local _M={\n"
        "[4]=setmetatable({[1]=4,[2]='FIGHT_POWER',[3]='战斗力',[5]='Value',[6]=1,[7]='战力说明',[8]=4,[9]=1,[10]='role',[11]='role_icon_power'},_P),\n"
        "[1001]=setmetatable({[1]=1001,[2]='MAXHP',[3]='气血',[5]='Ratio',[6]=1,[7]='气血说明',[8]=101,[9]=1,[10]='role',[11]='role_icon_hp'},_P),\n"
        "}\n"
        "return _M\n",
        encoding="utf-8",
    )
    attack_track = json.dumps(
        [
            json.dumps(
                {
                    "TrackName": "特效通道",
                    "TrackValue": json.dumps(
                        {
                            "ClipDataList": [
                                {
                                    "ClipType": 2,
                                    "Start_Frame": 0,
                                    "End_Frame": 3,
                                    "args": {
                                        "res_Name": "skill/eff_test",
                                        "action_Name": "act_test",
                                        "Sound_Id": 12345,
                                    },
                                },
                                {
                                    "ClipType": 7,
                                    "Start_Frame": 42,
                                    "End_Frame": 43,
                                    "args": {
                                        "Frame": 42,
                                        "Hurt_Precent": 100,
                                        "Hurt_Multi_Count": 0,
                                        "Hurt_Multi_Duration": 0.0,
                                        "Real_Multi_Hurt": True,
                                        "Damage_Center_Type": 0,
                                        "Damage_Scope_Type": 0,
                                        "Scope_Param1": 0.0,
                                        "Scope_Param2": 0.0,
                                    },
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
                ensure_ascii=False,
            )
        ],
        ensure_ascii=False,
    )
    (text_dir / "357101000.lua").write_text(
        "local _key2index={q_skillID=1,q_type=2,q_desc=3,q_hurt_events=4,q_keyframe_events=5,"
        "q_track_time=6,q_timeline_displayName=7,q_timeline_attacktrack=8,q_timeline_suffertrack=9,}\n"
        "local _M={\n"
        f"[357101000]=setmetatable({{357101000,\"主角灵界技能\",\"测试\",{{1400,100,0,1}},{{0,1000,0}},"
        f"1500,\"TimeLine357101000\",{json.dumps(attack_track, ensure_ascii=False)},\"[]\"}},_P),\n"
        "}\n"
        "return _M\n",
        encoding="utf-8",
    )
    resource_root = tmp_path / "game_files"
    (resource_root / "effect" / "skill").mkdir(parents=True)
    (resource_root / "playable" / "skill").mkdir(parents=True)
    (resource_root / "Audio" / "GeneratedSoundBanks" / "Android").mkdir(parents=True)
    (resource_root / "effect" / "skill" / "eff_test_abcd.bytes").write_bytes(b"UnityFS effect")
    (resource_root / "playable" / "skill" / "timeline357101000_abcd.bytes").write_bytes(b"UnityFS playable")
    (resource_root / "Audio" / "GeneratedSoundBanks" / "Android" / "skill_bank.bnk").write_bytes(
        b"BKHD" + struct.pack("<I", 0) + b"HIRC" + struct.pack("<I", 4) + struct.pack("<I", 67890)
    )
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))
    monkeypatch.setenv(FANXIU_RESOURCE_ROOT_ENV, str(resource_root))

    result = build_fanxiu_lingjie_gongfa_runtime_report()
    refs_text = Path(result["files"]["config_refs_tsv"]).read_text(encoding="utf-8-sig")
    packets_text = Path(result["files"]["packets_tsv"]).read_text(encoding="utf-8-sig")
    net_functions_text = Path(result["files"]["net_functions_tsv"]).read_text(encoding="utf-8-sig")
    call_sites_text = Path(result["files"]["net_call_sites_tsv"]).read_text(encoding="utf-8-sig")
    vo_fields_text = Path(result["files"]["vo_fields_tsv"]).read_text(encoding="utf-8-sig")
    vo_usage_text = Path(result["files"]["vo_usage_tsv"]).read_text(encoding="utf-8-sig")
    battle_refs_text = Path(result["files"]["battle_refs_tsv"]).read_text(encoding="utf-8-sig")
    projected_skills_text = Path(result["files"]["projected_skills_tsv"]).read_text(encoding="utf-8-sig")
    skill_next_hops_text = Path(result["files"]["skill_next_hops_tsv"]).read_text(encoding="utf-8-sig")
    battle_damage_flow_text = Path(result["files"]["battle_damage_flow_tsv"]).read_text(encoding="utf-8-sig")
    fight_result_schema_text = Path(result["files"]["fight_result_schema_tsv"]).read_text(encoding="utf-8-sig")
    fight_result_to_hurt_data_text = Path(result["files"]["fight_result_to_hurt_data_tsv"]).read_text(
        encoding="utf-8-sig"
    )
    fight_result_boundary_text = Path(result["files"]["fight_result_boundary_tsv"]).read_text(encoding="utf-8-sig")
    hp_update_side_paths_text = Path(result["files"]["hp_update_side_paths_tsv"]).read_text(encoding="utf-8-sig")
    fight_state_sync_paths_text = Path(result["files"]["fight_state_sync_paths_tsv"]).read_text(encoding="utf-8-sig")
    fight_request_intents_text = Path(result["files"]["fight_request_intents_tsv"]).read_text(encoding="utf-8-sig")
    fight_cast_broadcast_flow_text = Path(result["files"]["fight_cast_broadcast_flow_tsv"]).read_text(encoding="utf-8-sig")
    skill_instance_lifecycle_text = Path(result["files"]["skill_instance_lifecycle_tsv"]).read_text(encoding="utf-8-sig")
    fight_authority_boundaries_text = Path(result["files"]["fight_authority_boundaries_tsv"]).read_text(encoding="utf-8-sig")
    fight_side_channels_text = Path(result["files"]["fight_side_channels_tsv"]).read_text(encoding="utf-8-sig")
    fight_status_codes_text = Path(result["files"]["fight_status_codes_tsv"]).read_text(encoding="utf-8-sig")
    sync_unit_skill_cd_text = Path(result["files"]["sync_unit_skill_cd_tsv"]).read_text(encoding="utf-8-sig")
    sync_unit_state_text = Path(result["files"]["sync_unit_state_tsv"]).read_text(encoding="utf-8-sig")
    role_attribute_sync_text = Path(result["files"]["role_attribute_sync_tsv"]).read_text(encoding="utf-8-sig")
    attribute_definitions_text = Path(result["files"]["attribute_definitions_tsv"]).read_text(encoding="utf-8-sig")
    gongfa_attr_change_text = Path(result["files"]["gongfa_attr_change_tsv"]).read_text(encoding="utf-8-sig")
    gongfa_state_text = Path(result["files"]["gongfa_state_tsv"]).read_text(encoding="utf-8-sig")
    gongfa_attr_display_text = Path(result["files"]["gongfa_attr_display_tsv"]).read_text(encoding="utf-8-sig")
    gongfa_rich_text_text = Path(result["files"]["gongfa_rich_text_tsv"]).read_text(encoding="utf-8-sig")
    gongfa_localization_templates_text = Path(result["files"]["gongfa_localization_templates_tsv"]).read_text(
        encoding="utf-8-sig"
    )
    gongfa_description_composition_text = Path(result["files"]["gongfa_description_composition_tsv"]).read_text(
        encoding="utf-8-sig"
    )
    fight_effect_flags_text = Path(result["files"]["fight_effect_flags_tsv"]).read_text(encoding="utf-8-sig")
    fight_config_values_text = Path(result["files"]["fight_config_values_tsv"]).read_text(encoding="utf-8-sig")
    hurt_tips_config_text = Path(result["files"]["hurt_tips_config_tsv"]).read_text(encoding="utf-8-sig")
    blood_type_ui_text = Path(result["files"]["blood_type_ui_tsv"]).read_text(encoding="utf-8-sig")
    hurt_data_blood_sources_text = Path(result["files"]["hurt_data_blood_sources_tsv"]).read_text(encoding="utf-8-sig")
    projected_skill_damage_profiles_text = Path(result["files"]["projected_skill_damage_profiles_tsv"]).read_text(
        encoding="utf-8-sig"
    )
    projected_skill_damage_families_text = Path(result["files"]["projected_skill_damage_families_tsv"]).read_text(
        encoding="utf-8-sig"
    )
    timeline_details_text = Path(result["files"]["timeline_details_tsv"]).read_text(encoding="utf-8-sig")
    timeline_clips_text = Path(result["files"]["timeline_clips_tsv"]).read_text(encoding="utf-8-sig")
    timeline_clip_types_text = Path(result["files"]["timeline_clip_types_tsv"]).read_text(encoding="utf-8-sig")
    timeline_hit_frames_text = Path(result["files"]["timeline_hit_frames_tsv"]).read_text(encoding="utf-8-sig")
    timeline_channel_alignment_text = Path(result["files"]["timeline_channel_alignment_tsv"]).read_text(encoding="utf-8-sig")
    effect_assets_text = Path(result["files"]["effect_assets_tsv"]).read_text(encoding="utf-8-sig")
    effect_bundle_objects_text = Path(result["files"]["effect_bundle_objects_tsv"]).read_text(encoding="utf-8-sig")
    playable_bundle_objects_text = Path(result["files"]["playable_bundle_objects_tsv"]).read_text(encoding="utf-8-sig")
    timeline_sound_refs_text = Path(result["files"]["timeline_sound_refs_tsv"]).read_text(encoding="utf-8-sig")

    assert result["stats"]["config_ref_count"] == 2
    assert result["stats"]["packet_count"] == 4
    assert result["stats"]["value_object_count"] == 2
    assert result["stats"]["net_function_count"] == 2
    assert result["stats"]["net_call_site_count"] == 1
    assert result["stats"]["battle_integration_ref_count"] >= 2
    assert result["stats"]["battle_damage_flow_ref_count"] >= 8
    assert "server_result_damage_split" in result["stats"]["battle_damage_stage_counts"]
    assert result["stats"]["fight_result_schema_count"] == 2
    assert result["stats"]["fight_result_schema_field_count"] == 15
    assert result["stats"]["fight_result_to_hurt_data_count"] >= 16
    assert result["stats"]["fight_result_to_hurt_data_field_count"] >= 7
    assert result["stats"]["fight_result_boundary_count"] >= 10
    assert result["stats"]["fight_result_boundary_kind_count"] >= 5
    assert result["stats"]["hp_update_side_path_count"] >= 25
    assert result["stats"]["hp_update_side_path_field_count"] == 21
    assert result["stats"]["hp_update_side_path_param_count"] >= 48
    assert result["stats"]["fight_state_sync_count"] >= 35
    assert result["stats"]["fight_state_sync_field_count"] == 15
    assert result["stats"]["fight_state_sync_hurtdata_count"] == 0
    assert result["stats"]["fight_request_intent_count"] >= 60
    assert result["stats"]["fight_request_intent_request_field_count"] == 24
    assert result["stats"]["fight_request_intent_damage_field_count"] == 0
    assert result["stats"]["fight_request_intent_send_count"] == 4
    assert result["stats"]["fight_cast_broadcast_flow_count"] >= 25
    assert result["stats"]["fight_cast_broadcast_flow_file_count"] >= 7
    assert result["stats"]["fight_cast_broadcast_flow_skill_start_count"] >= 2
    assert result["stats"]["skill_instance_lifecycle_count"] >= 35
    assert result["stats"]["skill_instance_lifecycle_file_count"] >= 6
    assert result["stats"]["skill_instance_lifecycle_stage_count"] >= 7
    assert result["stats"]["skill_instance_lifecycle_result_to_hurtdata_count"] >= 1
    assert result["stats"]["skill_instance_lifecycle_hurt_execute_count"] >= 1
    assert result["stats"]["fight_authority_boundary_count"] >= 15
    assert result["stats"]["fight_authority_boundary_phase_count"] >= 10
    assert result["stats"]["fight_authority_boundary_server_authority_count"] >= 6
    assert result["stats"]["fight_side_channel_count"] >= 40
    assert result["stats"]["fight_side_channel_packet_count"] >= 12
    assert result["stats"]["fight_side_channel_runtime_count"] >= 20
    assert result["stats"]["fight_side_channel_group_count"] >= 7
    assert result["stats"]["fight_status_code_count"] >= 20
    assert result["stats"]["fight_restrict_status_enum_count"] >= 7
    assert result["stats"]["fight_restrict_status_usage_count"] >= 7
    assert result["stats"]["fight_unit_state_enum_count"] == 4
    assert result["stats"]["fight_unit_state_usage_count"] >= 4
    assert result["stats"]["sync_unit_skill_cd_count"] >= 35
    assert result["stats"]["sync_unit_skill_cd_packet_field_count"] >= 25
    assert result["stats"]["sync_unit_skill_cd_runtime_count"] >= 15
    assert result["stats"]["sync_unit_skill_cd_stage_count"] >= 6
    assert result["stats"]["sync_unit_skill_cd_skillinfo_field_count"] == 5
    assert result["stats"]["sync_unit_state_count"] >= 25
    assert result["stats"]["sync_unit_state_packet_field_count"] >= 19
    assert result["stats"]["sync_unit_state_runtime_count"] >= 20
    assert result["stats"]["sync_unit_state_gap_count"] == 0
    assert result["stats"]["role_attribute_sync_count"] >= 35
    assert result["stats"]["role_attribute_sync_packet_field_count"] == 11
    assert result["stats"]["role_attribute_sync_runtime_count"] >= 25
    assert result["stats"]["role_attribute_sync_stage_count"] >= 6
    assert result["stats"]["role_attribute_sync_changed_attrs_field_count"] == 3
    assert result["stats"]["role_attribute_sync_property_write_count"] >= 3
    assert result["stats"]["attribute_definition_count"] == 2
    assert result["stats"]["attribute_definition_show_tips_count"] == 2
    assert result["stats"]["attribute_definition_ratio_group_count"] == 1
    assert result["stats"]["attribute_definition_fight_power_count"] == 1
    assert result["stats"]["gongfa_attr_change_count"] >= 35
    assert result["stats"]["gongfa_attr_change_packet_field_count"] >= 18
    assert result["stats"]["gongfa_attr_change_runtime_count"] >= 20
    assert result["stats"]["gongfa_attr_change_apply_count"] == 3
    assert result["stats"]["gongfa_state_count"] >= 45
    assert result["stats"]["gongfa_state_packet_field_count"] >= 18
    assert result["stats"]["gongfa_state_packet_no_field_count"] == 1
    assert result["stats"]["gongfa_state_runtime_count"] >= 25
    assert result["stats"]["gongfa_state_inherited_simple_item_field_count"] == 3
    assert result["stats"]["gongfa_state_set_vo_callsite_count"] == 0
    assert result["stats"]["gongfa_state_gap_count"] == 1
    assert result["stats"]["gongfa_state_static_catalog_count"] >= 3
    assert result["stats"]["gongfa_state_vo_overlay_count"] >= 3
    assert result["stats"]["gongfa_attr_display_count"] >= 25
    assert result["stats"]["gongfa_attr_display_stage_count"] >= 5
    assert result["stats"]["gongfa_attr_display_attribute_config_ref_count"] >= 4
    assert result["stats"]["gongfa_attr_display_preview_call_count"] >= 3
    assert result["stats"]["gongfa_attr_display_format_count"] >= 3
    assert result["stats"]["gongfa_rich_text_count"] >= 20
    assert result["stats"]["gongfa_rich_text_stage_count"] >= 5
    assert result["stats"]["gongfa_rich_text_localization_key_count"] >= 5
    assert result["stats"]["gongfa_rich_text_color_ref_count"] >= 5
    assert result["stats"]["gongfa_rich_text_config_description_count"] >= 4
    assert result["stats"]["gongfa_rich_text_render_count"] >= 4
    assert result["stats"]["gongfa_localization_template_count"] >= 9
    assert result["stats"]["gongfa_localization_template_ok_count"] >= 9
    assert result["stats"]["gongfa_localization_template_missing_count"] == 0
    assert result["stats"]["gongfa_localization_template_color_count"] >= 9
    assert result["stats"]["gongfa_localization_template_href_count"] >= 2
    assert result["stats"]["gongfa_localization_template_placeholder_count"] >= 20
    assert result["stats"]["gongfa_description_composition_count"] >= 20
    assert result["stats"]["gongfa_description_composition_stage_count"] >= 3
    assert result["stats"]["gongfa_description_composition_localization_key_count"] >= 5
    assert result["stats"]["gongfa_description_composition_tongxuan_count"] >= 5
    assert result["stats"]["gongfa_description_composition_effect_template_count"] >= 4
    assert result["stats"]["fight_effect_flag_count"] == 8
    assert result["stats"]["fight_effect_formatted_flag_count"] >= 5
    assert result["stats"]["hurt_tips_type_count"] == 2
    assert result["stats"]["fight_config_value_count"] == 3
    assert result["stats"]["hurt_tips_config_row_count"] == 6
    assert result["stats"]["blood_type_count"] == 5
    assert result["stats"]["blood_type_ui_count"] == 2
    assert result["stats"]["blood_type_animation_count"] == 2
    assert result["stats"]["hurt_data_blood_source_count"] >= 4
    assert result["stats"]["hurt_data_direct_show_count"] >= 2
    assert result["stats"]["hurt_data_simple_aggregate_count"] >= 1
    assert result["stats"]["hurt_tips_type_decode_count"] >= 1
    assert result["stats"]["projected_skill_count"] == 2
    assert result["stats"]["projected_skill_matched_count"] == 2
    assert result["stats"]["skill_next_hop_timeline_skill_count"] == 1
    assert result["stats"]["skill_next_hop_feature_reuse_count"] == 1
    assert result["stats"]["projected_skill_damage_profile_count"] == 2
    assert result["stats"]["projected_skill_damage_profile_skill_count"] == 1
    assert result["stats"]["projected_skill_damage_profile_timeline_count"] == 2
    assert result["stats"]["projected_skill_damage_profile_missing_timeline_count"] == 1
    assert result["stats"]["projected_skill_damage_family_count"] == 1
    assert result["stats"]["projected_skill_damage_family_skipped_profile_count"] == 1
    assert result["stats"]["timeline_detail_ok_count"] == 1
    assert result["stats"]["timeline_detail_missing_lua_count"] == 1
    assert result["stats"]["timeline_playable_asset_count"] == 1
    assert result["stats"]["timeline_clip_event_count"] == 2
    assert result["stats"]["timeline_clip_effect_count"] == 1
    assert result["stats"]["timeline_clip_hit_frame_count"] == 1
    assert result["stats"]["timeline_clip_sound_id_ref_count"] == 1
    assert result["stats"]["timeline_clip_type_summary_count"] == 2
    assert result["stats"]["timeline_hit_frame_count"] == 1
    assert result["stats"]["timeline_hit_frame_hurt_event_match_count"] == 1
    assert result["stats"]["timeline_channel_alignment_count"] == 1
    assert result["stats"]["timeline_channel_alignment_matched_count"] == 1
    assert result["stats"]["effect_asset_ref_count"] == 1
    assert result["stats"]["effect_asset_ok_count"] == 1
    assert result["stats"]["effect_asset_missing_count"] == 0
    assert result["stats"]["effect_bundle_object_asset_count"] == 1
    assert result["stats"]["playable_bundle_object_asset_count"] == 1
    assert result["stats"]["timeline_sound_ref_count"] == 1
    assert result["stats"]["timeline_sound_matched_count"] == 1
    assert result["stats"]["timeline_sound_event_bank_hit_count"] == 1
    assert "LingjieGongfa_LingjieGongfaJie" in refs_text
    assert "GetMainFeatureCfgById" in refs_text
    assert "CM_GongFaHomeMakeCombine" in packets_text
    assert "mainId:Int, assist1:Int" in packets_text
    assert "mainId、assist1" in net_functions_text
    assert "DemoView.lua" in call_sites_text
    assert "effectMap" in vo_fields_text
    assert "server_read_only_in_client_class" in vo_fields_text
    assert "SM_GongFaHomeMakeCombine" in vo_usage_text
    assert "GongFaHomeMakeVO" in vo_usage_text
    assert "GongFaBattleCustomView.lua" in battle_refs_text
    assert "makeId" in battle_refs_text
    assert "server_result_damage_split" in battle_damage_flow_text
    assert "damage_num" in battle_damage_flow_text
    assert "real_section_dmg" in battle_damage_flow_text
    assert "SM_FightResult" in fight_result_schema_text
    assert "FightResultVO" in fight_result_schema_text
    assert "results" in fight_result_schema_text
    assert "damageView" in fight_result_schema_text
    assert "服务端下发的展示伤害值" in fight_result_schema_text
    assert "damage_num" in fight_result_to_hurt_data_text
    assert "damage_view" in fight_result_to_hurt_data_text
    assert "FightResultVO.damageView" in fight_result_to_hurt_data_text
    assert "recoverHp_num" in fight_result_to_hurt_data_text
    assert "FightResultVO.recoverHp" in fight_result_to_hurt_data_text
    assert "mpDamageAbsorb_num" in fight_result_to_hurt_data_text
    assert "FightResultVO.mpDamageAbsorb" in fight_result_to_hurt_data_text
    assert "packet_read_field" in fight_result_boundary_text
    assert "net_dispatch_to_actor" in fight_result_boundary_text
    assert "actor_dispatch_to_skill" in fight_result_boundary_text
    assert "skillbase_consume_field" in fight_result_boundary_text
    assert "direct_unit_hp_update" in hp_update_side_paths_text
    assert "buff_change_hp_mp" in hp_update_side_paths_text
    assert "SM_UnitHpUpdate.damage" in hp_update_side_paths_text
    assert "SM_UnitMpUpdate.recoverMp" in hp_update_side_paths_text
    assert "SM_UnitMpUpdate.changeMp" in hp_update_side_paths_text
    assert "BuffResultVO.damageView" in hp_update_side_paths_text
    assert "hurtdata_setdata_param" in hp_update_side_paths_text
    assert "hp_property_sync" in fight_state_sync_paths_text
    assert "SM_HpChange.changeHpMap" in fight_state_sync_paths_text
    assert "fixed_damage_hp_event_smoothing" in fight_state_sync_paths_text
    assert "CommonEventType.HURT_HP_CHANGE" in fight_state_sync_paths_text
    assert "shadow_hp_property_sync" in fight_state_sync_paths_text
    assert "max_hp_property_sync" in fight_state_sync_paths_text
    assert "\tyes\t" not in fight_state_sync_paths_text
    assert "CM_FightByTarget.targetId" in fight_request_intents_text
    assert "CM_FightByTargets.selectTargetIds" in fight_request_intents_text
    assert "CM_FightByDir.selectDir" in fight_request_intents_text
    assert "CM_FightByPosition.selectPos" in fight_request_intents_text
    assert "request_send" in fight_request_intents_text
    assert "client_local_release_precheck" in fight_request_intents_text
    assert "SM_FightCast.fightCastVO" in fight_request_intents_text
    assert "FightCastVO.selectTargetId" in fight_request_intents_text
    assert "\tCM_FightByTarget\tclient_to_server" in fight_request_intents_text
    assert "\tyes\trequest_payload" not in fight_request_intents_text
    assert "decode_fight_cast_vo" in fight_cast_broadcast_flow_text
    assert "route_user_cast" in fight_cast_broadcast_flow_text
    assert "route_release_execute" in fight_cast_broadcast_flow_text
    assert "enter_skill_state" in fight_cast_broadcast_flow_text
    assert "skill_start" in fight_cast_broadcast_flow_text
    assert "register_state_skill" in fight_cast_broadcast_flow_text
    assert "state_enter_next" in fight_cast_broadcast_flow_text
    assert "state_skill_release" in fight_cast_broadcast_flow_text
    assert "base_allow_skill_change" in fight_cast_broadcast_flow_text
    assert "FightCastVO.selectTargetId" in fight_cast_broadcast_flow_text
    assert "TempSkillParam.SkillId" in fight_cast_broadcast_flow_text
    assert "actor_start_skillbase" in skill_instance_lifecycle_text
    assert "skillbase_start_decl" in skill_instance_lifecycle_text
    assert "timeline_play_attack" in skill_instance_lifecycle_text
    assert "fight_result_to_hurtdata" in skill_instance_lifecycle_text
    assert "fight_result_schedule_hurt_frame" in skill_instance_lifecycle_text
    assert "timeline_fire_skill_hurt" in skill_instance_lifecycle_text
    assert "hurt_frame_execute_hurtdata" in skill_instance_lifecycle_text
    assert "trajectory_attach_hurtdata" in skill_instance_lifecycle_text
    assert "HurtData.damage_num" in skill_instance_lifecycle_text or "damage_num" in skill_instance_lifecycle_text
    assert "client_send_intent" in fight_authority_boundaries_text
    assert "server_cast_broadcast" in fight_authority_boundaries_text
    assert "local_skill_instance_start" in fight_authority_boundaries_text
    assert "server_result_to_hurtdata" in fight_authority_boundaries_text
    assert "timeline_hurt_trigger" in fight_authority_boundaries_text
    assert "hurtdata_execute_display" in fight_authority_boundaries_text
    assert "server_hp_property_sync" in fight_authority_boundaries_text
    assert "server_mp_property_sync" in fight_authority_boundaries_text
    assert "fixed_damage_hp_smoothing" in fight_authority_boundaries_text
    assert "qi_property_sync" in fight_side_channels_text
    assert "skill_failed_reset_position" in fight_side_channels_text
    assert "interrupt_stop_runtime" in fight_side_channels_text
    assert "restrict_add_code" in fight_side_channels_text
    assert "timeline_play_attack" in fight_side_channels_text
    assert "timeline_play_suffer" in fight_side_channels_text
    assert "skill_effect_reset_suffer_playable" in fight_side_channels_text
    assert "cd_refresh" in fight_side_channels_text
    assert "select_update" in fight_side_channels_text
    assert "sync_unit_revive_info" in fight_side_channels_text
    assert "test_shape_show_range" in fight_side_channels_text
    assert "fight_channel_reset_attack_playable" in fight_side_channels_text
    assert "unit_state_set" in fight_side_channels_text
    assert "MoveSkillEffectVO.forceMoveVOs" in fight_side_channels_text
    assert "FORBID_MOVE" in fight_status_codes_text
    assert "CANNOT_SELECT_AS_TARGET" in fight_status_codes_text
    assert "FORBID_USE_SKILL_GONGFA" in fight_status_codes_text
    assert "restrict_bit_check" in fight_status_codes_text
    assert "RESTRICT_STATUS_CHANGED" in fight_status_codes_text
    assert "fight_pvp" in fight_status_codes_text
    assert "unit_state_bit_check" in fight_status_codes_text
    assert "PlayerType.UnitState.horse" in fight_status_codes_text
    assert "SM_SyncUnit.skills" in sync_unit_skill_cd_text
    assert "SkillInfoVO.skillId" in sync_unit_skill_cd_text
    assert "set_skill_cd_from_sync_unit" in sync_unit_skill_cd_text
    assert "iterate_skill_cd_pairs" in sync_unit_skill_cd_text
    assert "cdList[index-1]" in sync_unit_skill_cd_text
    assert "store_cd_by_skill_id" in sync_unit_skill_cd_text
    assert "refresh_loaded_actor_skill_cd" in sync_unit_skill_cd_text
    assert "replace_group_cache_update" in sync_unit_skill_cd_text
    assert "change_group_cache_update" in sync_unit_skill_cd_text
    assert "apply_group_after_response" in sync_unit_skill_cd_text
    assert "dispatch_rolemgr_revive_info" in sync_unit_state_text
    assert "rolemgr_revive_info_decl" in sync_unit_state_text
    assert "rolemgr_set_runspeed" in sync_unit_state_text
    assert "LuaEntityPropertyType.RUNSPEED" in sync_unit_state_text
    assert "set_revive_hp" in sync_unit_state_text
    assert "set_revive_mp" in sync_unit_state_text
    assert "set_unit_max_hp" in sync_unit_state_text
    assert "set_shadow_max_hp" in sync_unit_state_text
    assert "SM_RoleChangedAttrs.attrs" in role_attribute_sync_text
    assert "ChangedAttrsVo.finalAttrs" in role_attribute_sync_text
    assert "dispatch_changed_attrs_vo" in role_attribute_sync_text
    assert "set_final_attr_property" in role_attribute_sync_text
    assert "SM_ChangedPlayerAttribute.attributes" in role_attribute_sync_text
    assert "set_attribute_map_property" in role_attribute_sync_text
    assert "SM_FightScore.score" in role_attribute_sync_text
    assert "set_fight_power_property" in role_attribute_sync_text
    assert "LuaEntityPropertyType.FIGHT_POWER" in role_attribute_sync_text
    assert "show_module_fight_score_debug" in role_attribute_sync_text
    assert "FIGHT_POWER\t战斗力" in attribute_definitions_text
    assert "MAXHP\t气血" in attribute_definitions_text
    assert "\tRatio\t" in attribute_definitions_text
    assert "SM_GongFaLearn.attrs" in gongfa_attr_change_text
    assert "SM_GongFaUpgrade.attrs" in gongfa_attr_change_text
    assert "dispatch_learn_to_model" in gongfa_attr_change_text
    assert "dispatch_upgrade_to_model" in gongfa_attr_change_text
    assert "apply_learn_attrs" in gongfa_attr_change_text
    assert "apply_upgrade_attrs" in gongfa_attr_change_text
    assert "apply_batch_upgrade_attrs" in gongfa_attr_change_text
    assert "take_last_final_attrs" in gongfa_attr_change_text
    assert "SM_GongFaView.actives" in gongfa_state_text
    assert "CM_GongFaView" in gongfa_state_text
    assert "SimpleItemVO.baseId" in gongfa_state_text
    assert "inherit_simple_item_vo" in gongfa_state_text
    assert "read_inherited_simple_item_fields" in gongfa_state_text
    assert "GongFaItemVO.grade" in gongfa_state_text
    assert "register_view_response" in gongfa_state_text
    assert "dispatch_view_to_model" in gongfa_state_text
    assert "store_active_map" in gongfa_state_text
    assert "load_gongfa_config" in gongfa_state_text
    assert "create_gongfa_vo_from_config" in gongfa_state_text
    assert "overlay_server_vo" in gongfa_state_text
    assert "update_batch_gongfa_vo" in gongfa_state_text
    assert "visible_gap_no_set_gongfa_vo_caller" in gongfa_state_text
    assert "GongFaNewModel:SetGongFaVo" in gongfa_state_text
    assert "detail_static_attr_entry" in gongfa_attr_display_text
    assert "GetAllAddAttrTb" in gongfa_attr_display_text
    assert "ConfigName.Attribute_Attribute" in gongfa_attr_display_text
    assert "filter_indirect_attr_group" in gongfa_attr_display_text
    assert "GetLevelAndStarAttr" in gongfa_attr_display_text
    assert "preview_next_attr_dispatch" in gongfa_attr_display_text
    assert "compute_attr_add_num" in gongfa_attr_display_text
    assert "mark_new_attr" in gongfa_attr_display_text
    assert "GetViewAttrListShow" in gongfa_attr_display_text
    assert "format_attr_value_number" in gongfa_attr_display_text
    assert "format_attr_value_ratio" in gongfa_attr_display_text
    assert "RatioAttribute" in gongfa_attr_display_text
    assert "ConvertBigDouble" in gongfa_attr_display_text
    assert "ui_attr_item_format_add" in gongfa_attr_display_text
    assert "render_static_gongfa_description" in gongfa_rich_text_text
    assert "Gongfa_Gongfa.descript" in gongfa_rich_text_text
    assert "inline_color_template" in gongfa_rich_text_text
    assert "LuaLocalization.Format:GongFa_LingJie_100" in gongfa_rich_text_text
    assert "LuaLocalization.Format:GongFa_LingJie_101" in gongfa_rich_text_text
    assert "LuaLocalization.Format:GongFa_LingJie_131" in gongfa_rich_text_text
    assert "compose_main_description" in gongfa_rich_text_text
    assert "render_xianshu_description_row" in gongfa_rich_text_text
    assert "set_locked_description_color" in gongfa_rich_text_text
    assert "render_description_item_text" in gongfa_rich_text_text
    assert "GongFa_LingJie_101" in gongfa_localization_templates_text
    assert "<href=67|%s_%s>" in gongfa_localization_templates_text
    assert "<color=#9e1e09>(未激活)</color>" in gongfa_localization_templates_text
    assert "GongFa_Tip_22" in gongfa_localization_templates_text
    assert "<color=#8de349>+%s</color>" in gongfa_localization_templates_text
    assert "\tok\n" in gongfa_localization_templates_text
    assert "format_star_jie_description" in gongfa_description_composition_text
    assert "LingjieGongfa_LingjieGongfaStar.describe" in gongfa_description_composition_text
    assert "append_tongxuan_activation_link" in gongfa_description_composition_text
    assert "wrap_locked_tongxuan_description" in gongfa_description_composition_text
    assert "filter_tongxuan_rows_with_main_description" in gongfa_description_composition_text
    assert "split_tongxuan_by_current_pin" in gongfa_description_composition_text
    assert "format_active_effect_description" in gongfa_description_composition_text
    assert "format_locked_effect_description" in gongfa_description_composition_text
    assert "GongFa_LingJie_131" in gongfa_description_composition_text
    assert "SKILL_MAIN_TARGET" in fight_effect_flags_text
    assert "技能主目标标记" in fight_effect_flags_text
    assert "SPELL_CRIT" in fight_effect_flags_text
    assert "CRIT_HURT" in fight_effect_flags_text
    assert "font_NormalDamage" in fight_config_values_text
    assert "0_1000,3_500,9_250" in fight_config_values_text
    assert "CRIT" in hurt_tips_config_text
    assert "0.5" in hurt_tips_config_text
    assert "HpRecover" in hurt_tips_config_text
    assert "font_special_rose" in hurt_tips_config_text
    assert "PanelBloodTips_1" in blood_type_ui_text
    assert "normalGO" in blood_type_ui_text
    assert "ani_jianxuego" in blood_type_ui_text
    assert "CRIT_HURT" in blood_type_ui_text
    assert "CRIT" in blood_type_ui_text
    assert "SPELL_CRIT" in blood_type_ui_text
    assert "NormalExecute" in hurt_data_blood_sources_text
    assert "ShowHurtTipsByType" in hurt_data_blood_sources_text
    assert "recoverHp_num" in hurt_data_blood_sources_text
    assert "FightResultVO.recoverHp" in hurt_data_blood_sources_text
    assert "HpRecover" in hurt_data_blood_sources_text
    assert "CURE" in hurt_data_blood_sources_text
    assert "378101000" in projected_skills_text
    assert "剑01" in projected_skills_text
    assert "BYPERIOD|1400,467" in skill_next_hops_text
    assert "jie=11" in skill_next_hops_text
    assert "【剑影如风】" in skill_next_hops_text
    assert "378101000" in projected_skill_damage_profiles_text
    assert "357101000" in projected_skill_damage_profiles_text
    assert "BYPERIOD|1400,467" in projected_skill_damage_profiles_text
    assert "100" in projected_skill_damage_profiles_text
    assert "damage_family_001" in projected_skill_damage_families_text
    assert "BYPERIOD|1400,467" in projected_skill_damage_families_text
    assert "skill/eff_test" in timeline_details_text
    assert "act_test" in timeline_details_text
    assert "12345" in timeline_details_text
    assert "skill/eff_test" in timeline_clips_text
    assert "act_test" in timeline_clips_text
    assert "12345" in timeline_clips_text
    assert "effect" in timeline_clip_types_text
    assert "res_Name" in timeline_clip_types_text
    assert "hit_frame" in timeline_clip_types_text
    assert "1400" in timeline_hit_frames_text
    assert "0.0" in timeline_hit_frames_text
    assert "BYPERIOD|1400,467" in timeline_channel_alignment_text
    assert "matched" in timeline_channel_alignment_text
    assert "skill/eff_test" in effect_assets_text
    assert "effect/skill/eff_test_abcd.bytes" in effect_assets_text
    assert "effect/skill/eff_test_abcd.bytes" in effect_bundle_objects_text
    assert "playable/skill/timeline357101000_abcd.bytes" in playable_bundle_objects_text
    assert "Play_test_skill" in timeline_sound_refs_text
    assert "skill_bank.bnk" in timeline_sound_refs_text

    apk_root = tmp_path / "apk"
    metadata_dir = apk_root / "assets" / "bin" / "Data" / "Managed" / "Metadata"
    metadata_dir.mkdir(parents=True)
    (apk_root / "AndroidManifest.xml").write_text("<manifest />", encoding="utf-8")
    (metadata_dir / "global-metadata.dat").write_bytes(
        b"SkillMgr CM_ReplaceSkill GongFaHomeMakeVO CreateSkillCommonVO"
    )
    result_with_apk = build_fanxiu_lingjie_gongfa_runtime_report(apk_root=apk_root)
    apk_hits_text = Path(result_with_apk["files"]["apk_symbol_hits_tsv"]).read_text(encoding="utf-8-sig")
    assert result_with_apk["stats"]["apk_symbol_hit_count"] >= 4
    assert "SkillMgr" in apk_hits_text
    assert "GongFaHomeMakeVO" in apk_hits_text


def test_fanxiu_apk_static_index_builds_dex_tables(tmp_path):
    root = tmp_path / "1023295_unpacked"
    assets_dir = root / "assets"
    lib_dir = root / "lib" / "arm64-v8a"
    assets_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    _write_minimal_dex(root / "classes.dex")
    (root / "AndroidManifest.xml").write_bytes(b"\x03\x00manifest")
    (assets_dir / "multiconfig").write_text(
        "pname=com.example.frxx\nversionName=1.2.3\nversionCode=123\ntargetSdkVersion=30\n",
        encoding="utf-8",
    )
    (assets_dir / "filelist.csv").write_text(
        "path,size,md5,package\nconfig/test.bytes,8,abc123,base\n",
        encoding="utf-8",
    )
    (lib_dir / "libil2cpp.so").write_bytes(b"so")

    result = build_fanxiu_apk_static_index(apk_root=root, export_root=tmp_path / "exports", keyword_hit_limit=100)
    output_dir = Path(result["output_dir"])

    assert result["package"] == "com.example.frxx"
    assert result["counts"]["dex_files"] == 1
    assert result["counts"]["native_libs"] == 1
    assert "downloadFile" in (output_dir / "dex_methods.tsv").read_text(encoding="utf-8")
    assert "download" in (output_dir / "dex_keyword_hits.tsv").read_text(encoding="utf-8")
    assert "unity-il2cpp" in (output_dir / "native_libs.tsv").read_text(encoding="utf-8")
    assert "config/test.bytes" in (output_dir / "asset_filelist.tsv").read_text(encoding="utf-8")


def test_fanxiu_apk_runtime_entry_report_collects_runtime_candidates(tmp_path):
    root = tmp_path / "1023295_unpacked"
    assets_dir = root / "assets"
    data_dir = assets_dir / "bin" / "Data"
    lib_dir = root / "lib" / "arm64-v8a"
    data_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    _write_minimal_dex(root / "classes.dex")
    (root / "AndroidManifest.xml").write_bytes(b"\x03\x00manifest")
    (assets_dir / "filelist.csv").write_text(
        "path,size,md5,package\n"
        "ui/resdownload/winresdownload.bytes,10,md5a,2\n"
        "atlasnew/gongfa.bytes,20,md5b,-1\n",
        encoding="utf-8",
    )
    (data_dir / "globalgamemanagers").write_bytes(
        b"https://prod-config-frxxz.akbing.com/config/android\x00"
        b"GameStart.unity\x00GameResDownLoad.unity\x00"
        b"MU.GameLogic.GameResDownLoad\x00LuaBridge.Load\x00"
        b"AssetBundleEncryptStream\x00HttpDownload\x00"
    )
    (lib_dir / "libtolua.so").write_bytes(b"so")
    (lib_dir / "libil2cpp.so").write_bytes(b"so")

    result = build_fanxiu_apk_runtime_entry_report(apk_root=root, export_root=tmp_path / "exports", max_rows=50)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["candidates"] >= 6
    candidates_text = (output_dir / "apk_runtime_entry_candidates.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "apk_runtime_entry_report.md").read_text(encoding="utf-8")
    assert "config_url" in candidates_text
    assert "https://prod-config-frxxz.akbing.com/config/android" in candidates_text
    assert "GameResDownLoad.unity" in candidates_text
    assert "MU.GameLogic.GameResDownLoad" in candidates_text
    assert "libtolua.so" in candidates_text
    assert "ui/resdownload/winresdownload.bytes" in candidates_text
    assert "凡修 APK 运行入口候选报告" in markdown_text


def test_fanxiu_apk_download_config_report_extracts_local_url_config(tmp_path):
    root = tmp_path / "1023295_unpacked"
    assets_dir = root / "assets"
    data_dir = assets_dir / "bin" / "Data"
    resource_root = tmp_path / "frxx_game_files"
    data_dir.mkdir(parents=True)
    resource_root.mkdir()
    (root / "AndroidManifest.xml").write_bytes(b"\x03\x00manifest")
    (assets_dir / "version.txt").write_text("https://prod-config-frxxz.akbing.com/config/android\n", encoding="utf-8")
    (assets_dir / "filelistVersion").write_text("hash-filelist", encoding="utf-8")
    (assets_dir / "AppVersion.txt").write_text("2.46.700211", encoding="utf-8")
    (assets_dir / "multiconfig").write_text(
        "pname=com.example.frxx\nversionName=2.47.700211\nversionCode=700211\nchannelName=37\n",
        encoding="utf-8",
    )
    (data_dir / "urlconfig").write_bytes(
        b"ResDownLoadURL=http://192.168.65.222/client/frxx_client_trunk_cn_editor/\x00"
        b"ServerListUrl=https://frxxz-test1.eyugame.com/xiuxian-platform/game/server\x00"
        b"BundleVersion=999.999.999\x00"
        b"GeTuiParam={\"datas\":[{\"bundleId\":\"com.sy.frxxz.gw\",\"appSecret\":\"secret-value\"}]}\x00"
    )
    (resource_root / "setting.config").write_text(
        "ResDownLoadURL=https://cdn-frxxz.akbing.com/client/android_prod/v20260522204211/\n"
        "ResDownLoadURLBackup1=https://cdn-frxxz2.akbing.com/client/android_prod/v20260522204211/\n"
        "ServerListUrl=https://prod-login-frxxz.akbing.com/game/server\n",
        encoding="utf-8",
    )
    (resource_root / "filelistVersion").write_text("remote-hash", encoding="utf-8")
    (resource_root / "filelist.csv").write_text("path,size,md5,package\nab,10,md5a,-1\n", encoding="utf-8")

    result = build_fanxiu_apk_download_config_report(apk_root=root, resource_root=resource_root, export_root=tmp_path / "exports")
    output_dir = Path(result["output_dir"])

    entries_text = (output_dir / "apk_download_config_entries.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "apk_download_config_report.md").read_text(encoding="utf-8")
    assert "bootstrap_url" in entries_text
    assert "ResDownLoadURL" in entries_text
    assert "cdn-frxxz.akbing.com/client/android_prod/v20260522204211" in entries_text
    assert "resource_filelistVersion" in entries_text
    assert "remote-hash" in entries_text
    assert "resource_filelist_rows" in entries_text
    assert "BundleVersion" in entries_text
    assert "com.sy.frxxz.gw" in entries_text
    assert "secret-value" not in entries_text
    assert "凡修 APK 下载配置报告" in markdown_text


def test_fanxiu_lua_download_bridge_report_collects_wrapper_and_calls(tmp_path):
    export_root = tmp_path / "exports"
    bridge_dir = export_root / "by_source" / "lscripts" / "core" / "text_assets"
    scene_dir = export_root / "by_source" / "lscripts" / "scene" / "text_assets"
    bridge_dir.mkdir(parents=True)
    scene_dir.mkdir(parents=True)
    (bridge_dir / "LuaGameResDownloadBridge.lua").write_text(
        'local _M={}\n'
        'function _M.ContainsScene(sceneRes)\n'
        'local GameResDownloadBridge=require"LuaBridge.Load.GameResDownloadBridge"\n'
        'return GameResDownloadBridge.ContainsScene(sceneRes)\n'
        'end\n'
        'function _M.DownloadPackage(packageId,finishHandlerId)\n'
        'local GameResDownloadBridge=require"LuaBridge.Load.GameResDownloadBridge"\n'
        'return GameResDownloadBridge.DownloadPackage(packageId,finishHandlerId or 0)\n'
        'end\n'
        'return _M\n',
        encoding="utf-8",
    )
    (scene_dir / "SceneMgr.lua").write_text(
        'local LuaGameResDownloadBridge=require"Core.Engine.CommonSystem.Asset.LuaGameResDownloadBridge"\n'
        'if not LuaGameResDownloadBridge.ContainsScene(sceneInfo.sceneRes) then\n'
        'LuaGameResDownloadBridge.DownloadPackage(2,0)\n'
        'end\n',
        encoding="utf-8",
    )

    result = build_fanxiu_lua_download_bridge_report(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["bridge_functions"] == 2
    functions_text = (output_dir / "lua_download_bridge_functions.tsv").read_text(encoding="utf-8")
    calls_text = (output_dir / "lua_download_bridge_call_sites.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "lua_download_bridge_report.md").read_text(encoding="utf-8")
    assert "ContainsScene" in functions_text
    assert "DownloadPackage" in functions_text
    assert "SceneMgr.lua" in calls_text
    assert "凡修 Lua 资源下载桥报告" in markdown_text


def test_fanxiu_il2cpp_download_inventory_filters_download_symbols(tmp_path):
    export_root = tmp_path / "exports"
    index_dir = export_root / "apk_static_index"
    index_dir.mkdir(parents=True)
    (index_dir / "il2cpp_types.tsv").write_text(
        "index\tnamespace\tname\tfull_name\tflags\tbitfield\ttoken\tfield_start\tfield_count\tmethod_start\tmethod_count\tparent_index\tbyval_type_index\tbyref_type_index\telement_type_index\n"
        "1\tMU.GameLogic.GameResDownLoad\tGameResDownLoadMgr\tMU.GameLogic.GameResDownLoad.GameResDownLoadMgr\t\t\t0x0201\t0\t1\t0\t2\t-1\t501\t502\t501\n"
        "2\tOther\tFoo\tOther.Foo\t\t\t0x0202\t1\t0\t2\t1\t-1\t503\t504\t503\n"
        "3\tSystem\tString\tSystem.String\t\t\t0x0203\t-1\t0\t-1\t0\t-1\t73\t74\t73\n"
        "4\tSystem\tInt32\tSystem.Int32\t\t\t0x0204\t-1\t0\t-1\t0\t-1\t169\t170\t169\n"
        "5\tSystem\tVoid\tSystem.Void\t\t\t0x0205\t-1\t0\t-1\t0\t-1\t28578\t28579\t28578\n",
        encoding="utf-8",
    )
    (index_dir / "il2cpp_methods.tsv").write_text(
        "index\towner\tname\tqualified_name\tparameters\tdeclaring_type\treturn_type\ttoken\tflags\tslot\n"
        "1\tMU.GameLogic.GameResDownLoad.GameResDownLoadMgr\tDownloadPackage\tMU.GameLogic.GameResDownLoad.GameResDownLoadMgr.DownloadPackage\tid:type#169\t1\t28578\t0x0601\t\t\n"
        "2\tOther.Foo\tBar\tOther.Foo.Bar\t\t2\t28578\t0x0602\t\t\n",
        encoding="utf-8",
    )
    (index_dir / "il2cpp_fields.tsv").write_text(
        "index\towner\tname\tqualified_name\ttype_index\ttoken\n"
        "1\tMU.GameLogic.GameResDownLoad.GameResDownLoadMgr\tsettingUrl\tMU.GameLogic.GameResDownLoad.GameResDownLoadMgr.settingUrl\t73\t0x0401\n",
        encoding="utf-8",
    )
    (index_dir / "il2cpp_string_literals.tsv").write_text(
        "index\tlength\tdata_index\tvalue\n"
        "1\t20\t0\tGet remote filelistVersion content failed. url=\n"
        "2\t5\t20\tother\n",
        encoding="utf-8",
    )

    result = build_fanxiu_il2cpp_download_inventory(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["types"] == 1
    assert result["counts"]["methods"] == 1
    assert result["counts"]["fields"] == 1
    assert result["counts"]["strings"] == 1
    methods_text = (output_dir / "il2cpp_download_methods.tsv").read_text(encoding="utf-8")
    fields_text = (output_dir / "il2cpp_download_fields.tsv").read_text(encoding="utf-8")
    assert "DownloadPackage" in methods_text
    assert "id:System.Int32" in methods_text
    assert "System.Void" in methods_text
    assert "settingUrl" in fields_text
    assert "System.String" in fields_text
    assert "filelistVersion" in (output_dir / "il2cpp_download_strings.tsv").read_text(encoding="utf-8")


def test_fanxiu_resource_package_report_groups_filelist_packages(tmp_path):
    root = tmp_path / "1023295_unpacked"
    assets_dir = root / "assets"
    resource_root = tmp_path / "frxx_game_files"
    assets_dir.mkdir(parents=True)
    resource_root.mkdir()
    _write_minimal_dex(root / "classes.dex")
    (root / "AndroidManifest.xml").write_bytes(b"\x03\x00manifest")
    (assets_dir / "filelist.csv").write_text(
        "path,size,md5,package\n"
        "ab,10,md5a,-1\n"
        "config/test.bytes,8,md5b,2\n"
        "Audio/test.bnk,12,md5c,2\n",
        encoding="utf-8",
    )
    (assets_dir / "filelist_streaming.csv").write_text("file,md5\nab,md5a\n", encoding="utf-8")
    (assets_dir / "filelistVersion").write_text("v1", encoding="utf-8")
    (assets_dir / "ab").write_bytes(b"asset")
    (resource_root / "config").mkdir()
    (resource_root / "config" / "test.bytes").write_bytes(b"resource")

    result = build_fanxiu_resource_package_report(apk_root=root, resource_root=resource_root, export_root=tmp_path / "exports")
    output_dir = Path(result["output_dir"])

    assert result["counts"]["packages"] == 2
    packages_text = (output_dir / "resource_packages.tsv").read_text(encoding="utf-8")
    assert "package\trole\tfile_count" in packages_text
    assert "2\t" in packages_text
    files_text = (output_dir / "resource_package_files.tsv").read_text(encoding="utf-8")
    assert "config/test.bytes" in files_text
    assert "\t1\t0" in files_text or "\t0\t1" in files_text
    assert "v1" in (output_dir / "resource_package_report.json").read_text(encoding="utf-8")


def test_fanxiu_resource_manifest_diff_report_compares_filelists(tmp_path):
    root = tmp_path / "1023295_unpacked"
    assets_dir = root / "assets"
    resource_root = tmp_path / "frxx_game_files"
    assets_dir.mkdir(parents=True)
    resource_root.mkdir()
    (root / "AndroidManifest.xml").write_bytes(b"\x03\x00manifest")
    (assets_dir / "filelistVersion").write_text("apk-list-v1", encoding="utf-8")
    (resource_root / "filelistVersion").write_text("resource-list-v2", encoding="utf-8")
    (assets_dir / "filelist.csv").write_text(
        "path,size,md5,package\n"
        "ab,10,md5-ab-v1,-1\n"
        "config/unchanged.bytes,20,md5-same,2\n"
        "config/change.bytes,30,md5-old,2\n"
        "Audio/removed.bnk,40,md5-removed,4\n",
        encoding="utf-8",
    )
    (resource_root / "filelist.csv").write_text(
        "path,size,md5,package\n"
        "ab,11,md5-ab-v2,-1\n"
        "config/unchanged.bytes,20,md5-same,2\n"
        "config/change.bytes,35,md5-new,5\n"
        "lua/new.bytes,50,md5-added,9\n",
        encoding="utf-8",
    )

    result = build_fanxiu_resource_manifest_diff_report(
        apk_root=root,
        resource_root=resource_root,
        export_root=tmp_path / "exports",
    )
    output_dir = Path(result["output_dir"])

    assert result["counts"]["by_status"]["added"]["file_count"] == 1
    assert result["counts"]["by_status"]["changed"]["file_count"] == 2
    assert result["counts"]["by_status"]["removed"]["file_count"] == 1
    assert result["counts"]["by_status"]["unchanged"]["file_count"] == 1
    assert result["counts"]["update_candidate_bytes"] == 96
    diff_text = (output_dir / "resource_manifest_diff.tsv").read_text(encoding="utf-8")
    top_dir_text = (output_dir / "resource_manifest_diff_by_top_dir.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "resource_manifest_diff_report.md").read_text(encoding="utf-8")
    assert "added\tlua/new.bytes" in diff_text
    assert "changed\tconfig/change.bytes\tsize,md5,package" in diff_text
    assert "removed\tAudio/removed.bnk" in diff_text
    assert "changed\tconfig" in top_dir_text
    assert "apk-list-v1" in markdown_text
    assert "resource-list-v2" in markdown_text
    assert "凡修资源清单差异报告" in markdown_text


def test_fanxiu_hot_update_lscripts_report_exports_changed_lua_assets(tmp_path, monkeypatch):
    import backend.core.fanxiu_hot_update as fanxiu_hot_update

    resource_root = tmp_path / "frxx_game_files"
    export_root = tmp_path / "exports"
    index_dir = export_root / "apk_static_index"
    resource_root.mkdir()
    index_dir.mkdir(parents=True)
    (index_dir / "resource_manifest_diff.tsv").write_text(
        "status\tpath\tresource_actual_path\tresource_size\tsize_delta\tresource_md5\n"
        "added\tlscripts/gamesystem/game/blld.bytes\tlscripts/gamesystem/game/blld_hash.bytes\t145211\t145211\thash\n"
        "changed\tlscripts/generate/cfg/gongfa.bytes\tlscripts/generate/cfg/gongfa_hash.bytes\t3788020\t276765\thash2\n"
        "added\tatlasnew/blld.bytes\tatlasnew/blld_hash.bytes\t445350\t445350\thash3\n",
        encoding="utf-8",
    )

    def fake_export(path, *, resource_root=None, export_root=None, max_assets=None):
        out_dir = Path(export_root) / "by_source" / Path(str(path)).with_suffix("") / "text_assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        if "gongfa" in str(path):
            asset_path = out_dir / "Gongfa.lua"
            asset_path.write_text("local M = {}\nfunction M.Load() require 'Game.Gongfa'\nend\nreturn M\n", encoding="utf-8")
            name = "Gongfa.lua"
        else:
            asset_path = out_dir / "BLLDSceneMgr.lua"
            asset_path.write_text("local M = {}\nfunction M.Start() require 'Game.BLLD'\nend\nreturn M\n", encoding="utf-8")
            name = "BLLDSceneMgr.lua"
        return {
            "output_dir": str(out_dir),
            "items": [
                {
                    "output_path": str(asset_path),
                    "name": name,
                    "path_id": 123,
                    "byte_size": asset_path.stat().st_size,
                }
            ],
        }

    monkeypatch.setattr(fanxiu_hot_update, "export_fanxiu_unity_text_assets", fake_export)

    result = build_fanxiu_hot_update_lscripts_report(resource_root=resource_root, export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["candidate_bundles"] == 2
    assert result["counts"]["bundles"] == 2
    assert result["counts"]["text_assets"] == 2
    bundles_text = (output_dir / "hot_update_lscripts_bundles.tsv").read_text(encoding="utf-8")
    assets_text = (output_dir / "hot_update_lscripts_text_assets.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "hot_update_lscripts_report.md").read_text(encoding="utf-8")
    assert "blld" in bundles_text
    assert "gongfa" in bundles_text
    assert "BLLDSceneMgr.lua" in assets_text
    assert "Game.BLLD" in assets_text
    assert "凡修热更新 Lua 脚本包索引" in markdown_text


def test_fanxiu_hot_update_feature_probe_parses_new_feature_configs(tmp_path):
    export_root = tmp_path / "exports"
    blld_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "blld_hash" / "text_assets"
    blue_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "bluestarsea_hash" / "text_assets"
    activity_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "activity_hash" / "text_assets"
    blld_dir.mkdir(parents=True)
    blue_dir.mkdir(parents=True)
    activity_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            if isinstance(value, list):
                return "{" + ",".join(value_expr(item) for item in value) + "}"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", row.get("_row_key", 1))
            if isinstance(row_id, str) and not row_id.lstrip("-").isdigit():
                row_key_expr = f"['{row_id}']"
            else:
                row_key_expr = f"[{row_id}]"
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"{row_key_expr}=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(
        blld_dir / "ActivityBase.lua",
        ["id", "activityId", "model", "levelGroup", "faqi", "blueStarSea", "ruleId"],
        [{"id": 1, "activityId": 11620001, "model": 3500000, "levelGroup": 1, "faqi": "1,2", "blueStarSea": 2, "ruleId": 845}],
    )
    write_config(
        blld_dir / "Level.lua",
        ["id", "group", "layer", "stage", "subLayer", "name", "recommendTips", "rewardShowTitle"],
        [{"id": 1, "group": 1, "layer": 1, "stage": 1, "subLayer": 1, "name": "第1关", "recommendTips": "淬灵剑气", "rewardShowTitle": "头像框"}],
    )
    write_config(blld_dir / "ConfigValue.lua", ["id", "value"], [{"id": "LIMIT_TIME", "value": "20"}])
    write_config(
        blue_dir / "Base.lua",
        ["id", "sort", "name", "interface", "opencondition", "openlan"],
        [{"id": 1, "sort": 1, "name": "提纯", "interface": 10308, "opencondition": "CL|999", "openlan": "未开启"}],
    )
    write_config(
        blue_dir / "Tree.lua",
        ["id", "faqiId", "group", "level", "name", "cost", "des"],
        [{"id": 1, "faqiId": 2, "group": 1, "level": 5, "name": "灵海归元", "cost": "Item|29805_1", "des": "攻击资质+10%"}],
    )
    write_config(
        activity_dir / "Activity.lua",
        ["id", "name", "activityId", "startTime", "endTime", "joinConditionDescribe", "redDot"],
        [{"id": 11620001, "name": "百炼轮回", "activityId": 245, "startTime": "start", "endTime": "end", "joinConditionDescribe": "筑基", "redDot": "BLLD"}],
    )

    result = build_fanxiu_hot_update_feature_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["blld_activities"] == 1
    assert result["counts"]["blld_levels"] == 1
    assert result["counts"]["bluestarsea_base"] == 1
    assert "百炼轮回" in (output_dir / "hot_update_blld_activities.tsv").read_text(encoding="utf-8")
    assert "灵海归元" in (output_dir / "hot_update_bluestarsea_tree_summary.tsv").read_text(encoding="utf-8")
    assert "凡修新增玩法配置探针" in (output_dir / "hot_update_feature_probe_report.md").read_text(encoding="utf-8")


def test_fanxiu_bluestarsea_catalog_probe_exports_nodes_and_runtime_evidence(tmp_path):
    export_root = tmp_path / "exports"
    blue_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "bluestarsea_hash" / "text_assets"
    item_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "item_hash" / "text_assets"
    game_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "bluestarsea_hash" / "text_assets"
    blue_dir.mkdir(parents=True)
    item_dir.mkdir(parents=True)
    game_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            if isinstance(value, list):
                return "{" + ",".join(value_expr(item) for item in value) + "}"
            if isinstance(value, dict):
                return "{" + ",".join(f"[{key}]={value_expr(item)}" for key, item in value.items()) + "}"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"[{row_id}]=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(blue_dir / "Base.lua", ["id", "sort", "name", "interface", "opencondition", "openlan"], [{"id": 2, "sort": 2, "name": "淬灵域", "interface": 10312, "opencondition": "CL|25", "openlan": "等待融合"}])
    write_config(
        blue_dir / "Tree.lua",
        ["id", "faqiId", "group", "level", "name", "cost", "des", "faze", "skill", "attr"],
        [
            {"id": 1, "faqiId": 2, "group": 1, "level": 1, "name": "灵海归元", "cost": "Item|29805_1", "des": "攻击资质+2%", "faze": "101", "skill": "201", "attr": {"ATK": 200}},
            {"id": 2, "faqiId": 2, "group": 1, "level": 2, "name": "灵海归元", "cost": "Item|29805_1", "des": "攻击资质+4%", "faze": "102", "skill": "202", "attr": {"ATK": 400}},
        ],
    )
    write_config(blue_dir / "Star.lua", ["id", "faqiId", "group", "jie", "star", "name", "cost", "des", "faze", "attr"], [{"id": 1, "faqiId": 2, "group": 1, "jie": 1, "star": 1, "name": "鸿蒙洗髓", "cost": "Item|29803_1", "des": "星海之力+1%", "faze": "10190001", "attr": {"XINGHAI_ATK_RATE": 100}}])
    write_config(blue_dir / "StarTree.lua", ["id", "faqiId", "group", "item", "condition", "conditionDes", "reward", "quality", "point"], [{"id": 1, "faqiId": 2, "group": "1", "item": 3110209, "condition": "GongfaJie|476601_1", "conditionDes": "九天玄功1重", "reward": "item|29803_3", "quality": 6, "point": "0,0"}])
    write_config(blue_dir / "Wake.lua", ["id", "faqiId", "Wake", "cost", "des", "faze", "skill"], [{"id": 1, "faqiId": 2, "Wake": 1, "cost": "Item|20432001_1", "des": "星海之力+10%", "faze": "10410001", "skill": "3001"}])
    write_config(blue_dir / "Level.lua", ["id", "faqiId", "level", "cost", "reward", "des", "attr", "partnerAttr"], [{"id": 1, "faqiId": 2, "level": 1, "cost": "item|29802_20", "reward": "item|29805_1", "des": "全体仙侣战斗属性+1%", "attr": {"XINGHAI_ATK_RATE": 100}, "partnerAttr": []}])
    write_config(blue_dir / "BreakItem.lua", ["id", "item", "filter", "sort", "energyConsume", "breakObtain"], [{"id": 1, "item": 29806, "filter": 6, "sort": 1, "energyConsume": 150, "breakObtain": "Item|394007003_1"}])
    write_config(blue_dir / "Charging.lua", ["id", "consume", "times", "energy", "fazeId"], [{"id": 1, "consume": "item|1_100", "times": 1, "energy": 300, "fazeId": {10230030: 300}}])
    write_config(
        item_dir / "Item.lua",
        ["id", "name", "descript", "quality", "icon"],
        [
            {"id": 1, "name": "灵石", "descript": "货币", "quality": 1, "icon": "coin"},
            {"id": 29802, "name": "星尘", "descript": "升级材料", "quality": 4, "icon": "dust"},
            {"id": 29803, "name": "淬灵星魄", "descript": "星图奖励", "quality": 5, "icon": "star"},
            {"id": 29805, "name": "进化点", "descript": "节点材料", "quality": 5, "icon": "point"},
            {"id": 29806, "name": "九天玄功", "descript": "分解道具", "quality": 6, "icon": "gongfa"},
            {"id": 3110209, "name": "九天玄功", "descript": "来源道具", "quality": 6, "icon": "gongfa"},
            {"id": 20432001, "name": "觉醒丹", "descript": "觉醒", "quality": 6, "icon": "wake"},
            {"id": 394007003, "name": "荒道残卷", "descript": "分解产物", "quality": 5, "icon": "break"},
        ],
    )
    (game_dir / "BlueStarSeaNetLogic.lua").write_text(
        "function _M.CM_BlueStarSeaTreeFun(self) F_SendMsg(CM_BlueStarSeaTree) end\n"
        "function _M.SM_BlueStarSeaTreeFun(msg) self.Model:SetBlueStarSea(msg) end\n"
        "F_Register(SM_BlueStarSeaTree:getId(),typeof(SM_BlueStarSeaTree))\n",
        encoding="utf-8",
    )
    (game_dir / "BlueStarSeaTreePanel.lua").write_text("self:OpenBlueStarSeaTree()\nlocal energy=300\n", encoding="utf-8")

    result = build_fanxiu_bluestarsea_catalog_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["bases"] == 1
    assert result["counts"]["tree_nodes"] == 1
    assert result["counts"]["star_nodes"] == 1
    assert result["counts"]["runtime_evidence"] >= 4
    assert "灵海归元" in (output_dir / "hot_update_bluestarsea_tree_nodes.tsv").read_text(encoding="utf-8")
    assert "鸿蒙洗髓" in (output_dir / "hot_update_bluestarsea_star_nodes.tsv").read_text(encoding="utf-8")
    assert "九天玄功" in (output_dir / "hot_update_bluestarsea_startree_sources.tsv").read_text(encoding="utf-8")
    assert "BlueStarSea 蓝色星海图鉴探针" in (output_dir / "hot_update_bluestarsea_catalog_report.md").read_text(encoding="utf-8")


def test_fanxiu_bluestarsea_runtime_probe_links_packets_and_netlogic(tmp_path):
    export_root = tmp_path / "exports"
    blue_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "bluestarsea_hash" / "text_assets"
    packet_dir = export_root / "parsed_configs" / "lua_packet_index"
    blue_dir.mkdir(parents=True)
    packet_dir.mkdir(parents=True)
    (blue_dir / "BlueStarSeaNetLogic.lua").write_text(
        "local _CM_BlueStarSeaCharge=require\"GameSystem.Game.Message.module.player.bluestarsea.packet.CM_BlueStarSeaCharge\"\n"
        "local _SM_BlueStarSeaCharge=require\"GameSystem.Game.Message.module.player.bluestarsea.packet.SM_BlueStarSeaCharge\"\n"
        "function _M.LuaBlueStarSeaNetLogic(self)\n"
        "_MessagePool.Inst_get():F_Register(_CM_BlueStarSeaCharge:getId(),typeof(_CM_BlueStarSeaCharge))\n"
        "_MessagePool.Inst_get():F_Register(_SM_BlueStarSeaCharge:getId(),typeof(_SM_BlueStarSeaCharge),function(msg)\n"
        "_M.SM_BlueStarSeaChargeFun(msg)\n"
        "end)\n"
        "end\n"
        "function _M.CM_BlueStarSeaChargeFun(self,times)\n"
        "local cm=SocketManager.Inst_get():GetMessageFromPools(_CM_BlueStarSeaCharge)\n"
        "cm.times=times\n"
        "SocketManager.Inst_get():F_SendMsg(cm)\n"
        "end\n"
        "function _M.SM_BlueStarSeaChargeFun(msg)\n"
        "if msg.code==0 then\n"
        "BlueStarSeaMgr.Inst_get().Model:OnCharge(msg)\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_dir / "BlueStarSeaChargeTipsView.lua").write_text(
        "BlueStarSeaMgr.Inst_get().NetLogic:CM_BlueStarSeaChargeFun(self._chooseCount)\n",
        encoding="utf-8",
    )
    (packet_dir / "packets.tsv").write_text(
        "id\tname\tdirection\tmodule\tfield_count\tbase_class\tbundle\tfile\trelative_path\tpackage\n"
        "98002\tCM_BlueStarSeaCharge\tclient_to_server\tplayer.bluestarsea\t1\tBaseMessage\tmessage\tCM_BlueStarSeaCharge.lua\tpath\tpkg\n"
        "98003\tSM_BlueStarSeaCharge\tserver_to_client\tplayer.bluestarsea\t3\tClientResult\tmessage\tSM_BlueStarSeaCharge.lua\tpath\tpkg\n"
        "98029\tSM_BlueStarSeaEnergyChange\tserver_to_client\tplayer.bluestarsea\t2\tClientResult\tmessage\tSM_BlueStarSeaEnergyChange.lua\tpath\tpkg\n",
        encoding="utf-8",
    )
    (packet_dir / "packet_fields.tsv").write_text(
        "packet_id\tpacket_name\tfield_index\tfield_name\tread_method\ttype_hint\tdirection\tmodule\tbundle\tfile\tline\n"
        "98002\tCM_BlueStarSeaCharge\t1\ttimes\tInt\t\tclient_to_server\tplayer.bluestarsea\tmessage\tCM_BlueStarSeaCharge.lua\t14\n"
        "98003\tSM_BlueStarSeaCharge\t1\tenergy\tInt\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaCharge.lua\t16\n"
        "98003\tSM_BlueStarSeaCharge\t2\ttodayChargingTimes\tInt\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaCharge.lua\t17\n"
        "98003\tSM_BlueStarSeaCharge\t3\tlastRecoverTime\tLong\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaCharge.lua\t18\n"
        "98029\tSM_BlueStarSeaEnergyChange\t1\tenergy\tInt\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaEnergyChange.lua\t15\n"
        "98029\tSM_BlueStarSeaEnergyChange\t2\tlastRecoverTime\tLong\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaEnergyChange.lua\t16\n",
        encoding="utf-8",
    )

    result = build_fanxiu_bluestarsea_runtime_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["packets"] == 3
    assert result["counts"]["flows"] == 2
    assert result["counts"]["call_sites"] == 1
    packets_text = (output_dir / "hot_update_bluestarsea_net_packets.tsv").read_text(encoding="utf-8")
    flows_text = (output_dir / "hot_update_bluestarsea_net_flows.tsv").read_text(encoding="utf-8")
    anomalies_text = (output_dir / "hot_update_bluestarsea_runtime_anomalies.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "hot_update_bluestarsea_runtime_probe_report.md").read_text(encoding="utf-8")
    assert "CM_BlueStarSeaCharge" in packets_text
    assert "times=times" in flows_text
    assert "OnCharge(msg)" in flows_text
    assert "SM_BlueStarSeaEnergyChange" in anomalies_text
    assert "BlueStarSea 客户端运行与网络探针" in markdown_text


def test_fanxiu_bluestarsea_model_state_probe_traces_state_updates_and_ui_reads(tmp_path):
    export_root = tmp_path / "exports"
    blue_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "bluestarsea_hash" / "text_assets"
    blue_dir.mkdir(parents=True)
    (blue_dir / "BlueStarSeaModel.lua").write_text(
        "function _M.OnCharge(self,msg)\n"
        "self.BlueStarSeaData:OnCharge(msg)\n"
        "self:RaiseEvent(BlueStarSeaType.EventType.Charge)\n"
        "end\n"
        "function _M.OnLevelUp(self,msg)\n"
        "self.BlueStarSeaData:OnLevelUp(msg)\n"
        "RedDotMgr.Inst_get():RaiseRedDotEvent(RedDotID.BlueStarSea_UpLevel,true)\n"
        "self:RaiseEvent(BlueStarSeaType.EventType.LevelUp,msg.faqi)\n"
        "end\n"
        "function _M.GetCurrentEnergyValue(self)\n"
        "return self.BlueStarSeaData:GetCurrentEnergyValue()\n"
        "end\n"
        "function _M.BuildPurifyItems(self)\n"
        "return self.BlueStarSeaData:BuildPurifyItems()\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_dir / "BlueStarSeaData.lua").write_text(
        "function _M.OnSyncInfo(self,msg)\n"
        "self._SyncInfo=msg\n"
        "end\n"
        "function _M.OnCharge(self,msg)\n"
        "self._SyncInfo.vo.energy=msg.energy\n"
        "self._SyncInfo.vo.todayChargingTimes=msg.todayChargingTimes\n"
        "self._SyncInfo.vo.lastRecoverTime=msg.lastRecoverTime\n"
        "end\n"
        "function _M.OnLevelUp(self,msg)\n"
        "for _,faqi in Cipairs(self._SyncInfo.vo.faqiList)do\n"
        "faqi.level=msg.faqi.level\n"
        "faqi.star=msg.faqi.star\n"
        "faqi.wake=msg.faqi.wake\n"
        "end\n"
        "end\n"
        "function _M.GetCurrentEnergyValue(self)\n"
        "return self._SyncInfo.vo.energy\n"
        "end\n"
        "function _M.BuildPurifyItems(self)\n"
        "local currentEnergy=self:GetCurrentEnergyValue()\n"
        "local vo=BlueStarSeaPurifyItemVO.new()\n"
        "remaining=remaining-count*costPer\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_dir / "BlueStarSeaType.lua").write_text("return {EventType={Charge='Charge',LevelUp='LevelUp'}}\n", encoding="utf-8")
    (blue_dir / "BlueStarSeaChargeTipsView.lua").write_text(
        "self:BinderEvent(BlueStarSeaMgr.Inst_get().Model,BlueStarSeaType.EventType.Charge,self.F_OnCharge)\n"
        "local energy=BlueStarSeaMgr.Inst_get().Model:GetCurrentEnergyValue()\n"
        "local currentEnergy=syncInfo.vo.energy\n",
        encoding="utf-8",
    )

    result = build_fanxiu_bluestarsea_model_state_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["events"] >= 2
    assert result["counts"]["state_updates"] >= 7
    assert result["counts"]["getters"] == 2
    assert result["counts"]["ui_bindings"] >= 3
    state_text = (output_dir / "hot_update_bluestarsea_data_state_updates.tsv").read_text(encoding="utf-8")
    getter_text = (output_dir / "hot_update_bluestarsea_model_getters.tsv").read_text(encoding="utf-8")
    ui_text = (output_dir / "hot_update_bluestarsea_ui_state_bindings.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "hot_update_bluestarsea_model_state_report.md").read_text(encoding="utf-8")
    assert "sync_field_write\tenergy\tmsg.energy" in state_text
    assert "faqi_state_update\tlevel\tlevel" in state_text
    assert "BuildPurifyItems" in getter_text
    assert "event_binding" in ui_text
    assert "BlueStarSea Model/Data 状态探针" in markdown_text


def test_fanxiu_bluestarsea_support_config_probe_exports_small_tables_and_missing_store(tmp_path):
    export_root = tmp_path / "exports"
    blue_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "bluestarsea_hash" / "text_assets"
    blue_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            row_key = f"['{row_id}']" if isinstance(row_id, str) and not row_id.isdigit() else f"[{row_id}]"
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"{row_key}=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(blue_dir / "ConfigValue.lua", ["id", "value"], [{"id": "LIMIT", "value": "9000"}])
    write_config(blue_dir / "Function.lua", ["id", "funcName", "iconPatch", "icon", "interface"], [])
    write_config(blue_dir / "Filter.lua", ["id", "filterid", "firstFilter", "secondFilter"], [{"id": 1, "filterid": 3, "firstFilter": "属性", "secondFilter": "战斗"}])
    write_config(blue_dir / "Skill.lua", ["id", "name", "pre", "des", "skill"], [{"id": 1, "name": "灵海归元", "pre": "前置", "des": "说明", "skill": 201}])

    result = build_fanxiu_bluestarsea_support_config_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["config_values"] == 1
    assert result["counts"]["functions"] == 0
    assert result["counts"]["filters"] == 1
    assert result["counts"]["skills"] == 1
    assert result["counts"]["missing_tables"] == ["Store"]
    assert "LIMIT\t9000" in (output_dir / "hot_update_bluestarsea_config_values.tsv").read_text(encoding="utf-8")
    assert "属性" in (output_dir / "hot_update_bluestarsea_filters.tsv").read_text(encoding="utf-8")
    assert "灵海归元" in (output_dir / "hot_update_bluestarsea_skills.tsv").read_text(encoding="utf-8")
    assert "BlueStarSea 支撑配置探针" in (output_dir / "hot_update_bluestarsea_support_config_report.md").read_text(encoding="utf-8")


def test_fanxiu_bluestarsea_open_red_dot_probe_exports_gates_rules_and_anomalies(tmp_path):
    export_root = tmp_path / "exports"
    blue_cfg_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "bluestarsea_hash" / "text_assets"
    blue_game_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "bluestarsea_hash" / "text_assets"
    red_dot_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "reddot_hash" / "text_assets"
    open_function_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "open_function_hash" / "text_assets"
    blue_cfg_dir.mkdir(parents=True)
    blue_game_dir.mkdir(parents=True)
    red_dot_dir.mkdir(parents=True)
    open_function_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            row_key = f"['{row_id}']" if isinstance(row_id, str) and not row_id.isdigit() else f"[{row_id}]"
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"{row_key}=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(
        blue_cfg_dir / "Base.lua",
        ["id", "sort", "showcondition", "name", "lockpre", "pre", "interface", "opencondition", "openlan"],
        [
            {"id": 1, "sort": 1, "showcondition": "CL|999", "name": "提纯", "interface": 10308},
            {"id": 2, "sort": 2, "showcondition": "CL|25", "name": "淬灵域", "interface": 10312, "opencondition": "FAQITUIMERGE|2,CL|25", "openlan": "等待融合"},
        ],
    )
    write_config(
        blue_cfg_dir / "ConfigValue.lua",
        ["id", "value"],
        [
            {"id": "OPENCONDITION2", "value": "FAQITUIMERGE|2,CL|25"},
            {"id": "LIMIT", "value": "9000"},
        ],
    )
    write_config(blue_cfg_dir / "Function.lua", ["id", "funcName", "iconPatch", "icon", "interface"], [])
    write_config(
        red_dot_dir / "RedDotId.lua",
        ["id", "parent", "type"],
        [
            {"id": "BLUESEA", "parent": "MAIN", "type": 1},
            {"id": "BlueStarSea_UpLevel", "parent": "BLUESEA", "type": 3},
        ],
    )
    write_config(
        open_function_dir / "OpenFunction.lua",
        ["id", "name", "condition", "showCondition", "redDot", "type", "luaPath"],
        [
            {"id": 350001, "name": "蓝色星海", "condition": "CT|932102", "showCondition": "CL|25", "redDot": "BLUESEA", "type": 1, "luaPath": "BlueStarSeaMainView"},
            {"id": 350002, "name": "淬炼升级", "redDot": "BlueStarSea_UpLevel", "type": 9, "luaPath": "GameSystem.Game.BlueStarSea.Model.View.BlueStarSeaRitualImplementLevelView"},
        ],
    )
    (blue_game_dir / "BlueStarSeaModel.lua").write_text(
        "function _M.CheckUpLevelRedDotByFaqiId(self, faqiId)\n"
        "local cfg=BlueStarSeaData:GetLevelCfg(faqiId,1)\n"
        "local itemCfg,needNum=GameUtil.GetItemIcon(cfg.cost)\n"
        "return GameUtil.GetBackpackNumByItem(itemCfg.id)>=needNum\n"
        "end\n"
        "function _M.CheckDisplayRedDot(self)\n"
        "if not GameUtil.CheckCondition(ConfigValue.OPENCONDITION2) then return false end\n"
        "return self:CalcDisplayRedDotByGroup(2,1)\n"
        "end\n"
        "function _M.InitTreeActiveRedDot(self)\n"
        "if not GameUtil.CheckCondition(ConfigValue.OPENCONDITION2) then return false end\n"
        "self._treeActiveRedDotCache[2]={}\n"
        "return self:CalcTreeActiveRedDotByGroup(2,1)\n"
        "end\n"
        "function _M.OnBackpackItemsChanged(self, ids)\n"
        "local groups=BlueStarSeaData:GetTreeGroupsByCostItem(ids[1])\n"
        "RedDotManager.Inst_get():RaiseRedDotEvent(RedDotID.BlueStarSea_TreeActive)\n"
        "end\n"
        "function _M.RaiseAllRedDotEvents(self)\n"
        "RedDotManager.Inst_get():RaiseRedDotEvent(RedDotID.BlueStarSea_Display)\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaMgr.lua").write_text(
        "function _M.InitRedDot(self)\n"
        "RedDotManager.Inst_get():BindRedDot(RedDotID.BlueStarSea_UpLevel,function()\n"
        "return false\n"
        "end)\n"
        "self._OnBackpackUpdate=function(typeList,subTypeList,itemList,indexList,updateType)\n"
        "RedDotManager.Inst_get():RaiseRedDotEvent(RedDotID.BlueStarSea_Wake)\n"
        "end\n"
        "BackpackMgr.Inst_get().Model:AddEventHandler(BackPackType.UPDATE_VIEW_1,self._OnBackpackUpdate)\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaPartItem.lua").write_text(
        "function _M.UpdateItem(self,data)\n"
        "local ok=GameUtil.CheckCondition(data.opencondition)\n"
        "self.RedDotComp:UpdateShow(self.Model:CheckDisplayRedDotByFaqiId(data.id),RedDotConst.RedDotType.Normal)\n"
        "end\n",
        encoding="utf-8",
    )

    result = build_fanxiu_bluestarsea_open_red_dot_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["open_entries"] == 2
    assert result["counts"]["gate_values"] == 2
    assert result["counts"]["red_dot_rules"] >= 5
    assert result["counts"]["bindings"] >= 4
    assert result["counts"]["red_dot_configs"] >= 4
    assert result["counts"]["lifecycle"] >= 4
    assert result["counts"]["anomalies"] >= 2
    assert "淬灵域" in (output_dir / "hot_update_bluestarsea_open_entries.tsv").read_text(encoding="utf-8")
    assert "CheckUpLevelRedDotByFaqiId\tupgrade_level" in (output_dir / "hot_update_bluestarsea_red_dot_rules.tsv").read_text(encoding="utf-8")
    assert "bind_red_dot_placeholder" in (output_dir / "hot_update_bluestarsea_red_dot_bindings.tsv").read_text(encoding="utf-8")
    assert "OpenFunction\t350001\t蓝色星海" in (output_dir / "hot_update_bluestarsea_red_dot_configs.tsv").read_text(encoding="utf-8")
    assert "manager_event_add" in (output_dir / "hot_update_bluestarsea_red_dot_lifecycle.tsv").read_text(encoding="utf-8")
    assert "manager_red_dot_placeholder" in (output_dir / "hot_update_bluestarsea_open_red_dot_anomalies.tsv").read_text(encoding="utf-8")
    assert "BlueStarSea 开放条件与红点探针" in (output_dir / "hot_update_bluestarsea_open_red_dot_report.md").read_text(encoding="utf-8")


def test_fanxiu_bluestarsea_purify_energy_probe_links_config_ui_packets_and_state(tmp_path):
    export_root = tmp_path / "exports"
    blue_cfg_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "bluestarsea_hash" / "text_assets"
    item_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "item_hash" / "text_assets"
    blue_game_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "bluestarsea_hash" / "text_assets"
    packet_dir = export_root / "parsed_configs" / "lua_packet_index"
    blue_cfg_dir.mkdir(parents=True)
    item_dir.mkdir(parents=True)
    blue_game_dir.mkdir(parents=True)
    packet_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            row_key = f"['{row_id}']" if isinstance(row_id, str) and not row_id.isdigit() else f"[{row_id}]"
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"{row_key}=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(
        item_dir / "Item.lua",
        ["id", "name"],
        [
            {"id": 1, "name": "灵石"},
            {"id": 29806, "name": "玄魔大法"},
            {"id": 394007003, "name": "蓝色星海"},
        ],
    )
    write_config(
        blue_cfg_dir / "BreakItem.lua",
        ["id", "item", "filter", "sort", "energyConsume", "breakObtain"],
        [{"id": 1, "item": 29806, "filter": 6, "sort": 1, "energyConsume": 150, "breakObtain": "Item|394007003_1"}],
    )
    write_config(
        blue_cfg_dir / "Charging.lua",
        ["id", "condition", "consume", "times", "energy", "fazeId"],
        [
            {"id": 1, "consume": "item|1_100", "times": 1, "energy": 300, "fazeId": {10230030: 300}},
            {"id": 2, "condition": "BLUESTARSEASTAR|2_150", "consume": "item|1_200", "times": 2, "energy": 350, "fazeId": {10230030: 350}},
        ],
    )
    write_config(
        blue_cfg_dir / "ConfigValue.lua",
        ["id", "value"],
        [
            {"id": "LIMIT", "value": "9000"},
            {"id": "STARTENERGY", "value": "4500"},
            {"id": "TIMERECOVER", "value": "60"},
            {"id": "SCHEME_LIMIT", "value": "3"},
        ],
    )
    (blue_game_dir / "BlueStarSeaData.lua").write_text(
        "function _M.LuaBlueStarSeaData(self)\n"
        "self.V_EnergyLimit=tonumber(configValue['LIMIT'].value)\n"
        "end\n"
        "function _M.GetCurrentEnergyValue(self)\n"
        "return self._SyncInfo.vo.energy\n"
        "end\n"
        "function _M.OnCharge(self,msg)\n"
        "self._SyncInfo.vo.energy=msg.energy\n"
        "self._SyncInfo.vo.todayChargingTimes=msg.todayChargingTimes\n"
        "self._SyncInfo.vo.lastRecoverTime=msg.lastRecoverTime\n"
        "end\n"
        "function _M.OnPurify(self,msg)\n"
        "self._SyncInfo.vo.energy=msg.energy\n"
        "self._PurifyRewardResults=msg.rewardResults\n"
        "end\n"
        "function _M.BuildPurifyItems(self)\n"
        "local currentEnergy=self:GetCurrentEnergyValue()\n"
        "local allBreakItems=self:GetAllBreakItemList()\n"
        "local vo=BlueStarSeaPurifyItemVO.new()\n"
        "vo.itemId=29806\n"
        "vo.count=math.floor(currentEnergy/150)\n"
        "remaining=remaining-count*costPer\n"
        "return CList.new()\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaModel.lua").write_text(
        "function _M.OnCharge(self,msg)\n"
        "self.BlueStarSeaData:OnCharge(msg)\n"
        "self:RaiseEvent(BlueStarSeaType.EventType.Charge)\n"
        "end\n"
        "function _M.OnPurify(self,msg)\n"
        "self.BlueStarSeaData:OnPurify(msg)\n"
        "self:RaiseEvent(BlueStarSeaType.EventType.Purify)\n"
        "end\n"
        "function _M.BuildPurifyItems(self)\n"
        "return self.BlueStarSeaData:BuildPurifyItems()\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaNetLogic.lua").write_text(
        "function _M.CM_BlueStarSeaChargeFun(self,times)\n"
        "local cm=SocketManager.Inst_get():GetMessageFromPools(_CM_BlueStarSeaCharge)\n"
        "cm.times=times\n"
        "SocketManager.Inst_get():F_SendMsg(cm)\n"
        "end\n"
        "function _M.SM_BlueStarSeaChargeFun(msg)\n"
        "if msg.code==0 then BlueStarSeaMgr.Inst_get().Model:OnCharge(msg) end\n"
        "end\n"
        "function _M.CM_BlueStarSeaPurifyFun(self,items)\n"
        "local cm=SocketManager.Inst_get():GetMessageFromPools(_CM_BlueStarSeaPurify)\n"
        "cm.items=items\n"
        "SocketManager.Inst_get():F_SendMsg(cm)\n"
        "end\n"
        "function _M.SM_BlueStarSeaPurifyFun(msg)\n"
        "if msg.code==0 then\n"
        "BlueStarSeaMgr.Inst_get().Model:OnPurify(msg)\n"
        "CostAndRewardMgr.Inst_get():AddRewardResults(msg.rewardResults,RewardAndCostPopType.BULLET_FRAME)\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaMutiPurifyView.lua").write_text(
        "function _M.OnPurifyConfirm(self)\n"
        "if self._chooseCount<=0 then return end\n"
        "BlueStarSeaMgr.Inst_get().NetLogic:CM_BlueStarSeaPurifyFun(self._pendingItems)\n"
        "end\n"
        "function _M.RefreshSlider(self)\n"
        "local currentEnergy=BlueStarSeaMgr.Inst_get().Model:GetCurrentEnergyValue()\n"
        "local energyLimit=BlueStarSeaMgr.Inst_get().Model:GetEnergyLimit()\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaMutiPurifyComp.lua").write_text(
        "function _M.OnOneKeyClick(self)\n"
        "local items=BlueStarSeaMgr.Inst_get().Model:BuildPurifyItems()\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaPurifyView.lua").write_text(
        "function _M.OnConfirmClick(self)\n"
        "local BlueStarSeaPurifyItemVO=require\"vo\"\n"
        "local vo=BlueStarSeaPurifyItemVO.new()\n"
        "vo.itemId=cfg.item\n"
        "vo.count=self._chooseCount\n"
        "BlueStarSeaMgr.Inst_get().NetLogic:CM_BlueStarSeaPurifyFun(items)\n"
        "end\n",
        encoding="utf-8",
    )
    (blue_game_dir / "BlueStarSeaChargeTipsView.lua").write_text(
        "function _M.UpdateView(self)\n"
        "local cfg=BlueStarSeaMgr.Inst_get().Model:GetChargeCfgByTimes(1)\n"
        "local have=GameUtil.GetItemNumById(1)\n"
        "end\n"
        "function _M.OnConfirmClick(self)\n"
        "BlueStarSeaMgr.Inst_get().NetLogic:CM_BlueStarSeaChargeFun(self._chooseCount)\n"
        "end\n",
        encoding="utf-8",
    )
    (packet_dir / "packets.tsv").write_text(
        "id\tname\tdirection\tmodule\tfield_count\tbase_class\tbundle\tfile\trelative_path\tpackage\n"
        "98002\tCM_BlueStarSeaCharge\tclient_to_server\tplayer.bluestarsea\t1\tBaseMessage\tmessage\tCM_BlueStarSeaCharge.lua\tpath\tpkg\n"
        "98003\tSM_BlueStarSeaCharge\tserver_to_client\tplayer.bluestarsea\t3\tClientResult\tmessage\tSM_BlueStarSeaCharge.lua\tpath\tpkg\n"
        "98004\tCM_BlueStarSeaPurify\tclient_to_server\tplayer.bluestarsea\t1\tBaseMessage\tmessage\tCM_BlueStarSeaPurify.lua\tpath\tpkg\n"
        "98005\tSM_BlueStarSeaPurify\tserver_to_client\tplayer.bluestarsea\t2\tClientResult\tmessage\tSM_BlueStarSeaPurify.lua\tpath\tpkg\n",
        encoding="utf-8",
    )
    (packet_dir / "packet_fields.tsv").write_text(
        "packet_id\tpacket_name\tfield_index\tfield_name\tread_method\ttype_hint\tdirection\tmodule\tbundle\tfile\tline\n"
        "98002\tCM_BlueStarSeaCharge\t1\ttimes\tInt\t\tclient_to_server\tplayer.bluestarsea\tmessage\tCM_BlueStarSeaCharge.lua\t14\n"
        "98003\tSM_BlueStarSeaCharge\t1\tenergy\tInt\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaCharge.lua\t17\n"
        "98004\tCM_BlueStarSeaPurify\t1\titems\tMessageList\tBlueStarSeaPurifyItemVO\tclient_to_server\tplayer.bluestarsea\tmessage\tCM_BlueStarSeaPurify.lua\t14\n"
        "98005\tSM_BlueStarSeaPurify\t1\tenergy\tInt\t\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaPurify.lua\t17\n"
        "98005\tSM_BlueStarSeaPurify\t2\trewardResults\tMessageList\tRewardResultVO\tserver_to_client\tplayer.bluestarsea\tmessage\tSM_BlueStarSeaPurify.lua\t18\n",
        encoding="utf-8",
    )

    result = build_fanxiu_bluestarsea_purify_energy_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["break_items"] == 1
    assert result["counts"]["charging"] == 2
    assert result["counts"]["runtime_flows"] >= 8
    assert result["counts"]["packet_fields"] >= 5
    assert "玄魔大法" in (output_dir / "hot_update_bluestarsea_purify_break_items.tsv").read_text(encoding="utf-8")
    assert "灵石x100" in (output_dir / "hot_update_bluestarsea_purify_charging.tsv").read_text(encoding="utf-8")
    assert "purify_receive" in (output_dir / "hot_update_bluestarsea_purify_runtime_flows.tsv").read_text(encoding="utf-8")
    assert "rewardResults" in (output_dir / "hot_update_bluestarsea_purify_packet_fields.tsv").read_text(encoding="utf-8")
    assert "BlueStarSea 提纯能量链路探针" in (output_dir / "hot_update_bluestarsea_purify_energy_report.md").read_text(encoding="utf-8")


def test_fanxiu_blld_runtime_probe_links_packets_and_netlogic(tmp_path):
    export_root = tmp_path / "exports"
    blld_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "blld_hash" / "text_assets"
    packet_dir = export_root / "parsed_configs" / "lua_packet_index"
    blld_dir.mkdir(parents=True)
    packet_dir.mkdir(parents=True)
    (blld_dir / "BLLDNetLogic.lua").write_text(
        "local _CM_BlldEnter=require\"GameSystem.Game.Message.module.world.blld.packet.CM_BlldEnter\"\n"
        "local _SM_BlldEnter=require\"GameSystem.Game.Message.module.world.blld.packet.SM_BlldEnter\"\n"
        "function _M.LuaBLLDNetLogic(self)\n"
        "_MessagePool.Inst_get():F_Register(_CM_BlldEnter:getId(),typeof(_CM_BlldEnter))\n"
        "_MessagePool.Inst_get():F_Register(_SM_BlldEnter:getId(),typeof(_SM_BlldEnter),function(msg)\n"
        "self.SM_BlldEnterFun(msg)\n"
        "end)\n"
        "end\n"
        "function _M.CM_BlldEnterFun(self,levelId)\n"
        "local CM_BlldEnter=SocketManager.Inst_get():GetMessageFromPools(_CM_BlldEnter)\n"
        "CM_BlldEnter.levelId=levelId\n"
        "SocketManager.Inst_get():F_SendMsg(CM_BlldEnter)\n"
        "end\n"
        "function _M.SM_BlldEnterFun(msg)\n"
        "if msg.code==0 then\n"
        "BLLDMgr.Inst_get().Model:MarkLevelEntered()\n"
        "BLLDMgr.Inst_get():OnBLLDEnterFunc(msg)\n"
        "end\n"
        "end\n",
        encoding="utf-8",
    )
    (blld_dir / "BLLDMgr.lua").write_text(
        "function _M.Start(self,levelId)\n"
        "self.NetLogic:CM_BlldEnterFun(levelId)\n"
        "self.NetLogic.CM_MissingFun()\n"
        "end\n",
        encoding="utf-8",
    )
    (packet_dir / "packets.tsv").write_text(
        "id\tname\tdirection\tmodule\tfield_count\tbase_class\tbundle\tfile\trelative_path\tpackage\n"
        "97328\tCM_BlldEnter\tclient_to_server\tworld.blld\t1\tBaseMessage\tmessage\tCM_BlldEnter.lua\tpath\tpkg\n"
        "97329\tSM_BlldEnter\tserver_to_client\tworld.blld\t3\tClientResult\tmessage\tSM_BlldEnter.lua\tpath\tpkg\n"
        "97338\tCM_BlldFind\tclient_to_server\tworld.blld\t1\tBaseMessage\tmessage\tCM_BlldFind.lua\tpath\tpkg\n",
        encoding="utf-8",
    )
    (packet_dir / "packet_fields.tsv").write_text(
        "packet_id\tpacket_name\tfield_index\tfield_name\tread_method\ttype_hint\tdirection\tmodule\tbundle\tfile\tline\n"
        "97328\tCM_BlldEnter\t1\tlevelId\tInt\t\tclient_to_server\tworld.blld\tmessage\tCM_BlldEnter.lua\t14\n"
        "97329\tSM_BlldEnter\t1\tlevelId\tInt\t\tserver_to_client\tworld.blld\tmessage\tSM_BlldEnter.lua\t17\n"
        "97329\tSM_BlldEnter\t2\troleLevel\tInt\t\tserver_to_client\tworld.blld\tmessage\tSM_BlldEnter.lua\t18\n"
        "97329\tSM_BlldEnter\t3\trewardFindInfo\tMessageMap2Dic\t\tserver_to_client\tworld.blld\tmessage\tSM_BlldEnter.lua\t19\n",
        encoding="utf-8",
    )

    result = build_fanxiu_blld_runtime_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["packets"] == 3
    assert result["counts"]["flows"] == 2
    assert result["counts"]["call_sites"] == 2
    packets_text = (output_dir / "hot_update_blld_net_packets.tsv").read_text(encoding="utf-8")
    flows_text = (output_dir / "hot_update_blld_net_flows.tsv").read_text(encoding="utf-8")
    anomalies_text = (output_dir / "hot_update_blld_runtime_anomalies.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "hot_update_blld_runtime_probe_report.md").read_text(encoding="utf-8")
    assert "CM_BlldEnter" in packets_text
    assert "levelId=levelId" in flows_text
    assert "CM_BlldFind" in anomalies_text
    assert "CM_MissingFun" in anomalies_text
    assert "BLLD 客户端运行与网络探针" in markdown_text


def test_fanxiu_blld_finish_flow_probe_summarizes_result_chain(tmp_path):
    export_root = tmp_path / "exports"
    blld_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "blld_hash" / "text_assets"
    blld_dir.mkdir(parents=True)
    (blld_dir / "BLLDMgr.lua").write_text(
        "function _M.BLLDStartGame(self)\n"
        "self.Model:AddEventHandler(BLLDType.EventType.GameOver,self._OnGameOver)\n"
        "end\n"
        "function _M.OnGameOver(self,isWin)\n"
        "local curLevelId=BLLDMgr.Inst_get().Model:GetLevelId()\n"
        "for _,entry in ipairs(inGameData:GetBagPlacementList())do\n"
        "local id=entry.rewardGroupId\n"
        "end\n"
        "BLLDMgr.Inst_get().NetLogic:CM_BlldFinishAndRewardFun(curLevelId,findReward,isWin)\n"
        "end\n",
        encoding="utf-8",
    )
    (blld_dir / "BLLDFightComponent.lua").write_text(
        "BLLDMgr.Inst_get().Model:RaiseEvent(BLLDType.EventType.GameOver,false)\n"
        "local surviveProgress=BLLDMgr.Inst_get().Model.BLLDData.LASTSURVIVE_PROGRESS\n"
        "BLLDMgr.Inst_get().Model:AddProgressVal(surviveProgress)\n",
        encoding="utf-8",
    )
    (blld_dir / "BLLDNetLogic.lua").write_text(
        "function _M.CM_BlldFinishAndRewardFun(self,levelId,findReward,success)\n"
        "CM_BlldFinishAndReward.passRate=BLLDMgr.Inst_get().Model:GetProgressVal()*100\n"
        "end\n"
        "function _M.SM_BlldFinishAndRewardFun(msg)\n"
        "BLLDMgr.Inst_get().Model:SetFinishAndReward(msg)\n"
        "end\n",
        encoding="utf-8",
    )
    (blld_dir / "BLLDGameResultView.lua").write_text(
        "local msg=BLLDMgr.Inst_get().Model:GetFinishAndReward()\n",
        encoding="utf-8",
    )

    result = build_fanxiu_blld_finish_flow_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["evidence"] >= 8
    evidence_text = (output_dir / "hot_update_blld_finish_flow_evidence.tsv").read_text(encoding="utf-8")
    markdown_text = (output_dir / "hot_update_blld_finish_flow_report.md").read_text(encoding="utf-8")
    assert "CM_BlldFinishAndRewardFun" in evidence_text
    assert "LASTSURVIVE_PROGRESS" in evidence_text
    assert "BLLD 结算链路探针" in markdown_text
    assert "客户端 Lua 的结算链路" in markdown_text


def test_fanxiu_blld_reward_catalog_probe_links_level_rewards_to_items(tmp_path):
    export_root = tmp_path / "exports"
    blld_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "blld_hash" / "text_assets"
    item_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "item_hash" / "text_assets"
    blld_dir.mkdir(parents=True)
    item_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            if isinstance(value, list):
                return "{" + ",".join(value_expr(item) for item in value) + "}"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"[{row_id}]=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(
        blld_dir / "RewardGroup.lua",
        ["id", "item", "limit", "num", "quality", "time", "weight", "bag"],
        [{"id": 1, "item": 1001, "limit": 5, "num": 3, "quality": 4, "time": 1, "weight": 900, "bag": 1}],
    )
    write_config(
        blld_dir / "Level.lua",
        ["id", "stage", "layer", "name", "rewardShowTitle", "pushReward", "findReward"],
        [
            {
                "id": 1,
                "stage": 1,
                "layer": 1,
                "name": "第1关",
                "rewardShowTitle": "通关奖励",
                "pushReward": ["Item|1002_2"],
                "findReward": ["1"],
            }
        ],
    )
    write_config(
        item_dir / "Item.lua",
        ["id", "name", "descript", "quality", "icon"],
        [
            {"id": 1001, "name": "轮回精火", "descript": "探索奖励", "quality": 5, "icon": "icon_fire"},
            {"id": 1002, "name": "天资丹", "descript": "通关奖励", "quality": 6, "icon": "icon_talent"},
        ],
    )

    result = build_fanxiu_blld_reward_catalog_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["reward_groups"] == 1
    assert result["counts"]["level_rewards"] == 1
    assert "轮回精火" in (output_dir / "hot_update_blld_reward_groups.tsv").read_text(encoding="utf-8")
    assert "天资丹x2" in (output_dir / "hot_update_blld_level_rewards.tsv").read_text(encoding="utf-8")
    assert "BLLD 奖励配置探针" in (output_dir / "hot_update_blld_reward_catalog_report.md").read_text(encoding="utf-8")


def test_fanxiu_blld_combat_mechanics_probe_exports_config_and_formula_evidence(tmp_path):
    export_root = tmp_path / "exports"
    blld_cfg_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "blld_hash" / "text_assets"
    blld_game_dir = export_root / "by_source" / "lscripts" / "gamesystem" / "game" / "blld_hash" / "text_assets"
    blld_cfg_dir.mkdir(parents=True)
    blld_game_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            if isinstance(value, list):
                return "{" + ",".join(value_expr(item) for item in value) + "}"
            if isinstance(value, dict):
                return "{" + ",".join(f"{key}={value_expr(item)}" for key, item in value.items()) + "}"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"[{row_id}]=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(
        blld_cfg_dir / "FaQI.lua",
        ["id", "name", "defaultSkill", "damageType", "skillDes", "unLockDesc"],
        [{"id": 1, "name": "淬灵焚炎", "defaultSkill": 1001, "damageType": 1, "skillDes": "火系法器", "unLockDesc": "默认"}],
    )
    write_config(
        blld_cfg_dir / "CharacterSkillInfo.lua",
        ["id", "skillType", "faqiId", "skillGroup", "cd", "range", "interval", "bulletCount", "fireInterval", "targetBuffId"],
        [{"id": 1001, "skillType": 1, "faqiId": 1, "skillGroup": 10, "cd": 4000, "range": 18, "interval": 400, "bulletCount": 2, "fireInterval": 500, "targetBuffId": [1027]}],
    )
    write_config(
        blld_cfg_dir / "FaQiLevel.lua",
        ["id", "faqiId", "level", "attr"],
        [
            {"id": 1, "faqiId": 1, "level": 1, "attr": {"FAQI_ATTACK_RATE": 8800, "SKILL_CD": 6000}},
            {"id": 2, "faqiId": 1, "level": 2, "attr": {"FAQI_ATTACK_RATE": 9000, "SKILL_CD": 5900}},
        ],
    )
    write_config(
        blld_cfg_dir / "CharacterLevel.lua",
        ["id", "group", "level", "cost", "attr"],
        [{"id": 1, "group": 1, "level": 1, "cost": ["Item|1_1"], "attr": {"ATTACK": 1000, "MAXHP": 10000}}],
    )
    write_config(
        blld_cfg_dir / "MonsterInfo.lua",
        ["id", "name", "type", "speed", "defaultSkill", "reduceDamage"],
        [{"id": 101, "name": "雪怪", "type": 1, "speed": 40000, "defaultSkill": 1001, "reduceDamage": "1|200"}],
    )
    write_config(
        blld_cfg_dir / "MonsterRefreshPoint.lua",
        ["id", "group", "type", "refreshWave", "killGold", "monsterId", "Attack", "finalAttack", "MAXHP", "waveTime", "refreshTotalNum", "refreshTime", "refreshNum", "plusLv"],
        [{"id": 21001, "group": 1, "type": 1, "refreshWave": 1, "killGold": 2, "monsterId": 101, "Attack": 50, "finalAttack": 10, "MAXHP": 2066, "waveTime": 10, "refreshTotalNum": 4, "refreshTime": 3000, "refreshNum": 1, "plusLv": 0}],
    )
    write_config(
        blld_cfg_dir / "Level.lua",
        ["id", "name", "group", "layer", "monsterGroup"],
        [{"id": 1, "name": "第1关", "group": 1, "layer": 1, "monsterGroup": 1}],
    )
    write_config(
        blld_cfg_dir / "SkillEnhance.lua",
        ["id", "faqiId", "name", "type", "quality", "time", "des", "condition", "limit", "weight", "effectId"],
        [{"id": 1001, "faqiId": 1, "name": "焚炎回灵·壹", "type": 2, "quality": 5, "time": 1, "des": "技能冷却时间-5%", "condition": 0, "limit": 1, "weight": 100, "effectId": [1001]}],
    )
    write_config(
        blld_cfg_dir / "SkillEnhanceEffect.lua",
        ["id", "skill", "buffId", "extCd", "extDamage", "extReleaseCount", "criticalRate"],
        [{"id": 1001, "skill": 1001, "buffId": 2001, "extCd": -500, "extDamage": 1200, "extReleaseCount": 1, "criticalRate": 300}],
    )
    write_config(
        blld_cfg_dir / "BuffEffect.lua",
        ["id", "type", "triggerType", "duration", "interval", "addAttr"],
        [{"id": 2001, "type": 1, "triggerType": 1, "duration": 3000, "interval": 0, "addAttr": "SKILL_CD_RATE:500"}],
    )
    write_config(blld_cfg_dir / "BloodMoon.lua", ["id", "group", "level", "pram"], [{"id": 1, "group": 1, "level": 1, "pram": 1000}])

    (blld_game_dir / "BLLDSkillData.lua").write_text(
        "damage=tonumber(attr.FAQI_ATTACK_RATE)or damage\n"
        "cd=tonumber(attr.SKILL_CD)or cd\n"
        "function _M.ModifyData(self,updateData)\n"
        "self._damageRate=self._damageRate+updateData.damageRate\n"
        "end\n"
        "local rate=skillData:GetDamageRate()+skillData:GetCriRate()+skillData:GetFireInterval()\n",
        encoding="utf-8",
    )
    (blld_game_dir / "BLLDFightComponent.lua").write_text(
        "function _M.AddPlayerDamage(self,damage)\n"
        "self:SetCurHp(self:GetCurHp()-damage)\n"
        "BLLDMgr.Inst_get().Model:RaiseEvent(BLLDType.EventType.GameOver,false)\n"
        "end\n"
        "local finalAttack=baseAttack*(skillCoeff/10000)*(1+attackRate/10000)\n"
        "local resistance=model:GetMonsterReduceDamage(monsterId,damageType)\n"
        "local increaseDamage=model:GetIncreaseDamage()\n"
        "local finalDamage=finalAttack*(1+increaseDamage/10000)\n"
        "local criAddDamage=model:GetCriAddDamage()\n"
        "BLLDHurtDataExecute(finalDamage)\n"
        "model:RaiseEvent(BLLDType.EventType.Faqi_Damage)\n",
        encoding="utf-8",
    )
    (blld_game_dir / "BLLDEntityMgr.lua").write_text(
        "function _M.InitMonsterRefresh(self,group) end\n"
        "function _M.UpdateType1Monster(self) local waveTime=cfg.waveTime local refreshTime=cfg.refreshTime end\n"
        "function _M.SpawnMonster(self,cfg,pos)\n"
        "local coeff=BLLDMgr.Inst_get().Model:GetBloodMoonCoeff()\n"
        "local monsterCfg={Attack=cfg.Attack*coeff,MAXHP=cfg.MAXHP*coeff}\n"
        "BLLDMgr.Inst_get().Model:AddBloodMoonLevel(1)\n"
        "end\n",
        encoding="utf-8",
    )
    (blld_game_dir / "BLLDBuffAddAttr.lua").write_text(
        "local skillDamageRate=self.V_Data:GetAddExtAttr(BLLDType.BattleAttrType.SkillDamageRate)\n"
        "targetSkill.skillData:ModifyData({damageRate=skillDamageRate})\n"
        "local addAttr='SKILL_CD_RATE:500'\n",
        encoding="utf-8",
    )

    result = build_fanxiu_blld_combat_mechanics_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["faqi_skills"] == 1
    assert result["counts"]["monster_wave_groups"] == 1
    assert result["counts"]["formula_evidence"] >= 12
    assert "淬灵焚炎" in (output_dir / "hot_update_blld_faqi_skills.tsv").read_text(encoding="utf-8")
    assert "雪怪" in (output_dir / "hot_update_blld_monster_waves.tsv").read_text(encoding="utf-8")
    enhance_text = (output_dir / "hot_update_blld_enhance_effects.tsv").read_text(encoding="utf-8")
    assert "焚炎回灵" in enhance_text
    assert "技能冷却缩短 5%" in enhance_text
    assert "finalDamage" in (output_dir / "hot_update_blld_combat_formula_evidence.tsv").read_text(encoding="utf-8")
    assert "BLLD 战斗机制探针" in (output_dir / "hot_update_blld_combat_mechanics_report.md").read_text(encoding="utf-8")


def test_fanxiu_blld_level_catalog_probe_joins_levels_rewards_and_monsters(tmp_path):
    export_root = tmp_path / "exports"
    blld_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "blld_hash" / "text_assets"
    item_dir = export_root / "by_source" / "lscripts" / "generate" / "cfg" / "item_hash" / "text_assets"
    blld_dir.mkdir(parents=True)
    item_dir.mkdir(parents=True)

    def write_config(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
        pool: list[str] = []

        def value_expr(value: object) -> str:
            if isinstance(value, str):
                pool.append(value)
                return f"_A[{len(pool)}]"
            if isinstance(value, list):
                return "{" + ",".join(value_expr(item) for item in value) + "}"
            return str(value)

        key2index = ",".join(f"{field}={index}" for index, field in enumerate(fields, start=1))
        key2null = ",".join(f"[{index}]=''" for index, _field in enumerate(fields, start=1))
        key2type = ",".join(f"[{index}]=0" for index, _field in enumerate(fields, start=1))
        row_lines = []
        for row in rows:
            row_id = row.get("id", 1)
            body = ",".join(
                f"[{index}]={value_expr(row[field])}"
                for index, field in enumerate(fields, start=1)
                if field in row
            )
            row_lines.append(f"[{row_id}]=setmetatable({{{body}}},_P),")
        pool_lines = [f"[{index}]='{value}'," for index, value in enumerate(pool, start=1)]
        path.write_text(
            "local c=require('Generate.Cfg.bean')\n"
            f"local _key2index={{{key2index}}}\n"
            f"local _key2null={{{key2null}}}\n"
            f"local _key2type={{{key2type}}}\n"
            "local _P=c.Init(_key2index,_key2null,_key2type)\n"
            "local _A={\n"
            + "\n".join(pool_lines)
            + "\n}\nlocal _M={\n"
            + "\n".join(row_lines)
            + "\n}\nreturn _M\n",
            encoding="utf-8",
        )

    write_config(
        blld_dir / "Level.lua",
        [
            "id",
            "group",
            "layer",
            "stage",
            "subLayer",
            "rogueGroup",
            "name",
            "recommendTips",
            "rewardShowTitle",
            "monsterGroup",
            "sceneGroup",
            "sceneId",
            "pushReward",
            "findReward",
            "allowSkipLevel",
            "minimumLevel",
        ],
        [
            {
                "id": 1,
                "group": 1,
                "layer": 1,
                "stage": 1,
                "subLayer": 1,
                "rogueGroup": 1,
                "name": "第1关",
                "recommendTips": "本关推荐使用：淬灵剑气",
                "rewardShowTitle": "通关第1关头像框",
                "monsterGroup": 1,
                "sceneGroup": 1,
                "sceneId": 998101,
                "pushReward": ["Item|1002_2"],
                "findReward": ["1"],
                "allowSkipLevel": 9999,
                "minimumLevel": 1,
            }
        ],
    )
    write_config(blld_dir / "RewardGroup.lua", ["id", "item", "limit", "num", "quality", "time", "weight", "bag"], [{"id": 1, "item": 1001, "limit": 5, "num": 3, "quality": 4, "time": 1, "weight": 900, "bag": 1}])
    write_config(blld_dir / "MonsterInfo.lua", ["id", "name", "type", "speed"], [{"id": 101, "name": "雪怪", "type": 1, "speed": 40000}])
    write_config(blld_dir / "MonsterRefreshPoint.lua", ["id", "group", "type", "killGold", "monsterId", "Attack", "finalAttack", "MAXHP", "waveTime", "refreshTotalNum", "refreshTime", "refreshNum"], [{"id": 21001, "group": 1, "type": 1, "killGold": 1, "monsterId": 101, "Attack": 50, "finalAttack": 10, "MAXHP": 2066, "waveTime": 10, "refreshTotalNum": 4, "refreshTime": 3000, "refreshNum": 1}])
    write_config(blld_dir / "ActivityBase.lua", ["id", "activityId", "levelGroup"], [{"id": 1, "activityId": 11620001, "levelGroup": 1}])
    write_config(
        item_dir / "Item.lua",
        ["id", "name", "descript", "quality", "icon"],
        [
            {"id": 1001, "name": "轮回精火", "descript": "探索奖励", "quality": 5, "icon": "fire"},
            {"id": 1002, "name": "天资丹", "descript": "通关奖励", "quality": 6, "icon": "talent"},
        ],
    )

    result = build_fanxiu_blld_level_catalog_probe(export_root=export_root)
    output_dir = Path(result["output_dir"])

    assert result["counts"]["levels"] == 1
    assert result["counts"]["reward_items"] == 2
    levels_text = (output_dir / "hot_update_blld_levels.tsv").read_text(encoding="utf-8")
    rewards_text = (output_dir / "hot_update_blld_level_reward_items.tsv").read_text(encoding="utf-8")
    assert "第1关" in levels_text
    assert "雪怪" in levels_text
    assert "天资丹x2" in levels_text
    assert "轮回精火" in rewards_text
    assert "BLLD 关卡图谱探针" in (output_dir / "hot_update_blld_level_catalog_report.md").read_text(encoding="utf-8")


def test_fanxiu_il2cpp_metadata_probe_exports_core_tables(tmp_path):
    metadata_path = tmp_path / "global-metadata.dat"
    _write_minimal_il2cpp_metadata(metadata_path)

    result = build_fanxiu_il2cpp_metadata_probe(
        metadata_path=metadata_path,
        export_root=tmp_path / "exports",
        keywords=["Player", "Move"],
    )
    output_dir = Path(result["output_dir"])

    assert result["version"] == 24
    assert result["counts"]["types"] == 1
    assert result["counts"]["methods"] == 1
    assert result["counts"]["fields"] == 1
    assert "Game.Player" in (output_dir / "il2cpp_types.tsv").read_text(encoding="utf-8")
    assert "Game.Player.Move" in (output_dir / "il2cpp_methods.tsv").read_text(encoding="utf-8")
    assert "Game.Player.health" in (output_dir / "il2cpp_fields.tsv").read_text(encoding="utf-8")
    assert "Hello" in (output_dir / "il2cpp_string_literals.tsv").read_text(encoding="utf-8")


def test_fanxiu_il2cpp_hot_update_report_groups_matching_types(tmp_path):
    metadata_path = tmp_path / "global-metadata.dat"
    _write_minimal_il2cpp_metadata(metadata_path)

    result = build_fanxiu_il2cpp_hot_update_report(
        metadata_path=metadata_path,
        export_root=tmp_path / "exports",
        keywords=["Player"],
        string_keywords=["Hello"],
    )
    output_dir = Path(result["output_dir"])

    assert result["counts"]["types"] == 1
    assert result["counts"]["methods"] == 1
    assert result["counts"]["fields"] == 1
    assert "Game.Player.Move" in (output_dir / "hot_update_methods.tsv").read_text(encoding="utf-8")
    assert "Hello" in (output_dir / "hot_update_strings.tsv").read_text(encoding="utf-8")
    assert "Game.Player" in (output_dir / "hot_update_report.md").read_text(encoding="utf-8")
