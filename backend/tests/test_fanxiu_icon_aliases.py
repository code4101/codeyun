from __future__ import annotations

from backend.core.fanxiu_resources import _candidate_sprite_atlas_files, export_fanxiu_sprite_icon


def test_fanxiu_icon_alias_metadata_survives_cached_export(tmp_path):
    export_root = tmp_path / "exports"
    output = export_root / "icons" / "icon_item_0067.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"png")

    result = export_fanxiu_sprite_icon("icon_item_0067", resource_root=tmp_path / "resources", export_root=export_root)

    assert result["cached"] is True
    assert result["alias_sprite_name"] == "xmgf_icon_0067"
    assert "玉骨煞甲丹" in result["alias_reason"]


def test_fanxiu_wallet_funds_icon_alias_metadata_survives_cached_export(tmp_path):
    export_root = tmp_path / "exports"
    output = export_root / "icons" / "icon_item_0052.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"png")

    result = export_fanxiu_sprite_icon("icon_item_0052", resource_root=tmp_path / "resources", export_root=export_root)

    assert result["cached"] is True
    assert result["alias_sprite_name"] == "xmgf_icon_0052"
    assert "宗门资金" in result["alias_reason"]


def test_fanxiu_icon_candidate_atlas_expands_prefixed_names(tmp_path):
    atlas_dir = tmp_path / "atlasnew"
    atlas_dir.mkdir()
    talisman = atlas_dir / "talisman_hash.bytes"
    skill = atlas_dir / "skill_hash.bytes"
    skill2 = atlas_dir / "skill2_hash.bytes"
    icon = atlas_dir / "icon_hash.bytes"
    icon7 = atlas_dir / "icon7_hash.bytes"
    for path in [talisman, skill, skill2, icon, icon7]:
        path.write_bytes(b"")

    talisman_candidates = _candidate_sprite_atlas_files(tmp_path, "icon_talisman_0142")
    skill_candidates = _candidate_sprite_atlas_files(tmp_path, "icon_skill_ld_zw_6001")
    item_candidates = _candidate_sprite_atlas_files(tmp_path, "icon_item_0052")

    assert talisman in talisman_candidates
    assert skill in skill_candidates
    assert skill2 in skill_candidates
    assert icon in item_candidates
    assert icon7 in item_candidates
