from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ANNOTATION_PAGE = REPO_ROOT / "frontend/src/standard/fanxiu/data-annotation/page.vue"


def test_fanxiu_data_annotation_frontend_uses_standard_asset_group_names():
    source = DATA_ANNOTATION_PAGE.read_text(encoding="utf-8")

    assert "const DEFAULT_ASSET_GROUP_TITLE = '默认';" in source
    assert "const OCCLUSION_ASSET_GROUP_TITLE = '遮挡';" in source
    assert "title: DEFAULT_ASSET_GROUP_TITLE" in source
    assert re.search(r"<el-checkbox[^>]*>\s*遮挡\s*</el-checkbox>", source)


def test_fanxiu_data_annotation_frontend_keeps_legacy_group_name_compatibility():
    source = DATA_ANNOTATION_PAGE.read_text(encoding="utf-8")

    assert "LEGACY_DEFAULT_ASSET_GROUP_TITLES" in source
    assert "LEGACY_OCCLUSION_ASSET_GROUP_TITLES" in source
    assert "normalizeAssetGroupTitle(node.title)" in source
